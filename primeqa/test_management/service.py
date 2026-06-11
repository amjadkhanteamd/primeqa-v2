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

    def __init__(self, section_repo, requirement_repo,
                 activity_repo=None):
        # D-221 R4: the v1 test-case/suite/review repos retired with the
        # engine; the service is sections + requirements (+ Jira import).
        missing = [name for name, val in [
            ("section_repo", section_repo),
            ("requirement_repo", requirement_repo),
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

    # ---- Multi-TC plan generation ----------------------------------------
    # "One click \u2192 one test case" hid coverage gaps. generate_test_plan
    # asks the model for an array of independent TCs covering positive /
    # negative / boundary / edge / regression. Each becomes a TC row in
    # one generation batch so the user can see "why 5 TCs?" and audit cost.

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
