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

    def update_release(self, release_id, tenant_id, updates,
                       expected_updated_at=None):
        if "status" in updates and updates["status"] not in VALID_STATUSES:
            raise ValueError(f"Invalid status. Must be one of: {', '.join(VALID_STATUSES)}")
        r, result = self.release_repo.update_release(
            release_id, tenant_id, updates,
            expected_updated_at=expected_updated_at,
        )
        if result == "not_found":
            raise ValueError("Release not found")
        if result == "conflict":
            # Surface a structured conflict so the route returns 409
            # with the current row so the UI can render a diff banner.
            from primeqa.shared.api import ConflictError
            raise ConflictError(
                "Release was modified by another user",
                details={"current_updated_at": r.updated_at.isoformat() if r and r.updated_at else None},
            )
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
            "test_plan": self._enrich_test_plan(test_plan, tenant_id),
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

    def _enrich_test_plan(self, test_plan_rows, tenant_id):
        """Join test_plan rows with TC title + coverage_type so the UI can
        render something human-readable instead of just a bare id."""
        from primeqa.test_management.models import TestCase
        tc_ids = [t.test_case_id for t in test_plan_rows]
        tc_by_id = {}
        if tc_ids:
            rows = self.release_repo.db.query(TestCase).filter(
                TestCase.id.in_(tc_ids),
                TestCase.tenant_id == tenant_id,
            ).all()
            tc_by_id = {r.id: r for r in rows}
        out = []
        for t in test_plan_rows:
            tc = tc_by_id.get(t.test_case_id)
            out.append({
                "id": t.id, "test_case_id": t.test_case_id,
                "priority": t.priority, "position": t.position,
                "risk_score": t.risk_score, "inclusion_reason": t.inclusion_reason,
                "title": tc.title if tc else f"Test Case #{t.test_case_id}",
                "status": tc.status if tc else None,
                "coverage_type": tc.coverage_type if tc else None,
                "requirement_id": tc.requirement_id if tc else None,
                "deleted": bool(tc.deleted_at) if tc else True,
            })
        return out

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

    # ---- Bulk attach helpers (release curation) --------------------------
    # Single-item add_requirement / add_test_plan_item are kept for existing
    # callers; the bulk variants below tenant-scope the payload and skip
    # duplicates in one commit so the UI can offer multi-select pickers.

    def add_requirements_bulk(self, release_id, tenant_id, requirement_ids, added_by):
        from primeqa.test_management.models import Requirement
        from primeqa.release.models import ReleaseRequirement

        r = self.release_repo.get_release(release_id, tenant_id)
        if not r:
            raise ValueError("Release not found")
        ids = [int(x) for x in (requirement_ids or []) if str(x).strip()]
        if len(ids) > 200:
            raise ValueError("Bulk add capped at 200 requirements per call")
        if not ids:
            return {"added": [], "already_in": [], "skipped": []}

        db = self.release_repo.db
        valid_rows = db.query(Requirement.id).filter(
            Requirement.id.in_(ids),
            Requirement.tenant_id == tenant_id,
            Requirement.deleted_at.is_(None),
        ).all()
        valid = {row[0] for row in valid_rows}
        skipped = [i for i in ids if i not in valid]

        existing = {row[0] for row in db.query(ReleaseRequirement.requirement_id).filter(
            ReleaseRequirement.release_id == release_id,
            ReleaseRequirement.requirement_id.in_(list(valid)),
        ).all()}
        added = []
        for rid in ids:
            if rid not in valid or rid in existing:
                continue
            db.add(ReleaseRequirement(
                release_id=release_id, requirement_id=rid, added_by=added_by,
            ))
            added.append(rid)
        if added:
            db.commit()
        return {"added": added, "already_in": sorted(existing), "skipped": skipped}

    def refresh_test_plan_from_requirements(self, release_id, tenant_id):
        """Rebuild the release's test plan from its linked requirements.

        Use case: each regeneration on a requirement soft-deletes prior
        own-drafts, but any release that had those draft ids pinned to
        its plan keeps pointing at the dead rows. Running the plan then
        reports "Release test plan is empty" because the filter strips
        deleted TCs.

        This method:
          1. Removes plan items whose TCs are now soft-deleted.
          2. For every requirement linked to the release, adds every
             currently-active (non-deleted) TC that isn't already
             represented in the plan.

        Idempotent: safe to call repeatedly; second call is a no-op.
        Does not touch manually-added TCs that don't belong to any
        linked requirement \u2014 those stay in the plan.

        Returns {"removed_dead": int, "added_live": int} so the UI can
        show a useful confirmation toast.
        """
        from primeqa.release.models import ReleaseTestPlanItem
        from primeqa.test_management.models import TestCase
        from sqlalchemy import func as _sfunc

        r = self.release_repo.get_release(release_id, tenant_id)
        if not r:
            raise ValueError("Release not found")

        db = self.release_repo.db

        # 1) Drop plan items whose TC has been soft-deleted.
        dead_items = (
            db.query(ReleaseTestPlanItem)
            .join(TestCase, TestCase.id == ReleaseTestPlanItem.test_case_id)
            .filter(
                ReleaseTestPlanItem.release_id == release_id,
                TestCase.deleted_at.isnot(None),
            )
            .all()
        )
        removed = 0
        for item in dead_items:
            db.delete(item)
            removed += 1

        # 2) Collect the set of tc_ids already in the plan AFTER deletes,
        #    so we don't try to re-add any live ones already pinned.
        existing = {
            row[0]
            for row in db.query(ReleaseTestPlanItem.test_case_id)
            .filter(ReleaseTestPlanItem.release_id == release_id)
            .all()
        }

        # 3) For each linked requirement, pull its active TCs.
        linked_reqs = self.release_repo.list_requirements(release_id)
        req_ids = [x.id for x in linked_reqs]
        if not req_ids:
            if removed:
                db.commit()
            return {"removed_dead": removed, "added_live": 0}

        live_tcs = (
            db.query(TestCase.id)
            .filter(
                TestCase.requirement_id.in_(req_ids),
                TestCase.tenant_id == tenant_id,
                TestCase.deleted_at.is_(None),
                TestCase.status.in_(("active", "approved", "draft")),
            )
            .all()
        )
        candidate_ids = [row[0] for row in live_tcs if row[0] not in existing]

        max_pos = db.query(_sfunc.max(ReleaseTestPlanItem.position)).filter(
            ReleaseTestPlanItem.release_id == release_id,
        ).scalar()
        next_pos = (max_pos or 0) + 1
        added = 0
        for tc_id in candidate_ids:
            db.add(ReleaseTestPlanItem(
                release_id=release_id,
                test_case_id=tc_id,
                priority="medium",
                position=next_pos,
                inclusion_reason="refreshed from linked requirements",
            ))
            next_pos += 1
            added += 1

        if removed or added:
            db.commit()
        return {"removed_dead": removed, "added_live": added}

    def add_test_plan_items_bulk(self, release_id, tenant_id, test_case_ids,
                                 added_by, priority="medium", inclusion_reason=None):
        from primeqa.test_management.models import TestCase
        from primeqa.release.models import ReleaseTestPlanItem
        from sqlalchemy import func as _sfunc

        r = self.release_repo.get_release(release_id, tenant_id)
        if not r:
            raise ValueError("Release not found")
        ids = [int(x) for x in (test_case_ids or []) if str(x).strip()]
        if len(ids) > 200:
            raise ValueError("Bulk add capped at 200 test cases per call")
        if not ids:
            return {"added": [], "already_in": [], "skipped": []}

        db = self.release_repo.db
        valid_rows = db.query(TestCase.id).filter(
            TestCase.id.in_(ids),
            TestCase.tenant_id == tenant_id,
            TestCase.deleted_at.is_(None),
        ).all()
        valid = {row[0] for row in valid_rows}
        skipped = [i for i in ids if i not in valid]

        existing = {row[0] for row in db.query(ReleaseTestPlanItem.test_case_id).filter(
            ReleaseTestPlanItem.release_id == release_id,
            ReleaseTestPlanItem.test_case_id.in_(list(valid)),
        ).all()}
        max_pos = db.query(_sfunc.max(ReleaseTestPlanItem.position)).filter(
            ReleaseTestPlanItem.release_id == release_id,
        ).scalar()
        next_pos = (max_pos or 0) + 1

        added = []
        for tc_id in ids:
            if tc_id not in valid or tc_id in existing:
                continue
            db.add(ReleaseTestPlanItem(
                release_id=release_id, test_case_id=tc_id,
                priority=priority, position=next_pos,
                inclusion_reason=inclusion_reason,
            ))
            added.append(tc_id)
            next_pos += 1
        if added:
            db.commit()
        return {"added": added, "already_in": sorted(existing), "skipped": skipped}

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
