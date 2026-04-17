"""Service layer for the test management domain.

Business logic: CRUD, versioning, Jira sync, stale detection, BA reviews.
"""

import logging
from datetime import datetime, timezone

import requests as http_requests

log = logging.getLogger(__name__)


class TestManagementService:
    def __init__(self, section_repo, requirement_repo, test_case_repo,
                 suite_repo, review_repo, impact_repo):
        self.section_repo = section_repo
        self.requirement_repo = requirement_repo

    def regenerate_for_impact(self, tenant_id, impact_id, created_by,
                               env_repo, conn_repo, metadata_repo):
        """Regenerate a test case for a metadata impact."""
        from primeqa.test_management.models import MetadataImpact, TestCase, Requirement
        impact = self.impact_repo.db.query(MetadataImpact).filter(
            MetadataImpact.id == impact_id,
        ).first()
        if not impact:
            raise ValueError("Impact not found")

        tc = self.test_case_repo.db.query(TestCase).filter(
            TestCase.id == impact.test_case_id, TestCase.tenant_id == tenant_id,
        ).first()
        if not tc:
            raise ValueError("Test case not found")

        env = env_repo.db.query(env_repo.db.query().statement.table.__class__).first() if False else None
        from primeqa.core.models import Environment
        env = env_repo.db.query(Environment).filter(
            Environment.tenant_id == tenant_id,
            Environment.current_meta_version_id == impact.new_meta_version_id,
        ).first()
        if not env:
            raise ValueError("Environment not found for impact")

        requirement_id = tc.requirement_id
        if not requirement_id:
            raise ValueError("Test case has no linked requirement")

        result = self.generate_test_case(
            tenant_id=tenant_id, requirement_id=requirement_id,
            environment_id=env.id, created_by=created_by,
            env_repo=env_repo, conn_repo=conn_repo, metadata_repo=metadata_repo,
            test_case_id=tc.id,
        )
        self.impact_repo.resolve_impact(impact_id, "regenerated", created_by)
        return result

    def generate_test_case(self, tenant_id, requirement_id, environment_id, created_by,
                           env_repo, conn_repo, metadata_repo, test_case_id=None):
        """Use AI to generate a test case from a requirement + environment metadata."""
        from primeqa.intelligence.generation import TestCaseGenerator

        requirement = self.requirement_repo.get_requirement(requirement_id, tenant_id)
        if not requirement:
            raise ValueError("Requirement not found")

        env = env_repo.get_environment(environment_id, tenant_id)
        if not env:
            raise ValueError("Environment not found")
        if not env.current_meta_version_id:
            raise ValueError("Environment has no metadata version. Refresh metadata first.")
        if not env.llm_connection_id:
            raise ValueError("Environment has no LLM connection configured")

        llm_conn = conn_repo.get_connection_decrypted(env.llm_connection_id, tenant_id)
        if not llm_conn:
            raise ValueError("LLM connection not found")

        import anthropic
        llm_client = anthropic.Anthropic(api_key=llm_conn["config"].get("api_key", ""))
        model = llm_conn["config"].get("model", "claude-sonnet-4-20250514")

        generator = TestCaseGenerator(llm_client, metadata_repo)
        result = generator.generate(requirement, env.current_meta_version_id, model=model)

        if test_case_id:
            tc = self.test_case_repo.get_test_case(test_case_id, tenant_id)
            if not tc:
                raise ValueError("Test case not found")
        else:
            title = requirement.jira_summary or f"Test for {requirement.jira_key or f'requirement {requirement.id}'}"
            tc = self.test_case_repo.create_test_case(
                tenant_id=tenant_id, title=title,
                owner_id=created_by, created_by=created_by,
                requirement_id=requirement_id, section_id=requirement.section_id,
                visibility="private", status="draft",
            )

        version = self.test_case_repo.create_version(
            test_case_id=tc.id,
            metadata_version_id=env.current_meta_version_id,
            created_by=created_by,
            steps=result["steps"],
            expected_results=result["expected_results"],
            preconditions=result["preconditions"],
            generation_method="ai" if not test_case_id else "regenerated",
            confidence_score=result["confidence_score"],
            referenced_entities=result["referenced_entities"],
        )

        auto_review_created = False
        if result["confidence_score"] < 0.7 and hasattr(self, "review_repo"):
            self.review_repo.create_review(
                tenant_id=tenant_id,
                test_case_version_id=version.id,
                assigned_to=created_by,
            )
            auto_review_created = True

        return {
            "test_case_id": tc.id,
            "version_id": version.id,
            "confidence_score": result["confidence_score"],
            "explanation": result["explanation"],
            "steps_count": len(result["steps"]),
            "auto_review_created": auto_review_created,
        }

        self.test_case_repo = test_case_repo
        self.suite_repo = suite_repo
        self.review_repo = review_repo
        self.impact_repo = impact_repo

    # --- Sections ---

    def create_section(self, tenant_id, name, created_by, **kwargs):
        return self._section_dict(
            self.section_repo.create_section(tenant_id, name, created_by, **kwargs)
        )

    def get_section_tree(self, tenant_id):
        return self.section_repo.get_section_tree(tenant_id)

    def update_section(self, section_id, tenant_id, updates):
        s = self.section_repo.update_section(section_id, tenant_id, updates)
        if not s:
            raise ValueError("Section not found")
        return self._section_dict(s)

    def delete_section(self, section_id, tenant_id):
        if not self.section_repo.delete_section(section_id, tenant_id):
            raise ValueError("Section not found")

    # --- Requirements ---

    def create_requirement(self, tenant_id, section_id, source, created_by, **kwargs):
        return self._req_dict(
            self.requirement_repo.create_requirement(
                tenant_id, section_id, source, created_by, **kwargs
            )
        )

    def import_jira_requirement(self, tenant_id, section_id, jira_base_url,
                                 jira_key, created_by, jira_auth=None):
        existing = self.requirement_repo.find_by_jira_key(tenant_id, jira_key)
        if existing:
            raise ValueError(f"Requirement for {jira_key} already exists")

        issue = self._fetch_jira_issue(jira_base_url, jira_key, jira_auth)
        fields = issue.get("fields", {})

        req = self.requirement_repo.create_requirement(
            tenant_id=tenant_id,
            section_id=section_id,
            source="jira",
            created_by=created_by,
            jira_key=jira_key,
            jira_summary=fields.get("summary", ""),
            jira_description=fields.get("description", ""),
            acceptance_criteria=self._extract_acceptance_criteria(fields),
        )
        self.requirement_repo.update_requirement(req.id, tenant_id, {
            "jira_last_synced": datetime.now(timezone.utc),
        })
        return self._req_dict(req)

    def sync_jira_requirement(self, requirement_id, tenant_id, jira_base_url, jira_auth=None):
        req = self.requirement_repo.get_requirement(requirement_id, tenant_id)
        if not req or not req.jira_key:
            raise ValueError("Requirement not found or not Jira-linked")

        issue = self._fetch_jira_issue(jira_base_url, req.jira_key, jira_auth)
        fields = issue.get("fields", {})

        new_summary = fields.get("summary", "")
        new_desc = fields.get("description", "")
        new_ac = self._extract_acceptance_criteria(fields)

        changed = (
            new_summary != (req.jira_summary or "") or
            new_desc != (req.jira_description or "") or
            new_ac != (req.acceptance_criteria or "")
        )

        updates = {"jira_last_synced": datetime.now(timezone.utc)}
        if changed:
            updates.update({
                "jira_summary": new_summary,
                "jira_description": new_desc,
                "acceptance_criteria": new_ac,
                "jira_version": req.jira_version + 1,
                "is_stale": True,
            })

        req = self.requirement_repo.update_requirement(requirement_id, tenant_id, updates)
        return self._req_dict(req), changed

    def list_requirements(self, tenant_id, section_id=None):
        reqs = self.requirement_repo.list_requirements(tenant_id, section_id)
        return [self._req_dict(r) for r in reqs]

    # --- Test Cases ---

    def create_test_case(self, tenant_id, title, owner_id, created_by, **kwargs):
        if not kwargs.get("requirement_id") and not kwargs.get("section_id"):
            raise ValueError("Either requirement_id or section_id is required")
        tc = self.test_case_repo.create_test_case(
            tenant_id, title, owner_id, created_by, **kwargs,
        )
        return self._tc_dict(tc)

    def get_test_case(self, test_case_id, tenant_id, requesting_user_id):
        tc = self.test_case_repo.get_test_case(test_case_id, tenant_id)
        if not tc:
            raise ValueError("Test case not found")
        if tc.visibility == "private" and tc.owner_id != requesting_user_id:
            raise ValueError("Test case not found")
        return self._tc_dict(tc)

    def list_test_cases(self, tenant_id, user_id, **filters):
        tcs = self.test_case_repo.list_test_cases(
            tenant_id, include_private_for=user_id, **filters,
        )
        return [self._tc_dict(tc) for tc in tcs]

    def update_test_case(self, test_case_id, tenant_id, updates, expected_version=None):
        tc, result = self.test_case_repo.update_test_case(
            test_case_id, tenant_id, updates, expected_version,
        )
        if result == "not_found":
            raise ValueError("Test case not found")
        if result == "conflict":
            raise ConflictError("Test case was modified by another user")
        return self._tc_dict(tc)

    def share_test_case(self, test_case_id, tenant_id, user_id):
        tc = self.test_case_repo.get_test_case(test_case_id, tenant_id)
        if not tc:
            raise ValueError("Test case not found")
        if tc.owner_id != user_id:
            raise ValueError("Only the owner can share a test case")
        tc, _ = self.test_case_repo.update_test_case(
            test_case_id, tenant_id, {"visibility": "shared"},
        )
        return self._tc_dict(tc)

    def activate_test_case(self, test_case_id, tenant_id):
        tc = self.test_case_repo.get_test_case(test_case_id, tenant_id)
        if not tc:
            raise ValueError("Test case not found")
        if tc.status != "approved":
            raise ValueError("Test case must be approved before activation")
        tc, _ = self.test_case_repo.update_test_case(
            test_case_id, tenant_id, {"status": "active"},
        )
        return self._tc_dict(tc)

    # --- Versions ---

    def create_version(self, test_case_id, tenant_id, metadata_version_id, created_by, **kwargs):
        tc = self.test_case_repo.get_test_case(test_case_id, tenant_id)
        if not tc:
            raise ValueError("Test case not found")
        tcv = self.test_case_repo.create_version(
            test_case_id, metadata_version_id, created_by, **kwargs,
        )
        return self._tcv_dict(tcv)

    def list_versions(self, test_case_id, tenant_id):
        tc = self.test_case_repo.get_test_case(test_case_id, tenant_id)
        if not tc:
            raise ValueError("Test case not found")
        versions = self.test_case_repo.get_versions(test_case_id)
        return [self._tcv_dict(v) for v in versions]

    # --- Suites ---

    def create_suite(self, tenant_id, name, suite_type, created_by, **kwargs):
        suite = self.suite_repo.create_suite(
            tenant_id, name, suite_type, created_by, kwargs.get("description"),
        )
        return self._suite_dict(suite)

    def list_suites(self, tenant_id):
        return [self._suite_dict(s) for s in self.suite_repo.list_suites(tenant_id)]

    def add_to_suite(self, suite_id, test_case_id, tenant_id, position=0):
        suite = self.suite_repo.get_suite(suite_id, tenant_id)
        if not suite:
            raise ValueError("Suite not found")
        self.suite_repo.add_test_case(suite_id, test_case_id, position)

    def remove_from_suite(self, suite_id, test_case_id, tenant_id):
        suite = self.suite_repo.get_suite(suite_id, tenant_id)
        if not suite:
            raise ValueError("Suite not found")
        self.suite_repo.remove_test_case(suite_id, test_case_id)

    def get_suite_test_cases(self, suite_id, tenant_id):
        suite = self.suite_repo.get_suite(suite_id, tenant_id)
        if not suite:
            raise ValueError("Suite not found")
        stcs = self.suite_repo.get_suite_test_cases(suite_id)
        return [{"test_case_id": s.test_case_id, "position": s.position} for s in stcs]

    def reorder_suite_test_case(self, suite_id, test_case_id, tenant_id, new_position):
        suite = self.suite_repo.get_suite(suite_id, tenant_id)
        if not suite:
            raise ValueError("Suite not found")
        self.suite_repo.reorder_test_case(suite_id, test_case_id, new_position)

    # --- Reviews ---

    def assign_review(self, tenant_id, test_case_version_id, assigned_to):
        return self._review_dict(
            self.review_repo.create_review(tenant_id, test_case_version_id, assigned_to)
        )

    def submit_review(self, review_id, status, feedback=None, reviewed_by=None):
        review = self.review_repo.update_review(review_id, status, feedback, reviewed_by)
        if not review:
            raise ValueError("Review not found")

        if status == "approved":
            tcv = self.test_case_repo.db.query(
                __import__("primeqa.test_management.models", fromlist=["TestCaseVersion"]).TestCaseVersion
            ).filter_by(id=review.test_case_version_id).first()
            if tcv:
                self.test_case_repo.update_test_case(
                    tcv.test_case_id, review.tenant_id,
                    {"status": "approved", "visibility": "shared"},
                )

        return self._review_dict(review)

    def list_reviews(self, tenant_id, status=None, assigned_to=None):
        reviews = self.review_repo.list_reviews(tenant_id, status, assigned_to)
        return [self._review_dict(r) for r in reviews]

    # --- Impacts ---

    def list_pending_impacts(self, tenant_id):
        impacts = self.impact_repo.list_pending_impacts(tenant_id)
        return [self._impact_dict(i) for i in impacts]

    def resolve_impact(self, impact_id, resolution, resolved_by):
        impact = self.impact_repo.resolve_impact(impact_id, resolution, resolved_by)
        if not impact:
            raise ValueError("Impact not found")
        return self._impact_dict(impact)

    # --- Jira helpers ---

    def _fetch_jira_issue(self, base_url, key, auth=None):
        url = f"{base_url.rstrip('/')}/rest/api/2/issue/{key}"
        headers = {}
        if auth:
            headers["Authorization"] = f"Basic {auth}"
        resp = http_requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _extract_acceptance_criteria(fields):
        for cf_key, cf_val in fields.items():
            if "acceptance" in cf_key.lower() and cf_val:
                return str(cf_val)
        return fields.get("description", "")

    # --- Dict helpers ---

    @staticmethod
    def _section_dict(s):
        return {
            "id": s.id, "tenant_id": s.tenant_id, "parent_id": s.parent_id,
            "name": s.name, "description": s.description, "position": s.position,
            "created_by": s.created_by,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }

    @staticmethod
    def _req_dict(r):
        return {
            "id": r.id, "tenant_id": r.tenant_id, "section_id": r.section_id,
            "source": r.source, "jira_key": r.jira_key,
            "jira_summary": r.jira_summary, "jira_description": r.jira_description,
            "acceptance_criteria": r.acceptance_criteria,
            "jira_version": r.jira_version, "is_stale": r.is_stale,
            "created_by": r.created_by,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }

    @staticmethod
    def _tc_dict(tc):
        return {
            "id": tc.id, "tenant_id": tc.tenant_id, "title": tc.title,
            "requirement_id": tc.requirement_id, "section_id": tc.section_id,
            "owner_id": tc.owner_id, "visibility": tc.visibility,
            "status": tc.status, "current_version_id": tc.current_version_id,
            "version": tc.version, "created_by": tc.created_by,
            "updated_at": tc.updated_at.isoformat() if tc.updated_at else None,
        }

    @staticmethod
    def _tcv_dict(v):
        return {
            "id": v.id, "test_case_id": v.test_case_id,
            "version_number": v.version_number,
            "metadata_version_id": v.metadata_version_id,
            "steps": v.steps, "expected_results": v.expected_results,
            "preconditions": v.preconditions,
            "generation_method": v.generation_method,
            "confidence_score": v.confidence_score,
            "referenced_entities": v.referenced_entities,
            "created_by": v.created_by,
            "created_at": v.created_at.isoformat() if v.created_at else None,
        }

    @staticmethod
    def _suite_dict(s):
        return {
            "id": s.id, "tenant_id": s.tenant_id, "name": s.name,
            "description": s.description, "suite_type": s.suite_type,
            "created_by": s.created_by,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }

    @staticmethod
    def _review_dict(r):
        return {
            "id": r.id, "tenant_id": r.tenant_id,
            "test_case_version_id": r.test_case_version_id,
            "assigned_to": r.assigned_to, "reviewed_by": r.reviewed_by,
            "status": r.status, "feedback": r.feedback,
            "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }

    @staticmethod
    def _impact_dict(i):
        return {
            "id": i.id, "test_case_id": i.test_case_id,
            "impact_type": i.impact_type, "entity_ref": i.entity_ref,
            "resolution": i.resolution,
            "created_at": i.created_at.isoformat() if i.created_at else None,
        }


class ConflictError(Exception):
    pass
