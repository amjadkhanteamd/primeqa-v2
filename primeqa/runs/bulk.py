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

from sqlalchemy.orm import Session

from primeqa.core.models import Environment
from primeqa.test_management.models import (
    Requirement, SuiteTestCase, TestCase, TestSuite,
)


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
    "environment_can_bulk_run",
]
