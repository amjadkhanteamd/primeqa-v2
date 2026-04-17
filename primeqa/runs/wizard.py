"""Run Wizard source resolver.

Takes a mixed selection (Jira projects/sprints/epics/JQL/issues, suites,
sections, hand-picked test_case_ids) and resolves it to:
  - A flat, deduplicated list of test_case_ids ready to execute
  - A structured `source_refs` payload that stays with the pipeline_run
    so history/rerun preserves what was originally requested.

This module is pure resolution logic; it does not touch Salesforce or
queue anything. The caller wires it into the existing `PipelineService`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests as http_requests

from primeqa.shared.api import NotFoundError, ValidationError

log = logging.getLogger(__name__)


# Run-size guardrails (Q6): soft warn at 100, hard block at 500.
SOFT_CAP = 100
HARD_CAP = 500


# ---- Input / output shapes ---------------------------------------------------

@dataclass
class WizardSelection:
    """What the user built in the wizard, before resolution."""
    # PrimeQA-native sources
    suite_ids: List[int] = field(default_factory=list)
    section_ids: List[int] = field(default_factory=list)
    test_case_ids: List[int] = field(default_factory=list)
    requirement_ids: List[int] = field(default_factory=list)
    # Jira sources (pass-through; resolved against a connection)
    jira: List[Dict[str, Any]] = field(default_factory=list)
    # Jira item schema examples:
    #   {"type": "sprint",  "connection_id": 7, "board_id": 42, "sprint_id": 128}
    #   {"type": "issues",  "connection_id": 7, "issue_keys": ["PROJ-12", "PROJ-13"]}
    #   {"type": "jql",     "connection_id": 7, "jql": "project=PROJ AND status='Ready for QA'"}
    #   {"type": "epic",    "connection_id": 7, "epic_key": "PROJ-100", "status": "Ready for QA"}


@dataclass
class ResolvedRun:
    test_case_ids: List[int]
    source_refs: Dict[str, Any]
    resolution_warnings: List[str] = field(default_factory=list)
    missing_jira_keys: List[str] = field(default_factory=list)  # Jira issues with no PrimeQA requirement

    @property
    def test_count(self) -> int:
        return len(self.test_case_ids)

    @property
    def over_soft_cap(self) -> bool:
        return self.test_count > SOFT_CAP

    @property
    def over_hard_cap(self) -> bool:
        return self.test_count > HARD_CAP


# ---- Resolver ----------------------------------------------------------------

class RunWizardResolver:
    """Resolve a WizardSelection into ResolvedRun.

    Takes repositories by dependency injection (same pattern as
    TestManagementService) so it can be unit-tested without a DB.
    """

    def __init__(self, db, *,
                 suite_repo, section_repo, tc_repo, req_repo,
                 connection_repo):
        self.db = db
        self.suite_repo = suite_repo
        self.section_repo = section_repo
        self.tc_repo = tc_repo
        self.req_repo = req_repo
        self.connection_repo = connection_repo

    def resolve(self, tenant_id: int, selection: WizardSelection) -> ResolvedRun:
        warnings: List[str] = []
        missing_keys: List[str] = []
        test_case_ids: List[int] = []
        source_refs: Dict[str, Any] = {}

        # ---- Hand-picked test cases -------------------------------------------
        if selection.test_case_ids:
            valid_ids = self._validate_test_case_ids(tenant_id, selection.test_case_ids)
            if len(valid_ids) < len(selection.test_case_ids):
                warnings.append(
                    f"{len(selection.test_case_ids) - len(valid_ids)} hand-picked "
                    f"test case(s) not found or not accessible; skipped."
                )
            test_case_ids.extend(valid_ids)
            source_refs["test_case_ids"] = valid_ids

        # ---- Suites -----------------------------------------------------------
        if selection.suite_ids:
            suite_details = []
            for sid in selection.suite_ids:
                suite = self.suite_repo.get_suite(sid, tenant_id)
                if not suite:
                    warnings.append(f"Suite #{sid} not found; skipped.")
                    continue
                stcs = self.suite_repo.get_suite_test_cases(sid)
                tcs_in_suite = [s.test_case_id for s in stcs]
                test_case_ids.extend(tcs_in_suite)
                suite_details.append({"id": sid, "name": suite.name, "test_count": len(tcs_in_suite)})
            source_refs["suites"] = suite_details

        # ---- Sections (by section tree; exclude deleted tests) ---------------
        if selection.section_ids:
            section_details = []
            for sec_id in selection.section_ids:
                section = self.section_repo.get_section(sec_id, tenant_id)
                if not section:
                    warnings.append(f"Section #{sec_id} not found; skipped.")
                    continue
                # Fetch all active test cases under this section.
                # (Recursive child-section expansion deferred — section tree is typically shallow)
                tcs = self.tc_repo.list_test_cases(
                    tenant_id, section_id=sec_id, include_private_for=None,
                )
                tcs_in_sec = [tc.id for tc in tcs]
                test_case_ids.extend(tcs_in_sec)
                section_details.append({"id": sec_id, "name": section.name, "test_count": len(tcs_in_sec)})
            source_refs["sections"] = section_details

        # ---- Requirements (each req \u2192 its test cases) ------------------------
        if selection.requirement_ids:
            req_details = []
            for rid in selection.requirement_ids:
                req = self.req_repo.get_requirement(rid, tenant_id)
                if not req:
                    warnings.append(f"Requirement #{rid} not found; skipped.")
                    continue
                tcs = self.tc_repo.list_test_cases(tenant_id, requirement_id=rid, include_private_for=None)
                tc_ids = [tc.id for tc in tcs]
                test_case_ids.extend(tc_ids)
                req_details.append({
                    "id": rid, "jira_key": req.jira_key,
                    "summary": req.jira_summary, "test_count": len(tc_ids),
                })
            source_refs["requirements"] = req_details

        # ---- Jira (pass-through, no persistence) -----------------------------
        if selection.jira:
            jira_details = []
            for entry in selection.jira:
                resolved, entry_warnings, missing = self._resolve_jira_entry(tenant_id, entry)
                test_case_ids.extend(resolved["test_case_ids"])
                warnings.extend(entry_warnings)
                missing_keys.extend(missing)
                jira_details.append(resolved)
            source_refs["jira"] = jira_details

        # Deduplicate while preserving first-seen order
        seen = set()
        deduped = []
        for tc_id in test_case_ids:
            if tc_id not in seen:
                seen.add(tc_id)
                deduped.append(tc_id)

        # Cap check
        if len(deduped) > HARD_CAP:
            raise ValidationError(
                f"Selection expands to {len(deduped)} tests, above the hard cap of {HARD_CAP}. "
                f"Please refine the selection (or ask a Super Admin to override).",
                code="RUN_SIZE_HARD_CAP",
                details={"count": len(deduped), "hard_cap": HARD_CAP},
            )

        return ResolvedRun(
            test_case_ids=deduped,
            source_refs=source_refs,
            resolution_warnings=warnings,
            missing_jira_keys=missing_keys,
        )

    # ---- Helpers -------------------------------------------------------------

    def _validate_test_case_ids(self, tenant_id, ids):
        """Return only ids that exist, belong to tenant, and aren't soft-deleted."""
        from primeqa.test_management.models import TestCase
        rows = self.db.query(TestCase).filter(
            TestCase.tenant_id == tenant_id,
            TestCase.id.in_(ids),
            TestCase.deleted_at.is_(None),
        ).all()
        return [r.id for r in rows]

    def _resolve_jira_entry(self, tenant_id, entry):
        """Resolve a single Jira selection entry \u2192 test_case_ids via jira_key lookup.

        Returns (resolved_dict, warnings_list, missing_keys_list).
        """
        from primeqa.test_management.models import Requirement, TestCase

        warnings: List[str] = []
        missing: List[str] = []
        etype = entry.get("type")
        connection_id = entry.get("connection_id")

        if not connection_id:
            warnings.append(f"Jira {etype} entry missing connection_id; skipped.")
            return {"type": etype, "test_case_ids": [], "error": "no connection_id"}, warnings, missing

        jira_client = self._jira_client(tenant_id, connection_id)
        if not jira_client:
            warnings.append(f"Jira connection #{connection_id} unreachable; skipped.")
            return {"type": etype, "test_case_ids": [], "error": "connection unreachable"}, warnings, missing

        # Collect issue keys from the entry
        issue_keys: List[str] = []
        resolved_meta: Dict[str, Any] = {"type": etype, "connection_id": connection_id}

        try:
            if etype == "issues":
                issue_keys = list(entry.get("issue_keys", []))
                resolved_meta["issue_keys"] = issue_keys

            elif etype == "sprint":
                sprint_id = entry.get("sprint_id")
                if not sprint_id:
                    warnings.append("Jira sprint entry missing sprint_id; skipped.")
                    return {"type": etype, **resolved_meta, "test_case_ids": []}, warnings, missing
                issues = jira_client.sprint_issues(sprint_id)
                issue_keys = [i.get("key") for i in issues if i.get("key")]
                resolved_meta["sprint_id"] = sprint_id
                resolved_meta["issue_keys"] = issue_keys

            elif etype == "jql":
                jql = entry.get("jql", "")
                if not jql:
                    warnings.append("Jira JQL entry empty; skipped.")
                    return {"type": etype, **resolved_meta, "test_case_ids": []}, warnings, missing
                issues = jira_client.search_jql(jql)
                issue_keys = [i.get("key") for i in issues if i.get("key")]
                resolved_meta["jql"] = jql
                resolved_meta["issue_keys"] = issue_keys

            elif etype == "epic":
                epic_key = entry.get("epic_key")
                status = entry.get("status")
                if not epic_key:
                    warnings.append("Jira epic entry missing epic_key; skipped.")
                    return {"type": etype, **resolved_meta, "test_case_ids": []}, warnings, missing
                jql = f'"Epic Link" = {epic_key}'
                if status:
                    jql += f' AND status = "{status}"'
                issues = jira_client.search_jql(jql)
                issue_keys = [i.get("key") for i in issues if i.get("key")]
                resolved_meta["epic_key"] = epic_key
                resolved_meta["status"] = status
                resolved_meta["issue_keys"] = issue_keys

            else:
                warnings.append(f"Unknown Jira source type '{etype}'; skipped.")
                return {"type": etype, **resolved_meta, "test_case_ids": []}, warnings, missing

        except Exception as e:
            log.warning("Jira resolution failed for %s: %s", etype, e)
            warnings.append(f"Jira {etype} resolution failed: {e}")
            return {"type": etype, **resolved_meta, "test_case_ids": [], "error": str(e)}, warnings, missing

        # Map Jira keys -> requirements -> test cases
        if not issue_keys:
            return {**resolved_meta, "test_case_ids": []}, warnings, missing

        reqs = self.db.query(Requirement).filter(
            Requirement.tenant_id == tenant_id,
            Requirement.jira_key.in_(issue_keys),
            Requirement.deleted_at.is_(None),
        ).all()
        req_map = {r.jira_key: r.id for r in reqs}

        missing = [k for k in issue_keys if k not in req_map]
        if missing:
            warnings.append(
                f"{len(missing)} Jira issue(s) have no matching PrimeQA requirement "
                f"({', '.join(missing[:3])}{'...' if len(missing) > 3 else ''}); skipped. "
                f"Import them first."
            )

        found_req_ids = list(req_map.values())
        if not found_req_ids:
            return {**resolved_meta, "test_case_ids": [], "missing_jira_keys": missing}, warnings, missing

        tcs = self.db.query(TestCase).filter(
            TestCase.tenant_id == tenant_id,
            TestCase.requirement_id.in_(found_req_ids),
            TestCase.deleted_at.is_(None),
        ).all()
        tc_ids = [tc.id for tc in tcs]

        resolved_meta["test_case_ids"] = tc_ids
        resolved_meta["missing_jira_keys"] = missing
        return resolved_meta, warnings, missing

    def _jira_client(self, tenant_id, connection_id):
        """Build a minimal Jira REST client from the stored connection."""
        conn = self.connection_repo.get_connection_decrypted(connection_id, tenant_id)
        if not conn or conn.get("connection_type") != "jira":
            return None
        cfg = conn["config"]
        base = cfg.get("base_url", "").rstrip("/")
        auth = None
        if cfg.get("auth_type") == "basic" and cfg.get("username") and cfg.get("api_token"):
            import base64
            auth = base64.b64encode(f"{cfg['username']}:{cfg['api_token']}".encode()).decode()
        return JiraClient(base, auth)


