"""Repository for the test management domain.

DB queries scoped to: sections, requirements, test_cases, test_case_versions,
                      test_suites, suite_test_cases, ba_reviews, metadata_impacts
"""

from datetime import datetime, timezone

from sqlalchemy import func, and_
from sqlalchemy.orm import joinedload

from primeqa.test_management.models import (
    Section, Requirement, TestCase, TestCaseVersion,
    TestSuite, SuiteTestCase, BAReview, MetadataImpact,
)


class SectionRepository:
    def __init__(self, db):
        self.db = db

    def create_section(self, tenant_id, name, created_by, parent_id=None, description=None, position=0):
        section = Section(
            tenant_id=tenant_id,
            name=name,
            parent_id=parent_id,
            description=description,
            position=position,
            created_by=created_by,
        )
        self.db.add(section)
        self.db.commit()
        self.db.refresh(section)
        return section

    def get_section(self, section_id, tenant_id):
        return self.db.query(Section).filter(
            Section.id == section_id, Section.tenant_id == tenant_id,
        ).first()

    def list_sections(self, tenant_id, parent_id=None):
        q = self.db.query(Section).filter(Section.tenant_id == tenant_id)
        if parent_id is not None:
            q = q.filter(Section.parent_id == parent_id)
        else:
            q = q.filter(Section.parent_id == None)
        return q.order_by(Section.position).all()

    def get_section_tree(self, tenant_id):
        all_sections = self.db.query(Section).filter(
            Section.tenant_id == tenant_id,
        ).order_by(Section.position).all()
        section_map = {s.id: {
            "id": s.id, "name": s.name, "description": s.description,
            "position": s.position, "parent_id": s.parent_id, "children": [],
        } for s in all_sections}
        roots = []
        for s in all_sections:
            node = section_map[s.id]
            if s.parent_id and s.parent_id in section_map:
                section_map[s.parent_id]["children"].append(node)
            else:
                roots.append(node)
        return roots

    def update_section(self, section_id, tenant_id, updates):
        section = self.get_section(section_id, tenant_id)
        if not section:
            return None
        for k, v in updates.items():
            if hasattr(section, k) and k not in ("id", "tenant_id", "created_by", "created_at"):
                setattr(section, k, v)
        self.db.commit()
        self.db.refresh(section)
        return section

    def delete_section(self, section_id, tenant_id):
        section = self.get_section(section_id, tenant_id)
        if not section:
            return False
        self.db.delete(section)
        self.db.commit()
        return True


class RequirementRepository:
    def __init__(self, db):
        self.db = db

    def create_requirement(self, tenant_id, section_id, source, created_by, **kwargs):
        req = Requirement(
            tenant_id=tenant_id,
            section_id=section_id,
            source=source,
            created_by=created_by,
            jira_key=kwargs.get("jira_key"),
            jira_summary=kwargs.get("jira_summary"),
            jira_description=kwargs.get("jira_description"),
            acceptance_criteria=kwargs.get("acceptance_criteria"),
        )
        self.db.add(req)
        self.db.commit()
        self.db.refresh(req)
        return req

    def get_requirement(self, requirement_id, tenant_id):
        return self.db.query(Requirement).filter(
            Requirement.id == requirement_id, Requirement.tenant_id == tenant_id,
        ).first()

    def list_requirements(self, tenant_id, section_id=None):
        q = self.db.query(Requirement).filter(Requirement.tenant_id == tenant_id)
        if section_id:
            q = q.filter(Requirement.section_id == section_id)
        return q.order_by(Requirement.created_at.desc()).all()

    def update_requirement(self, requirement_id, tenant_id, updates):
        req = self.get_requirement(requirement_id, tenant_id)
        if not req:
            return None
        for k, v in updates.items():
            if hasattr(req, k) and k not in ("id", "tenant_id", "created_by", "created_at"):
                setattr(req, k, v)
        req.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(req)
        return req

    def find_by_jira_key(self, tenant_id, jira_key):
        return self.db.query(Requirement).filter(
            Requirement.tenant_id == tenant_id,
            Requirement.jira_key == jira_key,
        ).first()

    def mark_stale(self, requirement_id, tenant_id):
        req = self.get_requirement(requirement_id, tenant_id)
        if req:
            req.is_stale = True
            req.updated_at = datetime.now(timezone.utc)
            self.db.commit()
        return req


