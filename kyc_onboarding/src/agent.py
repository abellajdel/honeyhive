"""
LangGraph KYC onboarding workflow with HoneyHive tracing.

HoneyHive tracer init aligns with https://docs.honeyhive.ai/v2/introduction/tracing-quickstart ;
LangChain instrumentor wiring follows fraud-agent LangGraph integration.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Literal, Sequence, TypedDict

from dotenv import load_dotenv
from honeyhive.config import SessionConfig
from honeyhive import HoneyHiveTracer
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from openinference.instrumentation.langchain import LangChainInstrumentor
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExportResult
from pydantic import BaseModel, ConfigDict, Field

from src.prompts import PACKET_SYSTEM, RISK_ASSESSMENT_SYSTEM
from src.rest_logger import ProjectsListForbidden, RestTraceLogger
from src.tools import ALL_TOOLS, TOOL_BY_NAME

# Mono-repo root: ``…/honeyhive`` (parent of ``fraud-agent/`` and ``kyc_onboarding/``).
_REPO_ROOT = Path(__file__).resolve().parents[2]

_tracer: HoneyHiveTracer | None = None
_compiled_graph: Any = None

_DEFAULT_PROJECT = "kyc-onboarding"


def _load_env() -> None:
    """
    Load HoneyHive + OpenAI secrets.

    Prefer the mono-repo root ``.env`` (folder that contains ``fraud-agent/`` and
    ``kyc_onboarding/``), then cwd ``.env``.

    Root ``.env`` uses ``override=True`` so ``HONEYHIVE_PROJECT`` in the file wins over a stale
    value already exported in the shell (``load_dotenv`` defaults to not overriding existing env).
    The second pass keeps ``override=False`` so a local cwd ``.env`` cannot clobber the root file.
    """
    root_dotenv = _REPO_ROOT / ".env"
    if root_dotenv.is_file():
        load_dotenv(root_dotenv, encoding="utf-8", override=True)
    load_dotenv(encoding="utf-8", override=False)


def _llm_client_kwargs() -> dict[str, Any]:
    """Bounded OpenAI HTTP timeouts."""
    kw: dict[str, Any] = {}
    raw_timeout = os.environ.get("OPENAI_REQUEST_TIMEOUT", "180").strip()
    if raw_timeout.lower() not in ("", "none", "0"):
        try:
            kw["timeout"] = float(raw_timeout)
        except ValueError:
            kw["timeout"] = 180.0
    mr = os.environ.get("OPENAI_MAX_RETRIES", "2").strip()
    try:
        kw["max_retries"] = max(0, int(mr))
    except ValueError:
        kw["max_retries"] = 2
    return kw


class KYCState(TypedDict, total=False):
    """Graph state for one onboarding application."""

    application: dict[str, Any]
    applicant_id: str
    verbose: bool
    identity_fields: dict[str, Any]
    document_verification_result: str
    sanctions_screening_result: str
    address_verification_result: str
    risk_assessment: str
    onboarding_packet: dict[str, Any]


class PacketIdentitySnapshot(BaseModel):
    """Frozen shape for OpenAI structured outputs (requires explicit keys, no arbitrary dict)."""

    model_config = ConfigDict(extra="forbid")

    full_name: str
    date_of_birth: str
    government_id_country: str
    government_id_last4: str
    street_address: str
    city: str
    region: str
    postal_code: str
    country_of_residence: str


class PacketVerificationSummary(BaseModel):
    """One-line summaries per verification stage."""

    model_config = ConfigDict(extra="forbid")

    document: str = Field(description="Brief document check summary.")
    sanctions: str = Field(description="Brief sanctions / PEP / media summary.")
    address: str = Field(description="Brief address corroboration summary.")


class OnboardingPacketModel(BaseModel):
    """Compliance officer-facing packet."""

    model_config = ConfigDict(extra="forbid")

    applicant_id: str = Field(description="Applicant identifier.")
    full_name: str
    risk_rating: Literal["low", "medium", "high"]
    recommendation: Literal["approve", "enhanced_due_diligence", "decline"]
    identity_snapshot: PacketIdentitySnapshot
    verification_summary: PacketVerificationSummary
    rationale: str = Field(description="Brief narrative for reviewer.")
    next_steps_for_officer: list[str] = Field(
        description="Checklist bullets (human actions only).",
    )


def get_tracer() -> HoneyHiveTracer:
    if _tracer is None:
        raise RuntimeError("HoneyHive tracer not initialized; call init_honeyhive() first.")
    return _tracer


def flush_honeyhive_tracer() -> None:
    """Drain queued OTLP spans (call after CLI output)."""
    if os.environ.get("HONEYHIVE_SKIP_FLUSH", "").strip().lower() in ("1", "true", "yes"):
        print("[kyc-onboarding] HONEYHIVE_SKIP_FLUSH set — skipping OTLP flush.", flush=True)
        return
    tracer = get_tracer()
    print(
        "[kyc-onboarding] flushing OTLP spans to HoneyHive …",
        flush=True,
    )
    flush_ms = float(os.environ.get("HONEYHIVE_FLUSH_TIMEOUT_MS", "120000"))
    tracer.force_flush(timeout_millis=max(5000.0, flush_ms))
    print("[kyc-onboarding] OTLP flush complete.", flush=True)


def _apply_otlp_http_timeout(tracer: HoneyHiveTracer) -> None:
    raw = os.environ.get("HH_OTLP_HTTP_TIMEOUT", "120").strip()
    if not raw:
        return
    try:
        seconds = float(raw)
    except ValueError:
        return
    if seconds <= 0:
        return
    exp = getattr(tracer, "otlp_exporter", None)
    if exp is None:
        return
    inner = getattr(exp, "_otlp_exporter", None)
    if inner is not None and hasattr(inner, "timeout"):
        inner.timeout = seconds


def _patch_otlp_json_export_retries(tracer: HoneyHiveTracer) -> None:
    raw_attempts = os.environ.get("HH_OTLP_EXPORT_ATTEMPTS", "5").strip()
    try:
        attempts = max(1, int(raw_attempts))
    except ValueError:
        attempts = 5
    raw_delay = os.environ.get("HH_OTLP_EXPORT_INITIAL_DELAY", "2.0").strip()
    try:
        base_delay = max(0.0, float(raw_delay))
    except ValueError:
        base_delay = 2.0

    exp = getattr(tracer, "otlp_exporter", None)
    if exp is None:
        return
    inner = getattr(exp, "_otlp_exporter", None)
    if inner is None or not hasattr(inner, "export"):
        return
    if getattr(inner, "_hh_retry_patched", False):
        return

    orig = inner.export

    def export_with_retry(spans: Sequence[ReadableSpan]) -> SpanExportResult:
        result = SpanExportResult.FAILURE
        for attempt in range(attempts):
            result = orig(spans)
            if result == SpanExportResult.SUCCESS:
                return result
            if attempt < attempts - 1 and base_delay > 0:
                time.sleep(base_delay * (2**attempt))
        return result

    inner.export = export_with_retry  # type: ignore[method-assign]
    setattr(inner, "_hh_retry_patched", True)


def _disable_batch_from_env() -> bool:
    v = os.environ.get("HONEYHIVE_OTLP_BATCH", "1").strip().lower()
    if v in ("0", "false", "no", "off"):
        return True
    return False


def honeyhive_project_name() -> str:
    """HoneyHive project slug — ``HONEYHIVE_PROJECT`` (swap in ``.env`` when switching demos)."""
    return os.environ.get("HONEYHIVE_PROJECT", _DEFAULT_PROJECT).strip() or _DEFAULT_PROJECT


_KYC_DEMO_VIOLATION_TOOLS = frozenset(
    {"create_account", "send_welcome_kit", "request_additional_documents", "override_sanctions_match"}
)


def _requested_demo_kyc_tool_misuse(applicant_id: str) -> str | None:
    """
    Optional scripted forbidden tool span after the risk LLM (deterministic evaluator demos).

    Env:
        KYC_AGENT_SIMULATE_TOOL_MISUSE — off if unset/false; ``1`` / ``true`` → ``create_account``;
          or explicit tool name from the forbidden set above.
        KYC_AGENT_SIMULATE_TOOL_MISUSE_APPLICANT — filter id (default ``kyc_005``); ``*`` matches all.

    Synthetic arguments are labeled **[demo only]** so traces stay honest versus organic misuse.
    """
    raw = os.environ.get("KYC_AGENT_SIMULATE_TOOL_MISUSE", "").strip().lower()
    if not raw or raw in ("0", "false", "no", "off"):
        return None
    if raw in ("1", "true", "yes", "on"):
        tool = "create_account"
    elif raw in _KYC_DEMO_VIOLATION_TOOLS:
        tool = raw
    else:
        return None

    filt_raw = os.environ.get("KYC_AGENT_SIMULATE_TOOL_MISUSE_APPLICANT")
    if filt_raw is None:
        filt = "kyc_005"
    else:
        filt = filt_raw.strip() or "kyc_005"
    match_all = filt in ("*", "any", "all")
    if not match_all and applicant_id != filt:
        return None
    return tool


def init_honeyhive() -> HoneyHiveTracer:
    """Initialize HoneyHive tracer + LangChain instrumentation for OTLP exports."""
    global _tracer
    if _tracer is not None:
        return _tracer

    _load_env()
    api_key = os.environ.get("HONEYHIVE_API_KEY")
    project = honeyhive_project_name()
    if not api_key:
        raise ValueError("HONEYHIVE_API_KEY is not set. Add it to repo-root .env .")
    if not os.environ.get("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY is not set.")

    os.environ.setdefault("HH_OTLP_PROTOCOL", "http/protobuf")

    use_backend_session = os.environ.get("HONEYHIVE_USE_BACKEND_SESSION", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    session_cfg: SessionConfig | None = None
    if not use_backend_session:
        session_cfg = SessionConfig(
            session_id=str(uuid.uuid4()),
            skip_backend_session_creation=True,
        )

    kwargs: dict[str, Any] = dict(
        api_key=api_key,
        project=project,
        source="development",
        disable_batch=_disable_batch_from_env(),
    )
    if session_cfg is not None:
        kwargs["session_config"] = session_cfg

    _tracer = HoneyHiveTracer.init(**kwargs)
    _apply_otlp_http_timeout(_tracer)
    _patch_otlp_json_export_retries(_tracer)
    LangChainInstrumentor().instrument(tracer_provider=_tracer.provider)
    return _tracer


def _build_graph(rest: RestTraceLogger | None = None) -> Any:
    """Linear LangGraph: intake → verification chain → LLM nodes."""

    llm_tools = ChatOpenAI(model="gpt-4o", temperature=0, **_llm_client_kwargs())
    llm_with_tools = llm_tools.bind_tools(ALL_TOOLS)

    def exec_risk_tool(name: str, arguments: dict[str, Any]) -> str:
        tool = TOOL_BY_NAME.get(name)
        if tool is None:
            raise ValueError(f"Unknown tool: {name}")
        if rest:
            with rest.span(name, event_type="tool", inputs=arguments) as s:
                out = str(tool.invoke(arguments))
                s.outputs = {"result": out}
            return out
        return str(tool.invoke(arguments))

    def _invoke_stage_tool(tool_name: str, applicant_id: str) -> str:
        arguments = {"applicant_id": applicant_id}
        return exec_risk_tool(tool_name, arguments)

    def intake(state: KYCState) -> dict[str, Any]:
        """
        Parse the application payload and extracted stable applicant identity fields
        suitable for reviewer packet.
        """
        app = state["application"]
        aid = str(app["applicant_id"])
        identity = {
            "full_name": str(app.get("full_name", "")),
            "date_of_birth": str(app.get("date_of_birth", "")),
            "government_id_country": str(app.get("government_id_country", "")),
            "government_id_last4": str(app.get("government_id_last4", "")),
            "street_address": str(app.get("street_address", "")),
            "city": str(app.get("city", "")),
            "region": str(app.get("region", "")),
            "postal_code": str(app.get("postal_code", "")),
            "country_of_residence": str(app.get("country_of_residence", "")),
        }
        if state.get("verbose"):
            print(f"[intake] applicant_id={aid}", flush=True)
        if rest:
            with rest.span("intake", event_type="chain", inputs={"application": app}) as s:
                s.outputs = {"applicant_id": aid, "identity_fields": identity}
        return {"applicant_id": aid, "identity_fields": identity}

    def document_verification(state: KYCState) -> dict[str, Any]:
        """Mock identity document validity and quality scoring."""
        aid = state["applicant_id"]
        if rest:
            with rest.span(
                "document_verification",
                event_type="chain",
                inputs={"applicant_id": aid},
            ) as s:
                out = _invoke_stage_tool("verify_identity_document", aid)
                s.outputs = {"verification_payload": json.loads(out)}
        else:
            out = _invoke_stage_tool("verify_identity_document", aid)
        if state.get("verbose"):
            print(f"[document_verification] result length={len(out)}", flush=True)
        return {"document_verification_result": out}

    def sanctions_screening(state: KYCState) -> dict[str, Any]:
        """Mock OFAC / PEP / adverse media screening."""
        aid = state["applicant_id"]
        if rest:
            with rest.span(
                "sanctions_screening",
                event_type="chain",
                inputs={"applicant_id": aid},
            ) as s:
                out = _invoke_stage_tool("screen_sanctions_lists", aid)
                s.outputs = {"sanctions_payload": json.loads(out)}
        else:
            out = _invoke_stage_tool("screen_sanctions_lists", aid)
        if state.get("verbose"):
            print(f"[sanctions_screening] result length={len(out)}", flush=True)
        return {"sanctions_screening_result": out}

    def address_verification(state: KYCState) -> dict[str, Any]:
        """Mock postal / utility address corroboration."""
        aid = state["applicant_id"]
        if rest:
            with rest.span(
                "address_verification",
                event_type="chain",
                inputs={"applicant_id": aid},
            ) as s:
                out = _invoke_stage_tool("verify_address", aid)
                s.outputs = {"address_payload": json.loads(out)}
        else:
            out = _invoke_stage_tool("verify_address", aid)
        if state.get("verbose"):
            print(f"[address_verification] result length={len(out)}", flush=True)
        return {"address_verification_result": out}

    def risk_assessment(state: KYCState) -> dict[str, Any]:
        """LLM step: synthesize verification signals and optional advisory tool reuse."""
        tx = json.dumps(state["application"], indent=2)
        human = HumanMessage(
            content=(
                f"Applicant onboarding application JSON:\n{tx}\n\n"
                f"Identity extraction (canonical):\n"
                f"{json.dumps(state.get('identity_fields', {}), indent=2)}\n\n"
                f"verify_identity_document result:\n{state.get('document_verification_result','')}\n\n"
                f"screen_sanctions_lists result:\n{state.get('sanctions_screening_result','')}\n\n"
                f"verify_address result:\n{state.get('address_verification_result','')}\n\n"
                "Provide your risk synthesis and implied rating / advisory recommendation "
                "(approve vs enhanced due diligence vs decline). Tools only if strictly needed."
            )
        )
        messages: list = [SystemMessage(content=RISK_ASSESSMENT_SYSTEM), human]

        def _run_loop() -> str:
            for _ in range(12):
                ai: AIMessage = llm_with_tools.invoke(messages)
                messages.append(ai)
                if not ai.tool_calls:
                    return ai.content or ""
                for tc in ai.tool_calls:
                    name = str(tc["name"])
                    arguments = dict(tc.get("args") or {})
                    if name not in TOOL_BY_NAME:
                        raise ValueError(f"Unknown tool requested by model: {name}")
                    out_tool = exec_risk_tool(name, arguments)
                    messages.append(ToolMessage(content=out_tool, tool_call_id=str(tc["id"])))
            final_msg = messages[-1]
            if isinstance(final_msg, AIMessage) and isinstance(final_msg.content, str):
                return final_msg.content
            return "risk_assessment stopped after iteration cap"

        def _finalize_assessment_narrative() -> str:
            body = _run_loop()
            misuse = _requested_demo_kyc_tool_misuse(str(state["application"]["applicant_id"]))
            if misuse:
                app = state["application"]
                aid = str(app["applicant_id"])
                label = "[demo only] scripted policy-violating tool span; not model-driven."
                if misuse == "create_account":
                    acct_types = app.get("intended_products") or ["checking_basic"]
                    acct_type = acct_types[0] if isinstance(acct_types, list) else "checking_basic"
                    exec_risk_tool("create_account", {"applicant_id": aid, "account_type": str(acct_type)})
                elif misuse == "send_welcome_kit":
                    addr = json.dumps(app.get("street_address"), default=str)
                    exec_risk_tool("send_welcome_kit", {"applicant_id": aid, "address": addr})
                elif misuse == "request_additional_documents":
                    exec_risk_tool(
                        "request_additional_documents",
                        {"applicant_id": aid, "document_type": "proof_of_address"},
                    )
                elif misuse == "override_sanctions_match":
                    exec_risk_tool(
                        "override_sanctions_match",
                        {"applicant_id": aid, "justification": label},
                    )
                if state.get("verbose"):
                    print(f"[risk_assessment] demo simulated misuse span: {misuse}", flush=True)
            return body

        if rest:
            with rest.span(
                "risk_assessment",
                event_type="model",
                inputs={"applicant_id": state["application"].get("applicant_id"), "model": "gpt-4o"},
                config={"model": "gpt-4o", "provider": "openai", "temperature": 0},
            ) as span:
                text = _finalize_assessment_narrative()
                span.outputs = {"risk_assessment": text}
        else:
            text = _finalize_assessment_narrative()

        if state.get("verbose"):
            print(f"[risk_assessment] narrative length={len(text)} chars", flush=True)

        return {"risk_assessment": text}

    def packet_generation(state: KYCState) -> dict[str, Any]:
        """Structured onboarding packet JSON for downstream compliance reviewers."""
        llm = ChatOpenAI(model="gpt-4o", temperature=0, **_llm_client_kwargs())
        structured = llm.with_structured_output(OnboardingPacketModel)
        prompt = HumanMessage(
            content=(
                f"Application JSON:\n{json.dumps(state['application'], indent=2)}\n\n"
                f"Risk narrative:\n{state.get('risk_assessment','')}\n\n"
                f"Captured identity:\n{json.dumps(state.get('identity_fields', {}), indent=2)}\n\n"
                "Document verification JSON line:\n"
                f"{state.get('document_verification_result','')}\n\n"
                "Sanctions screening JSON line:\n"
                f"{state.get('sanctions_screening_result','')}\n\n"
                "Address verification JSON line:\n"
                f"{state.get('address_verification_result','')}\n\n"
                "Produce the structured onboarding_packet fields; "
                "identity_snapshot must mirror Captured identity JSON exactly; "
                "verification_summary pulls from the three verification payloads."
            )
        )

        if rest:
            with rest.span(
                "packet_generation",
                event_type="model",
                inputs={"applicant_id": state["applicant_id"]},
                config={"model": "gpt-4o", "provider": "openai", "structured_output": "OnboardingPacketModel"},
            ) as span:
                result: OnboardingPacketModel = structured.invoke(
                    [SystemMessage(content=PACKET_SYSTEM), prompt]
                )
                pak = result.model_dump()
                span.outputs = pak
        else:
            result = structured.invoke([SystemMessage(content=PACKET_SYSTEM), prompt])
            pak = result.model_dump()

        if state.get("verbose"):
            print(
                f"[packet_generation] recommendation={pak.get('recommendation')} "
                f"risk_rating={pak.get('risk_rating')}",
                flush=True,
            )
        return {"onboarding_packet": pak}

    g = StateGraph(KYCState)
    g.add_node("intake", intake)
    g.add_node("document_verification", document_verification)
    g.add_node("sanctions_screening", sanctions_screening)
    g.add_node("address_verification", address_verification)
    g.add_node("risk_assessment", risk_assessment)
    g.add_node("packet_generation", packet_generation)

    g.add_edge(START, "intake")
    g.add_edge("intake", "document_verification")
    g.add_edge("document_verification", "sanctions_screening")
    g.add_edge("sanctions_screening", "address_verification")
    g.add_edge("address_verification", "risk_assessment")
    g.add_edge("risk_assessment", "packet_generation")
    g.add_edge("packet_generation", END)

    return g.compile()


def get_compiled_graph(rest: RestTraceLogger | None = None) -> Any:
    """Cached compile when REST logger is omitted; rebuilt per-session when REST is wired."""
    global _compiled_graph
    if rest is not None:
        return _build_graph(rest=rest)
    if _compiled_graph is None:
        _compiled_graph = _build_graph(rest=None)
    return _compiled_graph


def honeyhive_trace_url(session_id: str) -> str:
    """Best-effort dashboard URL — search traces by session id if the route changes."""
    base = os.environ.get("HONEYHIVE_UI_BASE", "https://app.honeyhive.ai").rstrip("/")
    return f"{base}/traces/sessions/{session_id}"


def _use_rest_logger() -> bool:
    """Default True: HoneyHive REST /events mirrors fraud-agent resilience story."""
    return os.environ.get("HONEYHIVE_DISABLE_REST", "").strip().lower() not in (
        "1",
        "true",
        "yes",
    )


def _use_otlp() -> bool:
    return os.environ.get("HONEYHIVE_ENABLE_OTLP", "").strip().lower() in ("1", "true", "yes")


def run_single_application(application: dict[str, Any], *, verbose: bool = False) -> dict[str, Any]:
    """
    Execute the graph for one application and log HoneyHive telemetry.

    Default path uses REST /session/start + /events (`HONEYHIVE_PROJECT`).
    Optionally enable OTLP with ``HONEYHIVE_ENABLE_OTLP=1`` (dual export when REST stays on).

    Loads env from mono-repo root ``.env`` when present (same file as fraud-agent).
    """
    _load_env()
    applicant_id = str(application.get("applicant_id", uuid.uuid4()))
    print(f"[kyc-onboarding] starting applicant {applicant_id} …", flush=True)

    api_key = os.environ.get("HONEYHIVE_API_KEY")
    project = honeyhive_project_name()
    env_set = bool(os.environ.get("HONEYHIVE_PROJECT", "").strip())
    suffix = (
        "from HONEYHIVE_PROJECT in .env"
        if env_set
        else f"default {_DEFAULT_PROJECT!r} — set HONEYHIVE_PROJECT to your KYC project slug"
    )
    print(f"[kyc-onboarding] HoneyHive project={project!r} ({suffix})", flush=True)

    if not api_key:
        raise ValueError("HONEYHIVE_API_KEY is not set.")
    if not os.environ.get("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY is not set.")

    rest: RestTraceLogger | None = None
    if _use_rest_logger():
        print("[kyc-onboarding] creating HoneyHive session via REST …", flush=True)
        rest = RestTraceLogger(api_key=api_key, project=project, source="development")
        # Pre-flight: confirm project exists for this API key so traces don't quietly land
        # in a default workspace project. A 401/403 here is diagnostic — it indicates a
        # project-scoped API key (not workspace-scoped), which forces all traces into that
        # key's home project regardless of payload.
        try:
            status = rest.ensure_project_exists(auto_create=True)
            print(
                f"[kyc-onboarding] HoneyHive project pre-flight: {project!r} {status}",
                flush=True,
            )
        except ProjectsListForbidden as forbidden:
            print(
                f"[kyc-onboarding] WARNING: project pre-flight skipped — {forbidden}\n"
                f"[kyc-onboarding] If traces appear under a different project than {project!r}, "
                "create a workspace-scoped API key in HoneyHive (Settings → Workspace → API Keys) "
                "and update HONEYHIVE_API_KEY in .env.",
                flush=True,
            )
        rest.start_session(
            name=f"kyc-onboarding-{applicant_id}",
            inputs={"applicant_id": applicant_id},
        )
        print(f"[kyc-onboarding] session_id={rest.session_id}", flush=True)

    if _use_otlp():
        init_honeyhive()

    session_id = rest.session_id if rest else str(uuid.uuid4())
    graph = get_compiled_graph(rest=rest)

    initial: KYCState = {"application": application, "verbose": verbose}
    print("[kyc-onboarding] invoking graph …", flush=True)
    final: KYCState = graph.invoke(initial)
    print("[kyc-onboarding] graph finished.", flush=True)

    packet = final.get("onboarding_packet") or {}

    if rest:
        print("[kyc-onboarding] finalizing session …", flush=True)
        rest.end_session(
            outputs={"onboarding_packet": packet, "applicant_id": applicant_id},
            metadata={"demo": "kyc-onboarding", "framework": "langgraph"},
        )
        print("[kyc-onboarding] trace logged.", flush=True)

    return {
        "session_id": session_id,
        "trace_url": honeyhive_trace_url(session_id),
        "onboarding_packet": packet,
        "final_state": final,
    }
