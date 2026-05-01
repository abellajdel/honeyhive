#!/usr/bin/env python3
"""Run all sample onboarding applications sequentially (six HoneyHive sessions)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agent import _load_env, flush_honeyhive_tracer, run_single_application


def main() -> None:
    _load_env()
    path = ROOT / "data" / "sample_applications.json"
    rows = json.loads(path.read_text(encoding="utf-8"))

    for app in rows:
        aid = app.get("applicant_id")
        print(f"\n=== Running applicant {aid} ===")
        out = run_single_application(app, verbose=False)
        packet = out.get("onboarding_packet") or {}
        print(json.dumps(packet, indent=2))
        print(f"session: {out['session_id']}")
        print(f"trace:   {out['trace_url']}")
        if os.environ.get("HONEYHIVE_ENABLE_OTLP", "").strip().lower() in ("1", "true", "yes"):
            flush_honeyhive_tracer()


if __name__ == "__main__":
    main()
