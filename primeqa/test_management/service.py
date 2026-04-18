"""Service layer for the test management domain.

Business logic: CRUD, versioning, Jira sync, stale detection, BA reviews,
soft delete + admin purge, optimistic locking, bulk ops with a size cap.

All dependencies are passed via the constructor (no late-bound attributes).
"""

import logging
from datetime import datetime, timezone

import requests as http_requests

from primeqa.core.repository import ActivityLogRepository
from primeqa.shared.api import (
    BULK_MAX_ITEMS, BulkLimitError, ConflictError, ForbiddenError,
    NotFoundError, ValidationError,
)

log = logging.getLogger(__name__)


class TestManagementService:
    """Coordinates all test-management repositories.

    ALL collaborators are required in the constructor. This is intentional —
    the previous version assigned some attributes after a `return` statement,
    so `generate_test_case`'s low-confidence branch (which references
    `self.review_repo`) would raise `AttributeError` at runtime.
    """

    def __init__(self, section_repo, requirement_repo, test_case_repo,
                 suite_repo, review_repo, impact_repo,
                 activity_repo=None):
        missing = [name for name, val in [
            ("section_repo", section_repo),
            ("requirement_repo", requirement_repo),
            ("test_case_repo", test_case_repo),
            ("suite_repo", suite_repo),
            ("review_repo", review_repo),
            ("impact_repo", impact_repo),
        ] if val is None]
        if missing:
            raise TypeError(
                f"TestManagementService missing required repositories: {missing}"
            )
        self.section_repo = section_repo
        self.requirement_repo = requirement_repo
        self.test_case_repo = test_case_repo
        self.suite_repo = suite_repo
        self.review_repo = review_repo
        self.impact_repo = impact_repo
        # activity log is optional — if absent we silently skip writes
        self.activity_repo = activity_repo

    # ---- activity log helper -------------------------------------------------

    def _log(self, tenant_id, user_id, action, entity_type, entity_id, details=None):
        if not self.activity_repo:
            return
        try:
            self.activity_repo.log_activity(
                tenant_id, user_id, action, entity_type, entity_id, details or {},
            )
        except Exception as e:
            log.warning("activity log write failed: %s", e)

    # ---- AI generation / regeneration ---------------------------------------

    def regenerate_for_impact(self, tenant_id, impact_id, created_by,
                              env_repo, conn_repo, metadata_repo):
        """Regenerate a test case for a metadata impact."""
        from primeqa.test_management.models import MetadataImpact, TestCase
        from primeqa.core.models import Environment
        impact = self.impact_repo.db.query(MetadataImpact).filter(
            MetadataImpact.id == impact_id,
        ).first()
        if not impact:
            raise NotFoundError("Impact not found")

        tc = self.test_case_repo.get_test_case(impact.test_case_id, tenant_id)
        if not tc:
            raise NotFoundError("Test case not found")

        env = env_repo.db.query(Environment).filter(
            Environment.tenant_id == tenant_id,
            Environment.current_meta_version_id == impact.new_meta_version_id,
        ).first()
        if not env:
            raise NotFoundError("Environment not found for impact")

        if not tc.requirement_id:
            raise ValidationError("Test case has no linked requirement")

        result = self.generate_test_case(
            tenant_id=tenant_id, requirement_id=tc.requirement_id,
            environment_id=env.id, created_by=created_by,
            env_repo=env_repo, conn_repo=conn_repo, metadata_repo=metadata_repo,
            test_case_id=tc.id,
        )
        self.impact_repo.resolve_impact(impact_id, "regenerated", created_by)
        return result

    # ---- Multi-TC plan generation ----------------------------------------
    # "One click \u2192 one test case" hid coverage gaps. generate_test_plan
    # asks the model for an array of independent TCs covering positive /
    # negative / boundary / edge / regression. Each becomes a TC row in
    # one generation batch so the user can see "why 5 TCs?" and audit cost.

    # Rough Anthropic pricing (USD / MTok), Apr 2026. Used for the
    # superadmin cost column on generation_batches. Keyed by substring
    # match on the model string.
    _MODEL_PRICING = [
        ("opus-4",   {"input": 15.00, "output": 75.00}),
        ("sonnet-4", {"input":  3.00, "output": 15.00}),
        ("haiku-4",  {"input":  0.80, "output":  4.00}),
        ("sonnet-3", {"input":  3.00, "output": 15.00}),
        ("haiku-3",  {"input":  0.25, "output":  1.25}),
    ]

    @classmethod
    def _estimate_cost(cls, model, input_tokens, output_tokens):
        if not model or input_tokens is None or output_tokens is None:
            return None
        ml = (model or "").lower()
        pricing = None
        for key, p in cls._MODEL_PRICING:
            if key in ml:
                pricing = p
                break
        if not pricing:
            return None
        cost = (input_tokens / 1_000_000) * pricing["input"] + \
               (output_tokens / 1_000_000) * pricing["output"]
        return round(cost, 4)

    def generate_test_plan(self, tenant_id, requirement_id, environment_id,
                           created_by, env_repo, conn_repo, metadata_repo,
                           min_tests=3, max_tests=6):
        """Generate a test plan (N independent test cases) for one requirement.

        Supersession: soft-deletes ALL prior-batch drafts for this user on
        this requirement, then creates a fresh batch with N TCs. Approved /
        active TCs from older batches are kept (immutable work).

        Returns:
          {
            "generation_batch_id": int,
            "requirement_id": int,
            "explanation": str,
            "test_cases": [ { test_case_id, version_id, version_number,
                              title, coverage_type, confidence, ... } ],
            "tokens": {"input": ..., "output": ...},
            "cost_usd": float or None,
            "model_used": str,
            "superseded_count": int,
          }
        """
        from primeqa.intelligence.generation import TestCaseGenerator
        from primeqa.test_management.models import GenerationBatch

        requirement = self.requirement_repo.get_requirement(requirement_id, tenant_id)
        if not requirement:
            raise NotFoundError("Requirement not found")

        env = env_repo.get_environment(environment_id, tenant_id)
        if not env:
            raise NotFoundError("Environment not found")
        if not env.current_meta_version_id:
            raise ValidationError("Environment has no metadata version. Refresh metadata first.")
        if not env.llm_connection_id:
            raise ValidationError("Environment has no LLM connection configured")

        llm_conn = conn_repo.get_connection_decrypted(env.llm_connection_id, tenant_id)
        if not llm_conn:
            raise NotFoundError("LLM connection not found")

        import anthropic
        llm_client = anthropic.Anthropic(api_key=llm_conn["config"].get("api_key", ""))
        model = llm_conn["config"].get("model", "claude-sonnet-4-20250514")

        generator = TestCaseGenerator(llm_client, metadata_repo)
        plan = generator.generate_plan(
            requirement, env.current_meta_version_id, model=model,
            min_tests=min_tests, max_tests=max_tests,
        )
        tcs_in_plan = plan.get("test_cases") or []
        if not tcs_in_plan:
            raise ValidationError("Generator produced no test cases")

        # Supersession: soft-delete all own-draft TCs for this requirement
        # across all prior batches. Approved / active stay.
        own_drafts = self.test_case_repo.list_test_cases(
            tenant_id=tenant_id,
            requirement_id=requirement_id,
            status="draft",
            include_private_for=created_by,
            include_deleted=False,
        )
        own_drafts = [t for t in own_drafts if t.owner_id == created_by]
        superseded = 0
        for stale in own_drafts:
            self.test_case_repo.soft_delete_test_case(stale.id, tenant_id, created_by)
            self._log(tenant_id, created_by,
                      "supersede_test_case", "test_case", stale.id,
                      {"reason": "regenerate_plan"})
            superseded += 1

        # Create the batch row first so each TC can reference its id.
        cost = self._estimate_cost(
            plan.get("model_used"),
            plan.get("prompt_tokens"), plan.get("completion_tokens"),
        )
        coverage_types = sorted({tc.get("coverage_type", "positive") for tc in tcs_in_plan})
        batch = GenerationBatch(
            tenant_id=tenant_id, requirement_id=requirement_id,
            created_by=created_by,
            llm_model=plan.get("model_used"),
            input_tokens=plan.get("prompt_tokens"),
            output_tokens=plan.get("completion_tokens"),
            cost_usd=cost,
            explanation=plan.get("explanation", ""),
            coverage_types=coverage_types,
        )
        self.test_case_repo.db.add(batch)
        self.test_case_repo.db.commit()
        self.test_case_repo.db.refresh(batch)

        # Build N TCs + N TestCaseVersions + optionally BA reviews
        created = []
        auto_reviews = 0
        for plan_tc in tcs_in_plan:
            title = (plan_tc.get("title") or "").strip() or (
                requirement.jira_summary or f"Test for {requirement.jira_key or requirement.id}"
            )
            # Coverage tag on the title so the library shows it at a glance
            # even before a dedicated badge column is wired everywhere.
            ct = plan_tc.get("coverage_type", "positive")
            if ct and not title.lower().startswith(ct.replace("_", " ")):
                # Only prefix when the AI's title doesn't already include
                # the coverage type to avoid "[positive] Positive test of X".
                prefix_map = {
                    "positive": "[+] ",
                    "negative_validation": "[-] ",
                    "boundary": "[|] ",
                    "edge_case": "[~] ",
                    "regression": "[R] ",
                }
                title = prefix_map.get(ct, "") + title

            tc = self.test_case_repo.create_test_case(
                tenant_id=tenant_id, title=title[:500],
                owner_id=created_by, created_by=created_by,
                requirement_id=requirement_id, section_id=requirement.section_id,
                visibility="private", status="draft",
            )
            tc.coverage_type = ct
            tc.generation_batch_id = batch.id
            self.test_case_repo.db.commit()

            version = self.test_case_repo.create_version(
                test_case_id=tc.id,
                metadata_version_id=env.current_meta_version_id,
                created_by=created_by,
                steps=plan_tc.get("steps", []),
                expected_results=plan_tc.get("expected_results", []),
                preconditions=plan_tc.get("preconditions", []),
                generation_method="ai",
                confidence_score=float(plan_tc.get("confidence_score", 0.7)),
                referenced_entities=plan_tc.get("referenced_entities", []),
            )

            if float(plan_tc.get("confidence_score", 0.7)) < 0.7:
                self.review_repo.create_review(
                    tenant_id=tenant_id,
                    test_case_version_id=version.id,
                    assigned_to=created_by,
                )
                auto_reviews += 1

            created.append({
                "test_case_id": tc.id,
                "version_id": version.id,
                "version_number": version.version_number,
                "title": title,
                "coverage_type": ct,
                "description": plan_tc.get("description", ""),
                "confidence": float(plan_tc.get("confidence_score", 0.7)),
                "steps_count": len(plan_tc.get("steps", [])),
            })

        self._log(tenant_id, created_by,
                  "generate_test_plan", "requirement", requirement_id,
                  {"batch_id": batch.id, "tc_count": len(created),
                   "coverage_types": coverage_types,
                   "superseded": superseded,
                   "cost_usd": float(cost) if cost is not None else None})

        return {
            "generation_batch_id": batch.id,
            "requirement_id": requirement_id,
            "explanation": plan.get("explanation", ""),
            "test_cases": created,
            "tokens": {
                "input": plan.get("prompt_tokens", 0),
                "output": plan.get("completion_tokens", 0),
            },
            "cost_usd": float(cost) if cost is not None else None,
            "model_used": plan.get("model_used"),
            "coverage_types": coverage_types,
            "superseded_count": superseded,
            "auto_reviews_created": auto_reviews,
        }

    def generate_test_case(self, tenant_id, requirement_id, environment_id, created_by,
                           env_repo, conn_repo, metadata_repo, test_case_id=None):
        """Single-TC back-compat wrapper. Delegates to generate_test_plan
        with max_tests=1 when creating a fresh TC. When `test_case_id` is
        passed (explicit regeneration of a specific TC), keeps the original
        single-version behavior."""
        from primeqa.intelligence.generation import TestCaseGenerator

        requirement = self.requirement_repo.get_requirement(requirement_id, tenant_id)
        if not requirement:
            raise NotFoundError("Requirement not found")

        env = env_repo.get_environment(environment_id, tenant_id)
        if not env:
            raise NotFoundError("Environment not found")
        if not env.current_meta_version_id:
            raise ValidationError("Environment has no metadata version. Refresh metadata first.")
        if not env.llm_connection_id:
            raise ValidationError("Environment has no LLM connection configured")

        llm_conn = conn_repo.get_connection_decrypted(env.llm_connection_id, tenant_id)
        if not llm_conn:
            raise NotFoundError("LLM connection not found")

        import anthropic
        llm_client = anthropic.Anthropic(api_key=llm_conn["config"].get("api_key", ""))
        model = llm_conn["config"].get("model", "claude-sonnet-4-20250514")

        generator = TestCaseGenerator(llm_client, metadata_repo)
        result = generator.generate(requirement, env.current_meta_version_id, model=model)

        # Whether the final write was a fresh TC, a reuse of an existing
        # draft (supersession), or explicit regeneration via test_case_id.
        # Drives generation_method on the new version + UI messaging.
        generation_mode = "new"  # "new" | "reused_draft" | "regenerated"

        if test_case_id:
            tc = self.test_case_repo.get_test_case(test_case_id, tenant_id)
            if not tc:
                raise NotFoundError("Test case not found")
            generation_mode = "regenerated"
        else:
            # Supersession: one requirement \u2192 one active draft per user.
            # If this user already has an open DRAFT TC for this requirement,
            # roll a new version onto it instead of cluttering the library
            # with duplicates. Approved / active TCs are mature work and
            # get a fresh TC alongside them rather than being mutated.
            #
            # MY drafts only \u2014 other users' drafts on the same requirement
            # are untouched so parallel QA work doesn't collide.
            own_drafts = self.test_case_repo.list_test_cases(
                tenant_id=tenant_id,
                requirement_id=requirement_id,
                status="draft",
                include_private_for=created_by,
                include_deleted=False,
            )
            own_drafts = [t for t in own_drafts if t.owner_id == created_by]

            if own_drafts:
                own_drafts.sort(key=lambda t: t.updated_at, reverse=True)
                tc = own_drafts[0]
                # Soft-delete stale drafts (keep most recent)
                for stale in own_drafts[1:]:
                    self.test_case_repo.soft_delete_test_case(
                        stale.id, tenant_id, created_by,
                    )
                    self._log(tenant_id, created_by,
                              "supersede_test_case", "test_case", stale.id,
                              {"superseded_by": tc.id, "reason": "regenerate_draft"})
                generation_mode = "reused_draft"
            else:
                title = requirement.jira_summary or (
                    f"Test for {requirement.jira_key or f'requirement {requirement.id}'}"
                )
                tc = self.test_case_repo.create_test_case(
                    tenant_id=tenant_id, title=title,
                    owner_id=created_by, created_by=created_by,
                    requirement_id=requirement_id, section_id=requirement.section_id,
                    visibility="private", status="draft",
                )
                generation_mode = "new"

        version = self.test_case_repo.create_version(
            test_case_id=tc.id,
            metadata_version_id=env.current_meta_version_id,
            created_by=created_by,
            steps=result["steps"],
            expected_results=result["expected_results"],
            preconditions=result["preconditions"],
            generation_method=("ai" if generation_mode == "new" else "regenerated"),
            confidence_score=result["confidence_score"],
            referenced_entities=result["referenced_entities"],
        )

        auto_review_created = False
        if result["confidence_score"] < 0.7:
            # review_repo is guaranteed-present thanks to constructor DI
            self.review_repo.create_review(
                tenant_id=tenant_id,
                test_case_version_id=version.id,
                assigned_to=created_by,
            )
            auto_review_created = True

        activity_action = {
            "new": "generate_test_case",
            "reused_draft": "regenerate_test_case",
            "regenerated": "regenerate_test_case",
        }[generation_mode]
        self._log(tenant_id, created_by, activity_action,
                  "test_case", tc.id,
                  {"version_id": version.id,
                   "confidence": result["confidence_score"],
                   "generation_mode": generation_mode})

        return {
            "test_case_id": tc.id,
            "version_id": version.id,
            "version_number": version.version_number,
            "generation_mode": generation_mode,
            "confidence_score": result["confidence_score"],
            "explanation": result["explanation"],
            "steps_count": len(result["steps"]),
            "auto_review_created": auto_review_created,
        }

    # ---- Sections ------------------------------------------------------------

    def create_section(self, tenant_id, name, created_by, **kwargs):
        s = self.section_repo.create_section(tenant_id, name, created_by, **kwargs)
        self._log(tenant_id, created_by, "create", "section", s.id, {"name": name})
        return self._section_dict(s)

    def get_section_tree(self, tenant_id, include_deleted=False):
        return self.section_repo.get_section_tree(tenant_id, include_deleted=include_deleted)

    def list_sections_page(self, tenant_id, **params):
        page = self.section_repo.list_page(tenant_id, **params)
        return page, self._section_dict

    def update_section(self, section_id, tenant_id, updates, expected_version=None, user_id=None):
        s, result = self.section_repo.update_section(
            section_id, tenant_id, updates, expected_version,
        )
        if result == "not_found":
            raise NotFoundError("Section not found")
        if result == "conflict":
            raise ConflictError("Section was modified by another user",
                                details={"current_version": self.section_repo.get_section(
                                    section_id, tenant_id).version})
        self._log(tenant_id, user_id, "update", "section", section_id, updates)
        return self._section_dict(s)

    def delete_section(self, section_id, tenant_id, user_id):
        s = self.section_repo.soft_delete_section(section_id, tenant_id, user_id)
        if not s:
            raise NotFoundError("Section not found")
        self._log(tenant_id, user_id, "soft_delete", "section", section_id)
        return self._section_dict(s)

    def restore_section(self, section_id, tenant_id, user_id):
        s = self.section_repo.restore_section(section_id, tenant_id)
        if not s:
            raise NotFoundError("Section not found")
        self._log(tenant_id, user_id, "restore", "section", section_id)
        return self._section_dict(s)

    def purge_section(self, section_id, tenant_id, user_id):
        if not self.section_repo.purge_section(section_id, tenant_id):
            raise NotFoundError("Section not found")
        self._log(tenant_id, user_id, "purge", "section", section_id)

    # ---- Requirements --------------------------------------------------------

    def create_requirement(self, tenant_id, section_id, source, created_by, **kwargs):
        r = self.requirement_repo.create_requirement(
            tenant_id, section_id, source, created_by, **kwargs,
        )
        self._log(tenant_id, created_by, "create", "requirement", r.id, {"source": source})
        return self._req_dict(r)

    def import_jira_requirement(self, tenant_id, section_id, jira_base_url,
                                jira_key, created_by, jira_auth=None):
        existing = self.requirement_repo.find_by_jira_key(tenant_id, jira_key)
        if existing:
            raise ValidationError(f"Requirement for {jira_key} already exists")

        issue = self._fetch_jira_issue(jira_base_url, jira_key, jira_auth)
        fields = issue.get("fields", {})

        req = self.requirement_repo.create_requirement(
            tenant_id=tenant_id, section_id=section_id, source="jira",
            created_by=created_by,
            jira_key=jira_key,
            jira_summary=fields.get("summary", ""),
            jira_description=fields.get("description", ""),
            acceptance_criteria=self._extract_acceptance_criteria(fields),
        )
        self.requirement_repo.update_requirement(req.id, tenant_id, {
            "jira_last_synced": datetime.now(timezone.utc),
        })
        self._log(tenant_id, created_by, "import_jira", "requirement", req.id,
                  {"jira_key": jira_key})
        return self._req_dict(req)

    def sync_jira_requirement(self, requirement_id, tenant_id, jira_base_url, jira_auth=None):
        req = self.requirement_repo.get_requirement(requirement_id, tenant_id)
        if not req or not req.jira_key:
            raise NotFoundError("Requirement not found or not Jira-linked")

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

        req, _result = self.requirement_repo.update_requirement(requirement_id, tenant_id, updates)
        return self._req_dict(req), changed

    def list_requirements(self, tenant_id, section_id=None):
        reqs = self.requirement_repo.list_requirements(tenant_id, section_id)
        return [self._req_dict(r) for r in reqs]

    def list_requirements_page(self, tenant_id, **params):
        page = self.requirement_repo.list_page(tenant_id, **params)
        return page, self._req_dict

    def update_requirement(self, requirement_id, tenant_id, updates,
                           expected_version=None, user_id=None):
        req, result = self.requirement_repo.update_requirement(
            requirement_id, tenant_id, updates, expected_version,
        )
        if result == "not_found":
            raise NotFoundError("Requirement not found")
        if result == "conflict":
            raise ConflictError("Requirement was modified by another user",
                                details={"current_version": self.requirement_repo.get_requirement(
                                    requirement_id, tenant_id).version})
        self._log(tenant_id, user_id, "update", "requirement", requirement_id, updates)
        return self._req_dict(req)

    def delete_requirement(self, requirement_id, tenant_id, user_id):
        r = self.requirement_repo.soft_delete_requirement(requirement_id, tenant_id, user_id)
        if not r:
            raise NotFoundError("Requirement not found")
        self._log(tenant_id, user_id, "soft_delete", "requirement", requirement_id)
        return self._req_dict(r)

    def restore_requirement(self, requirement_id, tenant_id, user_id):
        r = self.requirement_repo.restore_requirement(requirement_id, tenant_id)
        if not r:
            raise NotFoundError("Requirement not found")
        self._log(tenant_id, user_id, "restore", "requirement", requirement_id)
        return self._req_dict(r)

    def purge_requirement(self, requirement_id, tenant_id, user_id):
        if not self.requirement_repo.purge_requirement(requirement_id, tenant_id):
            raise NotFoundError("Requirement not found")
        self._log(tenant_id, user_id, "purge", "requirement", requirement_id)

    # ---- Test cases ----------------------------------------------------------

    def create_test_case(self, tenant_id, title, owner_id, created_by, **kwargs):
        if not kwargs.get("requirement_id") and not kwargs.get("section_id"):
            raise ValidationError("Either requirement_id or section_id is required")
        tc = self.test_case_repo.create_test_case(
            tenant_id, title, owner_id, created_by, **kwargs,
        )
        self._log(tenant_id, created_by, "create", "test_case", tc.id, {"title": title})
        return self._tc_dict(tc)

    def get_test_case(self, test_case_id, tenant_id, requesting_user_id):
        tc = self.test_case_repo.get_test_case(test_case_id, tenant_id)
        if not tc:
            raise NotFoundError("Test case not found")
        if tc.visibility == "private" and tc.owner_id != requesting_user_id:
            raise NotFoundError("Test case not found")
        return self._tc_dict(tc)

    def list_test_cases(self, tenant_id, user_id, **filters):
        tcs = self.test_case_repo.list_test_cases(
            tenant_id, include_private_for=user_id, **filters,
        )
        return [self._tc_dict(tc) for tc in tcs]

    def list_test_cases_page(self, tenant_id, user_id, **params):
        page = self.test_case_repo.list_page(tenant_id, user_id=user_id, **params)
        return page, self._tc_dict

    def update_test_case(self, test_case_id, tenant_id, updates,
                         expected_version=None, user_id=None):
        tc, result = self.test_case_repo.update_test_case(
            test_case_id, tenant_id, updates, expected_version,
        )
        if result == "not_found":
            raise NotFoundError("Test case not found")
        if result == "conflict":
            current = self.test_case_repo.get_test_case(test_case_id, tenant_id)
            raise ConflictError(
                "Test case was modified by another user",
                details={"current_version": current.version if current else None},
            )
        self._log(tenant_id, user_id, "update", "test_case", test_case_id, updates)
        return self._tc_dict(tc)

    def share_test_case(self, test_case_id, tenant_id, user_id):
        tc = self.test_case_repo.get_test_case(test_case_id, tenant_id)
        if not tc:
            raise NotFoundError("Test case not found")
        if tc.owner_id != user_id:
            raise ForbiddenError("Only the owner can share a test case")
        tc, _ = self.test_case_repo.update_test_case(
            test_case_id, tenant_id, {"visibility": "shared"},
        )
        self._log(tenant_id, user_id, "share", "test_case", test_case_id)
        return self._tc_dict(tc)

    def activate_test_case(self, test_case_id, tenant_id, user_id=None):
        tc = self.test_case_repo.get_test_case(test_case_id, tenant_id)
        if not tc:
            raise NotFoundError("Test case not found")
        if tc.status != "approved":
            raise ValidationError("Test case must be approved before activation")
        tc, _ = self.test_case_repo.update_test_case(
            test_case_id, tenant_id, {"status": "active"},
        )
        self._log(tenant_id, user_id, "activate", "test_case", test_case_id)
        return self._tc_dict(tc)

    def delete_test_case(self, test_case_id, tenant_id, user_id):
        tc = self.test_case_repo.soft_delete_test_case(test_case_id, tenant_id, user_id)
        if not tc:
            raise NotFoundError("Test case not found")
        self._log(tenant_id, user_id, "soft_delete", "test_case", test_case_id)
        return self._tc_dict(tc)

    def restore_test_case(self, test_case_id, tenant_id, user_id):
        tc = self.test_case_repo.restore_test_case(test_case_id, tenant_id)
        if not tc:
            raise NotFoundError("Test case not found")
        self._log(tenant_id, user_id, "restore", "test_case", test_case_id)
        return self._tc_dict(tc)

    def purge_test_case(self, test_case_id, tenant_id, user_id):
        if not self.test_case_repo.purge_test_case(test_case_id, tenant_id):
            raise NotFoundError("Test case not found")
        self._log(tenant_id, user_id, "purge", "test_case", test_case_id)

    # ---- Bulk ops (test cases) ----------------------------------------------

    def bulk_test_cases(self, tenant_id, user_id, ids, action, payload):
        """Execute a bulk action on a set of test cases.

        Guards:
          - `len(ids) > BULK_MAX_ITEMS` → BulkLimitError (400)
          - destructive actions require caller to pass `confirm == 'DELETE'`
            — that check happens in the route layer using `require_bulk_confirm`
        """
        if not ids:
            raise ValidationError("ids is required")
        if len(ids) > BULK_MAX_ITEMS:
            raise BulkLimitError(
                f"Bulk action exceeds the {BULK_MAX_ITEMS}-item limit",
                details={"limit": BULK_MAX_ITEMS, "received": len(ids)},
            )
        if not action:
            raise ValidationError("action is required")

        from primeqa.test_management.models import TestCase, TestCaseTag, SuiteTestCase
        db = self.test_case_repo.db
        tcs = db.query(TestCase).filter(
            TestCase.tenant_id == tenant_id,
            TestCase.id.in_(ids),
            TestCase.deleted_at.is_(None),
        ).all()
        count = 0
        if action == "move_section":
            sid = payload.get("section_id")
            for tc in tcs:
                tc.section_id = sid
                count += 1
        elif action == "set_status":
            status = payload.get("status")
            for tc in tcs:
                tc.status = status
                count += 1
        elif action == "add_tag":
            tag_id = payload.get("tag_id")
            for tc in tcs:
                exists = db.query(TestCaseTag).filter(
                    TestCaseTag.test_case_id == tc.id,
                    TestCaseTag.tag_id == tag_id,
                ).first()
                if not exists:
                    db.add(TestCaseTag(test_case_id=tc.id, tag_id=tag_id))
                    count += 1
        elif action == "add_to_suite":
            suite_id = payload.get("suite_id")
            for tc in tcs:
                exists = db.query(SuiteTestCase).filter(
                    SuiteTestCase.suite_id == suite_id,
                    SuiteTestCase.test_case_id == tc.id,
                ).first()
                if not exists:
                    db.add(SuiteTestCase(suite_id=suite_id, test_case_id=tc.id, position=0))
                    count += 1
        elif action == "soft_delete":
            now = datetime.now(timezone.utc)
            for tc in tcs:
                tc.deleted_at = now
                tc.deleted_by = user_id
                count += 1
        else:
            raise ValidationError(f"Unknown action: {action}")

        db.commit()
        self._log(tenant_id, user_id, f"bulk_{action}", "test_case", None,
                  {"ids": ids, "payload": payload, "affected": count})
        return {"affected": count}

    def bulk_purge_test_cases(self, tenant_id, user_id, ids):
        """Admin-only permanent deletion — caller enforces role + confirm."""
        if not ids:
            raise ValidationError("ids is required")
        if len(ids) > BULK_MAX_ITEMS:
            raise BulkLimitError(
                f"Bulk action exceeds the {BULK_MAX_ITEMS}-item limit",
                details={"limit": BULK_MAX_ITEMS, "received": len(ids)},
            )
        from primeqa.test_management.models import TestCase
        db = self.test_case_repo.db
        tcs = db.query(TestCase).filter(
            TestCase.tenant_id == tenant_id, TestCase.id.in_(ids),
        ).all()
        count = 0
        for tc in tcs:
            db.delete(tc)
            count += 1
        db.commit()
        self._log(tenant_id, user_id, "bulk_purge", "test_case", None,
                  {"ids": ids, "affected": count})
        return {"affected": count}

    # ---- Versions ------------------------------------------------------------

    def create_version(self, test_case_id, tenant_id, metadata_version_id, created_by, **kwargs):
        tc = self.test_case_repo.get_test_case(test_case_id, tenant_id)
        if not tc:
            raise NotFoundError("Test case not found")
        tcv = self.test_case_repo.create_version(
            test_case_id, metadata_version_id, created_by, **kwargs,
        )
        self._log(tenant_id, created_by, "create_version", "test_case", test_case_id,
                  {"version_id": tcv.id})
        return self._tcv_dict(tcv)

    def list_versions(self, test_case_id, tenant_id):
        tc = self.test_case_repo.get_test_case(test_case_id, tenant_id)
        if not tc:
            raise NotFoundError("Test case not found")
        versions = self.test_case_repo.get_versions(test_case_id)
        return [self._tcv_dict(v) for v in versions]

    # ---- Suites --------------------------------------------------------------

    def create_suite(self, tenant_id, name, suite_type, created_by, **kwargs):
        suite = self.suite_repo.create_suite(
            tenant_id, name, suite_type, created_by, kwargs.get("description"),
        )
        self._log(tenant_id, created_by, "create", "test_suite", suite.id, {"name": name})
        return self._suite_dict(suite)

    def list_suites(self, tenant_id):
        return [self._suite_dict(s) for s in self.suite_repo.list_suites(tenant_id)]

    def list_suites_page(self, tenant_id, **params):
        page = self.suite_repo.list_page(tenant_id, **params)
        return page, self._suite_dict

    def update_suite(self, suite_id, tenant_id, updates,
                     expected_version=None, user_id=None):
        suite, result = self.suite_repo.update_suite(
            suite_id, tenant_id, updates, expected_version,
        )
        if result == "not_found":
            raise NotFoundError("Suite not found")
        if result == "conflict":
            raise ConflictError("Suite was modified by another user",
                                details={"current_version": self.suite_repo.get_suite(
                                    suite_id, tenant_id).version})
        self._log(tenant_id, user_id, "update", "test_suite", suite_id, updates)
        return self._suite_dict(suite)

    def delete_suite(self, suite_id, tenant_id, user_id):
        suite = self.suite_repo.soft_delete_suite(suite_id, tenant_id, user_id)
        if not suite:
            raise NotFoundError("Suite not found")
        self._log(tenant_id, user_id, "soft_delete", "test_suite", suite_id)
        return self._suite_dict(suite)

    def restore_suite(self, suite_id, tenant_id, user_id):
        suite = self.suite_repo.restore_suite(suite_id, tenant_id)
        if not suite:
            raise NotFoundError("Suite not found")
        self._log(tenant_id, user_id, "restore", "test_suite", suite_id)
        return self._suite_dict(suite)

    def purge_suite(self, suite_id, tenant_id, user_id):
        if not self.suite_repo.purge_suite(suite_id, tenant_id):
            raise NotFoundError("Suite not found")
        self._log(tenant_id, user_id, "purge", "test_suite", suite_id)

    def add_to_suite(self, suite_id, test_case_id, tenant_id, position=0):
        suite = self.suite_repo.get_suite(suite_id, tenant_id)
        if not suite:
            raise NotFoundError("Suite not found")
        self.suite_repo.add_test_case(suite_id, test_case_id, position)

    def remove_from_suite(self, suite_id, test_case_id, tenant_id):
        suite = self.suite_repo.get_suite(suite_id, tenant_id)
        if not suite:
            raise NotFoundError("Suite not found")
        self.suite_repo.remove_test_case(suite_id, test_case_id)

    def get_suite_test_cases(self, suite_id, tenant_id):
        suite = self.suite_repo.get_suite(suite_id, tenant_id)
        if not suite:
            raise NotFoundError("Suite not found")
        stcs = self.suite_repo.get_suite_test_cases(suite_id)
        return [{"test_case_id": s.test_case_id, "position": s.position} for s in stcs]

    def reorder_suite_test_case(self, suite_id, test_case_id, tenant_id, new_position):
        suite = self.suite_repo.get_suite(suite_id, tenant_id)
        if not suite:
            raise NotFoundError("Suite not found")
        self.suite_repo.reorder_test_case(suite_id, test_case_id, new_position)

    # ---- Reviews -------------------------------------------------------------

    def assign_review(self, tenant_id, test_case_version_id, assigned_to):
        return self._review_dict(
            self.review_repo.create_review(tenant_id, test_case_version_id, assigned_to)
        )

    def submit_review(self, review_id, status, feedback=None, reviewed_by=None,
                      step_comments=None):
        review = self.review_repo.update_review(
            review_id, status, feedback, reviewed_by, step_comments=step_comments,
        )
        if not review:
            raise NotFoundError("Review not found")

        if status == "approved":
            from primeqa.test_management.models import TestCaseVersion
            tcv = self.test_case_repo.db.query(TestCaseVersion).filter_by(
                id=review.test_case_version_id,
            ).first()
            if tcv:
                self.test_case_repo.update_test_case(
                    tcv.test_case_id, review.tenant_id,
                    {"status": "approved", "visibility": "shared"},
                )

        return self._review_dict(review)

    def list_reviews(self, tenant_id, status=None, assigned_to=None):
        reviews = self.review_repo.list_reviews(tenant_id, status, assigned_to)
        return [self._review_dict(r) for r in reviews]

    def list_reviews_page(self, tenant_id, **params):
        page = self.review_repo.list_page(tenant_id, **params)
        return page, self._review_dict

    def delete_review(self, review_id, tenant_id, user_id):
        r = self.review_repo.soft_delete_review(review_id, tenant_id, user_id)
        if not r:
            raise NotFoundError("Review not found")
        self._log(tenant_id, user_id, "soft_delete", "ba_review", review_id)
        return self._review_dict(r)

    def restore_review(self, review_id, tenant_id, user_id):
        r = self.review_repo.restore_review(review_id, tenant_id)
        if not r:
            raise NotFoundError("Review not found")
        self._log(tenant_id, user_id, "restore", "ba_review", review_id)
        return self._review_dict(r)

    def purge_review(self, review_id, tenant_id, user_id):
        if not self.review_repo.purge_review(review_id, tenant_id):
            raise NotFoundError("Review not found")
        self._log(tenant_id, user_id, "purge", "ba_review", review_id)

    # ---- Impacts -------------------------------------------------------------

    def list_pending_impacts(self, tenant_id):
        impacts = self.impact_repo.list_pending_impacts(tenant_id)
        return [self._impact_dict(i) for i in impacts]

    def list_impacts_page(self, tenant_id, **params):
        page = self.impact_repo.list_page(tenant_id, **params)
        return page, self._impact_dict

    def resolve_impact(self, impact_id, resolution, resolved_by):
        impact = self.impact_repo.resolve_impact(impact_id, resolution, resolved_by)
        if not impact:
            raise NotFoundError("Impact not found")
        return self._impact_dict(impact)

    def delete_impact(self, impact_id, tenant_id, user_id):
        i = self.impact_repo.soft_delete_impact(impact_id, tenant_id, user_id)
        if not i:
            raise NotFoundError("Impact not found")
        self._log(tenant_id, user_id, "soft_delete", "metadata_impact", impact_id)
        return self._impact_dict(i)

    def restore_impact(self, impact_id, tenant_id, user_id):
        i = self.impact_repo.restore_impact(impact_id, tenant_id)
        if not i:
            raise NotFoundError("Impact not found")
        self._log(tenant_id, user_id, "restore", "metadata_impact", impact_id)
        return self._impact_dict(i)

    def purge_impact(self, impact_id, tenant_id, user_id):
        if not self.impact_repo.purge_impact(impact_id, tenant_id):
            raise NotFoundError("Impact not found")
        self._log(tenant_id, user_id, "purge", "metadata_impact", impact_id)

    # ---- Jira helpers --------------------------------------------------------

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

    # ---- Dict helpers --------------------------------------------------------

    @staticmethod
    def _section_dict(s):
        return {
            "id": s.id, "tenant_id": s.tenant_id, "parent_id": s.parent_id,
            "name": s.name, "description": s.description, "position": s.position,
            "created_by": s.created_by,
            "version": getattr(s, "version", 1),
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "updated_at": s.updated_at.isoformat() if getattr(s, "updated_at", None) else None,
            "deleted_at": s.deleted_at.isoformat() if getattr(s, "deleted_at", None) else None,
        }

    @staticmethod
    def _req_dict(r):
        return {
            "id": r.id, "tenant_id": r.tenant_id, "section_id": r.section_id,
            "source": r.source, "jira_key": r.jira_key,
            "jira_summary": r.jira_summary, "jira_description": r.jira_description,
            "acceptance_criteria": r.acceptance_criteria,
            "jira_version": r.jira_version, "is_stale": r.is_stale,
            "version": getattr(r, "version", 1),
            "created_by": r.created_by,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            "deleted_at": r.deleted_at.isoformat() if getattr(r, "deleted_at", None) else None,
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
            "deleted_at": tc.deleted_at.isoformat() if getattr(tc, "deleted_at", None) else None,
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
            "version": getattr(s, "version", 1),
            "created_by": s.created_by,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "updated_at": s.updated_at.isoformat() if s.updated_at else None,
            "deleted_at": s.deleted_at.isoformat() if getattr(s, "deleted_at", None) else None,
        }

    @staticmethod
    def _review_dict(r):
        return {
            "id": r.id, "tenant_id": r.tenant_id,
            "test_case_version_id": r.test_case_version_id,
            "assigned_to": r.assigned_to, "reviewed_by": r.reviewed_by,
            "status": r.status, "feedback": r.feedback,
            "step_comments": getattr(r, "step_comments", []) or [],
            "version": getattr(r, "version", 1),
            "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "deleted_at": r.deleted_at.isoformat() if getattr(r, "deleted_at", None) else None,
        }

    @staticmethod
    def _impact_dict(i):
        return {
            "id": i.id, "test_case_id": i.test_case_id,
            "impact_type": i.impact_type, "entity_ref": i.entity_ref,
            "resolution": i.resolution,
            "change_details": i.change_details,
            "new_meta_version_id": i.new_meta_version_id,
            "prev_meta_version_id": i.prev_meta_version_id,
            "created_at": i.created_at.isoformat() if i.created_at else None,
            "deleted_at": i.deleted_at.isoformat() if getattr(i, "deleted_at", None) else None,
        }


# Re-export ConflictError for external imports that expect it at module scope.
# The canonical home is primeqa.shared.api; this alias preserves the existing
# `from primeqa.test_management.service import ConflictError` import path.
ConflictError = ConflictError  # noqa: F811
