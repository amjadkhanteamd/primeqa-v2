"""Test-plan prioritization engine.

Ranks a release's test-plan items by priority. Factors: references to critical
entities, recent failure history.

(The metadata-impact risk-scoring path — score_impact / score_all_release_impacts
— was removed with the metadata-impact subsystem in D-195.2; its feeder table was
already retired in D-193.)
"""

from datetime import datetime, timezone, timedelta


CRITICAL_ENTITIES = {
    "Opportunity.StageName", "Opportunity.Amount", "Opportunity.CloseDate",
    "Account.OwnerId", "Lead.Status", "Case.Status", "User.IsActive",
}


class RiskEngine:
    def __init__(self, db):
        self.db = db

    def score_test_case_priority(self, test_case_id, release_id=None):
        """Score a test case's priority within a release."""
        from primeqa.test_management.models import TestCase
        tc = self.db.query(TestCase).filter(TestCase.id == test_case_id).first()
        if not tc:
            return None

        factors = []
        score = 50

        from primeqa.test_management.models import TestCaseVersion
        latest = self.db.query(TestCaseVersion).filter(
            TestCaseVersion.test_case_id == test_case_id,
        ).order_by(TestCaseVersion.version_number.desc()).first()
        if latest and latest.referenced_entities:
            critical_refs = [e for e in latest.referenced_entities
                           if any(e.startswith(c) for c in CRITICAL_ENTITIES)]
            if critical_refs:
                score += 25
                factors.append({"factor": "references_critical_entities", "weight": 25,
                               "detail": f"References {len(critical_refs)} critical entities"})

        from primeqa.execution.models import RunTestResult
        recent_fail = self.db.query(RunTestResult).filter(
            RunTestResult.test_case_id == test_case_id,
            RunTestResult.status.in_(["failed", "error"]),
            RunTestResult.executed_at > datetime.now(timezone.utc) - timedelta(days=30),
        ).count()
        if recent_fail >= 3:
            score += 20
            factors.append({"factor": "recent_failures", "weight": 20,
                           "detail": f"Failed {recent_fail} times in last 30 days"})
        elif recent_fail >= 1:
            score += 10
            factors.append({"factor": "recent_failures", "weight": 10,
                           "detail": f"Failed {recent_fail} times in last 30 days"})

        score = min(100, score)
        level = self._score_to_level(score)
        return {"score": score, "level": level, "factors": factors}

    def rank_release_test_plan(self, release_id):
        """Rank test plan items for a release by priority."""
        from primeqa.release.models import ReleaseTestPlanItem
        items = self.db.query(ReleaseTestPlanItem).filter(
            ReleaseTestPlanItem.release_id == release_id,
        ).all()
        scored = []
        for item in items:
            score_result = self.score_test_case_priority(item.test_case_id, release_id)
            if score_result:
                item.risk_score = score_result["score"]
                item.priority = score_result["level"]
                scored.append({"item": item, "score": score_result["score"]})

        scored.sort(key=lambda x: x["score"], reverse=True)
        for idx, entry in enumerate(scored):
            entry["item"].position = idx

        self.db.commit()
        return len(scored)

    @staticmethod
    def _score_to_level(score):
        if score >= 75:
            return "critical"
        if score >= 50:
            return "high"
        if score >= 25:
            return "medium"
        return "low"
