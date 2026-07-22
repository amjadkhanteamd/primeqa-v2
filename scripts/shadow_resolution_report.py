"""Shadow semantic-resolution report — D-376 telemetry reporting (read-only).

Aggregates the persisted ``attempted_interpretation.shadow_resolution`` maps
across generation outcomes: agreement classes, grades, would-veto entries
(with their evidence), and AMBIGUITY PERSISTENCE — the same business term
grading ambiguous in >=2 distinct outcomes per org, which is the measured
input to the deferred glossary-pin decision.

Usage (repo root, venv active, DATABASE_URL set or in .env):

    python scripts/shadow_resolution_report.py --requirement req-320
    python scripts/shadow_resolution_report.py --outcome <outcome-uuid>
    python scripts/shadow_resolution_report.py --since 2026-07-20 --tenant 1

Read-only: SELECTs only, no writes, no state change anywhere.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:  # optional convenience — the scratch-script convention
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), ".env"))
except ImportError:
    pass

from sqlalchemy import text  # noqa: E402

from primeqa import db  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--outcome", help="generation outcome_id (uuid)")
    ap.add_argument("--requirement", help="requirement key, e.g. req-320")
    ap.add_argument("--since", help="ISO date lower bound on created_at")
    ap.add_argument("--tenant", type=int, default=1)
    args = ap.parse_args()
    if not (args.outcome or args.requirement or args.since):
        ap.error("pass --outcome, --requirement, or --since")

    db.init_db(os.environ["DATABASE_URL"])
    s = db.SessionLocal()
    s.execute(text(f"SET search_path TO tenant_{args.tenant}, public"))

    clauses = ["attempted_interpretation ? 'shadow_resolution'"]
    params: dict = {}
    if args.outcome:
        clauses.append("outcome_id = CAST(:oid AS uuid)")
        params["oid"] = args.outcome
    if args.requirement:
        clauses.append("requirement_ref->>'key' = :k")
        params["k"] = args.requirement
    if args.since:
        clauses.append("created_at >= :since")
        params["since"] = args.since
    rows = s.execute(text(
        "SELECT outcome_id, requirement_ref->>'key' AS k, created_at, "
        "       outcome_kind, "
        "       attempted_interpretation->'shadow_resolution' AS m "
        "FROM generation_outcomes WHERE " + " AND ".join(clauses) +
        " ORDER BY created_at"), params).mappings().all()
    if not rows:
        print("no outcomes carry shadow_resolution telemetry for this filter")
        return 1

    print(f"{len(rows)} outcome(s) with shadow telemetry\n")
    agreements: Counter = Counter()
    grades: Counter = Counter()
    vetoes: list = []
    amb: dict = defaultdict(set)   # (org, term) -> outcome ids
    total = 0
    for r in rows:
        m = r["m"] or {}
        verdicts = m.get("verdicts") or []
        total += len(verdicts)
        print(f"- {r['k']}  {r['outcome_id']}  {r['created_at']}  "
              f"kind={r['outcome_kind']}  counts={m.get('counts')}")
        for v in verdicts:
            agreements[v.get("agreement")] += 1
            grades[v["shadow"]["grade"]] += 1
            if v.get("would_veto"):
                vetoes.append((r["k"], r["outcome_id"], v))
            if v["shadow"]["grade"] == "ambiguous":
                amb[(v.get("connected_org_id"), v.get("term"))].add(
                    str(r["outcome_id"]))

    print(f"\n===== {total} verdicts =====")
    for cls, n in agreements.most_common():
        print(f"  agreement {cls}: {n}")
    for g, n in grades.most_common():
        print(f"  grade {g}: {n}")

    # v2 (F0): per-field fate x slot — absent on v1 verdicts (tolerated)
    field_rows = [f for r in rows for v in ((r["m"] or {}).get("verdicts") or [])
                  for f in (v["shadow"].get("fields") or [])]
    if field_rows:
        per_slot = defaultdict(Counter)
        for f in field_rows:
            per_slot[f.get("slot")][f.get("actual") or "unbound"] += 1
        print(f"\n  field mentions ({len(field_rows)}) — fate x slot on the "
              "actual subject:")
        for slot, c in sorted(per_slot.items(),
                              key=lambda kv: -sum(kv[1].values())):
            print(f"    {str(slot):<34} exact={c.get('exact', 0):<4} "
                  f"ladder={c.get('ladder', 0):<4} "
                  f"foreign={c.get('foreign', 0):<4} "
                  f"unbound={c.get('unbound', 0)}")
        worst = Counter(f["term"] for f in field_rows
                        if (f.get("actual") or "unbound") == "unbound")
        if worst:
            print("    top unbound terms: "
                  + ", ".join(f"{t}({n})" for t, n in worst.most_common(6)))
    print(f"  would-veto: {len(vetoes)}")
    for k, oid, v in vetoes:
        print(f"    VETO [{k} {oid}] {v['term']!r} -> actual "
              f"{v['actual']['sf_api_name']!r} vs winner "
              f"{v['shadow']['winner']!r} "
              f"(discriminators {(v.get('veto_evidence') or {}).get('discriminators')})")
    persistent = {key: sorted(o) for key, o in amb.items() if len(o) >= 2}
    if persistent:
        print("\n  ambiguity persistence (org, term -> outcomes) — the "
              "glossary-pin input:")
        for (org, term), oids in sorted(persistent.items(),
                                        key=lambda kv: -len(kv[1])):
            print(f"    {term!r} (org {org}): {len(oids)} outcomes")
    else:
        print("\n  ambiguity persistence: none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
