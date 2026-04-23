"""Tester-oriented /run page: resolve sprint / suite / ticket selections
into a pipeline_run the existing executor can consume.

The Run Wizard at /runs/new already handles the messy mixed-source
case. This module is the lean, single-purpose path:

    selection (sprint / suite / ticket keys)
        -> resolve to list[test_case_id]
        -> PipelineService.create_run(source_type='test_cases', source_ids=[…])
        -> pipeline_run row
        -> redirect to /runs/:id

No new `bulk_runs` table — the existing pipeline_run row IS the bulk run.
One row wraps N test-case results via RunTestResult, and the Run
Detail page already has live SSE progress + cancel semantics. We
reuse it rather than duplicating.
"""

from __future__ import annotations

from typing import Iterable, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from primeqa.core.models import Environment
from primeqa.test_management.models import (
    Requirement, SuiteTestCase, TestCase, TestSuite,
)


# ---- Readiness model (four-state) ------------------------------------------
# Drives the /run page badges + the "Review your run" modal. Buckets a
# Jira ticket into exactly one of:
#
#   APPROVED          TC(s) with status IN ('approved', 'active')
#                     — BA-reviewed, first-class runnable
#   DRAFT             only has status='draft' TCs. Still runnable (the
#                     existing ticket_keys_to_test_case_ids fallback
#                     honours drafts). Badged so the user knows the
#                     review queue hasn't seen them.
#   GENERATING        no TCs yet, but a generation_jobs row is
#                     queued/claimed/running. Informational only —
#                     the worker is already on it; no user action.
#   NEEDS_GENERATION  no TCs, no active job. Blocks the run; the
#                     modal offers Generate as the remediation.
#
# APPROVED + DRAFT are "runnable". GENERATING + NEEDS_GENERATION block.
READY_APPROVED        = "APPROVED"
READY_DRAFT           = "DRAFT"
READY_GENERATING      = "GENERATING"
READY_NEEDS_GEN       = "NEEDS_GENERATION"

RUNNABLE_STATES: frozenset = frozenset({READY_APPROVED, READY_DRAFT})


def get_batch_readiness(jira_keys: list[str], tenant_id: int,
                        db: Session) -> dict[str, str]:
    """Batch-compute readiness for a list of Jira keys in ONE query.

    Returns {jira_key: READY_*} for every input key. Keys that aren't
    imported as requirements at all map to READY_NEEDS_GEN — the
    generate path will import them on demand.

    Single round-trip: LEFT JOIN requirements → test_cases +
    generation_jobs, with COUNT FILTER clauses bucketing per state.
    """
    # Normalise + dedupe so duplicate input keys don't inflate any metric
    clean = sorted({(k or "").strip() for k in (jira_keys or []) if (k or "").strip()})
    if not clean:
        return {}

    rows = db.execute(text("""
        SELECT r.jira_key,
               COUNT(tc.id) FILTER (
                   WHERE tc.deleted_at IS NULL
                     AND tc.status IN ('approved', 'active')
               ) AS approved_count,
               COUNT(tc.id) FILTER (
                   WHERE tc.deleted_at IS NULL
                     AND tc.status = 'draft'
               ) AS draft_count,
               COUNT(DISTINCT gj.id) FILTER (
                   WHERE gj.status IN ('queued', 'claimed', 'running')
               ) AS active_job_count
          FROM requirements r
          LEFT JOIN test_cases tc
            ON tc.requirement_id = r.id
          LEFT JOIN generation_jobs gj
            ON gj.requirement_id = r.id
         WHERE r.tenant_id = :tenant_id
           AND r.deleted_at IS NULL
           AND r.jira_key = ANY(:keys)
         GROUP BY r.jira_key
    """), {"tenant_id": tenant_id, "keys": clean}).fetchall()

    out: dict[str, str] = {}
    seen: set[str] = set()
    for row in rows:
        seen.add(row.jira_key)
        if (row.approved_count or 0) > 0:
            out[row.jira_key] = READY_APPROVED
        elif (row.draft_count or 0) > 0:
            out[row.jira_key] = READY_DRAFT
        elif (row.active_job_count or 0) > 0:
            out[row.jira_key] = READY_GENERATING
        else:
            # Requirement exists but has no TCs + no active job
            out[row.jira_key] = READY_NEEDS_GEN

    # Keys with no matching requirement row at all → never imported →
    # the generate path will import-then-queue for them.
    for k in clean:
        out.setdefault(k, READY_NEEDS_GEN)
    # Also return the original keys as input — useful when callers
    # pass mixed-case duplicates and want to look up by the raw key.
    for raw in (jira_keys or []):
        if raw and raw.strip() and raw.strip() not in out:
            out[raw.strip()] = out.get(raw.strip().strip(), READY_NEEDS_GEN)
    return out


