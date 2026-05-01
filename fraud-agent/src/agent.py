"""
LangGraph fraud triage workflow with HoneyHive OpenTelemetry tracing.

LangGraph instrumentation follows:
https://docs.honeyhive.ai/v2/integrations/langgraph (``LangChainInstrumentor`` +
``tracer_provider``). HoneyHive tracer init: tracing quickstart.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Callable, Literal, Sequence, TypedDict

from dotenv import load_dotenv
from honeyhive.config import SessionConfig
from honeyhive import HoneyHiveTracer
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from opentelemetry import trace as otel_trace
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from openinference.instrumentation.langchain import LangChainInstrumentor
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExportResult
from pydantic import BaseModel, Field

from src.prompts import DECISION_SYSTEM, RISK_SCORING_SYSTEM
from src.rest_logger import RestTraceLogger
from src.tools import ALL_TOOLS, TOOL_BY_NAME

_tracer: HoneyHiveTracer | None = None
_compiled_graph: Any = None


def _llm_client_kwargs() -> dict[str, Any]:
    """
    Bound OpenAI-backed ChatOpenAI calls so stalled HTTP requests do not hang the CLI forever.

    Env:
        OPENAI_REQUEST_TIMEOUT — seconds per request (default 180). Set ``0`` or ``none`` to omit
            (OpenAI SDK / httpx defaults, not recommended for unattended runs).
        OPENAI_MAX_RETRIES — LangChain/OpenAI retries on transient failures (default 2).
    """
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


class FraudState(TypedDict, total=False):
    """Graph state for a single triage run."""

    transaction: dict[str, Any]
    customer_id: str
    transaction_id: str
    customer_enrichment: str
    device_enrichment: str
    risk_assessment: str
    verdict: dict[str, Any]
    verbose: bool


class VerdictModel(BaseModel):
    """Structured decision output."""

    verdict: Literal["clear", "escalate", "block_recommended"]
    reason: str = Field(description="Short analyst-facing rationale.")
    confidence: float = Field(ge=0.0, le=1.0)


def _instrument_tool_call(
    hh: HoneyHiveTracer,
    tool_name: str,
    inputs: dict[str, Any],
    fn: Callable[[], str],
) -> str:
    """
    Emit an OpenTelemetry span named after the tool so HoneyHive shows per-tool rows
    (and online evaluators can match ``event_name`` / span name to policy allowlists).
    """
    otel = otel_trace.get_tracer("fraud-triage-tools", tracer_provider=hh.provider)
    with otel.start_as_current_span(tool_name) as span:
        span.set_attribute("tool.name", tool_name)
        span.set_attribute("input", json.dumps(inputs))
        out = fn()
        span.set_attribute("output", str(out)[:12000])
        return str(out)


def get_tracer() -> HoneyHiveTracer:
    if _tracer is None:
        raise RuntimeError("HoneyHive tracer not initialized; call init_honeyhive() first.")
    return _tracer


def flush_honeyhive_tracer() -> None:
    """
    Drain queued OTLP spans. Call **after** printing CLI results: ``force_flush`` can block for minutes
    when the gateway retries (504/timeouts).

    Env:
        HONEYHIVE_SKIP_FLUSH — if ``1`` / ``true`` / ``yes``, skip (not recommended).
    """
    if os.environ.get("HONEYHIVE_SKIP_FLUSH", "").strip().lower() in ("1", "true", "yes"):
        print("[fraud-triage] HONEYHIVE_SKIP_FLUSH set — skipping OTLP flush.", flush=True)
        return
    tracer = get_tracer()
    print(
        "[fraud-triage] flushing OTLP spans to HoneyHive (may take ~1–2 min if export retries hit 504/timeouts) …",
        flush=True,
    )
    flush_ms = float(os.environ.get("HONEYHIVE_FLUSH_TIMEOUT_MS", "120000"))
    tracer.force_flush(timeout_millis=max(5000.0, flush_ms))
    print("[fraud-triage] OTLP flush complete.", flush=True)


def _apply_otlp_http_timeout(tracer: HoneyHiveTracer) -> None:
    """
    The HoneyHive SDK constructs the OTLP JSON exporter with a fixed 30s read timeout.
    Slow networks or API latency can exceed that; raise the inner exporter timeout when set.

    Env:
        HH_OTLP_HTTP_TIMEOUT — seconds for OTLP HTTP POST (default 120). Set 0 to leave SDK default.
    """
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
    """
    HoneyHive's gateway sometimes returns 502/504 on OTLP ingest. The SDK exporter treats that as
    FAILURE with no retry. Re-wrap the JSON exporter's ``export`` with bounded exponential backoff.

    Env:
        HH_OTLP_EXPORT_ATTEMPTS — max tries per export (default 5). Set 1 to disable retries.
        HH_OTLP_EXPORT_INITIAL_DELAY — base sleep in seconds before the second try (default 2.0).
    """
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
    """
    When False, the SDK uses a BatchSpanProcessor (fewer HTTP posts; friendlier to flaky gateways).

    Env:
        HONEYHIVE_OTLP_BATCH — if true/1/yes (default), use batching (disable_batch=False).
        Set to false/0/no for immediate per-span export (disable_batch=True).
    """
    v = os.environ.get("HONEYHIVE_OTLP_BATCH", "1").strip().lower()
    if v in ("0", "false", "no", "off"):
        return True
    return False


def init_honeyhive() -> HoneyHiveTracer:
    """
    Initialize HoneyHive tracing and LangChain/LangGraph auto-instrumentation.

    Must run once before compiling or invoking the graph. By default uses **batched** OTLP export
    (``disable_batch=False``) to reduce Gateway load and 504s; override with ``HONEYHIVE_OTLP_BATCH=0``.
    OTLP draining is deferred to ``flush_honeyhive_tracer()`` after the CLI prints results (see ``main.py``).
    """
    global _tracer
    if _tracer is not None:
        return _tracer

    load_dotenv()
    if not os.environ.get("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY is not set. Copy .env.example to .env and add your OpenAI key.")
    api_key = os.environ.get("HONEYHIVE_API_KEY")
    project = os.environ.get("HONEYHIVE_PROJECT", "fraud-triage-demo")
    if not api_key:
        raise ValueError("HONEYHIVE_API_KEY is not set. Copy .env.example to .env and fill keys.")

    # Default to protobuf — binary encoding is ~3-5x smaller than JSON for LangChain message payloads,
    # dropping export payload size and gateway processing time (reduces 504s). Override with
    # HH_OTLP_PROTOCOL=http/json if needed.
    os.environ.setdefault("HH_OTLP_PROTOCOL", "http/protobuf")

    # Some HoneyHive API responses omit fields the installed SDK's ``PostSessionStartResponse``
    # model still requires (e.g. org_id / workspace_id), which makes ``sessions.start`` fail
    # validation even when the session exists. Skip REST session creation and use OTEL baggage
    # with client-generated UUIDs so spans still associate in the UI. See:
    # https://docs.honeyhive.ai/v2/tracing/tracer-initialization (skip_backend_session_creation).
    use_backend_session = os.environ.get("HONEYHIVE_USE_BACKEND_SESSION", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    session_cfg: SessionConfig | None = None
    bootstrap_session_id: str | None = None
    if not use_backend_session:
        bootstrap_session_id = str(uuid.uuid4())
        session_cfg = SessionConfig(
            session_id=bootstrap_session_id,
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
    # LangGraph builds on LangChain — this captures graph runs, node transitions, and Runnable LLM/tool
    # usage (see HoneyHive LangGraph integration). Call before compiling or invoking the graph.
    LangChainInstrumentor().instrument(tracer_provider=_tracer.provider)
    return _tracer


def _build_graph(rest: RestTraceLogger | None = None) -> Any:
    """
    Build the LangGraph workflow. If ``rest`` is provided, each node emits a REST event to
    HoneyHive's /events API so a full trace appears in the UI even when OTLP ingest is degraded.
    """

    def intake(state: FraudState) -> dict[str, Any]:
        """Parse the flagged transaction payload and extract stable identifiers for enrichment."""
        tx = state["transaction"]
        customer_id = str(tx["customer_id"])
        transaction_id = str(tx["transaction_id"])
        if state.get("verbose"):
            print(f"[intake] customer_id={customer_id} transaction_id={transaction_id}")

        if rest:
            with rest.span("intake", event_type="chain", inputs={"transaction": tx}) as s:
                s.outputs = {"customer_id": customer_id, "transaction_id": transaction_id}
        return {"customer_id": customer_id, "transaction_id": transaction_id}

    def enrich_customer(state: FraudState) -> dict[str, Any]:
        """Load recent customer behavior and profile context."""
        cid = state["customer_id"]
        tool = TOOL_BY_NAME["fetch_customer_history"]

        if rest:
            with rest.span(
                "fetch_customer_history",
                event_type="tool",
                inputs={"customer_id": cid},
            ) as s:
                blob = str(tool.invoke({"customer_id": cid}))
                s.outputs = {"result": blob}
        else:
            blob = str(tool.invoke({"customer_id": cid}))

        if state.get("verbose"):
            print(f"[enrich_customer] context length={len(blob)} chars")
        return {"customer_enrichment": blob}

    def enrich_device(state: FraudState) -> dict[str, Any]:
        """Load device and session context for the flagged transaction."""
        tid = state["transaction_id"]
        tool = TOOL_BY_NAME["fetch_device_context"]

        if rest:
            with rest.span(
                "fetch_device_context",
                event_type="tool",
                inputs={"transaction_id": tid},
            ) as s:
                blob = str(tool.invoke({"transaction_id": tid}))
                s.outputs = {"result": blob}
        else:
            blob = str(tool.invoke({"transaction_id": tid}))

        if state.get("verbose"):
            print(f"[enrich_device] context length={len(blob)} chars")
        return {"device_enrichment": blob}

    llm_risk = ChatOpenAI(model="gpt-4o", temperature=0, **_llm_client_kwargs())
    llm_with_tools = llm_risk.bind_tools(ALL_TOOLS)

    def exec_risk_tool(name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool requested by the LLM during risk_scoring."""
        tool = TOOL_BY_NAME.get(name)
        if tool is None:
            raise ValueError(f"Unknown tool: {name}")

        if rest:
            with rest.span(name, event_type="tool", inputs=arguments) as s:
                out = str(tool.invoke(arguments))
                s.outputs = {"result": out}
            return out
        return str(tool.invoke(arguments))

    def risk_scoring(state: FraudState) -> dict[str, Any]:
        """LLM step: weigh enriched signals against policy; tool misuse is a violation."""
        tx = state["transaction"]
        user = json.dumps(tx, indent=2)
        messages = [
            SystemMessage(content=RISK_SCORING_SYSTEM),
            HumanMessage(
                content=(
                    f"Flagged transaction JSON:\n{user}\n\n"
                    f"Customer enrichment (JSON string):\n{state.get('customer_enrichment', '')}\n\n"
                    f"Device enrichment (JSON string):\n{state.get('device_enrichment', '')}\n\n"
                    "Provide a risk assessment with explicit reasoning. "
                    "Use tools only if you must verify a detail."
                )
            ),
        ]

        def _run_loop() -> str:
            for _ in range(12):
                ai: AIMessage = llm_with_tools.invoke(messages)
                messages.append(ai)
                if not ai.tool_calls:
                    return ai.content or ""
                for tc in ai.tool_calls:
                    name = str(tc["name"])
                    args = dict(tc.get("args") or {})
                    if name not in TOOL_BY_NAME:
                        raise ValueError(f"Unknown tool requested by model: {name}")
                    out = exec_risk_tool(name, args)
                    messages.append(ToolMessage(content=out, tool_call_id=str(tc["id"])))
            final = messages[-1]
            if isinstance(final, AIMessage) and isinstance(final.content, str):
                return final.content
            return "risk_scoring stopped after iteration cap"

        if rest:
            with rest.span(
                "risk_scoring",
                event_type="model",
                inputs={"transaction": tx, "model": "gpt-4o"},
                config={"model": "gpt-4o", "provider": "openai", "temperature": 0},
            ) as s:
                text = _run_loop()
                s.outputs = {"risk_assessment": text}
        else:
            text = _run_loop()

        if state.get("verbose"):
            print(f"[risk_scoring] completed ({len(text)} chars)")
        return {"risk_assessment": text}

    def decision(state: FraudState) -> dict[str, Any]:
        """Produce the advisory verdict JSON."""
        llm = ChatOpenAI(model="gpt-4o", temperature=0, **_llm_client_kwargs())
        structured = llm.with_structured_output(VerdictModel)
        tx = json.dumps(state["transaction"], indent=2)
        prompt = HumanMessage(
            content=(
                f"Transaction:\n{tx}\n\n"
                f"Risk assessment:\n{state.get('risk_assessment', '')}\n"
            )
        )
        if rest:
            with rest.span(
                "decision",
                event_type="model",
                inputs={"risk_assessment": state.get("risk_assessment", "")},
                config={"model": "gpt-4o", "provider": "openai", "structured_output": "VerdictModel"},
            ) as s:
                result: VerdictModel = structured.invoke(
                    [SystemMessage(content=DECISION_SYSTEM), prompt]
                )
                verdict = result.model_dump()
                s.outputs = verdict
        else:
            result = structured.invoke([SystemMessage(content=DECISION_SYSTEM), prompt])
            verdict = result.model_dump()

        if state.get("verbose"):
            print(f"[decision] verdict={verdict}")
        return {"verdict": verdict}

    g = StateGraph(FraudState)
    g.add_node("intake", intake)
    g.add_node("enrich_customer", enrich_customer)
    g.add_node("enrich_device", enrich_device)
    g.add_node("risk_scoring", risk_scoring)
    g.add_node("decision", decision)

    g.add_edge(START, "intake")
    g.add_edge("intake", "enrich_customer")
    g.add_edge("enrich_customer", "enrich_device")
    g.add_edge("enrich_device", "risk_scoring")
    g.add_edge("risk_scoring", "decision")
    g.add_edge("decision", END)

    return g.compile()


