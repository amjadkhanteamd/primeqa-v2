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

        # Pass the API key + tenant/user through to the Gateway; the
        # anthropic client instance is kept on the generator only for
        # backwards compatibility with tests that inject a mock.
        import anthropic
        api_key = llm_conn["config"].get("api_key", "")
        llm_client = anthropic.Anthropic(api_key=api_key)
        llm_client.api_key = api_key  # ensure attr exists for gateway lookup
        model = llm_conn["config"].get("model", "claude-sonnet-4-20250514")

        generator = TestCaseGenerator(
            llm_client, metadata_repo,
            tenant_id=tenant_id, user_id=created_by, api_key=api_key,
        )
        plan = generator.generate_plan(
            requirement, env.current_meta_version_id, model=model,
            min_tests=min_tests, max_tests=max_tests,
            requirement_id=requirement_id,
        )
        tcs_in_plan = plan.get("test_cases") or []
        if not tcs_in_plan:
            raise ValidationError("Generator produced no test cases")

        # Lazy import to keep cold-start light; validator is cheap to
        # construct since metadata is hot in memory by this point.
        from primeqa.intelligence.validator import TestCaseValidator
        validator = TestCaseValidator(metadata_repo, env.current_meta_version_id)

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
            # Feedback signal: supersession means the user rejected the
            # prior draft \u2014 a useful hint to the next generation. We
            # record once per stale draft so the signal weight scales
            # with how many were invalidated.
            try:
                from primeqa.intelligence.llm import feedback as _fb
                prior_batch = getattr(stale, "generation_batch_id", None)
                _fb.capture(
                    tenant_id=tenant_id,
                    signal_type=_fb.SIGNAL_REGENERATED_SOON,
                    severity="medium",
                    detail={
                        "prior_batch_id": prior_batch,
                        "superseded_test_case_id": stale.id,
                        "reason": "user regenerated",
                    },
                    generation_batch_id=None,  # the *new* batch hasn't been created yet
                    test_case_id=stale.id,
                    ttl_days=7,
                )
            except Exception:
                pass

        # Create the batch row first so each TC can reference its id.
        # Cost comes from the gateway's pricing.compute_cost_usd() via
        # plan["cost_usd"], which is the authoritative number (includes
        # cache-read discount and cache-write overhead). Replaces the
        # old _estimate_cost classmethod whose simplified input+output
        # formula differed from llm_usage_log.cost_usd by ~20% on calls
        # that hit or wrote the prompt cache.
        cost = plan.get("cost_usd")
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

        # Attribution: every LLM call that produced this plan was logged
        # BEFORE the batch row existed (we need the response to set the
        # batch's tokens + cost). Back-link ALL attempt rows here so the
        # superadmin "Spend for this run" panel credits the right batch.
        # Chain escalation fires multiple billable calls: the primary
        # (thrown away) + the escalation (used) both cost money; prior
        # implementation only attached the final one so the panel
        # under-reported by ~50% when escalation fired.
        usage_log_ids = plan.get("usage_log_ids") or (
            [plan.get("usage_log_id")] if plan.get("usage_log_id") else []
        )
        if usage_log_ids:
            try:
                from primeqa.intelligence.llm import usage as _usage
                for uid in usage_log_ids:
                    if uid:
                        _usage.attach_batch(uid, batch.id)
            except Exception:
                pass  # observability-only; never block generation

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

            # Validate the freshly-generated steps and persist a report so
            # the detail page shows issues inline, pre-flight can block on
            # critical, and lists can badge the TC. Validation is
            # idempotent and re-runnable via the Revalidate button.
            report = validator.validate(plan_tc.get("steps", []))
            self._store_validation_report(
                version.id, report, env.current_meta_version_id,
            )

            # Feed critical validator findings back into the feedback
            # loop so the NEXT generation for this tenant includes them
            # as "don't do this" context (Phase 4 / migration 033).
            try:
                from primeqa.intelligence.llm import feedback as _fb
                for issue in (report.get("issues") or []):
                    if issue.get("severity") != "critical":
                        continue
                    _fb.capture(
                        tenant_id=tenant_id,
                        signal_type=_fb.SIGNAL_VALIDATION_CRITICAL,
                        severity="high",
                        detail={
                            "rule": issue.get("rule"),
                            "object": issue.get("object_name"),
                            "field": issue.get("field"),
                            "message": (issue.get("message") or "")[:200],
                        },
                        generation_batch_id=batch.id,
                        test_case_id=tc.id,
                        test_case_version_id=version.id,
                        ttl_days=14,   # stale validator info decays
                    )
            except Exception:
                pass  # feedback is best-effort

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
                "validation_status": report["status"],
                "validation_critical_count": report["summary"][
                    "critical"] if "critical" in report["summary"] else 0,
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

    # ---- Validation plumbing ---------------------------------------------

    def _store_validation_report(self, version_id, report, meta_version_id):
        """Persist a validation_report JSONB onto a test_case_version.
        Uses the repo's db session to keep this in the caller's transaction."""
        from datetime import datetime, timezone
        from primeqa.test_management.models import TestCaseVersion
        db = self.test_case_repo.db
        tcv = db.query(TestCaseVersion).filter(
            TestCaseVersion.id == version_id,
        ).first()
        if not tcv:
            return
        tcv.validation_report = report
        tcv.validated_at = datetime.now(timezone.utc)
        tcv.validated_against_meta_version_id = meta_version_id
        db.commit()

    def revalidate_test_case_version(self, tc_id, tenant_id, metadata_repo,
                                     env_repo=None, environment_id=None):
        """Re-run the validator on a test case's current version. Falls
        back to the version's stored metadata_version_id if no env is
        passed, which matches the "Revalidate" button on the detail page
        (no env context, just refresh against whatever was used)."""
        from primeqa.intelligence.validator import TestCaseValidator
        from primeqa.test_management.models import TestCase, TestCaseVersion

        tc = self.test_case_repo.get_test_case(tc_id, tenant_id)
        if not tc:
            raise NotFoundError("Test case not found")
        if not tc.current_version_id:
            raise ValidationError("Test case has no current version")
        tcv = self.test_case_repo.db.query(TestCaseVersion).filter(
            TestCaseVersion.id == tc.current_version_id,
        ).first()
        if not tcv:
            raise NotFoundError("Test case version not found")

        # Resolve which meta version to validate against: explicit env wins,
        # otherwise fall back to the version's original snapshot.
        meta_version_id = None
        if env_repo and environment_id:
            env = env_repo.get_environment(environment_id, tenant_id)
            if env and env.current_meta_version_id:
                meta_version_id = env.current_meta_version_id
        if not meta_version_id:
            meta_version_id = tcv.metadata_version_id

        validator = TestCaseValidator(metadata_repo, meta_version_id)
        report = validator.validate(tcv.steps or [])
        self._store_validation_report(tcv.id, report, meta_version_id)
        return report

    def apply_validation_fix(self, tc_id, tenant_id, issue, replacement,
                             created_by, metadata_repo):
        """Apply a single suggested fix by creating a NEW test case version
        with the patched steps. Old version stays in history. Re-runs
        validation on the new version so the UI can show the updated
        issue count."""
        from primeqa.intelligence.validator import TestCaseValidator
        from primeqa.test_management.models import TestCase, TestCaseVersion

        tc = self.test_case_repo.get_test_case(tc_id, tenant_id)
        if not tc:
            raise NotFoundError("Test case not found")
        tcv = self.test_case_repo.db.query(TestCaseVersion).filter(
            TestCaseVersion.id == tc.current_version_id,
        ).first()
        if not tcv:
            raise NotFoundError("Test case version not found")

        validator = TestCaseValidator(metadata_repo, tcv.metadata_version_id)
        new_steps = validator.apply_fix(tcv.steps or [], issue, replacement)

        # Create a new version (same convention as regenerate)
        new_version = self.test_case_repo.create_version(
            test_case_id=tc.id,
            metadata_version_id=tcv.metadata_version_id,
            created_by=created_by,
            steps=new_steps,
            expected_results=tcv.expected_results or [],
            preconditions=tcv.preconditions or [],
            generation_method="manual",
            confidence_score=tcv.confidence_score,
            referenced_entities=tcv.referenced_entities or [],
        )
        report = validator.validate(new_steps)
        self._store_validation_report(
            new_version.id, report, tcv.metadata_version_id,
        )
        self._log(tenant_id, created_by, "apply_validation_fix",
                  "test_case", tc.id,
                  {"from_version_id": tcv.id, "to_version_id": new_version.id,
                   "rule": issue.get("rule"),
                   "replacement": replacement})
        return {
            "test_case_id": tc.id,
            "version_id": new_version.id,
            "version_number": new_version.version_number,
            "validation_report": report,
        }

    # ---------------------------------------------------------------------

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
        # Audit fix C-3 (2026-04-19): validate inputs BEFORE hitting the
        # DB, so that 1MB strings / emoji spam / null bytes return a
        # clean 400 instead of a 500 from Postgres's VARCHAR(255) overflow.
        if not isinstance(name, str):
            raise ValidationError("name must be a string")
        name = name.strip()
        if not name:
            raise ValidationError("name is required")
        # Guard on byte-length too — Postgres VARCHAR counts characters,
        # but with 4-byte emoji a 60-char name is 240 bytes. Keep it
        # conservative at 200 chars / 500 bytes.
        if len(name) > 200 or len(name.encode("utf-8")) > 500:
            raise ValidationError("name too long (max 200 characters)")
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

    def get_requirement(self, requirement_id, tenant_id):
        """Return the serialised requirement or raise NotFoundError."""
        req = self.requirement_repo.get_requirement(requirement_id, tenant_id)
        if not req:
            raise NotFoundError("Requirement not found")
        return self._req_dict(req)

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
        # Capture a `user_edited` signal when the user edits an AI-
        # generated TC. The signal flows into feedback_rules and the
        # next generate_test_plan prompt sees it as an implicit
        # correction — closes the Phase 7 feedback loop.
        #
        # Rule: look up the TC's current active version; if its
        # generation_method is 'ai' or 'regenerated', this edit counts
        # as AI-output correction. Deduped per (tc_id, 10-min bucket) so
        # keystroke-level saves don't flood the signal table.
        prior_ai_version_id = None
        try:
            from primeqa.test_management.models import TestCaseVersion
            tc_before = self.test_case_repo.get_test_case(test_case_id, tenant_id)
            if tc_before and tc_before.current_version_id:
                cv = self.test_case_repo.db.query(TestCaseVersion).filter_by(
                    id=tc_before.current_version_id,
                ).first()
                if cv and cv.generation_method in ("ai", "regenerated"):
                    prior_ai_version_id = cv.id
        except Exception:
            # Best-effort — never break the user update.
            prior_ai_version_id = None

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

        if prior_ai_version_id is not None:
            from primeqa.intelligence.llm import feedback
            feedback.capture(
                tenant_id=tenant_id,
                signal_type=feedback.SIGNAL_USER_EDITED,
                detail={
                    "tc_id": test_case_id,
                    "prior_version_id": prior_ai_version_id,
                    "user_id": user_id,
                    "source": feedback.SOURCE_IMPLICIT,
                    "coverage_type": getattr(tc, "coverage_type", None),
                },
                test_case_id=test_case_id,
                test_case_version_id=prior_ai_version_id,
                dedup_window_minutes=10,
            )
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

    def submit_user_feedback(self, test_case_id, tenant_id, user_id,
                             verdict, reason=None, reason_text=None):
        """Phase 7: explicit user feedback (thumbs up/down) on a TC.

        Returns the status dict from `feedback.capture_user_feedback`
        (contains throttled flag for rate-limited spam).

        Verifies the TC exists + is visible to this user, then
        delegates. Visibility matters — if a private TC belongs to
        someone else, we return NotFound (same semantics as get_test_case).
        """
        tc = self.test_case_repo.get_test_case(test_case_id, tenant_id)
        if not tc:
            raise NotFoundError("Test case not found")
        if tc.visibility == "private" and tc.owner_id != user_id:
            raise NotFoundError("Test case not found")

        from primeqa.intelligence.llm import feedback as _feedback

        try:
            result = _feedback.capture_user_feedback(
                tenant_id=tenant_id,
                user_id=user_id,
                test_case_id=test_case_id,
                verdict=verdict,
                reason=reason,
                reason_text=reason_text,
                coverage_type=getattr(tc, "coverage_type", None),
                test_case_version_id=getattr(tc, "current_version_id", None),
                generation_batch_id=getattr(tc, "generation_batch_id", None),
            )
        except ValueError as e:
            raise ValidationError(str(e))

        # Audit trail: every explicit feedback lands in activity_log too
        # so a tenant admin can review what reviewers said. Silent no-ops
        # (throttled) don't log — throttling is designed to be invisible.
        if result.get("ok") and not result.get("throttled"):
            self._log(
                tenant_id, user_id, "feedback", "test_case", test_case_id,
                {
                    "verdict": verdict,
                    "reason": reason,
                    "signal_type": result.get("signal_type"),
                },
            )
        return result

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

    def get_suite(self, suite_id, tenant_id):
        """Return the serialised suite or raise NotFoundError."""
        suite = self.suite_repo.get_suite(suite_id, tenant_id)
        if not suite:
            raise NotFoundError("Suite not found")
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

    def add_to_suite_bulk(self, suite_id, test_case_ids, tenant_id, created_by):
        """Add many TCs to a suite in one go. Tenant-scoped on both the
        suite (can the user edit this suite) and the TCs (prevent leaking
        other tenants' TC ids through the API). Returns {added, already_in,
        skipped_not_found_or_wrong_tenant}.
        """
        from primeqa.test_management.models import TestCase
        suite = self.suite_repo.get_suite(suite_id, tenant_id)
        if not suite:
            raise NotFoundError("Suite not found")

        ids = [int(x) for x in (test_case_ids or []) if str(x).strip()]
        if len(ids) > 200:
            raise ValidationError("Bulk add is capped at 200 test cases per call")

        if not ids:
            return {"added": [], "already_in": [], "skipped": []}

        # Tenant-check TCs: only those belonging to the caller's tenant
        # and not soft-deleted are eligible. Matching owner/visibility
        # rules are deferred to the picker UI \u2014 if the user could see
        # the TC, they can add it.
        valid_tcs = self.test_case_repo.db.query(TestCase.id).filter(
            TestCase.id.in_(ids),
            TestCase.tenant_id == tenant_id,
            TestCase.deleted_at.is_(None),
        ).all()
        valid_ids = {row[0] for row in valid_tcs}
        skipped = [i for i in ids if i not in valid_ids]
        eligible = [i for i in ids if i in valid_ids]  # preserve order

        result = self.suite_repo.add_test_cases_bulk(suite_id, eligible)
        result["skipped"] = skipped

        self._log(tenant_id, created_by, "add_to_suite_bulk",
                  "test_suite", suite_id,
                  {"added": result["added"], "already_in": result["already_in"],
                   "skipped": skipped})
        return result

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
                      step_comments=None, reason=None):
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

        # Phase 7: wire the dead `ba_rejected` signal. BA rejection is
        # the highest-quality human signal we have; feed it into the
        # next generation prompt via feedback_rules.
        if status == "rejected":
            try:
                from primeqa.intelligence.llm import feedback as _feedback
                from primeqa.test_management.models import TestCaseVersion
                tcv = self.test_case_repo.db.query(TestCaseVersion).filter_by(
                    id=review.test_case_version_id,
                ).first()
                _feedback.capture(
                    tenant_id=review.tenant_id,
                    signal_type=_feedback.SIGNAL_BA_REJECTED,
                    detail={
                        "tc_id": tcv.test_case_id if tcv else None,
                        "version_id": review.test_case_version_id,
                        "reason": reason,
                        "reason_text": feedback or "",
                        "reviewed_by": reviewed_by,
                        "source": _feedback.SOURCE_EXPLICIT,
                    },
                    test_case_id=(tcv.test_case_id if tcv else None),
                    test_case_version_id=review.test_case_version_id,
                )
            except Exception:
                # Best-effort — never break the review flow.
                pass

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
