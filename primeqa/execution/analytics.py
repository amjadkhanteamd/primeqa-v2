"""Analytics service — pass rate, flakiness, trend aggregations for dashboards."""

from datetime import datetime, timezone, timedelta
from sqlalchemy import func


class AnalyticsService:
    def __init__(self, db):
        self.db = db

    def pass_rate_by_environment(self, tenant_id, days=30):
        from primeqa.execution.models import RunTestResult, PipelineRun
        from primeqa.core.models import Environment
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        rows = self.db.query(
            Environment.id, Environment.name,
            func.count(RunTestResult.id).label("total"),
            func.sum(func.cast(RunTestResult.status == "passed", type_=func.Integer)).label("passed"),
        ).join(PipelineRun, PipelineRun.environment_id == Environment.id).join(
            RunTestResult, RunTestResult.run_id == PipelineRun.id,
        ).filter(
            Environment.tenant_id == tenant_id,
            RunTestResult.executed_at >= cutoff,
        ).group_by(Environment.id, Environment.name).all()
        return [{
            "environment_id": r[0], "name": r[1],
            "total": int(r[2] or 0),
            "passed": int(r[3] or 0),
            "pass_rate": round((int(r[3] or 0) / int(r[2])) * 100, 1) if r[2] else 0,
        } for r in rows]

    def flaky_tests(self, tenant_id, days=30, limit=10):
        from primeqa.execution.models import RunTestResult, PipelineRun
        from primeqa.test_management.models import TestCase
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        rows = self.db.query(
            TestCase.id, TestCase.title,
            func.count(RunTestResult.id).label("total"),
            func.sum(func.cast(RunTestResult.status == "passed", type_=func.Integer)).label("passed"),
            func.sum(func.cast(RunTestResult.status.in_(["failed", "error"]), type_=func.Integer)).label("failed"),
        ).join(RunTestResult, RunTestResult.test_case_id == TestCase.id).join(
            PipelineRun, PipelineRun.id == RunTestResult.run_id,
        ).filter(
            TestCase.tenant_id == tenant_id,
            RunTestResult.executed_at >= cutoff,
        ).group_by(TestCase.id, TestCase.title).having(
            func.count(RunTestResult.id) >= 3,
        ).all()

        scored = []
        for r in rows:
            total = int(r[2] or 0)
            passed = int(r[3] or 0)
            failed = int(r[4] or 0)
            if total == 0:
                continue
            flaky_score = min(passed, failed) / total
            if flaky_score > 0:
                scored.append({
                    "test_case_id": r[0], "title": r[1],
                    "total": total, "passed": passed, "failed": failed,
                    "flaky_score": round(flaky_score, 2),
                })
        scored.sort(key=lambda x: x["flaky_score"], reverse=True)
        return scored[:limit]

    def release_health(self, tenant_id):
        from primeqa.release.models import Release, ReleaseDecision
        from sqlalchemy import desc
        active = self.db.query(Release).filter(
            Release.tenant_id == tenant_id,
            Release.status.in_(["planning", "in_progress", "ready"]),
        ).order_by(Release.target_date.asc().nullslast()).limit(10).all()

        result = []
        for r in active:
            latest = self.db.query(ReleaseDecision).filter(
                ReleaseDecision.release_id == r.id,
            ).order_by(desc(ReleaseDecision.created_at)).first()
            result.append({
                "id": r.id, "name": r.name, "status": r.status,
                "target_date": r.target_date.isoformat() if r.target_date else None,
                "recommendation": latest.recommendation if latest else None,
                "final_decision": latest.final_decision if latest else None,
            })
        return result

    def overall_stats(self, tenant_id, days=30):
        from primeqa.execution.models import RunTestResult, PipelineRun
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        total = self.db.query(func.count(RunTestResult.id)).join(
            PipelineRun, PipelineRun.id == RunTestResult.run_id,
        ).filter(
            PipelineRun.tenant_id == tenant_id,
            RunTestResult.executed_at >= cutoff,
        ).scalar() or 0

        passed = self.db.query(func.count(RunTestResult.id)).join(
            PipelineRun, PipelineRun.id == RunTestResult.run_id,
        ).filter(
            PipelineRun.tenant_id == tenant_id,
            RunTestResult.executed_at >= cutoff,
            RunTestResult.status == "passed",
        ).scalar() or 0

        return {
            "total_results_30d": total,
            "passed_results_30d": passed,
            "pass_rate_30d": round((passed / total) * 100, 1) if total else 0,
        }