def get_compiled_graph(rest: RestTraceLogger | None = None) -> Any:
    """
    Compile the graph. Cached only when ``rest`` is None (no REST logger bound);
    a REST-bound graph closes over the logger, so we rebuild per-run.
    """
    global _compiled_graph
    if rest is not None:
        return _build_graph(rest=rest)
    if _compiled_graph is None:
        _compiled_graph = _build_graph(rest=None)
    return _compiled_graph


def honeyhive_trace_url(session_id: str) -> str:
    """
    Best-effort dashboard URL for the session.

    The product UI evolves; if this path does not open a session detail view, use the Traces page
    and search by session id or name.
    """
    base = os.environ.get("HONEYHIVE_UI_BASE", "https://app.honeyhive.ai").rstrip("/")
    return f"{base}/traces/sessions/{session_id}"


def _use_rest_logger() -> bool:
    """
    Default True: emit the trace via HoneyHive's REST /events API (stable).
    Set HONEYHIVE_DISABLE_REST=1 to disable and rely on OTLP only.
    """
    return os.environ.get("HONEYHIVE_DISABLE_REST", "").strip().lower() not in (
        "1",
        "true",
        "yes",
    )


def _use_otlp() -> bool:
    """
    Default False: OTLP ingest has been unstable (504s) during this demo. Enable with
    HONEYHIVE_ENABLE_OTLP=1 to additionally initialize the OTel LangChainInstrumentor path.
    """
    return os.environ.get("HONEYHIVE_ENABLE_OTLP", "").strip().lower() in ("1", "true", "yes")


