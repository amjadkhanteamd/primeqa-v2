"""Release Owner Dashboard — data assembly.

One call from the view: `get_dashboard_data(environment_id, tenant_id, db)`
returns every value the template needs. No template touches the DB.

Go/No-Go contract:
  - Any suite with a quality_gate_threshold AND latest pass rate below
    threshold → NO-GO (with per-gate callout)
  - Else if release_status=='APPROVED' on the latest run → GO (sticky
    approval)
  - Else if release_status=='OVERRIDDEN' → OVERRIDDEN (sticky)
  - Else if no gates defined: latest pass rate >= 80% → GO, < 80% → NO-GO
  - Else empty / no data → UNKNOWN (template shows empty state)

All data is scoped to the given environment_id + tenant_id. No
cross-tenant reads.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

from sqlalchemy import func as sf, or_
from sqlalchemy.orm import Session

from primeqa.core.models import Environment, User
from primeqa.execution.models import PipelineRun, RunTestResult
from primeqa.test_management.models import (
    Requirement, SuiteTestCase, TestCase, TestSuite,
)


GO_FALLBACK_THRESHOLD = 80  # pass rate when no quality gates defined


@dataclass
class GateStatus:
    suite_id: int
    name: str
    threshold: int
    pass_rate: Optional[float]
    passing: bool

    def as_dict(self) -> dict:
        return {
            "suite_id": self.suite_id,
            "name": self.name,
            "threshold": self.threshold,
            "pass_rate": self.pass_rate,
            "passing": self.passing,
        }


def _latest_run_for_env(db: Session, tenant_id: int, environment_id: int
                       ) -> Optional[PipelineRun]:
    return (db.query(PipelineRun)
            .filter(PipelineRun.tenant_id == tenant_id,
                    PipelineRun.environment_id == environment_id)
            .order_by(PipelineRun.queued_at.desc())
            .first())


def _pass_rate(passed: int, total: int) -> Optional[float]:
    if not total:
        return None
    return round(100.0 * passed / total, 1)


def _gate_statuses(db: Session, tenant_id: int, environment_id: int
                  ) -> list[GateStatus]:
    """Per-suite gate status: pass_rate of latest run containing
    suite's test cases vs threshold.

    Skips suites with no quality_gate_threshold.
    """
    suites = (db.query(TestSuite)
              .filter(TestSuite.tenant_id == tenant_id,
                      TestSuite.deleted_at.is_(None),
                      TestSuite.quality_gate_threshold.isnot(None))
              .all())
    if not suites:
        return []

    out: list[GateStatus] = []
    for s in suites:
        # Latest run that included any TC from this suite.
        subq = (db.query(SuiteTestCase.test_case_id)
                .filter(SuiteTestCase.suite_id == s.id).subquery())
        run = (db.query(PipelineRun)
               .join(RunTestResult, RunTestResult.run_id == PipelineRun.id)
               .filter(PipelineRun.tenant_id == tenant_id,
                       PipelineRun.environment_id == environment_id,
                       RunTestResult.test_case_id.in_(subq))
               .order_by(PipelineRun.queued_at.desc())
               .first())
        if run is None or not run.total_tests:
            out.append(GateStatus(
                suite_id=s.id, name=s.name,
                threshold=s.quality_gate_threshold,
                pass_rate=None, passing=False,
            ))
            continue
        # Scope: only this suite's TCs inside that run.
        rtr = (db.query(sf.count().label("total"),
                        sf.sum(sf.case((RunTestResult.status == "passed", 1),
                                       else_=0)).label("passed"))
               .filter(RunTestResult.run_id == run.id,
                       RunTestResult.test_case_id.in_(subq))
               .one())
        pr = _pass_rate(rtr.passed or 0, rtr.total or 0)
        out.append(GateStatus(
            suite_id=s.id, name=s.name,
            threshold=s.quality_gate_threshold,
            pass_rate=pr,
            passing=(pr is not None and pr >= s.quality_gate_threshold),
        ))
    return out


def _ticket_grid(db: Session, tenant_id: int, environment_id: int,
                 run: Optional[PipelineRun]) -> list[dict]:
    """Per-ticket status for the dashboard grid.

    For each requirement with a jira_key in the tenant, figure out
    whether it passed / failed / blocked / untested in the latest run.
    """
    reqs = (db.query(Requirement)
            .filter(Requirement.tenant_id == tenant_id,
                    Requirement.jira_key.isnot(None),
                    Requirement.deleted_at.is_(None))
            .order_by(Requirement.jira_key.asc())
            .all())
    if not reqs:
        return []

    # If no run, everything is UNTESTED.
    if run is None:
        return [{"jira_key": r.jira_key, "status": "untested",
                 "requirement_id": r.id} for r in reqs]

    # Find worst status per requirement in the current run.
    # worst = "failed" > "blocked" > "passed" > "untested"
    rank = {"failed": 3, "error": 3, "skipped": 2, "passed": 1}
    by_req: dict[int, str] = {}
    rows = (db.query(TestCase.requirement_id, RunTestResult.status)
            .join(RunTestResult, RunTestResult.test_case_id == TestCase.id)
            .filter(RunTestResult.run_id == run.id,
                    TestCase.requirement_id.isnot(None))
            .all())
    for req_id, status in rows:
        if status not in rank:
            continue
        current = by_req.get(req_id)
        if current is None or rank[status] > rank.get(current, 0):
            by_req[req_id] = status

    grid = []
    for r in reqs:
        status = by_req.get(r.id)
        if status is None:
            render = "untested"
        elif status in ("failed", "error"):
            render = "failed"
        elif status == "skipped":
            render = "blocked"
        else:
            render = "passed"
        grid.append({
            "jira_key": r.jira_key, "status": render,
            "requirement_id": r.id,
        })
    return grid


def _sprint_trends(db: Session, tenant_id: int, environment_id: int,
                   limit: int = 5) -> list[dict]:
    """Last N runs' pass rates. Sprint-label optional — we key by run id
    so the Release Owner sees the recent trajectory regardless of how
    the runs were sourced (sprint, suite, hand-pick).
    """
    rows = (db.query(PipelineRun)
            .filter(PipelineRun.tenant_id == tenant_id,
                    PipelineRun.environment_id == environment_id,
                    PipelineRun.total_tests > 0)
            .order_by(PipelineRun.queued_at.desc())
            .limit(limit)
            .all())
    out = []
    for r in reversed(rows):  # oldest -> newest for chart
        out.append({
            "run_id": r.id,
            "label": r.label or f"#{r.id}",
            "pass_rate": _pass_rate(r.passed or 0, r.total_tests or 0),
            "queued_at": r.queued_at.isoformat() if r.queued_at else None,
        })
    return out


def _determine_go_no_go(run: Optional[PipelineRun],
                        gates: list[GateStatus]) -> tuple[str, str]:
    """Return (state, reason). state ∈ GO / NO-GO / APPROVED / OVERRIDDEN
    / UNKNOWN. reason is a short string suitable for the hero callout."""
    if run is None:
        return "UNKNOWN", "No runs yet."
    if run.release_status == "APPROVED":
        return "APPROVED", "Release approved."
    if run.release_status == "OVERRIDDEN":
        return "OVERRIDDEN", run.override_reason or "Override recorded."

    # Any gate below threshold → NO-GO with that gate called out.
    failing = [g for g in gates
               if g.pass_rate is not None and not g.passing]
    if failing:
        g = failing[0]
        return "NO-GO", (f"{g.name} below {g.threshold}% gate "
                         f"({g.pass_rate:.0f}%)")

    # No gates defined: use the fallback pass-rate threshold.
    if not gates:
        pr = _pass_rate(run.passed or 0, run.total_tests or 0)
        if pr is None:
            return "UNKNOWN", "No test results yet."
        if pr >= GO_FALLBACK_THRESHOLD:
            return "GO", f"Pass rate {pr:.0f}% \u2265 {GO_FALLBACK_THRESHOLD}%."
        return "NO-GO", f"Pass rate {pr:.0f}% < {GO_FALLBACK_THRESHOLD}%."

    # All gates passing.
    return "GO", "All quality gates passing."


def compute_negative_counts(db: Session, run_id: int) -> dict:
    """Prompt 15 Fix 2: count expected-failure outcomes across a run.

    The executor flips expect_fail_verified steps to status=passed (the
    step achieved its goal of being rejected by SF). So run.passed +
    run.failed already reflect the right totals — but the dashboard +
    copy-summary need to ATTRIBUTE how many of those 'passes' were
    verified negative cases so the reporting distinguishes
    '9 positive passes' from '6 positive + 3 verified negatives'.

    Returns {
      'expected_failures': int,   # verified negatives (ran + SF rejected)
      'unexpected_passes': int,   # negative tests SF did NOT reject
    }
    """
    from primeqa.execution.models import RunStepResult, RunTestResult
    try:
        rows = (db.query(RunStepResult.failure_class, sf.count().label("n"))
                .join(RunTestResult, RunTestResult.id == RunStepResult.run_test_result_id)
                .filter(RunTestResult.run_id == run_id,
                        RunStepResult.failure_class.in_(
                            ("expected_fail_verified",
                             "expected_fail_unverified")))
                .group_by(RunStepResult.failure_class)
                .all())
    except Exception:
        return {"expected_failures": 0, "unexpected_passes": 0}
    out = {"expected_failures": 0, "unexpected_passes": 0}
    for fc, n in rows:
        if fc == "expected_fail_verified":
            out["expected_failures"] = int(n or 0)
        elif fc == "expected_fail_unverified":
            out["unexpected_passes"] = int(n or 0)
    return out


def _risk_from_summary(run: Optional[PipelineRun]) -> str:
    """Coarse risk tier from the run's failure counts.

    0 failures        -> LOW
    1-3 failures      -> MEDIUM
    4+ failures or unknown -> HIGH
    """
    if run is None:
        return "UNKNOWN"
    failed = run.failed or 0
    if failed == 0:
        return "LOW"
    if failed <= 3:
        return "MEDIUM"
    return "HIGH"


def get_dashboard_data(environment_id: int, tenant_id: int,
                        db: Session) -> dict:
    """Assemble every value the dashboard template reads."""
    env = db.query(Environment).filter_by(id=environment_id,
                                           tenant_id=tenant_id).first()
    if env is None:
        return {"environment": None, "empty": True}

    run = _latest_run_for_env(db, tenant_id, environment_id)
    gates = _gate_statuses(db, tenant_id, environment_id)
    state, reason = _determine_go_no_go(run, gates)
    grid = _ticket_grid(db, tenant_id, environment_id, run)
    trends = _sprint_trends(db, tenant_id, environment_id, limit=5)
    pass_rate = _pass_rate(run.passed or 0, run.total_tests or 0) if run else None

    approved_by_name = None
    if run and run.approved_by:
        u = db.query(User).filter_by(id=run.approved_by).first()
        approved_by_name = u.full_name if u else None

    # Fix 2: attribute how many of the passes / failures were verified
    # expected failures (a passing negative test) vs unexpected passes
    # (a broken negative test — missing validation rule).
    neg_counts = compute_negative_counts(db, run.id) if run else {
        "expected_failures": 0, "unexpected_passes": 0,
    }

    return {
        "environment": {
            "id": env.id, "name": env.name,
            "is_production": env.is_production,
        },
        "latest_run": {
            "id": run.id if run else None,
            "total_tests": run.total_tests if run else 0,
            "passed": run.passed if run else 0,
            "failed": run.failed if run else 0,
            "skipped": run.skipped if run else 0,
            "pass_rate": pass_rate,
            "queued_at": run.queued_at.isoformat() if (run and run.queued_at) else None,
            "release_status": run.release_status if run else None,
            "approved_by_name": approved_by_name,
            "approved_at": run.approved_at.isoformat() if (run and run.approved_at) else None,
            "override_reason": run.override_reason if run else None,
            "label": run.label if run else None,
            "failure_summary_ai": run.failure_summary_ai if run else None,
        } if run else None,
        "state": state,
        "state_reason": reason,
        "risk": _risk_from_summary(run),
        "gates": [g.as_dict() for g in gates],
        "ticket_grid": grid,
        "ticket_counts": {
            "total": len(grid),
            "passed": sum(1 for t in grid if t["status"] == "passed"),
            "failed": sum(1 for t in grid if t["status"] == "failed"),
            "blocked": sum(1 for t in grid if t["status"] == "blocked"),
            "untested": sum(1 for t in grid if t["status"] == "untested"),
            # Fix 2: negative-test attribution (step-level, not TC-level)
            "expected_failures": neg_counts["expected_failures"],
            "unexpected_passes": neg_counts["unexpected_passes"],
        },
        "trends": trends,
        "empty": run is None,
    }


__all__ = [
    "GO_FALLBACK_THRESHOLD",
    "GateStatus",
    "get_dashboard_data",
]
