"""CLI entrypoint for the KYC onboarding demo."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from src.agent import _load_env, flush_honeyhive_tracer, run_single_application


def _load_applications(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Applications file must contain a JSON array of objects.")
    return data


def _find_application(rows: list[dict], application_id: str) -> dict:
    for row in rows:
        if str(row.get("applicant_id")) == application_id:
            return row
    raise SystemExit(f"applicant_id {application_id!r} not found in {len(rows)} records.")


def main(argv: list[str] | None = None) -> None:
    _load_env()
    parser = argparse.ArgumentParser(description="KYC onboarding LangGraph + HoneyHive demo")
    parser.add_argument(
        "--applications",
        type=Path,
        default=Path("data/sample_applications.json"),
        help="Path to JSON array of onboarding applications",
    )
    parser.add_argument("--id", dest="applicant_id", required=True, help="applicant_id to run (e.g. kyc_001)")
    parser.add_argument("--verbose", action="store_true", help="Print per-node debug lines")
    args = parser.parse_args(argv)

    rows = _load_applications(args.applications)
    app = _find_application(rows, args.applicant_id)

    result = run_single_application(app, verbose=args.verbose)
    packet = result.get("onboarding_packet") or {}
    print(json.dumps(packet, indent=2))
    print()
    print(f"HoneyHive session id: {result['session_id']}")
    print(f"HoneyHive trace URL: {result['trace_url']}")

    if os.environ.get("HONEYHIVE_ENABLE_OTLP", "").strip().lower() in ("1", "true", "yes"):
        flush_honeyhive_tracer()


if __name__ == "__main__":
    main()
