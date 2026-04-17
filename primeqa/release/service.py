"""Service layer for release management."""

DEFAULT_DECISION_CRITERIA = {
    "min_pass_rate": 95,
    "critical_tests_must_pass": True,
    "no_unresolved_high_risk_impacts": True,
    "max_flaky_test_percent": 10,
}

VALID_STATUSES = {"planning", "in_progress", "ready", "decided", "shipped", "cancelled"}


class ReleaseService:
    def __init__(self, release_repo):
        self.release_repo = release_repo

    def create_release(self, tenant_id, name, created_by, **kwargs):
        if kwargs.get("status") and kwargs["status"] not in VALID_STATUSES:
            raise ValueError(f"Invalid status. Must be one of: {', '.join(VALID_STATUSES)}")
        criteria = kwargs.get("decision_criteria")
        if not criteria:
            kwargs["decision_criteria"] = dict(DEFAULT_DECISION_CRITERIA)
        r = self.release_repo.create_release(tenant_id, name, created_by, **kwargs)
        return self._release_dict(r)

    def update_release(self, release_id, tenant_id, updates):
        if "status" in updates and updates["status"] not in VALID_STATUSES:
            raise ValueError(f"Invalid status. Must be one of: {', '.join(VALID_STATUSES)}")
        r = self.release_repo.update_release(release_id, tenant_id, updates)
        if not r:
            raise ValueError("Release not found")
        return self._release_dict(r)

    def delete_release(self, release_id, tenant_id):
        if not self.release_repo.delete_release(release_id, tenant_id):
            raise ValueError("Release not found")

    def list_releases(self, tenant_id, status=None):
        return [self._release_dict(r) for r in self.release_repo.list_releases(tenant_id, status)]

    def get_release(self, release_id, tenant_id):
        r = self.release_repo.get_release(release_id, tenant_id)
        return self._release_dict(r) if r else None

    def get_release_detail(self, release_id, tenant_id):
        r = self.release_repo.get_release(release_id, tenant_id)
        if not r:
            return None
        requirements = self.release_repo.list_requirements(release_id)
        impacts = self.release_repo.list_impacts(release_id)
        test_plan = self.release_repo.list_test_plan(release_id)
        runs = self.release_repo.list_runs(release_id)
        latest_decision = self.release_repo.get_latest_decision(release_id)

        return {
            **self._release_dict(r),
            "requirements": [{
                "id": req.id, "jira_key": req.jira_key,
                "jira_summary": req.jira_summary, "is_stale": req.is_stale,
            } for req in requirements],
            "impacts": [{
                "id": i.id, "metadata_impact_id": i.metadata_impact_id,
                "risk_score": i.risk_score, "risk_level": i.risk_level,
            } for i in impacts],
            "test_plan": [{
                "id": t.id, "test_case_id": t.test_case_id,
                "priority": t.priority, "position": t.position,
                "risk_score": t.risk_score, "inclusion_reason": t.inclusion_reason,
            } for t in test_plan],
            "runs": [{
                "id": run.id, "pipeline_run_id": run.pipeline_run_id,
                "triggered_at": run.triggered_at.isoformat() if run.triggered_at else None,
            } for run in runs],
            "latest_decision": {
                "id": latest_decision.id, "recommendation": latest_decision.recommendation,
                "confidence": latest_decision.confidence,
                "reasoning": latest_decision.reasoning,
                "final_decision": latest_decision.final_decision,
                "decided_at": latest_decision.decided_at.isoformat() if latest_decision.decided_at else None,
            } if latest_decision else None,
        }

    def add_requirement(self, release_id, tenant_id, requirement_id, added_by):
        r = self.release_repo.get_release(release_id, tenant_id)
        if not r:
            raise ValueError("Release not found")
        self.release_repo.add_requirement(release_id, requirement_id, added_by)

    def remove_requirement(self, release_id, tenant_id, requirement_id):
        r = self.release_repo.get_release(release_id, tenant_id)
        if not r:
            raise ValueError("Release not found")
        self.release_repo.remove_requirement(release_id, requirement_id)

    def add_test_plan_item(self, release_id, tenant_id, test_case_id, **kwargs):
        r = self.release_repo.get_release(release_id, tenant_id)
        if not r:
            raise ValueError("Release not found")
        self.release_repo.add_test_plan_item(release_id, test_case_id, **kwargs)

    def remove_test_plan_item(self, release_id, tenant_id, test_case_id):
        r = self.release_repo.get_release(release_id, tenant_id)
        if not r:
            raise ValueError("Release not found")
        self.release_repo.remove_test_plan_item(release_id, test_case_id)

    @staticmethod
    def _release_dict(r):
        return {
            "id": r.id, "tenant_id": r.tenant_id,
            "name": r.name, "version_tag": r.version_tag,
            "description": r.description, "status": r.status,
            "target_date": r.target_date.isoformat() if r.target_date else None,
            "decision_criteria": r.decision_criteria,
            "created_by": r.created_by,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
