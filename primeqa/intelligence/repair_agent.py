"""The repair-agent spine (Build 7, D-215/.1) — deterministic, proposal-only.

Triage consumes S6 interpretations (verdict + cause_kind — never re-parsed
error strings, D-215 §1) and maps each failed/errored run to at most one
deterministic repair proposal:

  - ``regenerate_from_current_org`` — the claim predates the org's current
    truth (``vr_formula_drift`` / ``no_active_vr`` / ``vr_formula_indeterminate``
    / ``rejected_unasserted_reason``): the repair is a fresh S3 generation for
    the claim's requirement (the D-205.1 re-version path).
  - ``rerun`` — the run could not be evaluated (``not_evaluated`` / outcome
    ``errored``): infrastructure, not semantics.

**Findings never get proposals**: ``enforcement_gap`` and the *_not_* verdicts
are the product's OUTPUT. Nothing auto-applies in the spine — a human approves
on the Repairs panel; apply executes immediately and stamps the ledger.
Best-effort consoles throughout (never raise into a page or tick).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text

log = logging.getLogger(__name__)

# verdict/cause → proposal kind (the deterministic spine map, D-215.1 §1)
_REGENERATE_CAUSES = frozenset({
    "vr_formula_drift", "no_active_vr", "vr_formula_indeterminate",
})
_REGENERATE_VERDICTS = frozenset({"rejected_unasserted_reason"})
_RERUN_VERDICTS = frozenset({"not_evaluated"})
# the product's findings — NEVER repaired (D-215 §1)
_FINDING_VERDICTS = frozenset({
    "prohibition_not_enforced", "value_not_persisted",
    "state_not_transitioned", "automation_not_triggered",
    "asserted_metadata_absent", "asserted_value_differs",
})


def proposal_for(verdict: Optional[str], cause_kind: Optional[str],
                 outcome: Optional[str]) -> Optional[str]:
    """The deterministic triage map. Pure. None = no proposal (a finding, a
    pass, or an unmapped shape — the spine never guesses)."""
    if verdict in _FINDING_VERDICTS:
        return None
    if cause_kind in _REGENERATE_CAUSES or verdict in _REGENERATE_VERDICTS:
        return "regenerate_from_current_org"
    if verdict in _RERUN_VERDICTS or outcome == "errored":
        return "rerun"
    return None


def triage_new_failures(tenant_id: int, *, limit: int = 50) -> dict:
    """Scan failed/errored interpretations that have no proposal yet and write
    proposals for the mapped shapes (proposed status; dedup by the partial
    unique index — one active proposal per (claim, kind)). Best-effort; never
    raises. Returns ``{proposed: n, scanned: n}``."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        proposed = scanned = 0
        with get_tenant_connection(tenant_id) as conn:
            rows = conn.execute(text(
                "SELECT i.run_id, i.claim_test_id, i.outcome::text AS outcome, "
                "       i.verdict, i.cause_kind, r.environment_id "
                "FROM s6_interpretations i "
                "JOIN s4_execution_runs r ON r.run_id = i.run_id "
                "WHERE i.outcome IN ('failed', 'errored') "
                "  AND NOT EXISTS (SELECT 1 FROM repair_proposals p "
                "                  WHERE p.run_id = i.run_id) "
                "ORDER BY r.finished_at DESC LIMIT :lim"),
                {"lim": limit}).mappings().all()
            for row in rows:
                scanned += 1
                kind = proposal_for(row["verdict"], row["cause_kind"],
                                    row["outcome"])
                if kind is None:
                    continue
                n = conn.execute(text(
                    "INSERT INTO repair_proposals "
                    "(run_id, claim_test_id, environment_id, verdict, "
                    " cause_kind, proposal_kind, payload) "
                    "VALUES (:rid, :tid, :eid, :v, :c, :k, "
                    "        CAST(:p AS jsonb)) "
                    "ON CONFLICT DO NOTHING"),
                    {"rid": str(row["run_id"]), "tid": str(row["claim_test_id"]),
                     "eid": row["environment_id"], "v": row["verdict"],
                     "c": row["cause_kind"], "k": kind,
                     "p": json.dumps({})}).rowcount
                proposed += n
        return {"proposed": proposed, "scanned": scanned}
    except Exception as exc:
        log.warning("repair triage failed for tenant %s: %s", tenant_id, exc)
        return {"proposed": 0, "scanned": 0}


