"""Substrate dashboard source (D-219 slice 1).

Drop-in replacement for ``release/dashboard.py:get_dashboard_data`` — emits
the SAME dict shape (the D-189 drop-in-reader pattern) computed from substrate
evidence instead of the frozen ``pipeline_runs`` corpus:

- corpus       = every requirement external key carrying substrate claims
- hero verdict = ``compute_substrate_decision`` over the whole corpus
- latest_run   = the "current evidence" aggregate over latest-per-claim runs
                 (``id=None`` — the template's guards then hide the v1
                 approve buttons / run links / AI failure summary)
- ticket_grid  = per-requirement rollup of claim outcomes
- trends       = daily pass-rate buckets over s4 runs (last 5 days with runs)
- gates        = [] (the template renders ``substrate_checks`` instead — the
                 decision's reasoning list IS the quality-gate analog)

The v1 function is intentionally NOT deleted (pipeline_runs still exist;
retirement Step 5b owns its removal) — the routes simply stop calling it.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Optional

from sqlalchemy import text

log = logging.getLogger(__name__)

_STATE_BY_RECOMMENDATION = {
    "go": "GO",
    "conditional_go": "CONDITIONAL GO",
    "no_go": "NO-GO",
}

# Verdicts that grade a passing negative test (the org rejected correctly) —
# the substrate analog of v1's expected-failure attribution (Prompt 15 Fix 2).
_EXPECTED_FAIL_VERDICTS = ("prohibition_enforced", "rejected_with_asserted_reason")
_UNEXPECTED_PASS_VERDICTS = ("prohibition_not_enforced", "enforcement_gap")


def _corpus_keys(session) -> list[str]:
    """Every distinct requirement external key with substrate claims."""
    rows = session.execute(text(
        "SELECT DISTINCT external_key FROM test_requirement_links "
        "WHERE link_kind = 'generated_from' ORDER BY external_key"
    )).fetchall()
    return [r[0] for r in rows]


def _keys_to_test_ids(session, keys) -> dict:
    """{external_key: [test_id, ...]} via the coordinator's requirement index."""
    from primeqa.test_representation.coordinator import (
        SemanticTransactionCoordinator,
    )
    coord = SemanticTransactionCoordinator()
    out: dict = {}
    for key in keys:
        out[key] = [str(m.test_id) for m in coord.list_tests_by_requirement(
            session, external_system="jira", external_key=key,
            link_kind="generated_from")]
    return out


def _requirement_rows(db, tenant_id: int, keys) -> dict:
    """{external_key: requirement_id} from the shared public pivot store.
    Keys are ``jira_key`` for Jira-imported reqs, ``req-<id>`` for manual."""
    from primeqa.test_management.models import Requirement
    out: dict = {}
    jira_keys = [k for k in keys if not k.startswith("req-")]
    if jira_keys:
        for r in (db.query(Requirement)
                  .filter(Requirement.tenant_id == tenant_id,
                          Requirement.jira_key.in_(jira_keys)).all()):
            out[r.jira_key] = r.id
    for k in keys:
        if k.startswith("req-"):
            try:
                out[k] = int(k[4:])
            except ValueError:
                pass
    return out


def _grid_status(rows) -> str:
    """v1 ticket-grid vocabulary from a requirement's evidence rows:
    any failing latest run → failed; full green coverage → passed;
    partial coverage with green → blocked; no evidence → untested."""
    counted = [r for r in rows if r["latest_run"] is not None]
    if any(r["latest_run"]["outcome"] in ("failed", "errored") for r in counted):
        return "failed"
    if counted and len(counted) == len(rows):
        return "passed"
    if counted:
        return "blocked"
    return "untested"


def _trends(session, environment_id: int) -> list[dict]:
    """Daily pass-rate buckets over s4 runs — last 5 distinct days with runs."""
    rows = session.execute(text(
        "SELECT date(finished_at) AS day, "
        "       COUNT(*) FILTER (WHERE outcome = 'passed') AS passed, "
        "       COUNT(*) AS total "
        "FROM s4_execution_runs WHERE environment_id = :eid "
        "GROUP BY date(finished_at) ORDER BY day DESC LIMIT 5"
    ), {"eid": environment_id}).mappings().all()
    out = [{"label": r["day"].strftime("%m-%d"),
            "pass_rate": round(r["passed"] / r["total"] * 100, 1)
            if r["total"] else None}
           for r in rows]
    return list(reversed(out))


