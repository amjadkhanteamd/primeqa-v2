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