def list_proposals(tenant_id: int, *, statuses=("proposed", "approved"),
                   limit: int = 50) -> dict:
    """Best-effort: the Repairs panel read. Never raises."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            rows = conn.execute(text(
                "SELECT id, run_id, claim_test_id, environment_id, verdict, "
                "       cause_kind, proposal_kind, status, payload, created_at "
                "FROM repair_proposals WHERE status = ANY(:st) "
                "ORDER BY created_at DESC LIMIT :lim"),
                {"st": list(statuses), "lim": limit}).mappings().all()
        return {"available": True, "proposals": [{
            "id": r["id"], "run_id": str(r["run_id"]),
            "claim_test_id": str(r["claim_test_id"]),
            "environment_id": r["environment_id"],
            "verdict": r["verdict"], "cause_kind": r["cause_kind"],
            "proposal_kind": r["proposal_kind"], "status": r["status"],
            "payload": r["payload"],
            "created_at": r["created_at"].isoformat(),
        } for r in rows]}
    except Exception as exc:
        log.warning("list_proposals failed for tenant %s: %s", tenant_id, exc)
        return {"available": False, "proposals": []}


def decide_proposal(tenant_id: int, proposal_id: int, *, approve: bool,
                    decided_by: Optional[int] = None) -> dict:
    """Approve (and immediately APPLY) or reject one proposal. Apply:
    ``rerun`` → enqueue S4 on the proposal's environment;
    ``regenerate_from_current_org`` → enqueue S3 for the claim's
    ``generated_from`` requirement (idempotency at an unchanged S1 seq is
    reported as already-current, not a failure). Best-effort; never raises."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            row = conn.execute(text(
                "SELECT id, run_id, claim_test_id, environment_id, "
                "       proposal_kind, status FROM repair_proposals "
                "WHERE id = :pid"), {"pid": proposal_id}).mappings().first()
        if row is None or row["status"] not in ("proposed", "approved"):
            return {"ok": False, "error": "proposal not found or already decided"}

        if not approve:
            _stamp(tenant_id, proposal_id, "rejected", decided_by, {})
            return {"ok": True, "status": "rejected"}

        outcome = _apply(tenant_id, row)
        _stamp(tenant_id, proposal_id, "applied", decided_by, outcome)
        return {"ok": True, "status": "applied", **outcome}
    except Exception as exc:
        log.warning("decide_proposal failed for tenant %s proposal %s: %s",
                    tenant_id, proposal_id, exc)
        return {"ok": False, "error": str(exc)}


def _apply(tenant_id: int, row) -> dict:
    """Execute one approved proposal. Returns the payload to stamp."""
    if row["proposal_kind"] == "rerun":
        from primeqa.execution_engine.intake import enqueue_s4_execution
        job = enqueue_s4_execution(
            tenant_id=tenant_id, test_id=row["claim_test_id"],
            environment_id=row["environment_id"])
        return {"action": "rerun", "s4_job_id": job.id}

    # regenerate_from_current_org: resolve the claim's requirement key, then
    # enqueue a fresh S3 generation (the D-205.1 re-version path; idempotent
    # per (key, s1_seq) — an unchanged org reports already-current).
    from sqlalchemy import text as _text

    from primeqa.generation.intake import enqueue_s3_generation
    from primeqa.semantic.connection import get_tenant_connection
    with get_tenant_connection(tenant_id) as conn:
        key = conn.execute(_text(
            "SELECT external_key FROM test_requirement_links "
            "WHERE test_id = :tid AND link_kind = 'generated_from' "
            "ORDER BY linked_at DESC LIMIT 1"),
            {"tid": str(row["claim_test_id"])}).scalar()
    if not key:
        return {"action": "regenerate", "error": "no generated_from link"}
    try:
        job = enqueue_s3_generation(
            tenant_id=tenant_id,
            requirement_ref={"key": key, "text": ""},
            environment_id=row["environment_id"])
        already = job.status not in ("queued",)
        return {"action": "regenerate", "requirement_key": key,
                "s3_job_id": job.id,
                "note": ("already generated at the current org version"
                         if already else None)}
    except Exception as exc:
        return {"action": "regenerate", "requirement_key": key,
                "error": str(exc)}


def _stamp(tenant_id: int, proposal_id: int, status: str,
           decided_by: Optional[int], payload: dict) -> None:
    from primeqa.semantic.connection import get_tenant_connection
    with get_tenant_connection(tenant_id) as conn:
        conn.execute(text(
            "UPDATE repair_proposals SET status = :st, decided_by = :by, "
            "decided_at = :at, payload = payload || CAST(:p AS jsonb) "
            "WHERE id = :pid"),
            {"st": status, "by": decided_by,
             "at": datetime.now(timezone.utc),
             "p": json.dumps({k: v for k, v in payload.items() if v is not None}),
             "pid": proposal_id})