class TestCaseRepository:
    def __init__(self, db):
        self.db = db

    def create_test_case(self, tenant_id, title, owner_id, created_by, **kwargs):
        tc = TestCase(
            tenant_id=tenant_id,
            title=title,
            owner_id=owner_id,
            created_by=created_by,
            requirement_id=kwargs.get("requirement_id"),
            section_id=kwargs.get("section_id"),
            visibility=kwargs.get("visibility", "private"),
            status=kwargs.get("status", "draft"),
        )
        self.db.add(tc)
        self.db.commit()
        self.db.refresh(tc)
        return tc

    def get_test_case(self, test_case_id, tenant_id):
        return self.db.query(TestCase).filter(
            TestCase.id == test_case_id, TestCase.tenant_id == tenant_id,
        ).first()

    def list_test_cases(self, tenant_id, user_id=None, requirement_id=None,
                        section_id=None, status=None, include_private_for=None):
        q = self.db.query(TestCase).filter(TestCase.tenant_id == tenant_id)
        if requirement_id:
            q = q.filter(TestCase.requirement_id == requirement_id)
        if section_id:
            q = q.filter(TestCase.section_id == section_id)
        if status:
            q = q.filter(TestCase.status == status)
        if include_private_for:
            q = q.filter(
                (TestCase.visibility == "shared") |
                (TestCase.owner_id == include_private_for)
            )
        else:
            q = q.filter(TestCase.visibility == "shared")
        return q.order_by(TestCase.updated_at.desc()).all()

    def update_test_case(self, test_case_id, tenant_id, updates, expected_version=None):
        tc = self.get_test_case(test_case_id, tenant_id)
        if not tc:
            return None, "not_found"
        if expected_version is not None and tc.version != expected_version:
            return None, "conflict"
        for k, v in updates.items():
            if hasattr(tc, k) and k not in ("id", "tenant_id", "created_by", "version"):
                setattr(tc, k, v)
        self.db.commit()
        self.db.refresh(tc)
        return tc, "ok"

    def create_version(self, test_case_id, metadata_version_id, created_by, **kwargs):
        latest = self.db.query(func.max(TestCaseVersion.version_number)).filter(
            TestCaseVersion.test_case_id == test_case_id,
        ).scalar() or 0

        tcv = TestCaseVersion(
            test_case_id=test_case_id,
            version_number=latest + 1,
            metadata_version_id=metadata_version_id,
            steps=kwargs.get("steps", []),
            expected_results=kwargs.get("expected_results", []),
            preconditions=kwargs.get("preconditions", []),
            generation_method=kwargs.get("generation_method", "manual"),
            confidence_score=kwargs.get("confidence_score"),
            referenced_entities=kwargs.get("referenced_entities", []),
            created_by=created_by,
        )
        self.db.add(tcv)
        self.db.commit()
        self.db.refresh(tcv)

        tc = self.db.query(TestCase).filter(TestCase.id == test_case_id).first()
        if tc:
            tc.current_version_id = tcv.id
            self.db.commit()

        return tcv

    def get_versions(self, test_case_id):
        return self.db.query(TestCaseVersion).filter(
            TestCaseVersion.test_case_id == test_case_id,
        ).order_by(TestCaseVersion.version_number.desc()).all()

    def get_latest_version(self, test_case_id):
        return self.db.query(TestCaseVersion).filter(
            TestCaseVersion.test_case_id == test_case_id,
        ).order_by(TestCaseVersion.version_number.desc()).first()


