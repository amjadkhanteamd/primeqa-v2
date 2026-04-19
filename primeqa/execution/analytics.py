"""Analytics service — pass rate, flakiness, trend aggregations for dashboards."""

from datetime import datetime, timezone, timedelta
from sqlalchemy import func, case


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
            func.sum(case((RunTestResult.status == "passed", 1), else_=0)).label("passed"),
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
            func.sum(case((RunTestResult.status == "passed", 1), else_=0)).label("passed"),
            func.sum(case((RunTestResult.status.in_(["failed", "error"]), 1), else_=0)).label("failed"),
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
        """Audit fix M-7 (2026-04-19): eliminated N+1. Was iterating up to
        10 active releases and issuing a SELECT per release for the
        latest decision. Now one DISTINCT ON query."""
        from sqlalchemy import text as sql
        rows = self.db.execute(sql("""
            WITH active AS (
              SELECT id, name, status, target_date
              FROM releases
              WHERE tenant_id = :tid
                AND status IN ('planning', 'in_progress', 'ready')
              ORDER BY target_date ASC NULLS LAST
              LIMIT 10
            ),
            latest_dec AS (
              SELECT DISTINCT ON (release_id)
                     release_id, recommendation, final_decision
              FROM release_decisions
              WHERE release_id IN (SELECT id FROM active)
              ORDER BY release_id, created_at DESC
            )
            SELECT a.id, a.name, a.status, a.target_date,
                   d.recommendation, d.final_decision
            FROM active a
            LEFT JOIN latest_dec d ON d.release_id = a.id
            ORDER BY a.target_date ASC NULLS LAST
        """), {"tid": tenant_id}).all()
        return [{
            "id": r._mapping["id"], "name": r._mapping["name"],
            "status": r._mapping["status"],
            "target_date": r._mapping["target_date"].isoformat() if r._mapping["target_date"] else None,
            "recommendation": r._mapping["recommendation"],
            "final_decision": r._mapping["final_decision"],
        } for r in rows]

    def overall_stats(self, tenant_id, days=30):
        """Audit fix M-7: 2 queries → 1 (CASE aggregate)."""
        from sqlalchemy import text as sql
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        row = self.db.execute(sql("""
            SELECT COUNT(*)::int AS total,
                   SUM(CASE WHEN r.status = 'passed' THEN 1 ELSE 0 END)::int AS passed
            FROM run_test_results r
            JOIN pipeline_runs p ON p.id = r.run_id
            WHERE p.tenant_id = :tid
              AND r.executed_at >= :cutoff
        """), {"tid": tenant_id, "cutoff": cutoff}).one()._mapping
        total = int(row["total"] or 0)
        passed = int(row["passed"] or 0)
        return {
            "total_results_30d": total,
            "passed_results_30d": passed,
            "pass_rate_30d": round((passed / total) * 100, 1) if total else 0,
        }
