"""Release substrate console — v1's UI bridge to the substrate EVIDENCE for a
release decision (D-172, UI Area 5 slice 5a).

Best-effort, read-only. A release reaches the substrate through its linked
**requirements** (not its v1 test-plan ``test_cases``, which don't map to S2
claims): ``release → requirements → external_key`` (``jira_key`` or ``req-<id>``)
``→ list_tests_by_requirement(generated_from) → claim test_ids → {S8 grounding-
validity, S6 latest run verdict}``. The v1 ``DecisionEngine`` stays the GO/NO-GO
authority; this surfaces grounding-drift + per-claim verdict **evidence** the human
weighs alongside it — it does not produce or flip the verdict (D-172).

One tenant connection + a shared session (not N ``read_claim_runs`` calls). Never
raises (``available=False`` on any error); the S6/S8 stores are empty until the live
sync, so ``available=True, claim_count=0`` is the common state.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_AT_RISK = ("drifted", "broken")


def _assemble_release_substrate(session, external_keys) -> dict:
    """Pure: fan the release's requirement keys → claims → grounding + latest
    verdict, rolled up. Directly testable on the substrate session."""
    from primeqa.test_representation.coordinator import SemanticTransactionCoordinator
    from primeqa.evolution import list_grounding_validity
    from primeqa.intelligence.s4_execution_console import _read_claim_runs

    coord = SemanticTransactionCoordinator()
    test_ids, seen = [], set()
    for key in external_keys:                          # release's requirement keys
        for m in coord.list_tests_by_requirement(
                session, external_system="jira", external_key=key,
                link_kind="generated_from"):
            sid = str(m.test_id)
            if sid not in seen:                        # dedupe a claim shared by 2 reqs
                seen.add(sid)
                test_ids.append(m.test_id)

    gc = {"intact": 0, "drifted": 0, "broken": 0, "not_computed": 0}
    vc = {"passed": 0, "failed": 0, "errored": 0, "never_run": 0}
    at_risk = []
    for tid in test_ids:
        gv_rows = list_grounding_validity(session, test_id=tid)
        gv = gv_rows[-1] if gv_rows else None          # ordered (test_id, version_seq) → last = latest
        overall = gv.overall if gv is not None else None
        gc[overall if overall in gc else "not_computed"] += 1

        runs = _read_claim_runs(session, tid)          # recency-correct, newest-first
        latest = runs[0] if runs else None
        if latest is None:
            vc["never_run"] += 1
        else:
            o = latest.get("outcome")
            vc[o if o in vc else "errored"] += 1

        if overall in _AT_RISK:                        # the actionable list
            at_risk.append({
                "test_id": str(tid), "overall": overall,
                "claim_verdict": gv.claim_verdict, "detail": gv.detail or {},
                "latest_outcome": latest.get("outcome") if latest else None,
                "latest_verdict": latest.get("verdict") if latest else None,
            })
    return {"available": True, "claim_count": len(test_ids),
            "grounding_counts": gc, "verdict_counts": vc, "at_risk": at_risk}


def get_release_substrate(tenant_id: int, external_keys) -> dict:
    """Best-effort substrate evidence for a release (its requirements' claims →
    grounding + latest verdict). Never raises. Returns ``{available, claim_count,
    grounding_counts, verdict_counts, at_risk}``; ``available=False`` on read error.
    An empty ``external_keys`` short-circuits to a zero-claim available payload."""
    keys = [k for k in (external_keys or []) if k]
    if not keys:
        return {"available": True, "claim_count": 0, "grounding_counts": {},
                "verdict_counts": {}, "at_risk": []}
    try:
        from primeqa.semantic.connection import get_tenant_connection
        from sqlalchemy.orm import Session
        with get_tenant_connection(tenant_id) as conn:
            session = Session(bind=conn)
            try:
                return _assemble_release_substrate(session, keys)
            finally:
                session.close()
    except Exception as exc:
        log.warning("release substrate unavailable for tenant %s: %s", tenant_id, exc)
        return {"available": False, "claim_count": 0, "grounding_counts": {},
                "verdict_counts": {}, "at_risk": []}
