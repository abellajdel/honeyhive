#!/usr/bin/env python3
"""Run all sample transactions sequentially (six traces in HoneyHive)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Project root: fraud-agent/
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

import os

from src.agent import flush_honeyhive_tracer, run_single_transaction


def main() -> None:
    load_dotenv()
    path = ROOT / "data" / "sample_transactions.json"
    rows = json.loads(path.read_text(encoding="utf-8"))

    for txn in rows:
        tid = txn.get("transaction_id")
        print(f"\n=== Running {tid} ===")
        out = run_single_transaction(txn, verbose=False)
        print(json.dumps(out.get("verdict"), indent=2))
        print(f"session: {out['session_id']}")
        print(f"trace:   {out['trace_url']}")
        if os.environ.get("HONEYHIVE_ENABLE_OTLP", "").strip().lower() in ("1", "true", "yes"):
            flush_honeyhive_tracer()


if __name__ == "__main__":
    main()
