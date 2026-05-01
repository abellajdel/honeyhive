#!/usr/bin/env python3
"""
Diagnostic for HoneyHive connectivity. Runs four checks:

  1. TLS reachability to api.honeyhive.ai
  2. REST auth: GET /projects with the API key
  3. OTLP ingest: POST a trivial span to /opentelemetry/v1/traces (JSON)
  4. OTLP ingest: same, but protobuf (often faster on the server)

Prints a plain-English verdict of which layer is broken.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv


def _check_reachability() -> tuple[bool, str]:
    import requests

    t0 = time.time()
    try:
        r = requests.get("https://api.honeyhive.ai/", timeout=5)
    except Exception as e:  # noqa: BLE001 - diagnostic surface
        return False, f"{type(e).__name__}: {e}"
    return True, f"HTTP {r.status_code} in {time.time() - t0:.2f}s"


def _check_rest_auth(api_key: str, project: str) -> tuple[bool, str]:
    """GET /projects — valid auth returns 200 with a JSON list."""
    import requests

    t0 = time.time()
    try:
        r = requests.get(
            "https://api.honeyhive.ai/projects",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15,
        )
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    dt = time.time() - t0
    if r.status_code == 200:
        body_snippet = r.text[:400].replace("\n", " ")
        has_project = False
        try:
            data = r.json()
            items = data if isinstance(data, list) else data.get("projects", [])
            names = [p.get("name") for p in items if isinstance(p, dict)]
            has_project = project in names
            return True, (
                f"HTTP 200 in {dt:.2f}s · projects visible={len(names)}"
                f" · contains '{project}'={has_project}"
                + ("" if has_project else f" · sample={names[:5]!r}")
            )
        except Exception:  # noqa: BLE001
            return True, f"HTTP 200 in {dt:.2f}s · non-JSON body: {body_snippet!r}"
    if r.status_code in (401, 403):
        return False, f"AUTH FAIL {r.status_code} in {dt:.2f}s · body: {r.text[:200]!r}"
    return False, f"HTTP {r.status_code} in {dt:.2f}s · body: {r.text[:200]!r}"


def _otlp_json_payload() -> dict:
    now_ns = int(time.time() * 1e9)
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": []},
                "scopeSpans": [
                    {
                        "scope": {"name": "honeyhive-diag"},
                        "spans": [
                            {
                                "traceId": f"{int(time.time()):032x}",
                                "spanId": f"{int(time.time()) & 0xFFFFFFFFFFFFFFFF:016x}",
                                "name": "honeyhive.diag.ping",
                                "kind": "SPAN_KIND_INTERNAL",
                                "startTimeUnixNano": str(now_ns),
                                "endTimeUnixNano": str(now_ns + 1_000_000),
                                "status": {"code": "STATUS_CODE_OK"},
                            }
                        ],
                    }
                ],
            }
        ]
    }


def _check_otlp_json(api_key: str, project: str, timeout_s: int) -> tuple[bool, str]:
    import requests

    url = "https://api.honeyhive.ai/opentelemetry/v1/traces"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "X-Project": project,
        "X-Source": "development",
        "Content-Type": "application/json",
    }
    t0 = time.time()
    try:
        r = requests.post(url, json=_otlp_json_payload(), headers=headers, timeout=timeout_s)
    except requests.Timeout:
        return False, f"READ TIMEOUT after {time.time() - t0:.1f}s"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    dt = time.time() - t0
    return r.status_code < 400, f"HTTP {r.status_code} in {dt:.2f}s · body[:200]: {r.text[:200]!r}"


def _check_otlp_protobuf(api_key: str, project: str, timeout_s: int) -> tuple[bool, str]:
    """Use the official OTLP HTTP exporter (binary protobuf) — most authoritative test."""
    import requests
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
    from opentelemetry.trace import SpanContext, SpanKind, TraceFlags

    provider = TracerProvider(resource=Resource.create({"service.name": "honeyhive-diag"}))
    tracer = provider.get_tracer("honeyhive-diag")

    span = tracer.start_span("honeyhive.diag.ping.pb", kind=SpanKind.INTERNAL)
    span.set_attribute("diag", "check_honeyhive")
    readable: ReadableSpan = span  # type: ignore[assignment]
    span.end()

    exporter = OTLPSpanExporter(
        endpoint="https://api.honeyhive.ai/opentelemetry/v1/traces",
        headers={
            "Authorization": f"Bearer {api_key}",
            "X-Project": project,
            "X-Source": "development",
        },
        timeout=timeout_s,
    )
    t0 = time.time()
    try:
        result = exporter.export([readable])
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    dt = time.time() - t0
    ok = getattr(result, "name", str(result)) == "SUCCESS"
    return ok, f"result={getattr(result, 'name', result)} in {dt:.2f}s"


def main() -> int:
    load_dotenv(ROOT / ".env")
    api_key = os.environ.get("HONEYHIVE_API_KEY")
    project = os.environ.get("HONEYHIVE_PROJECT", "fraud-triage-demo")
    if not api_key:
        print("HONEYHIVE_API_KEY is not set. Fill in .env first.")
        return 2

    print(f"HoneyHive diagnostic · project={project} · key=***{api_key[-4:]}\n")

    print("1/4  TLS reachability")
    ok1, msg1 = _check_reachability()
    print(f"     {'OK ' if ok1 else 'FAIL'} · {msg1}\n")
    if not ok1:
        print("→ Network/firewall/VPN is blocking api.honeyhive.ai. Fix that first.")
        return 3

    print("2/4  REST auth (GET /projects)")
    ok2, msg2 = _check_rest_auth(api_key, project)
    print(f"     {'OK ' if ok2 else 'FAIL'} · {msg2}\n")

    print("3/4  OTLP JSON ingest")
    ok3, msg3 = _check_otlp_json(api_key, project, timeout_s=30)
    print(f"     {'OK ' if ok3 else 'FAIL'} · {msg3}\n")

    print("4/4  OTLP protobuf ingest (official otel exporter)")
    ok4, msg4 = _check_otlp_protobuf(api_key, project, timeout_s=30)
    print(f"     {'OK ' if ok4 else 'FAIL'} · {msg4}\n")

    print("─" * 60)
    if ok2 and (ok3 or ok4):
        print("✓ HoneyHive is fully healthy. Any app-side 504 is transient; retry.")
        return 0
    if ok2 and not ok3 and not ok4:
        print("Diagnosis:  Auth + REST work; OTLP ingest is hanging (both JSON and protobuf).")
        print("Root cause: HoneyHive's OTLP pipeline is degraded for this workspace.")
        print("")
        print("Options for a time-sensitive take-home:")
        print("  a) Email HoneyHive support (support@honeyhive.ai) with this diagnostic output")
        print("     and the OTLP endpoint URL. They can see ingest health instantly.")
        print("  b) Retry in 10-30 min — transient degradations often self-heal.")
        print("  c) Run in TEST mode for the demo: add HH_TEST_MODE=true to .env. Traces won't")
        print("     appear in their UI but your code runs clean — enough to narrate the flow.")
        print("  d) Switch HONEYHIVE_PROJECT to a fresh project name (sometimes per-project ingest")
        print("     state is bad; a new project starts from scratch).")
        return 1
    if not ok2:
        print("Diagnosis:  REST auth failed — API key or project issue.")
        print("→ Verify the key at https://app.honeyhive.ai/settings/project/keys")
        print("  and that HONEYHIVE_PROJECT matches a project you own.")
        return 1
    print("Diagnosis:  Mixed signal. Share the output above with HoneyHive support.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