class TestSuiteRepository:
    def __init__(self, db):
        self.db = db

    def create_suite(self, tenant_id, name, suite_type, created_by, description=None):
        suite = TestSuite(
            tenant_id=tenant_id,
            name=name,
            suite_type=suite_type,
            description=description,
            created_by=created_by,
        )
        self.db.add(suite)
        self.db.commit()
        self.db.refresh(suite)
        return suite

    def get_suite(self, suite_id, tenant_id):
        return self.db.query(TestSuite).filter(
            TestSuite.id == suite_id, TestSuite.tenant_id == tenant_id,
        ).first()

    def list_suites(self, tenant_id):
        return self.db.query(TestSuite).filter(
            TestSuite.tenant_id == tenant_id,
        ).order_by(TestSuite.created_at.desc()).all()

    def update_suite(self, suite_id, tenant_id, updates):
        suite = self.get_suite(suite_id, tenant_id)
        if not suite:
            return None
        for k, v in updates.items():
            if hasattr(suite, k) and k not in ("id", "tenant_id", "created_by", "created_at"):
                setattr(suite, k, v)
        suite.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(suite)
        return suite

    def add_test_case(self, suite_id, test_case_id, position=0):
        existing = self.db.query(SuiteTestCase).filter(
            SuiteTestCase.suite_id == suite_id,
            SuiteTestCase.test_case_id == test_case_id,
        ).first()
        if existing:
            return existing
        stc = SuiteTestCase(suite_id=suite_id, test_case_id=test_case_id, position=position)
        self.db.add(stc)
        self.db.commit()
        self.db.refresh(stc)
        return stc

    def remove_test_case(self, suite_id, test_case_id):
        stc = self.db.query(SuiteTestCase).filter(
            SuiteTestCase.suite_id == suite_id,
            SuiteTestCase.test_case_id == test_case_id,
        ).first()
        if stc:
            self.db.delete(stc)
            self.db.commit()
            return True
        return False

    def get_suite_test_cases(self, suite_id):
        return self.db.query(SuiteTestCase).filter(
            SuiteTestCase.suite_id == suite_id,
        ).order_by(SuiteTestCase.position).all()

    def reorder_test_case(self, suite_id, test_case_id, new_position):
        stc = self.db.query(SuiteTestCase).filter(
            SuiteTestCase.suite_id == suite_id,
            SuiteTestCase.test_case_id == test_case_id,
        ).first()
        if stc:
            stc.position = new_position
            self.db.commit()
        return stc


class BAReviewRepository:
    def __init__(self, db):
        self.db = db

    def create_review(self, tenant_id, test_case_version_id, assigned_to):
        review = BAReview(
            tenant_id=tenant_id,
            test_case_version_id=test_case_version_id,
            assigned_to=assigned_to,
        )
        self.db.add(review)
        self.db.commit()
        self.db.refresh(review)
        return review

    def get_review(self, review_id):
        return self.db.query(BAReview).filter(BAReview.id == review_id).first()

    def list_reviews(self, tenant_id, status=None, assigned_to=None):
        q = self.db.query(BAReview).filter(BAReview.tenant_id == tenant_id)
        if status:
            q = q.filter(BAReview.status == status)
        if assigned_to:
            q = q.filter(BAReview.assigned_to == assigned_to)
        return q.order_by(BAReview.created_at.desc()).all()

    def update_review(self, review_id, status, feedback=None, reviewed_by=None):
        review = self.get_review(review_id)
        if not review:
            return None
        review.status = status
        review.feedback = feedback
        review.reviewed_by = reviewed_by
        review.reviewed_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(review)
        return review


class MetadataImpactRepository:
    def __init__(self, db):
        self.db = db

    def list_pending_impacts(self, tenant_id):
        return self.db.query(MetadataImpact).join(
            TestCase, MetadataImpact.test_case_id == TestCase.id,
        ).filter(
            TestCase.tenant_id == tenant_id,
            MetadataImpact.resolution == "pending",
        ).all()

    def resolve_impact(self, impact_id, resolution, resolved_by):
        impact = self.db.query(MetadataImpact).filter(
            MetadataImpact.id == impact_id,
        ).first()
        if not impact:
            return None
        impact.resolution = resolution
        impact.resolved_by = resolved_by
        impact.resolved_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(impact)
        return impact
