"""Run-policy gate shared by the run-triggering routes.

The v1 selection-resolution pipeline that used to live here (sprint /
suite / ticket keys -> pipeline_run) was retired with the v1 engine
(D-221 R2/R4). What remains is the environment run-policy gate
(`environment_can_bulk_run`) used by POST /claims/<id>/run and the
substrate enqueue API, plus the four-state readiness constants.
"""

from __future__ import annotations

from primeqa.core.models import Environment


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
    "READY_APPROVED", "READY_DRAFT",
    "READY_GENERATING", "READY_NEEDS_GEN",
    "RUNNABLE_STATES",
    "environment_can_bulk_run",
]
