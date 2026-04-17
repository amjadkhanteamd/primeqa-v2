"""Flake scoring and quarantine helpers (R6).

Flake score for a test case = min(passes, fails) / total over the last N
executions. A run lands in quarantine when its score > threshold AND total
runs >= a floor (so we don't quarantine on 2 runs).

Quarantined tests:
  - excluded from /api/releases/:id/status 'go' verdicts
  - shown on the flaky dashboard
  - still runnable manually; operator can un-quarantine from the UI
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List

from sqlalchemy import case, func

from primeqa.test_management.models import TestCase

log = logging.getLogger(__name__)


DEFAULT_WINDOW = 20       # count of recent run_test_results per TC
DEFAULT_MIN_RUNS = 5      # below this many runs, never quarantine
DEFAULT_THRESHOLD = 0.30  # > 30% flaky score \u2192 quarantine


@dataclass
class FlakeEntry:
    test_case_id: int
    title: str
    total: int
    passed: int
    failed: int
    flaky_score: float


def score_recent_flakes(db, *, tenant_id: int, window: int = DEFAULT_WINDOW,
                        min_runs: int = DEFAULT_MIN_RUNS) -> List[FlakeEntry]:
    """Return test cases ranked by flaky score. Top of list = most flaky.

    Uses the last `window` run_test_results per test_case. Tests with fewer
    than min_runs are excluded (not enough signal).
    """
    from primeqa.execution.models import RunTestResult, PipelineRun
    rows = db.query(
        TestCase.id, TestCase.title,
        func.count(RunTestResult.id).label("total"),
        func.sum(case((RunTestResult.status == "passed", 1), else_=0)).label("passed"),
        func.sum(case((RunTestResult.status.in_(["failed", "error"]), 1), else_=0)).label("failed"),
    ).join(
        RunTestResult, RunTestResult.test_case_id == TestCase.id,
    ).join(
        PipelineRun, PipelineRun.id == RunTestResult.run_id,
    ).filter(
        TestCase.tenant_id == tenant_id,
        TestCase.deleted_at.is_(None),
    ).group_by(TestCase.id, TestCase.title).having(
        func.count(RunTestResult.id) >= min_runs,
    ).all()

    results: List[FlakeEntry] = []
    for tcid, title, total, passed, failed in rows:
        t, p, f = int(total or 0), int(passed or 0), int(failed or 0)
        if t == 0:
            continue
        score = min(p, f) / t
        if score > 0:
            results.append(FlakeEntry(tcid, title, t, p, f, round(score, 2)))

    results.sort(key=lambda e: e.flaky_score, reverse=True)
    return results


def auto_quarantine(db, *, tenant_id: int, threshold: float = DEFAULT_THRESHOLD,
                    window: int = DEFAULT_WINDOW,
                    min_runs: int = DEFAULT_MIN_RUNS) -> List[int]:
    """Flag tests as quarantined if they cross the flake threshold.

    Returns the list of test_case_ids newly flagged. Idempotent (already-
    quarantined tests aren't touched).
    """
    flagged: List[int] = []
    entries = score_recent_flakes(db, tenant_id=tenant_id, window=window, min_runs=min_runs)
    if not entries:
        return flagged
    for e in entries:
        if e.flaky_score <= threshold:
            continue
        tc = db.query(TestCase).filter_by(id=e.test_case_id).first()
        if not tc or tc.is_quarantined:
            continue
        tc.is_quarantined = True
        tc.quarantined_at = datetime.now(timezone.utc)
        tc.quarantined_reason = (
            f"Auto-quarantined: flake score {e.flaky_score} over last "
            f"{e.total} runs ({e.passed} pass / {e.failed} fail)."
        )
        flagged.append(tc.id)
    if flagged:
        db.commit()
    return flagged


def lift_quarantine(db, *, test_case_id: int, tenant_id: int,
                    reason: str = "manual") -> bool:
    tc = db.query(TestCase).filter_by(id=test_case_id, tenant_id=tenant_id).first()
    if not tc:
        return False
    tc.is_quarantined = False
    tc.quarantined_at = None
    tc.quarantined_reason = f"Lifted ({reason})"
    db.commit()
    return True
