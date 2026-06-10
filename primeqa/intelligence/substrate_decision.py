"""Substrate decision evidence — theme #3 slice 1 (D-198).

Assembles **decision-grade** per-claim evidence for a release: the D-172 console
chain (release requirement keys → ``generated_from`` links → claims → approved
version → grounding + latest run) hardened with the three correctness rules a
*decision* needs that an evidence *panel* didn't:

1. **Version-correct runs** — the counted run is the latest whose
   ``claim_version_seq`` is NULL or equals the **approved** version. A non-NULL
   mismatch is *superseded evidence* (a run of an older/newer version of the
   test): excluded from outcomes, surfaced as ``superseded_newer_run`` so the
   decision can warn that the freshest run isn't of the shipped version.
2. **NULL-seq tolerance** — ``claim_version_seq`` is legitimately Optional
   through the plan chain; a NULL-seq run **counts** (strict exclusion would
   zero out real evidence) but carries ``version_unknown=True``.
3. **Grounding staleness** — grounding evaluated at an S1 version older than the
   tenant's current one is flagged ``stale`` (the org may have moved under it);
   ``stale=None`` when either side is unknowable (no S1 version yet — tolerant,
   never raises).

Read-only over the caller's tenant-scoped session. The pure compute over this
evidence (risk rollup + GO/NO-GO) is slice 2; the v1 ``DecisionEngine`` is
untouched throughout (the composer isolation, D-198).
"""
from __future__ import annotations

import logging

from sqlalchemy import text

log = logging.getLogger(__name__)

# The latest COUNTED run: S4 as the base (true recency; interpret-failed runs
# still show, verdict NULL — the _CLAIM_RUNS_SQL discipline) + the D-198 version
# filter. :seq is the approved version_seq; pass NULL to disable the filter
# (unapproved claim — no reference version to count against).
_COUNTED_RUN_SQL = (
    "SELECT CAST(r.run_id AS text) AS run_id, r.outcome::text AS outcome, "
    "r.finished_at, r.claim_version_seq, i.verdict::text AS verdict "
    "FROM s4_execution_runs r "
    "LEFT JOIN s6_interpretations i ON i.run_id = r.run_id "
    "WHERE r.claim_test_id = CAST(:tid AS uuid) "
    "AND (CAST(:seq AS int) IS NULL "
    "     OR r.claim_version_seq IS NULL OR r.claim_version_seq = :seq) "
    "ORDER BY r.finished_at DESC LIMIT 1")

# Is there a NEWER run of a DIFFERENT (non-NULL) version than the approved one?
# (Superseded evidence newer than what we counted — the decision warns on it.)
_NEWER_SUPERSEDED_SQL = (
    "SELECT 1 FROM s4_execution_runs "
    "WHERE claim_test_id = CAST(:tid AS uuid) "
    "AND claim_version_seq IS NOT NULL AND claim_version_seq != :seq "
    "AND finished_at > COALESCE(CAST(:after AS timestamptz), '-infinity') "
    "LIMIT 1")


def _current_s1_seq(session):
    """The tenant's current S1 version_seq, or None when no version exists yet
    (tolerant — staleness is then unknowable, not an error)."""
    from primeqa.semantic.query import SemanticOrgModel, VersionNotFoundError
    try:
        return SemanticOrgModel(session.connection()).current_version_seq()
    except VersionNotFoundError:
        return None


def _claim_grounding(session, coord, test_id, approved_seq):
    """Grounding for the version the release ships: at the approved seq, else the
    latest verdict when no approved version exists (the D-172 idiom)."""
    from primeqa.evolution import list_grounding_validity, read_grounding_validity
    if approved_seq is not None:
        return read_grounding_validity(session, test_id, approved_seq)
    rows = list_grounding_validity(session, test_id=test_id)
    return rows[-1] if rows else None


