"""Release Decision Engine — GO/NO-GO recommendation with reasoning.

Evaluates a release against its decision_criteria and produces a recommendation.
Recommendation-only: human always makes the final call.
"""


class DecisionEngine:
    def __init__(self, db):
        self.db = db

    def evaluate(self, release):
        """Evaluate a release and return a decision recommendation."""
        criteria = release.decision_criteria or {}
        reasoning = []
        criteria_met = {}
        blockers = 0
        warnings = 0

        # Gather metrics
        from primeqa.release.models import ReleaseRun, ReleaseImpact, ReleaseTestPlanItem
        from primeqa.execution.models import PipelineRun, RunTestResult
        release_runs = self.db.query(ReleaseRun).filter(ReleaseRun.release_id == release.id).all()
        run_ids = [rr.pipeline_run_id for rr in release_runs]

        if not run_ids:
            return {
                "recommendation": "no_go",
                "confidence": 0.9,
                "reasoning": [{"check": "has_runs", "status": "fail",
                              "detail": "No test runs have been executed for this release"}],
                "criteria_met": {"has_runs": False},
            }

        test_results = self.db.query(RunTestResult).filter(
            RunTestResult.run_id.in_(run_ids),
        ).all()
        total = len(test_results)
        passed = sum(1 for r in test_results if r.status == "passed")
        failed = sum(1 for r in test_results if r.status in ("failed", "error"))
        pass_rate = (passed / total * 100) if total > 0 else 0

        # Check 1: Minimum pass rate
        min_pass_rate = criteria.get("min_pass_rate", 95)
        if pass_rate >= min_pass_rate:
            reasoning.append({"check": "pass_rate", "status": "pass",
                             "detail": f"Pass rate {pass_rate:.1f}% meets threshold of {min_pass_rate}%"})
            criteria_met["pass_rate"] = True
        else:
            reasoning.append({"check": "pass_rate", "status": "fail",
                             "detail": f"Pass rate {pass_rate:.1f}% below threshold of {min_pass_rate}%"})
            criteria_met["pass_rate"] = False
            blockers += 1

        # Check 2: Critical tests
        if criteria.get("critical_tests_must_pass", True):
            critical_plan = self.db.query(ReleaseTestPlanItem).filter(
                ReleaseTestPlanItem.release_id == release.id,
                ReleaseTestPlanItem.priority == "critical",
            ).all()
            critical_ids = {p.test_case_id for p in critical_plan}
            critical_fails = [r for r in test_results
                            if r.test_case_id in critical_ids and r.status in ("failed", "error")]
            if not critical_fails:
                reasoning.append({"check": "critical_tests", "status": "pass",
                                 "detail": f"All {len(critical_ids)} critical tests passed"})
                criteria_met["critical_tests"] = True
            else:
                reasoning.append({"check": "critical_tests", "status": "fail",
                                 "detail": f"{len(critical_fails)} critical test(s) failed"})
                criteria_met["critical_tests"] = False
                blockers += 1

        # Check 3: High-risk impacts resolved
        if criteria.get("no_unresolved_high_risk_impacts", True):
            from primeqa.test_management.models import MetadataImpact
            unresolved_high = self.db.query(ReleaseImpact).filter(
                ReleaseImpact.release_id == release.id,
                ReleaseImpact.risk_level.in_(["high", "critical"]),
            ).count()
            if unresolved_high == 0:
                reasoning.append({"check": "impacts_resolved", "status": "pass",
                                 "detail": "No unresolved high-risk impacts"})
                criteria_met["impacts_resolved"] = True
            else:
                reasoning.append({"check": "impacts_resolved", "status": "warn",
                                 "detail": f"{unresolved_high} high-risk impact(s) in release"})
                criteria_met["impacts_resolved"] = False
                warnings += 1

        # Determine recommendation
        if blockers == 0 and warnings == 0:
            recommendation = "go"
            confidence = 0.95
        elif blockers == 0:
            recommendation = "conditional_go"
            confidence = 0.75
        else:
            recommendation = "no_go"
            confidence = 0.90

        return {
            "recommendation": recommendation,
            "confidence": confidence,
            "reasoning": reasoning,
            "criteria_met": criteria_met,
            "metrics": {
                "total_tests": total, "passed": passed, "failed": failed,
                "pass_rate": round(pass_rate, 1),
                "blockers": blockers, "warnings": warnings,
            },
        }
