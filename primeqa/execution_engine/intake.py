"""Substrate-4 execution enqueue boundary — the mirror of S3's generation intake.

The thin seam that puts a recipe-execution job on the ``s4_execution_jobs`` queue;
the worker's ``s4_execution_tick`` then claims + runs it. Unlike S3's intake there
is **no S1-version pin and no requirement read** — the job row carries only
``(test_id, environment_id, created_by)`` and the recipe is selected at run time by
``test_id`` (S2). Caller-fed: the v1 route validates the environment (+ the prod
gate) and closes its db *before* calling this; this opens its own tenant connection
(the S3 seam discipline) and never touches the v1 / public schema.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from primeqa.execution_engine.jobs import ExecutionJob, ExecutionJobStore


def enqueue_s4_execution(
    *, tenant_id: int, test_id, environment_id: int,
    created_by: Optional[int] = None,
) -> ExecutionJob:
    """Get-or-create the **active** execution job for ``(test_id, environment_id)``
    — idempotent while active, re-runnable once terminal (D-130.A). ``test_id`` is
    the S2 claim/test id; ``environment_id`` is a route-validated v1 environment.
    Returns the queued :class:`ExecutionJob` (the worker runs it on the next tick)."""
    return ExecutionJobStore(tenant_id).create_or_get_job(
        test_id=UUID(str(test_id)),
        environment_id=int(environment_id),
        created_by=created_by,
    )