class JiraClient:
    """Minimal Jira Cloud REST wrapper. Session-less, on-demand (Q: fetch on demand)."""

    def __init__(self, base_url: str, basic_auth_b64: Optional[str]):
        self.base_url = base_url
        self.headers = {"Accept": "application/json"}
        if basic_auth_b64:
            self.headers["Authorization"] = f"Basic {basic_auth_b64}"

    # ---- Discovery: project + sprint pickers in the wizard -------------------

    def list_projects(self, max_results: int = 100) -> List[Dict[str, Any]]:
        """GET /rest/api/3/project/search. Returns a trimmed projection."""
        url = f"{self.base_url}/rest/api/3/project/search"
        r = http_requests.get(url, headers=self.headers, params={"maxResults": max_results}, timeout=15)
        r.raise_for_status()
        body = r.json()
        return [
            {"id": p.get("id"), "key": p.get("key"), "name": p.get("name"),
             "style": p.get("style")}  # 'classic' (scrum w/ boards) or 'next-gen'
            for p in body.get("values", [])
        ]

    def list_boards_for_project(self, project_key: str) -> List[Dict[str, Any]]:
        """GET /rest/agile/1.0/board?projectKeyOrId=KEY&type=scrum."""
        url = f"{self.base_url}/rest/agile/1.0/board"
        r = http_requests.get(
            url, headers=self.headers,
            params={"projectKeyOrId": project_key, "type": "scrum"},
            timeout=15,
        )
        r.raise_for_status()
        body = r.json()
        return [{"id": b.get("id"), "name": b.get("name"), "type": b.get("type")}
                for b in body.get("values", [])]

    def list_sprints(self, board_id: int, states: str = "active,closed,future") -> List[Dict[str, Any]]:
        """GET /rest/agile/1.0/board/{boardId}/sprint?state=..."""
        url = f"{self.base_url}/rest/agile/1.0/board/{board_id}/sprint"
        r = http_requests.get(url, headers=self.headers, params={"state": states}, timeout=15)
        r.raise_for_status()
        body = r.json()
        return [{"id": s.get("id"), "name": s.get("name"), "state": s.get("state"),
                 "startDate": s.get("startDate"), "endDate": s.get("endDate")}
                for s in body.get("values", [])]

    # ---- Resolution: sprint / JQL / epic \u2192 issues --------------------------

    def sprint_issues(self, sprint_id: int) -> List[Dict[str, Any]]:
        """Paginated GET /rest/agile/1.0/sprint/{id}/issue."""
        url = f"{self.base_url}/rest/agile/1.0/sprint/{sprint_id}/issue"
        return self._paginated_issues(url, {})

    def search_jql(self, jql: str) -> List[Dict[str, Any]]:
        """Paginated GET /rest/api/3/search."""
        url = f"{self.base_url}/rest/api/3/search"
        return self._paginated_issues(url, {"jql": jql})

    def _paginated_issues(self, url: str, extra_params: Dict[str, Any]) -> List[Dict[str, Any]]:
        start_at = 0
        page_size = 100
        results: List[Dict[str, Any]] = []
        while True:
            params = {"startAt": start_at, "maxResults": page_size,
                      "fields": "summary,status,issuetype", **extra_params}
            r = http_requests.get(url, headers=self.headers, params=params, timeout=20)
            r.raise_for_status()
            body = r.json()
            page = body.get("issues", [])
            results.extend({"key": i.get("key"),
                            "summary": (i.get("fields") or {}).get("summary"),
                            "status": ((i.get("fields") or {}).get("status") or {}).get("name")}
                           for i in page)
            total = body.get("total", len(results))
            start_at += len(page)
            if len(page) < page_size or start_at >= total:
                break
            if len(results) > 2000:  # safety valve
                break
        return results
