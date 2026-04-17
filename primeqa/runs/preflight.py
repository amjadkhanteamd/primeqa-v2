"""Pre-flight checks for run submission.

Runs between the Wizard (selection resolved) and PipelineService (queued):

    WizardSelection -> ResolvedRun -> PreflightReport -> pipeline_run

PreflightReport has:
  - `blockers`   : must be fixed before the run can start
  - `warnings`   : surfaced in the preview but don't block
  - `summary`    : high-level data for the preview screen
                   (env name, LLM model, test_count, eta_ms, cost estimate gated to super admin)
  - `per_test_decisions` : which tests will run vs. be skipped with metadata_stale etc.

Super Admin can override blockers with an explicit "OVERRIDE" typed token.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

from primeqa.runs.wizard import HARD_CAP, SOFT_CAP, ResolvedRun
from primeqa.shared.api import ForbiddenError, ValidationError

log = logging.getLogger(__name__)


METADATA_STALE_HOURS = 24 * 7        # 7 days = stale (warn); override still possible
METADATA_BLOCK_HOURS = 24 * 30       # 30 days = block without override


@dataclass
class PerTestDecision:
    test_case_id: int
    will_run: bool
    reason: Optional[str] = None  # 'ok' | 'skipped_metadata_stale' | 'skipped_private' | ...


@dataclass
class PreflightReport:
    blockers: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[Dict[str, Any]] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)
    per_test_decisions: List[PerTestDecision] = field(default_factory=list)

    @property
    def has_blockers(self) -> bool:
        return bool(self.blockers)

    @property
    def will_run_count(self) -> int:
        return sum(1 for d in self.per_test_decisions if d.will_run)

    @property
    def skip_count(self) -> int:
        return sum(1 for d in self.per_test_decisions if not d.will_run)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "blockers": self.blockers,
            "warnings": self.warnings,
            "summary": self.summary,
            "will_run_count": self.will_run_count,
            "skip_count": self.skip_count,
            "per_test_decisions": [
                {"test_case_id": d.test_case_id, "will_run": d.will_run, "reason": d.reason}
                for d in self.per_test_decisions
            ],
        }


class Preflight:
    """Pre-flight check runner. Builds a PreflightReport; does not queue anything."""

    def __init__(self, db, *, env_repo, conn_repo, tc_repo, meta_repo):
        self.db = db
        self.env_repo = env_repo
        self.conn_repo = conn_repo
        self.tc_repo = tc_repo
        self.meta_repo = meta_repo

    def check(self, tenant_id: int, user: Dict[str, Any],
              environment_id: int, resolved: ResolvedRun) -> PreflightReport:
        report = PreflightReport()

        env = self.env_repo.get_environment(environment_id, tenant_id)
        if not env:
            report.blockers.append(self._issue(
                "ENV_NOT_FOUND", "Environment not found.",
            ))
            report.summary = self._empty_summary(resolved)
            return report

        # ---- 0. Run-size guardrails (Q6) --------------------------------------
        if resolved.test_count == 0:
            report.blockers.append(self._issue(
                "NO_TESTS_SELECTED",
                "Your selection resolves to 0 test cases. Pick at least one suite, "
                "requirement, or hand-picked test.",
            ))
            report.summary = self._basic_summary(env, None, resolved)
            return report

        if resolved.test_count > HARD_CAP:
            report.blockers.append(self._issue(
                "RUN_SIZE_HARD_CAP",
                f"Selection expands to {resolved.test_count} tests; hard cap is {HARD_CAP}. "
                f"Super Admin can override.",
                details={"count": resolved.test_count, "hard_cap": HARD_CAP, "override_role": "superadmin"},
            ))
        elif resolved.test_count > SOFT_CAP:
            report.warnings.append(self._issue(
                "RUN_SIZE_SOFT_CAP",
                f"{resolved.test_count} tests is above the soft cap ({SOFT_CAP}). "
                f"Runs this large may take a while; consider refining the selection.",
            ))

        # ---- 1. Env / connection sanity ---------------------------------------
        if not env.is_active:
            report.blockers.append(self._issue(
                "ENV_INACTIVE", f"Environment '{env.name}' is inactive.",
            ))

        if not env.connection_id:
            report.blockers.append(self._issue(
                "NO_SF_CONNECTION",
                f"Environment '{env.name}' has no Salesforce connection attached.",
            ))
        else:
            # Credentials (decrypted)
            creds = self.conn_repo.get_connection_decrypted(env.connection_id, tenant_id)
            if not creds:
                report.blockers.append(self._issue(
                    "NO_CREDENTIALS",
                    "Salesforce credentials are missing for this environment.",
                ))
            elif creds.get("status") == "failed":
                report.blockers.append(self._issue(
                    "CREDENTIALS_FAILED",
                    "Salesforce credentials are in 'failed' state; re-authenticate the connection.",
                ))
            elif creds.get("token_expires_at"):
                # soft check; refresh handled lazily at execution time
                try:
                    exp = datetime.fromisoformat(creds["token_expires_at"].replace("Z", "+00:00"))
                    if exp < datetime.now(timezone.utc):
                        report.warnings.append(self._issue(
                            "CREDENTIALS_EXPIRED",
                            "Salesforce access token expired; will attempt refresh at run time.",
                        ))
                except Exception:
                    pass

        # ---- 2. LLM connection (only required if run_type needs LLM) ---------
        # Kept as a warning for R1; R5 agent loop needs LLM but regular execute
        # doesn't. The wizard will toggle run_type later.
        if not env.llm_connection_id:
            report.warnings.append(self._issue(
                "NO_LLM_CONNECTION",
                f"Environment '{env.name}' has no LLM connection. "
                f"AI-generated steps and the fix-and-rerun agent will be unavailable.",
            ))

        # ---- 3. Metadata freshness -------------------------------------------
        meta_version = None
        meta_age_hours = None
        if env.current_meta_version_id:
            meta_version = self.meta_repo.get_version(env.current_meta_version_id)
            if meta_version and meta_version.completed_at:
                meta_age_hours = (
                    datetime.now(timezone.utc) - meta_version.completed_at
                ).total_seconds() / 3600.0
        else:
            report.blockers.append(self._issue(
                "NO_METADATA",
                f"Environment '{env.name}' has never had a metadata refresh. "
                f"Run metadata sync first.",
            ))

        if meta_age_hours is not None:
            if meta_age_hours > METADATA_BLOCK_HOURS:
                report.blockers.append(self._issue(
                    "METADATA_VERY_STALE",
                    f"Metadata is {meta_age_hours:.0f}h old (> {METADATA_BLOCK_HOURS}h). "
                    f"Refresh it before running.",
                ))
            elif meta_age_hours > METADATA_STALE_HOURS:
                report.warnings.append(self._issue(
                    "METADATA_STALE",
                    f"Metadata is {meta_age_hours:.0f}h old. Consider refreshing for accurate results.",
                ))

        # ---- 4. Prod-safety --------------------------------------------------
        if env.env_type == "production":
            report.warnings.append(self._issue(
                "PRODUCTION_TARGET",
                "You are about to run tests against a PRODUCTION environment. "
                "Agent auto-fix is disabled; any destructive step will still execute.",
            ))

        # ---- 5. Per-test metadata check --------------------------------------
        # For each test, look at referenced_entities on its current version; if
        # any entity references a category whose sync is missing/failed, mark
        # the test as skipped_metadata_stale (plan Q-pre, metadata partial).
        decisions = self._per_test_checks(tenant_id, resolved.test_case_ids, meta_version)
        report.per_test_decisions = decisions

        # ---- 6. Summary for preview screen -----------------------------------
        report.summary = {
            "environment": {
                "id": env.id, "name": env.name, "env_type": env.env_type,
                "instance_url": env.sf_instance_url,
            },
            "meta_version": {
                "id": meta_version.id if meta_version else None,
                "version_label": meta_version.version_label if meta_version else None,
                "age_hours": round(meta_age_hours, 1) if meta_age_hours is not None else None,
            },
            "llm_connection_id": env.llm_connection_id,
            "test_count": resolved.test_count,
            "will_run_count": report.will_run_count,
            "skip_count": report.skip_count,
            "eta_ms_range": self._eta_range(tenant_id, resolved.test_case_ids),
            "resolution_warnings": resolved.resolution_warnings,
            "missing_jira_keys": resolved.missing_jira_keys,
        }

        # Cost forecast is added externally (Super-Admin only) via
        # `cost.attach_forecast(report, resolved, env, model)` so this module
        # stays tenant-isolated + role-agnostic.

        return report

    def ensure_runnable(self, report: PreflightReport, user: Dict[str, Any],
                       override_token: Optional[str] = None) -> None:
        """Raise if report has blockers and user hasn't validly overridden them."""
        if not report.has_blockers:
            return
        if override_token == "OVERRIDE" and user.get("role") == "superadmin":
            # Super-admin override (pre-flight override, typed OVERRIDE)
            return
        if report.has_blockers:
            raise ValidationError(
                "Pre-flight blockers must be resolved before running.",
                code="PREFLIGHT_BLOCKERS",
                details={"blockers": report.blockers, "override_role": "superadmin"},
            )

    # ---- Internals -----------------------------------------------------------

    def _per_test_checks(self, tenant_id, test_case_ids, meta_version) -> List[PerTestDecision]:
        """Per-test metadata check (Q-pre: metadata partial state -> per-test skip)."""
        from primeqa.test_management.models import TestCase, TestCaseVersion
        decisions: List[PerTestDecision] = []

        if not test_case_ids:
            return decisions

        # Fetch test cases + their current version
        tcs = self.db.query(TestCase).filter(
            TestCase.id.in_(test_case_ids),
            TestCase.tenant_id == tenant_id,
            TestCase.deleted_at.is_(None),
        ).all()
        tc_by_id = {tc.id: tc for tc in tcs}

        current_version_ids = [tc.current_version_id for tc in tcs if tc.current_version_id]
        versions = {}
        if current_version_ids:
            rows = self.db.query(TestCaseVersion).filter(
                TestCaseVersion.id.in_(current_version_ids),
            ).all()
            versions = {v.id: v for v in rows}

        # Which metadata categories are healthy (R3 will populate meta_sync_status
        # for real; for R1 we assume all categories healthy if meta_version exists).
        healthy_categories = self._healthy_meta_categories(meta_version)

        for tc_id in test_case_ids:
            tc = tc_by_id.get(tc_id)
            if not tc:
                decisions.append(PerTestDecision(tc_id, False, "not_found"))
                continue
            if tc.deleted_at:
                decisions.append(PerTestDecision(tc_id, False, "deleted"))
                continue
            ver = versions.get(tc.current_version_id) if tc.current_version_id else None
            if not ver:
                decisions.append(PerTestDecision(tc_id, False, "no_version"))
                continue

            # Per-entity category classification
            refs = ver.referenced_entities or []
            stale_cats = self._categories_for_refs(refs) - healthy_categories
            if stale_cats:
                decisions.append(PerTestDecision(
                    tc_id, False,
                    f"skipped_metadata_stale:{','.join(sorted(stale_cats))}",
                ))
                continue

            decisions.append(PerTestDecision(tc_id, True, "ok"))

        return decisions

    def _healthy_meta_categories(self, meta_version) -> Set[str]:
        """Return the set of healthy categories for this meta_version.

        R3: reads `meta_sync_status` rows to answer per-category. Falls back
        to the legacy "meta_version.status == 'complete' \u2192 all healthy"
        if no status rows exist (pre-R3 meta versions).
        """
        if not meta_version:
            return set()
        from primeqa.metadata.models import MetaSyncStatus
        rows = self.db.query(MetaSyncStatus).filter(
            MetaSyncStatus.meta_version_id == meta_version.id,
        ).all()
        if not rows:
            # Legacy meta_version, no per-category data
            if meta_version.status == "complete":
                return {"objects", "fields", "record_types",
                        "validation_rules", "flows", "triggers"}
            return set()
        return {r.category for r in rows if r.status == "complete"}

    def _categories_for_refs(self, referenced_entities: List[Any]) -> Set[str]:
        """Map referenced_entities list -> the set of metadata categories they depend on.

        Entries look like "Account.Industry" (field), "Account" (object), or dicts
        with type info. For R1 keep it simple: anything with a dot implies
        'objects' + 'fields'; plain object name implies 'objects'.
        """
        cats: Set[str] = set()
        for ref in referenced_entities:
            if isinstance(ref, dict):
                t = ref.get("type")
                if t in ("object", "field", "validation_rule", "flow", "trigger", "record_type"):
                    cats.add({"object": "objects", "field": "fields",
                              "validation_rule": "validation_rules",
                              "flow": "flows", "trigger": "triggers",
                              "record_type": "record_types"}[t])
                continue
            if isinstance(ref, str):
                if "." in ref:
                    cats.add("objects")
                    cats.add("fields")
                else:
                    cats.add("objects")
        return cats

    def _eta_range(self, tenant_id, test_case_ids) -> Dict[str, Optional[int]]:
        """Approximate ETA range using recent run_test_results.duration_ms."""
        from primeqa.execution.models import RunTestResult
        if not test_case_ids:
            return {"p50_ms": 0, "p95_ms": 0}
        rows = self.db.query(RunTestResult.duration_ms).filter(
            RunTestResult.test_case_id.in_(test_case_ids),
            RunTestResult.duration_ms.isnot(None),
        ).limit(500).all()
        durations = sorted([r[0] for r in rows if r[0] is not None])
        if not durations:
            # Fallback: 10s per test (conservative; we'll learn fast)
            default_ms = 10_000
            return {"p50_ms": default_ms * len(test_case_ids),
                    "p95_ms": default_ms * len(test_case_ids) * 2}
        p50 = durations[len(durations) // 2]
        p95 = durations[int(len(durations) * 0.95)] if len(durations) > 1 else durations[0]
        return {"p50_ms": p50 * len(test_case_ids),
                "p95_ms": p95 * len(test_case_ids)}

    def _empty_summary(self, resolved):
        return {
            "environment": {"id": None, "name": "(unknown)", "env_type": None, "instance_url": None},
            "meta_version": {"id": None, "version_label": None, "age_hours": None},
            "llm_connection_id": None,
            "test_count": resolved.test_count,
            "will_run_count": 0, "skip_count": resolved.test_count,
            "eta_ms_range": {"p50_ms": 0, "p95_ms": 0},
            "resolution_warnings": resolved.resolution_warnings,
            "missing_jira_keys": resolved.missing_jira_keys,
        }

    def _basic_summary(self, env, meta_version, resolved):
        return {
            "environment": {
                "id": env.id, "name": env.name, "env_type": env.env_type,
                "instance_url": env.sf_instance_url,
            },
            "meta_version": {
                "id": meta_version.id if meta_version else None,
                "version_label": meta_version.version_label if meta_version else None,
                "age_hours": None,
            },
            "llm_connection_id": env.llm_connection_id,
            "test_count": resolved.test_count,
            "will_run_count": 0, "skip_count": resolved.test_count,
            "eta_ms_range": {"p50_ms": 0, "p95_ms": 0},
            "resolution_warnings": resolved.resolution_warnings,
            "missing_jira_keys": resolved.missing_jira_keys,
        }

    @staticmethod
    def _issue(code: str, message: str, details=None) -> Dict[str, Any]:
        return {"code": code, "message": message, "details": details or {}}