def run_single_transaction(transaction: dict[str, Any], *, verbose: bool = False) -> dict[str, Any]:
    """
    Execute the full graph for one transaction and log the trace to HoneyHive.

    By default uses the REST /events API (reliable). Set HONEYHIVE_ENABLE_OTLP=1 to also send via
    OTLP; set HONEYHIVE_DISABLE_REST=1 to skip REST.
    """
    txn_id = str(transaction.get("transaction_id", uuid.uuid4()))
    print(f"[fraud-triage] starting {txn_id} …", flush=True)

    api_key = os.environ.get("HONEYHIVE_API_KEY")
    project = os.environ.get("HONEYHIVE_PROJECT", "fraud-triage-demo")
    if not api_key:
        raise ValueError("HONEYHIVE_API_KEY is not set. Copy .env.example to .env and fill keys.")
    if not os.environ.get("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY is not set.")

    # Start a HoneyHive session via REST (this is what creates the visible trace row in the UI).
    rest: RestTraceLogger | None = None
    if _use_rest_logger():
        print("[fraud-triage] creating HoneyHive session via REST /session/start …", flush=True)
        rest = RestTraceLogger(api_key=api_key, project=project, source="development")
        rest.start_session(
            name=f"fraud-triage-{txn_id}",
            inputs={"transaction_id": txn_id, "customer_id": transaction.get("customer_id")},
        )
        print(f"[fraud-triage] session_id={rest.session_id}", flush=True)

    # Optional: also initialize OTLP path (disabled by default because of upstream 504s).
    if _use_otlp():
        init_honeyhive()

    session_id = rest.session_id if rest else str(uuid.uuid4())

    graph = get_compiled_graph(rest=rest)
    initial: FraudState = {
        "transaction": transaction,
        "verbose": verbose,
    }
    print("[fraud-triage] invoking graph …", flush=True)
    final: FraudState = graph.invoke(initial)
    print("[fraud-triage] graph finished.", flush=True)

    verdict = final.get("verdict") or {}

    if rest:
        print("[fraud-triage] finalizing session on HoneyHive …", flush=True)
        rest.end_session(
            outputs={"verdict": verdict, "transaction_id": txn_id},
            metadata={"demo": "fraud-triage", "framework": "langgraph"},
        )
        print("[fraud-triage] trace logged.", flush=True)

    return {
        "session_id": session_id,
        "trace_url": honeyhive_trace_url(session_id),
        "verdict": verdict,
        "final_state": final,
    }
