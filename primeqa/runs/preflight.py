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

# The six v1 metadata categories. In S1 mode there is no per-category partial
# state (a sync lands all entity types into one versioned run), so the healthy
# set is all-or-nothing — all six when the org model is usable, else empty.
_ALL_META_CATEGORIES = {"objects", "fields", "record_types",
                        "validation_rules", "flows", "triggers"}


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

    def __init__(self, db, *, env_repo, conn_repo, tc_repo):
        # GAP-2 (D-192): no ``meta_repo`` — preflight reads freshness/health from S1.
        self.db = db
        self.env_repo = env_repo
        self.conn_repo = conn_repo
        self.tc_repo = tc_repo

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

        # ---- 3. Metadata freshness (S1 — GAP-2 / D-192) ----------------------
        # Preflight reads the S1 substrate UNCONDITIONALLY: the env's last successful
        # sync_run (``completed_at``) + the current org-model version. The v1
        # ``meta_*`` fallback was removed at GAP-2 — the ``meta_*`` drop (Step 5)
        # cannot proceed while preflight still reads ``meta_*``, and tenant reads are
        # already on S1. Health is all-or-nothing: S1 syncs the
        # whole org atomically, so a usable org model => every category is present.
        meta_age_hours = None
        metadata_source = "s1"
        s1_seq = None
        from primeqa.metadata_bridge.s1_sync_console import read_s1_freshness
        s1 = read_s1_freshness(tenant_id, environment_id)
        if not (s1.get("available") and s1.get("provisioned") and s1.get("usable")):
            report.blockers.append(self._issue(
                "NO_METADATA",
                f"Environment '{env.name}' has no synced org model (S1). "
                f"Run a substrate sync first.",
            ))
        else:
            s1_seq = s1.get("current_version_seq")
            meta_age_hours = s1.get("age_hours")
            if meta_age_hours is not None:
                if meta_age_hours > METADATA_BLOCK_HOURS:
                    report.blockers.append(self._issue(
                        "METADATA_VERY_STALE",
                        f"Org model is {meta_age_hours:.0f}h old "
                        f"(> {METADATA_BLOCK_HOURS}h). Re-sync before running.",
                    ))
                elif meta_age_hours > METADATA_STALE_HOURS:
                    report.warnings.append(self._issue(
                        "METADATA_STALE",
                        f"Org model is {meta_age_hours:.0f}h old. "
                        f"Consider re-syncing for accurate results.",
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
        # S1 has no per-category partial state, so healthy = all-or-nothing: every
        # category is present when the org model is usable (s1_seq set), else none.
        healthy_categories = _ALL_META_CATEGORIES if s1_seq is not None else set()
        decisions = self._per_test_checks(
            tenant_id, resolved.test_case_ids, healthy_categories)
        report.per_test_decisions = decisions

        # ---- 6. Summary for preview screen -----------------------------------
        # `meta_version` keys are populated from the S1 org model (GAP-2) so
        # templates/runs/preview.html renders unchanged; `metadata_source` is always
        # 's1' now that preflight no longer reads `meta_*`.
        report.summary = {
            "environment": {
                "id": env.id, "name": env.name, "env_type": env.env_type,
                "instance_url": env.sf_instance_url,
            },
            "meta_version": {
                "id": s1_seq,
                "version_label": f"Org model v{s1_seq}" if s1_seq is not None else None,
                "age_hours": round(meta_age_hours, 1) if meta_age_hours is not None else None,
            },
            "metadata_source": metadata_source,  # always 's1' (GAP-2)
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

    def _per_test_checks(self, tenant_id, test_case_ids,
                         healthy_categories) -> List[PerTestDecision]:
        """Per-test metadata check (Q-pre: metadata partial state -> per-test skip).

        ``healthy_categories`` is computed by the caller from the active source
        (S1: all-or-nothing; meta_*: per-category ``meta_sync_status``)."""
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
