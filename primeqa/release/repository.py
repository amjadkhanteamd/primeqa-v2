"""Repository for the release domain."""

from datetime import datetime, timezone

from sqlalchemy import func

from primeqa.release.models import (
    Release, ReleaseRequirement, ReleaseImpact,
    ReleaseTestPlanItem, ReleaseRun, ReleaseDecision,
)


class ReleaseRepository:
    def __init__(self, db):
        self.db = db

    def create_release(self, tenant_id, name, created_by, **kwargs):
        r = Release(
            tenant_id=tenant_id, name=name, created_by=created_by,
            version_tag=kwargs.get("version_tag"),
            description=kwargs.get("description"),
            status=kwargs.get("status", "planning"),
            target_date=kwargs.get("target_date"),
            decision_criteria=kwargs.get("decision_criteria") or {},
        )
        self.db.add(r)
        self.db.commit()
        self.db.refresh(r)
        return r

    def get_release(self, release_id, tenant_id=None):
        q = self.db.query(Release).filter(Release.id == release_id)
        if tenant_id:
            q = q.filter(Release.tenant_id == tenant_id)
        return q.first()

    def list_releases(self, tenant_id, status=None):
        q = self.db.query(Release).filter(Release.tenant_id == tenant_id)
        if status:
            q = q.filter(Release.status == status)
        return q.order_by(Release.target_date.asc().nullslast(), Release.created_at.desc()).all()

    def update_release(self, release_id, tenant_id, updates,
                       expected_updated_at=None):
        """Audit M-1 (2026-04-19): optimistic lock via `updated_at` token.

        Returns a 2-tuple `(row, status)` where status is 'ok',
        'not_found', or 'conflict'. Caller maps:
          conflict → 409 with diff banner
          not_found → 404
        `expected_updated_at` may be None (legacy caller that doesn't
        care about races — discouraged but supported).
        """
        r = self.get_release(release_id, tenant_id)
        if not r:
            return None, "not_found"
        if expected_updated_at is not None:
            # Accept either an ISO string or a datetime; compare loosely
            # (trim sub-second drift) so clients can echo what they got.
            incoming = expected_updated_at
            if isinstance(incoming, str):
                try:
                    incoming = datetime.fromisoformat(incoming.rstrip("Z"))
                except ValueError:
                    return None, "conflict"
            if not incoming.tzinfo:
                incoming = incoming.replace(tzinfo=timezone.utc)
            current = r.updated_at
            if current and not current.tzinfo:
                current = current.replace(tzinfo=timezone.utc)
            # Compare at second resolution — Postgres stores microseconds
            # but JSON serialisation commonly rounds.
            if current and abs((current - incoming).total_seconds()) > 1:
                return r, "conflict"
        for k, v in updates.items():
            if hasattr(r, k) and k not in ("id", "tenant_id", "created_by",
                                            "created_at", "updated_at"):
                setattr(r, k, v)
        r.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(r)
        return r, "ok"

    def delete_release(self, release_id, tenant_id):
        r = self.get_release(release_id, tenant_id)
        if not r:
            return False
        self.db.delete(r)
        self.db.commit()
        return True

    # --- Requirements ---

    def add_requirement(self, release_id, requirement_id, added_by):
        existing = self.db.query(ReleaseRequirement).filter(
            ReleaseRequirement.release_id == release_id,
            ReleaseRequirement.requirement_id == requirement_id,
        ).first()
        if existing:
            return existing
        rr = ReleaseRequirement(release_id=release_id, requirement_id=requirement_id, added_by=added_by)
        self.db.add(rr)
        self.db.commit()
        self.db.refresh(rr)
        return rr

    def remove_requirement(self, release_id, requirement_id):
        rr = self.db.query(ReleaseRequirement).filter(
            ReleaseRequirement.release_id == release_id,
            ReleaseRequirement.requirement_id == requirement_id,
        ).first()
        if rr:
            self.db.delete(rr)
            self.db.commit()
            return True
        return False

    def list_requirements(self, release_id):
        from primeqa.test_management.models import Requirement
        return self.db.query(Requirement).join(
            ReleaseRequirement, ReleaseRequirement.requirement_id == Requirement.id,
        ).filter(ReleaseRequirement.release_id == release_id).all()

    # --- Impacts ---

    def add_impact(self, release_id, metadata_impact_id, risk_score=None, risk_level=None, risk_reasoning=None):
        existing = self.db.query(ReleaseImpact).filter(
            ReleaseImpact.release_id == release_id,
            ReleaseImpact.metadata_impact_id == metadata_impact_id,
        ).first()
        if existing:
            return existing
        ri = ReleaseImpact(
            release_id=release_id, metadata_impact_id=metadata_impact_id,
            risk_score=risk_score, risk_level=risk_level, risk_reasoning=risk_reasoning,
        )
        self.db.add(ri)
        self.db.commit()
        self.db.refresh(ri)
        return ri

    def list_impacts(self, release_id):
        return self.db.query(ReleaseImpact).filter(
            ReleaseImpact.release_id == release_id,
        ).order_by(ReleaseImpact.risk_score.desc().nullslast()).all()

    # --- Test Plan Items ---

    def add_test_plan_item(self, release_id, test_case_id, priority="medium", position=0,
                           risk_score=None, inclusion_reason=None):
        existing = self.db.query(ReleaseTestPlanItem).filter(
            ReleaseTestPlanItem.release_id == release_id,
            ReleaseTestPlanItem.test_case_id == test_case_id,
        ).first()
        if existing:
            return existing
        item = ReleaseTestPlanItem(
            release_id=release_id, test_case_id=test_case_id,
            priority=priority, position=position,
            risk_score=risk_score, inclusion_reason=inclusion_reason,
        )
        self.db.add(item)
        self.db.commit()
        self.db.refresh(item)
        return item

    def remove_test_plan_item(self, release_id, test_case_id):
        item = self.db.query(ReleaseTestPlanItem).filter(
            ReleaseTestPlanItem.release_id == release_id,
            ReleaseTestPlanItem.test_case_id == test_case_id,
        ).first()
        if item:
            self.db.delete(item)
            self.db.commit()
            return True
        return False

    def list_test_plan(self, release_id):
        return self.db.query(ReleaseTestPlanItem).filter(
            ReleaseTestPlanItem.release_id == release_id,
        ).order_by(ReleaseTestPlanItem.position, ReleaseTestPlanItem.risk_score.desc().nullslast()).all()

    # --- Runs ---

    def link_run(self, release_id, pipeline_run_id, triggered_by):
        rr = ReleaseRun(release_id=release_id, pipeline_run_id=pipeline_run_id, triggered_by=triggered_by)
        self.db.add(rr)
        self.db.commit()
        self.db.refresh(rr)
        return rr

    def list_runs(self, release_id):
        return self.db.query(ReleaseRun).filter(
            ReleaseRun.release_id == release_id,
        ).order_by(ReleaseRun.triggered_at.desc()).all()

    # --- Decisions ---

    def create_decision(self, release_id, recommendation, **kwargs):
        d = ReleaseDecision(
            release_id=release_id, recommendation=recommendation,
            confidence=kwargs.get("confidence"),
            reasoning=kwargs.get("reasoning"),
            criteria_met=kwargs.get("criteria_met"),
            recommended_by=kwargs.get("recommended_by", "ai"),
        )
        self.db.add(d)
        self.db.commit()
        self.db.refresh(d)
        return d

    def get_latest_decision(self, release_id):
        return self.db.query(ReleaseDecision).filter(
            ReleaseDecision.release_id == release_id,
        ).order_by(ReleaseDecision.created_at.desc()).first()

    def finalize_decision(self, decision_id, final_decision, decided_by, override_reason=None):
        d = self.db.query(ReleaseDecision).filter(ReleaseDecision.id == decision_id).first()
        if not d:
            return None
        d.final_decision = final_decision
        d.decided_by = decided_by
        d.decided_at = datetime.now(timezone.utc)
        d.override_reason = override_reason
        self.db.commit()
        self.db.refresh(d)
        return d
