"""The run path — end-to-end recipe execution (SPEC §6, D-108.4).

The caller that wires slices 1–4 into a runnable synchronous path:
``select_recipe_for_execution`` (S2) → ``build_metadata_inspection_plan``
(bridge) → ``resolve_tooling_client`` / injected client → ``execute_metadata_inspection``
(executor) → ``finalize_run`` (persist + posture). Until this, nothing called
the executor (the slice-4 grounding finding).

Transaction boundary **A** (D-108.4): one tenant-scoped session/transaction
spans select → execute → finalize, committed once on clean exit (the
``LedgerPersister`` idiom). One session spans both data domains because
``get_tenant_connection`` sets ``search_path = "tenant_<id>", public`` — the
per-tenant S2/S4 tables resolve unqualified to ``tenant_N``; the v1
``environments``/``connections`` that ``resolve_tooling_client`` reads resolve
via ``public``. A holds the DB transaction across the bounded live read —
acceptable for this sync path; the future async orchestration must bracket the
read with brief transactions instead.

`run_recipe_execution` takes a caller-provided session (testable, composable);
`run_recipe_execution_for_tenant` is the thin production entry that owns the
``get_tenant_connection`` context + the single commit. Both inject `client` /
`coordinator` (the executor/finalize discipline) — and the live test *must*
inject a real client because the local substrate test DB has no
``environments``/``connections`` tables (D-108.4).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from primeqa.execution_engine.bridge import build_metadata_inspection_plan
from primeqa.execution_engine.bridge import (
    build_data_recipe_plan,
    build_metadata_inspection_plan,
)
from primeqa.execution_engine.credentials import (
    resolve_data_mutation_client,
    resolve_tooling_client,
)
from primeqa.execution_engine.data_executor import execute_data_recipe
from primeqa.execution_engine.errors import PlanTranslationError
from primeqa.execution_engine.evidence import RunEvidence
from primeqa.execution_engine.executor import execute_metadata_inspection
from primeqa.execution_engine.finalize import finalize_run
from primeqa.test_representation import SemanticTransactionCoordinator
from primeqa.test_representation.models.environment import (
    AuthAssumption,
    ExecutionEnvironmentBody,
)

# The capabilities the run path's verticals assume: a Metadata-API reader
# (inspection — generation/emission.py `_inspection_recipe`) and a Data-API
# user (the behavioral-negative create, D-110.2). Selection runs *before* the
# recipe kind is known, so the default available-environment advertises both —
# a real org has both capabilities. Richer capability discovery is F5-deferred.
_MIN_AVAILABLE_ENV = ExecutionEnvironmentBody(
    auth_assumptions=[
        AuthAssumption(auth_kind="metadata_api_user"),
        AuthAssumption(auth_kind="data_api_user"),
    ],
)

# Recipe-kind → the run path's executor for it (D-110.2 dispatch).
_METADATA_RECIPE_KIND = "metadata-recipe"
_DATA_RECIPE_KIND = "data-recipe"


@dataclass(frozen=True)
class RunPathResult:
    """The outcome of a run-path invocation — two distinguishable shapes.

    - **ran** (``ran=True``): a recipe was selected and executed;
      ``selected_recipe_id`` / ``evidence`` / ``runtime_state`` are set.
    - **no-eligible-recipe** (``ran=False``): ``select_recipe_for_execution``
      returned ``None`` (no approved claim / no approved recipe / no env match);
      ``reason`` is set and nothing ran. Not an error — a first-class result.
    """

    ran: bool
    reason: Optional[str] = None
    selected_recipe_id: Optional[UUID] = None
    evidence: Optional[RunEvidence] = None
    runtime_state: Optional[object] = None     # RecipeRuntimeState when ran


def run_recipe_execution(
    session,
    test_id: UUID,
    *,
    environment_id: int,
    available_environment: Optional[ExecutionEnvironmentBody] = None,
    client=None,
    coordinator=None,
) -> RunPathResult:
    """Execute the eligible recipe for ``test_id`` end-to-end on ``session``.

    Selects (S2) → **dispatches on ``recipe_kind``** to the right vertical
    (inspection or behavioral-negative) → bridges → resolves/uses a client →
    executes live → finalizes (persist + posture). Returns a
    :class:`RunPathResult`. All writes join the caller's transaction (flush, not
    commit); the caller owns the commit boundary (boundary A — see module
    docstring).

    ``available_environment`` defaults to the minimal env advertising both
    verticals' capabilities; ``client`` defaults to the kind's resolver
    (:func:`resolve_tooling_client` / :func:`resolve_data_mutation_client`);
    ``coordinator`` defaults to a fresh :class:`SemanticTransactionCoordinator`.
    """
    coord = coordinator or SemanticTransactionCoordinator()

    recipe = coord.select_recipe_for_execution(
        session,
        test_id,
        available_environment=available_environment or _MIN_AVAILABLE_ENV,
        replay_mode="live",
    )
    if recipe is None:
        return RunPathResult(ran=False, reason="no_eligible_recipe")

    evidence = _execute_for_kind(recipe, session, environment_id, client)
    state = finalize_run(session, evidence, coordinator=coord)

    return RunPathResult(
        ran=True,
        selected_recipe_id=recipe.recipe_id,
        evidence=evidence,
        runtime_state=state,
    )


def _execute_for_kind(recipe, session, environment_id: int, client):
    """Dispatch on ``recipe.recipe_kind`` → the matching bridge + executor.

    The inspection path (``metadata-recipe``) is unchanged from D-108.4; the
    behavioral-negative path (``data-recipe``, D-110.2) is the parallel build.
    An injected ``client`` overrides the kind's resolver (the seam the live
    tests use). An unknown kind fails loud — the run path has no executor for
    it (deferred recipe kinds)."""
    if recipe.recipe_kind == _METADATA_RECIPE_KIND:
        plan = build_metadata_inspection_plan(recipe)
        tooling = client or resolve_tooling_client(session, environment_id)
        return execute_metadata_inspection(
            plan, client=tooling, environment_id=environment_id)

    if recipe.recipe_kind == _DATA_RECIPE_KIND:
        plan = build_data_recipe_plan(recipe)
        data_client = client or resolve_data_mutation_client(session, environment_id)
        return execute_data_recipe(
            plan, client=data_client, environment_id=environment_id)

    raise PlanTranslationError(
        f"run path has no executor for recipe_kind={recipe.recipe_kind!r} "
        f"(only metadata-recipe + data-recipe are wired)",
        recipe_id=recipe.recipe_id,
    )


def run_recipe_execution_for_tenant(
    tenant_id: int,
    test_id: UUID,
    *,
    environment_id: int,
    available_environment: Optional[ExecutionEnvironmentBody] = None,
    client=None,
    coordinator=None,
) -> RunPathResult:
    """Production entry: own the tenant connection + the single commit.

    Opens ``get_tenant_connection(tenant_id)`` (search_path = tenant_N, public),
    binds a Session, runs :func:`run_recipe_execution`, and lets the context
    **commit atomically on clean exit** (boundary A). An exception propagates
    and triggers the context's rollback — a half-run from a defect is never
    persisted.
    """
    from sqlalchemy.orm import Session

    from primeqa.semantic.connection import get_tenant_connection

    with get_tenant_connection(tenant_id) as conn:
        session = Session(bind=conn)
        try:
            return run_recipe_execution(
                session,
                test_id,
                environment_id=environment_id,
                available_environment=available_environment,
                client=client,
                coordinator=coordinator,
            )
        finally:
            session.close()
    # get_tenant_connection commits on clean exit (atomic); rolls back on raise.
