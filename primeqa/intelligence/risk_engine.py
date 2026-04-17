"""Risk scoring and test prioritization engine.

Computes risk scores for metadata impacts and ranks test cases for a release.
Factors: blast radius, entity criticality, historical failure rate, business priority, recency.
"""

import json
from datetime import datetime, timezone, timedelta


CRITICAL_ENTITIES = {
    "Opportunity.StageName", "Opportunity.Amount", "Opportunity.CloseDate",
    "Account.OwnerId", "Lead.Status", "Case.Status", "User.IsActive",
}


class RiskEngine:
    def __init__(self, db):
        self.db = db

    def score_impact(self, impact, release_id=None):
        """Compute a risk score (0-100) for a metadata impact."""
        from primeqa.test_management.models import MetadataImpact, TestCaseVersion

        if isinstance(impact, int):
            impact = self.db.query(MetadataImpact).filter(MetadataImpact.id == impact).first()
        if not impact:
            return None

        factors = []
        score = 0

        entity_ref = impact.entity_ref or ""
        is_critical = any(entity_ref.startswith(e) for e in CRITICAL_ENTITIES)
        if is_critical:
            score += 40
            factors.append({"factor": "critical_entity", "weight": 40,
                           "detail": f"{entity_ref} is a critical field"})

        from sqlalchemy import cast
        from sqlalchemy.dialects.postgresql import JSONB
        blast = self.db.query(TestCaseVersion).filter(
            cast(TestCaseVersion.referenced_entities, JSONB).op("@>")(json.dumps([entity_ref])),
        ).count()
        if blast >= 20:
            score += 30
            factors.append({"factor": "blast_radius", "weight": 30,
                           "detail": f"References found in {blast} test case versions"})
        elif blast >= 5:
            score += 20
            factors.append({"factor": "blast_radius", "weight": 20,
                           "detail": f"References found in {blast} test case versions"})
        elif blast >= 1:
            score += 10
            factors.append({"factor": "blast_radius", "weight": 10,
                           "detail": f"References found in {blast} test case versions"})

        impact_type = impact.impact_type or ""
        if "removed" in impact_type:
            score += 20
            factors.append({"factor": "change_type", "weight": 20,
                           "detail": "Removal is higher-risk than addition"})
        elif "changed" in impact_type:
            score += 15
            factors.append({"factor": "change_type", "weight": 15,
                           "detail": "Field changed"})
        elif "added" in impact_type:
            score += 5
            factors.append({"factor": "change_type", "weight": 5,
                           "detail": "Addition is lower-risk"})

        score = min(100, score)
        level = self._score_to_level(score)

        return {
            "score": score, "level": level, "factors": factors,
            "blast_radius": blast, "entity_ref": entity_ref,
        }

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

    def score_all_release_impacts(self, release_id):
        """Score all impacts linked to a release."""
        from primeqa.release.models import ReleaseImpact
        impacts = self.db.query(ReleaseImpact).filter(
            ReleaseImpact.release_id == release_id,
        ).all()
        for ri in impacts:
            result = self.score_impact(ri.metadata_impact_id)
            if result:
                ri.risk_score = result["score"]
                ri.risk_level = result["level"]
                ri.risk_reasoning = {"factors": result["factors"],
                                    "blast_radius": result["blast_radius"]}
        self.db.commit()
        return len(impacts)

    @staticmethod
    def _score_to_level(score):
        if score >= 75:
            return "critical"
        if score >= 50:
            return "high"
        if score >= 25:
            return "medium"
        return "low"