def get_substrate_dashboard_data(environment_id: int, tenant_id: int,
                                 db) -> dict:
    """Assemble every value the dashboard templates read — substrate-sourced,
    v1-shaped (see module docstring). ``db`` is the v1 session (environment +
    requirement lookups); substrate reads open their own tenant connection."""
    from sqlalchemy.orm import Session

    from primeqa.core.models import Environment
    from primeqa.intelligence.substrate_decision import (
        _assemble_claim_evidence, compute_substrate_decision,
    )
    from primeqa.semantic.connection import get_tenant_connection

    env = db.query(Environment).filter_by(id=environment_id,
                                          tenant_id=tenant_id).first()
    if env is None:
        return {"environment": None, "empty": True}

    with get_tenant_connection(tenant_id) as conn:
        session = Session(bind=conn)
        try:
            keys = _corpus_keys(session)
            evidence = _assemble_claim_evidence(session, keys, tenant_id=tenant_id)
            by_key = _keys_to_test_ids(session, keys)
            trends = _trends(session, environment_id)
        finally:
            session.close()

    decision = compute_substrate_decision(evidence)
    if not decision.get("applicable"):
        return {
            "environment": {"id": env.id, "name": env.name,
                            "is_production": env.is_production},
            "latest_run": None, "state": "UNKNOWN",
            "state_reason": "No substrate test evidence yet.",
            "risk": "low", "gates": [], "substrate_checks": [],
            "ticket_grid": [], "ticket_counts": {
                "total": 0, "passed": 0, "failed": 0, "blocked": 0,
                "untested": 0, "expected_failures": 0, "unexpected_passes": 0,
            },
            "trends": trends, "empty": True,
        }

    m = decision["metrics"]
    state = _STATE_BY_RECOMMENDATION.get(decision["recommendation"], "UNKNOWN")
    not_pass = [r for r in decision["reasoning"] if r["status"] != "pass"]
    state_reason = (not_pass[0]["detail"] if not_pass
                    else "All substrate checks passing.")

    ev_by_id = {r["test_id"]: r for r in evidence}
    req_ids = _requirement_rows(db, tenant_id, keys)
    grid = []
    for key in keys:
        rows = [ev_by_id[t] for t in by_key.get(key, ()) if t in ev_by_id]
        if not rows:
            continue
        grid.append({"requirement_id": req_ids.get(key), "jira_key": key,
                     "status": _grid_status(rows)})

    latest_finished = [r["latest_run"]["finished_at"] for r in evidence
                       if r["latest_run"] and r["latest_run"]["finished_at"]]
    verdicts = [r["latest_run"].get("verdict") for r in evidence
                if r["latest_run"]]
    expected_failures = sum(1 for v in verdicts if v in _EXPECTED_FAIL_VERDICTS)
    unexpected_passes = sum(1 for v in verdicts if v in _UNEXPECTED_PASS_VERDICTS)

    return {
        "environment": {"id": env.id, "name": env.name,
                        "is_production": env.is_production},
        # "Current evidence" aggregate — id=None hides every v1-run affordance.
        "latest_run": {
            "id": None,
            "total_tests": m["counted_runs"] + m["never_run"],
            "passed": m["passed"],
            "failed": m["failed"] + m["errored"],
            "skipped": m["never_run"],
            "pass_rate": m["pass_rate"],
            "queued_at": max(latest_finished) if latest_finished else None,
            "release_status": None, "approved_by_name": None,
            "approved_at": None, "override_reason": None,
            "label": None, "failure_summary_ai": None,
        },
        "state": state,
        "state_reason": state_reason,
        "risk": decision["risk"]["level"],
        "gates": [],
        "substrate_checks": decision["reasoning"],
        "ticket_grid": grid,
        "ticket_counts": {
            "total": len(grid),
            "passed": sum(1 for t in grid if t["status"] == "passed"),
            "failed": sum(1 for t in grid if t["status"] == "failed"),
            "blocked": sum(1 for t in grid if t["status"] == "blocked"),
            "untested": sum(1 for t in grid if t["status"] == "untested"),
            "expected_failures": expected_failures,
            "unexpected_passes": unexpected_passes,
        },
        "trends": trends,
        "empty": False,
    }


def get_landing_substrate_stats(tenant_id: int) -> dict:
    """Best-effort substrate stats for the `/` landing page: claim counts,
    runs today, latest-per-claim pass rate, recent runs, flaky claims.
    Never raises — zeros/empties on any read error."""
    from sqlalchemy.orm import Session

    from primeqa.intelligence.substrate_decision import (
        _assemble_claim_evidence, compute_substrate_decision,
    )
    from primeqa.semantic.connection import get_tenant_connection

    try:
        with get_tenant_connection(tenant_id) as conn:
            session = Session(bind=conn)
            try:
                counts = session.execute(text(
                    "SELECT "
                    " COUNT(*) FILTER (WHERE status = 'approved') AS approved,"
                    " COUNT(*) FILTER (WHERE status = 'draft')    AS draft "
                    "FROM test_claims WHERE valid_to IS NULL"
                )).mappings().one()
                runs_today = session.execute(text(
                    "SELECT COUNT(*) FROM s4_execution_runs "
                    "WHERE finished_at >= date_trunc('day', now())"
                )).scalar() or 0
                recent = session.execute(text(
                    "SELECT r.run_id, r.outcome, r.finished_at, i.verdict "
                    "FROM s4_execution_runs r "
                    "LEFT JOIN s6_interpretations i ON i.run_id = r.run_id "
                    "ORDER BY r.finished_at DESC LIMIT 10"
                )).mappings().all()
                evidence = _assemble_claim_evidence(
                    session, _corpus_keys(session), tenant_id=tenant_id)
            finally:
                session.close()

        decision = compute_substrate_decision(evidence)
        pass_rate = (decision["metrics"]["pass_rate"]
                     if decision.get("applicable") else None)
        flaky = [{"test_id": r["test_id"],
                  "recent_outcomes": r["recent_outcomes"]}
                 for r in evidence if r.get("flaky")][:5]
        return {
            "available": True,
            "approved_claims": counts["approved"],
            "draft_claims": counts["draft"],
            "runs_today": runs_today,
            "pass_rate": pass_rate,
            "recent_runs": [{
                "id": str(r["run_id"]), "status": r["outcome"],
                "run_type": (r["verdict"] or "").replace("_", " ") or "—",
                "priority": "—",
                "queued_at": (r["finished_at"].isoformat()
                              if r["finished_at"] else ""),
            } for r in recent],
            "flaky_claims": flaky,
        }
    except Exception as exc:                          # pragma: no cover
        log.warning("landing substrate stats unavailable for tenant %s: %s",
                    tenant_id, exc)
        return {"available": False, "approved_claims": 0, "draft_claims": 0,
                "runs_today": 0, "pass_rate": None, "recent_runs": [],
                "flaky_claims": []}


__all__ = ["get_substrate_dashboard_data", "get_landing_substrate_stats"]
