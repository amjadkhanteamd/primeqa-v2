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