def ticket_keys_to_test_case_ids(keys: Iterable[str], tenant_id: int,
                                 db: Session) -> tuple[list[int], list[str]]:
    """Resolve a list of Jira keys to live test-case ids + list any keys
    that don't have matching requirements / TCs in this tenant.

    Returns (test_case_ids, missing_keys).
    """
    clean_keys = sorted({(k or "").strip() for k in keys if (k or "").strip()})
    if not clean_keys:
        return [], []

    reqs = (db.query(Requirement)
            .filter(Requirement.tenant_id == tenant_id,
                    Requirement.jira_key.in_(clean_keys),
                    Requirement.deleted_at.is_(None))
            .all())
    req_by_key = {r.jira_key: r for r in reqs}
    missing = [k for k in clean_keys if k not in req_by_key]

    req_ids = [r.id for r in reqs]
    if not req_ids:
        return [], missing

    tcs = (db.query(TestCase)
           .filter(TestCase.tenant_id == tenant_id,
                   TestCase.requirement_id.in_(req_ids),
                   TestCase.deleted_at.is_(None),
                   TestCase.status.in_(("approved", "active")))
           .all())
    # If there are no approved/active TCs, fall back to *any* non-deleted
    # TCs (drafts) so the Tester's "run what I have" intent still works.
    if not tcs:
        tcs = (db.query(TestCase)
               .filter(TestCase.tenant_id == tenant_id,
                       TestCase.requirement_id.in_(req_ids),
                       TestCase.deleted_at.is_(None))
               .all())

    # Keys whose requirement exists but have zero TCs count as "missing"
    # for the user: we can't run what doesn't exist.
    reqs_with_tcs = {tc.requirement_id for tc in tcs}
    for k, r in req_by_key.items():
        if r.id not in reqs_with_tcs:
            missing.append(k)

    return [tc.id for tc in tcs], sorted(set(missing))


def release_to_test_case_ids(release_id: int, tenant_id: int, db: Session,
                             *, explicit_tc_ids: Optional[list[int]] = None,
                             explicit_jira_keys: Optional[list[str]] = None,
                             ) -> tuple[list[int], Optional[dict]]:
    """Resolve a release to TCs.

    By default: take everything in the release (requirements' TCs +
    test-plan TCs). If `explicit_tc_ids` or `explicit_jira_keys` is
    passed, use exactly those — the /run UI lets the user toggle
    individual items off before submitting.

    Returns (test_case_ids, release_summary_dict or None).
    """
    from primeqa.release.models import (
        Release, ReleaseRequirement, ReleaseTestPlanItem,
    )
    rel = (db.query(Release)
           .filter(Release.id == release_id,
                   Release.tenant_id == tenant_id)
           .first())
    if rel is None:
        return [], None

    # If the caller passed explicit ids/keys, use them (the user
    # unchecked some items in the picker). Otherwise take the whole
    # release.
    if explicit_tc_ids or explicit_jira_keys:
        tc_id_set: set[int] = set()
        if explicit_tc_ids:
            # Bound to tenant + alive
            rows = (db.query(TestCase.id)
                    .filter(TestCase.tenant_id == tenant_id,
                            TestCase.id.in_(explicit_tc_ids),
                            TestCase.deleted_at.is_(None))
                    .all())
            tc_id_set.update(r[0] for r in rows)
        if explicit_jira_keys:
            keyed_ids, _missing = ticket_keys_to_test_case_ids(
                explicit_jira_keys, tenant_id, db)
            tc_id_set.update(keyed_ids)
    else:
        # Whole-release default: union of release_test_plan_items + the
        # TCs attached to each release_requirement.
        plan_rows = (db.query(ReleaseTestPlanItem.test_case_id)
                     .join(TestCase,
                           TestCase.id == ReleaseTestPlanItem.test_case_id)
                     .filter(ReleaseTestPlanItem.release_id == release_id,
                             TestCase.tenant_id == tenant_id,
                             TestCase.deleted_at.is_(None))
                     .all())
        req_ids_in_release = (db.query(ReleaseRequirement.requirement_id)
                              .filter(ReleaseRequirement.release_id == release_id)
                              .all())
        req_ids_list = [r[0] for r in req_ids_in_release]
        req_tc_rows: list = []
        if req_ids_list:
            req_tc_rows = (db.query(TestCase.id)
                           .filter(TestCase.tenant_id == tenant_id,
                                   TestCase.requirement_id.in_(req_ids_list),
                                   TestCase.deleted_at.is_(None),
                                   TestCase.status.in_(("approved", "active")))
                           .all())
        tc_id_set = {r[0] for r in plan_rows} | {r[0] for r in req_tc_rows}

    summary = {"id": rel.id, "name": rel.name, "version_tag": rel.version_tag}
    return sorted(tc_id_set), summary


def suite_to_test_case_ids(suite_id: int, tenant_id: int,
                           db: Session) -> tuple[list[int], Optional[TestSuite]]:
    """Resolve a suite to its active test-case ids."""
    suite = (db.query(TestSuite)
             .filter_by(id=suite_id, tenant_id=tenant_id)
             .first())
    if suite is None or suite.deleted_at is not None:
        return [], None

    rows = (db.query(SuiteTestCase, TestCase)
            .join(TestCase, TestCase.id == SuiteTestCase.test_case_id)
            .filter(SuiteTestCase.suite_id == suite_id,
                    TestCase.tenant_id == tenant_id,
                    TestCase.deleted_at.is_(None))
            .order_by(SuiteTestCase.position.asc())
            .all())
    return [tc.id for (_link, tc) in rows], suite


def environment_can_bulk_run(env: Environment, confirm_production: bool
                             ) -> tuple[bool, str]:
    """Env-policy gate for the bulk run (layer 2 of the two-layer check).

    Keeps a copy close to the page/API so we can surface a precise
    inline-error message before punting to the executor.
    """
    if not getattr(env, "allow_bulk_run", True):
        return False, (
            f"Environment '{env.name}' does not allow bulk runs. "
            "Ask an admin to update the env's run policy."
        )
    if getattr(env, "is_production", False) and not confirm_production:
        return False, (
            "Production org confirmation required. "
            "Set confirm_production=true to proceed."
        )
    return True, ""


__all__ = [
    "ticket_keys_to_test_case_ids",
    "release_to_test_case_ids",
    "suite_to_test_case_ids",
    "get_batch_readiness",
    "READY_APPROVED", "READY_DRAFT",
    "READY_GENERATING", "READY_NEEDS_GEN",
    "RUNNABLE_STATES",
    "environment_can_bulk_run",
]
