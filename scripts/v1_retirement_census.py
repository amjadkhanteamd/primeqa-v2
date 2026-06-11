"""v1 product-table retirement census (D-213 step 4 — READ-ONLY).

Run this AT DROP TIME (never earlier — the surface keeps moving until the
parity windows clear, D-213): it inventories every remaining code reference
to the v1 product tables plus their live row counts, producing the pre-drop
checklist input. It changes nothing.

Usage:
    DATABASE_URL=... python scripts/v1_retirement_census.py
"""
from __future__ import annotations

import os
import subprocess
import sys

V1_PRODUCT_TABLES = (
    "test_cases", "test_case_versions", "generation_batches",
    "pipeline_runs", "run_test_results", "run_step_results", "run_events",
    "test_suites", "suite_test_cases", "scheduled_runs",
)


def code_references(table: str) -> list[str]:
    """Files under primeqa/ referencing the table name (word-boundary grep)."""
    try:
        out = subprocess.run(
            ["grep", "-rlw", table, "primeqa/", "--include=*.py",
             "--include=*.html"],
            capture_output=True, text=True, check=False)
        return sorted(p for p in out.stdout.splitlines() if p)
    except Exception as exc:                               # pragma: no cover
        return [f"(grep failed: {exc})"]


def row_counts(tables) -> dict:
    from sqlalchemy import create_engine, text
    url = os.environ.get("DATABASE_URL")
    if not url:
        return {t: "(no DATABASE_URL)" for t in tables}
    eng = create_engine(url)
    counts = {}
    with eng.connect() as conn:
        for t in tables:
            try:
                counts[t] = conn.execute(
                    text(f"SELECT count(*) FROM public.{t}")).scalar()
            except Exception as exc:
                counts[t] = f"(error: {type(exc).__name__})"
    eng.dispose()
    return counts


def main() -> int:
    counts = row_counts(V1_PRODUCT_TABLES)
    print("v1 product-table retirement census (read-only)\n" + "=" * 48)
    blocking = 0
    for t in V1_PRODUCT_TABLES:
        refs = code_references(t)
        print(f"\n{t}: {counts.get(t)} rows, {len(refs)} referencing file(s)")
        for r in refs:
            print(f"    {r}")
        if refs:
            blocking += 1
    print(f"\n{blocking}/{len(V1_PRODUCT_TABLES)} tables still have code "
          f"references — the drop is blocked until each is retired or "
          f"consciously accepted in the pre-drop checklist (D-213).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