def _assemble_claim_evidence(session, external_keys) -> list[dict]:
    """Pure: the release's requirement keys → one decision-grade evidence row per
    distinct claim. Each row::

        {test_id, approved_seq,
         grounding: {overall, stale, evaluated_at_version_seq} | None,
         latest_run: {run_id, outcome, verdict, finished_at,
                      version_unknown} | None,
         superseded_newer_run, never_run}
    """
    from primeqa.test_representation.coordinator import (
        SemanticTransactionCoordinator,
    )

    coord = SemanticTransactionCoordinator()
    test_ids, seen = [], set()
    for key in external_keys:
        for m in coord.list_tests_by_requirement(
                session, external_system="jira", external_key=key,
                link_kind="generated_from"):
            sid = str(m.test_id)
            if sid not in seen:                    # a claim shared by 2 reqs
                seen.add(sid)
                test_ids.append(m.test_id)

    current_seq = _current_s1_seq(session)
    out = []
    for tid in test_ids:
        approved = coord.get_current_approved_claim(session, tid)
        approved_seq = approved.version_seq if approved is not None else None

        gv = _claim_grounding(session, coord, tid, approved_seq)
        grounding = None
        if gv is not None:
            stale = (gv.evaluated_at_version_seq < current_seq
                     if current_seq is not None else None)
            grounding = {"overall": gv.overall, "stale": stale,
                         "evaluated_at_version_seq": gv.evaluated_at_version_seq}

        row = session.execute(text(_COUNTED_RUN_SQL),
                              {"tid": str(tid), "seq": approved_seq}
                              ).mappings().first()
        latest_run = None
        if row is not None:
            latest_run = {
                "run_id": row["run_id"], "outcome": row["outcome"],
                "verdict": row["verdict"],
                "finished_at": (row["finished_at"].isoformat()
                                if row["finished_at"] else None),
                "version_unknown": row["claim_version_seq"] is None,
            }

        superseded_newer = False
        if approved_seq is not None:
            superseded_newer = session.execute(
                text(_NEWER_SUPERSEDED_SQL),
                {"tid": str(tid), "seq": approved_seq,
                 "after": row["finished_at"] if row is not None else None},
            ).first() is not None

        out.append({
            "test_id": str(tid),
            "approved_seq": approved_seq,
            "grounding": grounding,
            "latest_run": latest_run,
            "superseded_newer_run": superseded_newer,
            "never_run": latest_run is None,
        })
    return out


# ---------------------------------------------------------------------------
# Slice 2 — the pure decision compute + the best-effort wrapper (D-198).
# Output shape mirrors the v1 DecisionEngine.evaluate dict ({recommendation,
# confidence, reasoning[], criteria_met, metrics}) so the ledger / template /
# CI render both engines uniformly; `risk` is the substrate-native addition.
# ---------------------------------------------------------------------------

# Risk-level vocabulary kept identical to risk_engine._score_to_level for UI
# continuity (the internals are NOT reused — its inputs are v1-only).
def _risk_level(score: int) -> str:
    if score >= 75:
        return "critical"
    if score >= 50:
        return "high"
    if score >= 25:
        return "medium"
    return "low"


