"""Substrate-insights bridge (D-155, cutover Step 2 / slice 2.1).

The first v1→substrate read consumer: a best-effort, tenant-scoped read of the
S6 interpretation store + its cross-run clustering + the S8 grounding-validity
verdicts, flattened to plain dicts the ``/substrate-insights`` page renders.

**v1 owns the bridge** (the allowed v1→substrate direction, like
``interpretation_phrasing.py``) — the substrate packages stay route-agnostic.
The S6/S8 read APIs run on a tenant-schema-scoped ORM ``Session``; a v1 Flask
route holds the main-DB session, so the bridge is
``with get_tenant_connection(tenant_id) as conn: Session(bind=conn)`` (the
verified precedent in ``evolution/recompute.py`` + ``execution_engine/run.py``).

Two layers, split for testability:
  - :func:`_assemble_insights` — **pure**: takes a tenant-scoped session, runs the
    reads, returns the dict. Directly testable on the test ``session`` fixture.
  - :func:`get_substrate_insights` — the **bridge**: opens the tenant connection,
    calls the pure assembler, and is **best-effort** — any failure (absent tenant
    schema, empty store, read error) returns ``available=False`` and never raises,
    so a substrate hiccup can never break the v1 page.

Every substrate store is empty until the ops live-SF run (Step 0's deferred
exit-gate) populates S1 + the first runs/recompute ticks land, so the common
prod result is ``available=True, empty=True`` — the page renders an empty-state.
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from primeqa.evolution import list_grounding_validity
from primeqa.interpretation import (
    cluster_by_vr,
    cluster_flapping,
    cluster_recurring_causes,
)

log = logging.getLogger(__name__)


# --- recency-correct runs feed (D-170 4a keystone) ---------------------------
# Section A reads s4_execution_runs (the only table with a time axis) LEFT JOIN
# the S6 verdict/cause, newest-first. This fixes the S6-only read which ordered
# by the random-uuid run_id AND silently dropped interpret-failed runs (a run
# whose best-effort interpret step failed has no s6_interpretations row).

_RECENT_RUNS_SQL = (
    "SELECT CAST(r.run_id AS text) AS run_id, CAST(r.claim_test_id AS text) AS claim_test_id, "
    "r.outcome::text AS outcome, r.finished_at, r.environment_id, "
    "i.verdict::text AS verdict, "
    "i.cause_kind, i.vr_name "
    "FROM s4_execution_runs r LEFT JOIN s6_interpretations i ON i.run_id = r.run_id "
    "WHERE (CAST(:env AS int) IS NULL OR r.environment_id = :env) "
    "ORDER BY r.finished_at DESC LIMIT :limit")


def _recent_runs(session, limit: int, environment_id=None) -> list[dict]:
    rows = session.execute(text(_RECENT_RUNS_SQL),
                           {"limit": limit, "env": environment_id}).mappings().all()
    return [{
        "run_id": r["run_id"], "claim_test_id": r["claim_test_id"],
        "outcome": r["outcome"], "verdict": r["verdict"],
        "environment_id": r["environment_id"],
        "cause": ({"cause_kind": r["cause_kind"], "vr_name": r["vr_name"]}
                  if r["cause_kind"] else None),
        "finished_at": r["finished_at"].isoformat() if r["finished_at"] is not None else None,
    } for r in rows]


# --- flatteners: read DTOs (frozen dataclasses) -> JSON-safe plain dicts -----


def _cause_cluster_dict(c) -> dict:
    return {"cause_kind": c.cause_kind, "count": c.count,
            "run_ids": [str(x) for x in c.run_ids]}


def _vr_cluster_dict(c) -> dict:
    return {"vr_name": c.vr_name, "count": c.count,
            "outcomes": list(c.outcomes), "run_ids": [str(x) for x in c.run_ids]}


def _flapping_dict(c) -> dict:
    return {"claim_test_id": str(c.claim_test_id), "outcomes": list(c.outcomes),
            "run_ids": [str(x) for x in c.run_ids]}


def _grounding_dict(r) -> dict:
    return {"test_id": str(r.test_id), "version_seq": r.version_seq,
            "evaluated_at_version_seq": r.evaluated_at_version_seq,
            "overall": r.overall, "claim_verdict": r.claim_verdict,
            # Per-org grounding (3f): which org this verdict grounds against.
            "connected_org_id": (str(r.connected_org_id)
                                 if r.connected_org_id else None),
            # D-170 4c: the rich "why drifted/broke" — claim_grounding{reason,
            # unresolved}, recipe_verdicts[{recipe_grounding, field_value{invalid},
            # rolled_up}]. Already a JSON-safe dict (the stored JSONB).
            "detail": r.detail or {}}


_GROUNDING_COUNTS_SQL = (
    "SELECT overall, COUNT(*) AS n FROM s8_grounding_validity GROUP BY overall")


def _grounding_counts(session) -> dict:
    """``{overall: count}`` over the full grounding store (accurate, not capped by
    the list limit) — the drift-board headline (D-170 4c)."""
    rows = session.execute(text(_GROUNDING_COUNTS_SQL)).mappings().all()
    return {r["overall"]: r["n"] for r in rows}


def _empty_payload(*, available: bool) -> dict:
    return {"recent_runs": [], "cause_clusters": [], "vr_clusters": [],
            "flapping": [], "grounding": [], "grounding_counts": {},
            "available": available, "empty": True}


# --- the pure assembler (directly testable on a tenant-scoped session) --------

def _assemble_insights(session, limit: int, environment_id=None) -> dict:
    """Read S6 interpretations + clustering + S8 grounding verdicts on a
    **tenant-scoped** ``session`` and return JSON-safe plain dicts. Pure — no
    connection management; the caller owns the session's scope.

    ``environment_id`` scopes the run-derived reads (recent runs + the three
    clusterings) to one connected org — flapping in a sandbox stops polluting
    the production view. Grounding stays tenant-wide (the store carries no org
    dimension yet)."""
    recent_runs = _recent_runs(session, limit, environment_id)
    cause_clusters = [_cause_cluster_dict(c)
                      for c in cluster_recurring_causes(
                          session, min_runs=2, environment_id=environment_id)]
    vr_clusters = [_vr_cluster_dict(c)
                   for c in cluster_by_vr(
                       session, min_runs=2, environment_id=environment_id)]
    flapping = [_flapping_dict(c) for c in cluster_flapping(
        session, environment_id=environment_id)]
    grounding = [_grounding_dict(r)
                 for r in list_grounding_validity(session, limit=limit)]
    grounding_counts = _grounding_counts(session)
    empty = not any([recent_runs, cause_clusters, vr_clusters,
                     flapping, grounding])
    return {"recent_runs": recent_runs, "cause_clusters": cause_clusters,
            "vr_clusters": vr_clusters, "flapping": flapping,
            "grounding": grounding, "grounding_counts": grounding_counts,
            "available": True, "empty": empty}


# --- the bridge (best-effort; v1 owns it) ------------------------------------

def get_substrate_insights(tenant_id: int, *, limit: int = 50,
                           environment_id=None) -> dict:
    """Tenant-scoped best-effort read of the substrate insights for the
    ``/substrate-insights`` page. Opens a tenant connection, binds an ORM
    ``Session`` over it (the read APIs need ``.query()``), and assembles the
    dict. ``environment_id`` scopes the run-derived sections to one connected
    org (``None`` = tenant-wide, the single-org behavior). **Never raises** —
    any failure (absent tenant schema, the stores not yet migrated, a read
    error) returns ``available=False`` so the v1 page degrades to an
    empty-state rather than erroring."""
    try:
        from sqlalchemy.orm import Session
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            return _assemble_insights(Session(bind=conn), limit, environment_id)
    except Exception as exc:                 # absent schema / empty / read error
        log.warning("substrate insights unavailable for tenant %s: %s",
                    tenant_id, exc)
        return _empty_payload(available=False)
