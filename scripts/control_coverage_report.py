"""Control-coverage lifecycle report — Phase 0 benchmark reporting (read-only).

Renders, for one generation outcome, where every org control stands on the
lifecycle EXPECTED -> NOMINATED -> SELECTED -> EMITTED -> EXECUTED ->
ATTRIBUTED (see ``primeqa/generation/control_coverage.py``). This is the
control-axis companion to the benchmark's three-number report (VRB-V1
EXECUTION.md §8): apparent AC coverage can hold while a control silently
drops — this report makes that visible.

Usage (repo root, venv active, DATABASE_URL set or in .env):

    python scripts/control_coverage_report.py --requirement req-315
    python scripts/control_coverage_report.py --outcome <outcome-uuid>
    python scripts/control_coverage_report.py --requirement req-315 --tenant 1

Read-only: SELECTs only, no writes, no state change anywhere.

Sources per stage:
- EXPECTED/NOMINATED/EMITTED (+ per-control fields/signatures): the outcome's
  persisted ``attempted_interpretation.control_coverage`` map. For outcomes
  that PRE-DATE the telemetry, facts fall back to the CURRENT S1
  ``ValidationRule`` entities on the subject (a printed caveat — the org may
  have drifted since that generation).
- EXECUTED: ``s4_execution_runs`` rows for the outcome's claims.
- ATTRIBUTED: ``s6_interpretations`` verdict ``prohibition_enforced`` whose
  run's recipe pins the control's signature (``vr_name`` cross-checked when
  S6 recorded one).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:  # optional convenience — the scratch-script convention
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), ".env"))
except ImportError:
    pass

from sqlalchemy import text  # noqa: E402

from primeqa import db  # noqa: E402
from primeqa.generation import control_coverage as cc  # noqa: E402


def _bare(name: str) -> str:
    return (name or "").rsplit(".", 1)[-1]


def _facts_from_map(cmap: dict) -> list[cc.ControlFact]:
    facts = []
    for ref, entry in (cmap.get("controls") or {}).items():
        facts.append(cc.ControlFact(
            mechanism=entry.get("mechanism") or cc.MECHANISM_VALIDATION_RULE,
            control_ref=ref,
            subject_ref=entry.get("subject") or "",
            formula_text=None,  # fields persisted directly; formula not needed
            firing_signature=entry.get("signature"),
            active=bool(entry.get("active")),
        ))
    return facts


def _facts_from_s1(session, subject_prefix: str) -> list[cc.ControlFact]:
    """Fallback for pre-telemetry outcomes: CURRENT ValidationRule entities
    whose sf_api_name sits under the subject (e.g. 'PLS_BM_Deal__c.%')."""
    from primeqa.semantic.entity_attributes import (
        vr_error_message, vr_formula_text, vr_is_active)
    rows = session.execute(text(
        "SELECT sf_api_name, attributes FROM entities "
        "WHERE entity_type = 'ValidationRule' "
        "AND sf_api_name LIKE :prefix AND valid_to_seq IS NULL "
        "ORDER BY sf_api_name"), {"prefix": subject_prefix + ".%"}).fetchall()
    facts, seen = [], set()
    for r in rows:                    # dedup across per-org rows (D-255 shape)
        if r[0] in seen:
            continue
        seen.add(r[0])
        facts.append(cc.ControlFact(
            mechanism=cc.MECHANISM_VALIDATION_RULE,
            control_ref=r[0],
            subject_ref=subject_prefix,
            formula_text=vr_formula_text(r[1]),
            firing_signature=vr_error_message(r[1]),
            active=vr_is_active(r[1]),
        ))
    return facts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--outcome", help="generation outcome_id (uuid)")
    ap.add_argument("--requirement", help="requirement key, e.g. req-315 "
                                          "(latest outcome wins)")
    ap.add_argument("--tenant", type=int, default=1)
    args = ap.parse_args()
    if not args.outcome and not args.requirement:
        ap.error("pass --outcome or --requirement")

    db.init_db(os.environ["DATABASE_URL"])
    s = db.SessionLocal()
    s.execute(text(f"SET search_path TO tenant_{args.tenant}, public"))

    # -- 1. the outcome ------------------------------------------------------
    if args.outcome:
        row = s.execute(text(
            "SELECT outcome_id, requirement_ref, claims_written, "
            "attempted_interpretation, created_at FROM generation_outcomes "
            "WHERE outcome_id = CAST(:o AS uuid)"), {"o": args.outcome}
        ).mappings().first()
    else:
        row = s.execute(text(
            "SELECT outcome_id, requirement_ref, claims_written, "
            "attempted_interpretation, created_at FROM generation_outcomes "
            "WHERE requirement_ref->>'key' = :k AND outcome_kind = 'draft' "
            "ORDER BY created_at DESC LIMIT 1"), {"k": args.requirement}
        ).mappings().first()
    if row is None:
        print("no matching generation outcome")
        return 1

    ai = row["attempted_interpretation"] or {}
    claims_written = row["claims_written"] or []
    print(f"outcome {row['outcome_id']}  requirement "
          f"{(row['requirement_ref'] or {}).get('key')}  {row['created_at']}")

    # -- 2. claims + recipes -------------------------------------------------
    claim_ids = [c["test_id"] for c in claims_written]
    conds_by_claim: dict[str, frozenset] = {}
    patterns_by_claim: dict[str, list[str]] = defaultdict(list)
    recipe_claims: dict[str, str] = {}
    for tid in claim_ids:
        c = s.execute(text(
            "SELECT semantic_conditions FROM test_claims "
            "WHERE test_id = CAST(:t AS uuid) "
            "ORDER BY version_seq DESC LIMIT 1"), {"t": tid}).first()
        conds_by_claim[tid] = cc.condition_fields(c[0] if c else None)
        for rid, body in s.execute(text(
                "SELECT recipe_id, observation_realization FROM test_recipes "
                "WHERE claim_test_id = CAST(:t AS uuid)"), {"t": tid}):
            recipe_claims[str(rid)] = tid
            patterns_by_claim[tid].extend(cc.rejection_patterns(body))

    # -- 3. control facts ----------------------------------------------------
    cmap = ai.get("control_coverage")
    if cmap:
        facts = _facts_from_map(cmap)
        fields_by_ref = {ref: frozenset(e.get("fields") or [])
                         for ref, e in (cmap.get("controls") or {}).items()}
        source = "persisted control_coverage map"
    else:
        subjects = {p.get("subject_refs", [{}])[0].get("sf_api_name")
                    for p in (ai.get("candidate_paths") or [])
                    if p.get("subject_refs")}
        subjects.discard(None)
        facts = []
        for subj in sorted(subjects):
            facts.extend(_facts_from_s1(s, subj))
        fields_by_ref = {f.control_ref: cc.formula_fields(f.formula_text)
                         for f in facts}
        source = ("CURRENT S1 state (outcome pre-dates control telemetry — "
                  "org may have drifted since generation)")
    print(f"control facts: {len(facts)} from {source}\n")
    if not facts:
        print("no controls found — nothing to report")
        return 1

    # -- 4. runs + interpretations ------------------------------------------
    runs_by_claim: dict[str, list] = defaultdict(list)
    interp_by_run: dict[str, dict] = {}
    if claim_ids:
        for r in s.execute(text(
                "SELECT run_id, recipe_id, claim_test_id, outcome "
                "FROM s4_execution_runs WHERE claim_test_id IN :ids"),
                {"ids": tuple(claim_ids)}).mappings():
            runs_by_claim[str(r["claim_test_id"])].append(dict(r))
        for r in s.execute(text(
                "SELECT run_id, claim_test_id, verdict, vr_name "
                "FROM s6_interpretations WHERE claim_test_id IN :ids"),
                {"ids": tuple(claim_ids)}).mappings():
            interp_by_run[str(r["run_id"])] = dict(r)

    # -- 5. lifecycle per control -------------------------------------------
    all_cond_sets = list(conds_by_claim.values())
    report: dict[str, dict] = {}
    for f in sorted(facts, key=lambda x: x.control_ref):
        entry = {"active": f.active, "stage": None, "claims": [], "runs": 0,
                 "attributed_runs": 0}
        if f.active:
            entry["stage"] = cc.EXPECTED
            fields = fields_by_ref.get(f.control_ref, frozenset())
            if fields and any(fields & cs for cs in all_cond_sets):
                entry["stage"] = cc.NOMINATED
        for tid, patterns in patterns_by_claim.items():
            if not any(f in cc.match_signature(p, [f]) for p in patterns):
                continue
            entry["claims"].append(str(tid)[:8])
            if f.active:
                entry["stage"] = cc.EMITTED
            for run in runs_by_claim.get(str(tid), ()):
                entry["runs"] += 1
                if f.active and cc.stage_rank(entry["stage"]) < cc.stage_rank(cc.EXECUTED):
                    entry["stage"] = cc.EXECUTED
                interp = interp_by_run.get(str(run["run_id"]))
                if not interp or interp.get("verdict") != "prohibition_enforced":
                    continue
                vr = interp.get("vr_name")
                if vr and _bare(vr).lower() != _bare(f.control_ref).lower():
                    continue  # S6 named a DIFFERENT rule — not this control
                entry["attributed_runs"] += 1
                if f.active:
                    entry["stage"] = cc.ATTRIBUTED
        report[f.control_ref] = entry

    # -- 6. render ------------------------------------------------------------
    w = max(len(_bare(r)) for r in report)
    print(f"{'control':<{w}}  {'active':<6}  {'stage':<10}  "
          f"{'claims':<20}  runs  attributed")
    for ref, e in report.items():
        print(f"{_bare(ref):<{w}}  {str(e['active']).lower():<6}  "
              f"{e['stage'] or '-':<10}  {','.join(e['claims']) or '-':<20}  "
              f"{e['runs']:>4}  {e['attributed_runs']:>10}")
    active = [e for e in report.values() if e["active"]]
    n = len(active)

    def _count(stage):
        return sum(1 for e in active
                   if cc.stage_rank(e["stage"] or "") >= cc.stage_rank(stage))

    print(f"\ncontrols: expected {n} | nominated {_count(cc.NOMINATED)} | "
          f"emitted {_count(cc.EMITTED)} | executed {_count(cc.EXECUTED)} | "
          f"ATTRIBUTED {_count(cc.ATTRIBUTED)}/{n}")
    silent = [_bare(r) for r, e in report.items()
              if e["active"] and cc.stage_rank(e["stage"] or "") < cc.stage_rank(cc.EMITTED)]
    if silent:
        print(f"unexercised controls (no bound claim): {', '.join(silent)}")
    s.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
