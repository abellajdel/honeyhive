"""CLI entrypoint for the fraud triage demo."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.agent import flush_honeyhive_tracer, run_single_transaction


def _load_transactions(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Transaction file must contain a JSON array of objects.")
    return data


def _find_transaction(rows: list[dict], txn_id: str) -> dict:
    for row in rows:
        if str(row.get("transaction_id")) == txn_id:
            return row
    raise SystemExit(f"transaction_id {txn_id!r} not found in {len(rows)} records.")


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Fraud triage LangGraph + HoneyHive demo")
    parser.add_argument(
        "--transactions",
        type=Path,
        default=Path("data/sample_transactions.json"),
        help="Path to JSON array of flagged transactions",
    )
    parser.add_argument("--id", dest="txn_id", required=True, help="transaction_id to triage (e.g. txn_001)")
    parser.add_argument("--verbose", action="store_true", help="Print per-node debug lines")
    args = parser.parse_args(argv)

    rows = _load_transactions(args.transactions)
    txn = _find_transaction(rows, args.txn_id)

    result = run_single_transaction(txn, verbose=args.verbose)

    verdict = result.get("verdict") or {}
    print(json.dumps(verdict, indent=2))
    print()
    print(f"HoneyHive session id: {result['session_id']}")
    print(f"HoneyHive trace URL: {result['trace_url']}")

    if os.environ.get("HONEYHIVE_ENABLE_OTLP", "").strip().lower() in ("1", "true", "yes"):
        flush_honeyhive_tracer()


if __name__ == "__main__":
    main()
