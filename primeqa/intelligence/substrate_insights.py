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

from primeqa.evolution import list_grounding_validity
from primeqa.interpretation import (
    cluster_by_vr,
    cluster_flapping,
    cluster_recurring_causes,
    list_interpretations,
)

log = logging.getLogger(__name__)


# --- flatteners: read DTOs (frozen dataclasses) -> JSON-safe plain dicts -----

def _interp_dict(r) -> dict:
    return {
        "run_id": str(r.run_id),
        "recipe_id": str(r.recipe_id),
        "claim_test_id": str(r.claim_test_id),
        "outcome": r.outcome,
        "verdict": r.verdict,
        "attribution": r.attribution,
        "cause": ({"cause_kind": r.cause.cause_kind, "vr_name": r.cause.vr_name,
                   "detail": r.cause.detail} if r.cause else None),
        "evidence_count": len(r.evidence_refs),
        "has_phrasing": bool(r.phrasing),     # phrasing render is deferred (D-117)
    }


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
            "overall": r.overall, "claim_verdict": r.claim_verdict}


def _empty_payload(*, available: bool) -> dict:
    return {"interpretations": [], "cause_clusters": [], "vr_clusters": [],
            "flapping": [], "grounding": [], "available": available, "empty": True}


# --- the pure assembler (directly testable on a tenant-scoped session) --------

def _assemble_insights(session, limit: int) -> dict:
    """Read S6 interpretations + clustering + S8 grounding verdicts on a
    **tenant-scoped** ``session`` and return JSON-safe plain dicts. Pure — no
    connection management; the caller owns the session's scope."""
    interpretations = [_interp_dict(r)
                       for r in list_interpretations(session, limit=limit)]
    cause_clusters = [_cause_cluster_dict(c)
                      for c in cluster_recurring_causes(session, min_runs=2)]
    vr_clusters = [_vr_cluster_dict(c)
                   for c in cluster_by_vr(session, min_runs=2)]
    flapping = [_flapping_dict(c) for c in cluster_flapping(session)]
    grounding = [_grounding_dict(r)
                 for r in list_grounding_validity(session, limit=limit)]
    empty = not any([interpretations, cause_clusters, vr_clusters,
                     flapping, grounding])
    return {"interpretations": interpretations, "cause_clusters": cause_clusters,
            "vr_clusters": vr_clusters, "flapping": flapping,
            "grounding": grounding, "available": True, "empty": empty}


# --- the bridge (best-effort; v1 owns it) ------------------------------------

def get_substrate_insights(tenant_id: int, *, limit: int = 50) -> dict:
    """Tenant-scoped best-effort read of the substrate insights for the
    ``/substrate-insights`` page. Opens a tenant connection, binds an ORM
    ``Session`` over it (the read APIs need ``.query()``), and assembles the
    dict. **Never raises** — any failure (absent tenant schema, the stores not
    yet migrated, a read error) returns ``available=False`` so the v1 page
    degrades to an empty-state rather than erroring."""
    try:
        from sqlalchemy.orm import Session
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            return _assemble_insights(Session(bind=conn), limit)
    except Exception as exc:                 # absent schema / empty / read error
        log.warning("substrate insights unavailable for tenant %s: %s",
                    tenant_id, exc)
        return _empty_payload(available=False)
