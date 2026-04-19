"""CLI: `python -m primeqa.intelligence.llm.eval <task> [options]`

Usage examples:
    # Dry-run: build spec + score expected output. Zero cost.
    python -m primeqa.intelligence.llm.eval test_plan_generation

    # Live run: hit Anthropic. Requires ANTHROPIC_API_KEY + --tenant-id.
    python -m primeqa.intelligence.llm.eval test_plan_generation \\
        --mode live --tenant-id 1

    # Filter to specific fixture ids
    python -m primeqa.intelligence.llm.eval test_plan_generation \\
        --filter missing_required_field basic_validation

    # Machine-readable JSON for CI
    python -m primeqa.intelligence.llm.eval test_plan_generation --json

Exit code: 0 if every fixture passed, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional


def _format_table(report) -> str:
    """Pretty per-fixture + summary table for the console."""
    lines = []
    header = f"\nEval: {report.suite}  mode={report.mode}  ({report.elapsed_ms} ms)"
    lines.append(header)
    lines.append("-" * len(header))
    for f in report.fixtures:
        icon = "PASS" if f.passed else "FAIL"
        lines.append(f"{icon:<5} {f.fixture_id}")
        if f.error:
            lines.append(f"      error: {f.error}")
        for c in f.checks:
            sub = "  ok" if c["passed"] else "FAIL"
            note = f"  -- {c['note']}" if c.get("note") else ""
            lines.append(f"      {sub}  {c['name']}{note}")
        if f.response_info:
            lines.append(
                f"      ({f.response_info.get('model', '')}, "
                f"{f.response_info.get('latency_ms', 0)}ms, "
                f"${f.response_info.get('cost_usd', 0):.6f}, "
                f"in/out {f.response_info.get('input_tokens', 0)}/"
                f"{f.response_info.get('output_tokens', 0)})"
            )
    lines.append("")
    lines.append(f"Summary: {report.passed}/{report.total} passed  "
                 f"({report.failed} failed)")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="primeqa.intelligence.llm.eval",
        description="Offline prompt-quality regression harness.",
    )
    ap.add_argument("task", help="Task name, e.g. test_plan_generation")
    ap.add_argument("--mode", choices=("dry", "live"), default="dry",
                    help="'dry' = no API call; 'live' = call Anthropic")
    ap.add_argument("--tenant-id", type=int, default=None,
                    help="Required in live mode")
    ap.add_argument("--filter", nargs="+", default=None,
                    help="Only run these fixture ids")
    ap.add_argument("--json", action="store_true",
                    help="Emit machine-readable JSON instead of table")
    ap.add_argument("--api-key", default=None,
                    help="Anthropic API key. Falls back to ANTHROPIC_API_KEY env.")
    args = ap.parse_args(argv)

    # Resolve API key for live mode.
    api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY")
    if args.mode == "live" and not (args.tenant_id and api_key):
        print("ERROR: live mode requires --tenant-id and ANTHROPIC_API_KEY",
              file=sys.stderr)
        return 2

    # Initialise DB if we'll hit the gateway — rate-limit + usage-log
    # queries need a session.
    if args.mode == "live":
        from dotenv import load_dotenv
        load_dotenv()
        from primeqa.db import init_db
        db_url = os.environ.get("DATABASE_URL")
        if db_url:
            init_db(db_url)

    from primeqa.intelligence.llm.eval import available_suites
    from primeqa.intelligence.llm.eval.runner import run_suite

    if args.task not in available_suites():
        print(f"ERROR: no fixtures for task '{args.task}'. "
              f"Available: {available_suites()}", file=sys.stderr)
        return 2

    report = run_suite(
        args.task,
        mode=args.mode,
        tenant_id=args.tenant_id,
        api_key=api_key,
        include_ids=args.filter,
    )

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, default=str))
    else:
        print(_format_table(report))

    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