def compute_substrate_decision(claim_evidence, criteria=None, *, now=None) -> dict:
    """Pure: slice-1 evidence rows + per-release ``decision_criteria`` → the
    substrate's recommendation. Checks:

    - ``has_runs``      — ALL claims never_run → blocker (no evidence at all).
    - ``pass_rate``     — counted latest runs vs ``substrate_min_pass_rate``
                          (default 95); errored counts as not-passed.
    - ``grounding_integrity`` — each ``broken`` grounding is a blocker when
                          ``substrate_block_on_broken_grounding`` (default True);
                          ``drifted`` / stale-``intact`` are warnings.
    - ``coverage``      — SOME claims never_run → warning.
    - ``version_currency`` — superseded-newer / version-unknown runs → warning.
    - ``freshness``     — newest counted run older than
                          ``substrate_max_run_age_hours`` (default 168) → warning.

    Recommendation: 0 blockers + 0 warnings → ``go`` (0.95); 0 blockers →
    ``conditional_go`` (0.75); else ``no_go`` (0.90) — v1's confidence scheme.
    """
    from datetime import datetime, timezone

    criteria = criteria or {}
    min_pass_rate = criteria.get("substrate_min_pass_rate", 95)
    block_on_broken = criteria.get("substrate_block_on_broken_grounding", True)
    max_age_hours = criteria.get("substrate_max_run_age_hours", 168)
    now = now or datetime.now(timezone.utc)

    if not claim_evidence:
        return {"applicable": False, "claim_count": 0}

    reasoning, criteria_met = [], {}
    blockers = warnings = 0

    counted = [c for c in claim_evidence if c["latest_run"] is not None]
    never_run = [c for c in claim_evidence if c["never_run"]]
    passed = sum(1 for c in counted if c["latest_run"]["outcome"] == "passed")
    failed = sum(1 for c in counted if c["latest_run"]["outcome"] == "failed")
    errored = sum(1 for c in counted if c["latest_run"]["outcome"] == "errored")

    # has_runs — no evidence at all is a blocker (the v1 no-runs parallel).
    if not counted:
        reasoning.append({"check": "has_runs", "status": "fail",
                          "detail": "No substrate runs exist for any of this "
                                    "release's claims"})
        criteria_met["has_runs"] = False
        blockers += 1
    else:
        criteria_met["has_runs"] = True

    # pass_rate over the counted latest runs.
    pass_rate = (passed / len(counted) * 100) if counted else 0.0
    if counted:
        if pass_rate >= min_pass_rate:
            reasoning.append({"check": "pass_rate", "status": "pass",
                              "detail": f"Pass rate {pass_rate:.1f}% meets "
                                        f"threshold of {min_pass_rate}%"})
            criteria_met["pass_rate"] = True
        else:
            reasoning.append({"check": "pass_rate", "status": "fail",
                              "detail": f"Pass rate {pass_rate:.1f}% below "
                                        f"threshold of {min_pass_rate}%"})
            criteria_met["pass_rate"] = False
            blockers += 1

    # grounding_integrity — a passing run of a broken claim is vacuous.
    broken = [c for c in claim_evidence
              if (c["grounding"] or {}).get("overall") == "broken"]
    drifted = [c for c in claim_evidence
               if (c["grounding"] or {}).get("overall") == "drifted"]
    stale = [c for c in claim_evidence
             if (c["grounding"] or {}).get("stale") is True
             and (c["grounding"] or {}).get("overall") == "intact"]
    if broken and block_on_broken:
        reasoning.append({"check": "grounding_integrity", "status": "fail",
                          "detail": f"{len(broken)} claim(s) have BROKEN "
                                    "grounding — their run evidence is vacuous"})
        criteria_met["grounding_integrity"] = False
        blockers += 1
    elif drifted or stale or broken:
        reasoning.append({"check": "grounding_integrity", "status": "warn",
                          "detail": f"{len(drifted)} drifted / {len(stale)} "
                                    f"stale grounding(s)"
                                    + (f"; {len(broken)} broken (blocking "
                                       "disabled)" if broken else "")})
        criteria_met["grounding_integrity"] = True
        warnings += 1
    else:
        reasoning.append({"check": "grounding_integrity", "status": "pass",
                          "detail": "All claim groundings intact and current"})
        criteria_met["grounding_integrity"] = True

    # coverage — partial never_run is a warning (total never_run already blocked).
    if counted and never_run:
        reasoning.append({"check": "coverage", "status": "warn",
                          "detail": f"{len(never_run)} of {len(claim_evidence)} "
                                    "claim(s) have no current-version run"})
        warnings += 1

    # version_currency — evidence that isn't cleanly of the shipped version.
    superseded = [c for c in claim_evidence if c["superseded_newer_run"]]
    unknown = [c for c in counted if c["latest_run"]["version_unknown"]]
    if superseded or unknown:
        reasoning.append({"check": "version_currency", "status": "warn",
                          "detail": f"{len(superseded)} claim(s) have newer "
                                    f"superseded-version runs; {len(unknown)} "
                                    "counted run(s) carry no version pin"})
        warnings += 1

    # freshness — the newest counted run must be recent enough to trust.
    finished = [c["latest_run"]["finished_at"] for c in counted
                if c["latest_run"]["finished_at"]]
    if finished:
        newest = max(datetime.fromisoformat(f) for f in finished)
        age_hours = (now - newest).total_seconds() / 3600
        if age_hours > max_age_hours:
            reasoning.append({"check": "freshness", "status": "warn",
                              "detail": f"Newest run is {age_hours:.0f}h old "
                                        f"(window {max_age_hours}h)"})
            warnings += 1

    if blockers == 0 and warnings == 0:
        recommendation, confidence = "go", 0.95
    elif blockers == 0:
        recommendation, confidence = "conditional_go", 0.75
    else:
        recommendation, confidence = "no_go", 0.90

    # Substrate-native risk: failure share + per-finding increments, capped.
    score = 0
    if counted:
        score += round((100 - pass_rate) * 0.5)
    score += 25 * blockers + 10 * warnings
    score = max(0, min(100, score))

    return {
        "applicable": True,
        "recommendation": recommendation,
        "confidence": confidence,
        "reasoning": reasoning,
        "criteria_met": criteria_met,
        "metrics": {
            "claim_count": len(claim_evidence),
            "counted_runs": len(counted),
            "passed": passed, "failed": failed, "errored": errored,
            "never_run": len(never_run),
            "pass_rate": round(pass_rate, 1),
            "grounding": {"broken": len(broken), "drifted": len(drifted),
                          "stale": len(stale)},
            "blockers": blockers, "warnings": warnings,
        },
        "risk": {"score": score, "level": _risk_level(score)},
    }


def get_release_substrate_decision(tenant_id: int, external_keys,
                                   criteria=None) -> dict:
    """Best-effort: assemble + compute in one tenant connection. Never raises —
    ``{available: False}`` on any read error; zero claims → ``{available: True,
    applicable: False}`` (the composer skips cleanly). The release_substrate_console
    wrapper discipline."""
    keys = [k for k in (external_keys or []) if k]
    if not keys:
        return {"available": True, "applicable": False, "claim_count": 0}
    try:
        from sqlalchemy.orm import Session

        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            session = Session(bind=conn)
            try:
                evidence = _assemble_claim_evidence(session, keys)
                out = compute_substrate_decision(evidence, criteria)
                out["available"] = True
                return out
            finally:
                session.close()
    except Exception as exc:
        log.warning("substrate decision unavailable for tenant %s: %s",
                    tenant_id, exc)
        return {"available": False, "applicable": False, "claim_count": 0}
