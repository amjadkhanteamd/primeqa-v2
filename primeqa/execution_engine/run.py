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

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional
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
from primeqa.execution_engine.data_executor import (
    execute_data_recipe,
    plan_data_recipe_world,
)
from primeqa.execution_engine.errors import PlanTranslationError
from primeqa.execution_engine.evidence import RunEvidence
from primeqa.execution_engine.executor import execute_metadata_inspection
from primeqa.execution_engine.finalize import finalize_run
from primeqa.test_representation import SemanticTransactionCoordinator
from primeqa.test_representation.models.environment import (
    AuthAssumption,
    ExecutionEnvironmentBody,
)

if TYPE_CHECKING:  # annotations only — the runtime S6 imports are lazy (inside
    # _interpret_and_persist) to avoid an execution_engine <-> interpretation
    # import cycle: interpretation.attribution imports execution_engine.evidence,
    # and execution_engine/__init__ imports this run module.
    from primeqa.interpretation.model import Interpretation

_log = logging.getLogger(__name__)

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
      ``selected_recipe_id`` / ``evidence`` / ``runtime_state`` are set, and
      ``interpretation`` carries the S6 reading of the run (or ``None`` if the
      best-effort interpret step failed — the run truth is unaffected, D-111.2).
    - **no-eligible-recipe** (``ran=False``): ``select_recipe_for_execution``
      returned ``None`` (no approved claim / no approved recipe / no env match);
      ``reason`` is set and nothing ran. Not an error — a first-class result.
    """

    ran: bool
    reason: Optional[str] = None
    selected_recipe_id: Optional[UUID] = None
    evidence: Optional[RunEvidence] = None
    runtime_state: Optional[object] = None     # RecipeRuntimeState when ran
    interpretation: Optional[Interpretation] = None  # S6 reading (None if the best-effort interpret step failed)


def run_recipe_execution(
    session,
    test_id: UUID,
    *,
    environment_id: int,
    available_environment: Optional[ExecutionEnvironmentBody] = None,
    client=None,
    coordinator=None,
    record_sink=None,
) -> RunPathResult:
    """Execute the eligible recipe for ``test_id`` end-to-end on ``session``.

    Selects (S2) → **dispatches on ``recipe_kind``** to the right vertical
    (inspection or behavioral-negative) → bridges → resolves/uses a client →
    executes live → finalizes (persist + posture) → **interprets + persists**
    (S6, best-effort — D-111.2). Returns a :class:`RunPathResult`. All writes
    join the caller's transaction (flush, not commit); the caller owns the
    commit boundary (boundary A — see module docstring).

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

    evidence = _execute_for_kind(
        recipe, session, environment_id, client, record_sink=record_sink)
    state = finalize_run(session, evidence, coordinator=coord)
    interpretation = _interpret_and_persist(session, evidence)

    return RunPathResult(
        ran=True,
        selected_recipe_id=recipe.recipe_id,
        evidence=evidence,
        runtime_state=state,
        interpretation=interpretation,
    )


def _execute_for_kind(recipe, session, environment_id: int, client,
                      record_sink=None, world_plans=None):
    """Dispatch on ``recipe.recipe_kind`` → the matching bridge + executor.

    The inspection path (``metadata-recipe``) is unchanged from D-108.4; the
    behavioral-negative path (``data-recipe``, D-110.2) is the parallel build.
    An injected ``client`` overrides the kind's resolver (the seam the live
    tests use). ``record_sink`` (D-230) is the write-ahead durability sink for
    the data path — the metadata path creates nothing, so it ignores it.
    ``world_plans`` (D-230.2) is the pre-resolved ``{step_id: WorldPlan}`` map for
    the async data path: when supplied, the data branch reads no live S1 (so
    ``session`` may be ``None``); when ``None``, the sync path builds the live S1
    reader from ``session`` as before. An unknown kind fails loud — the run path
    has no executor for it (deferred recipe kinds)."""
    if recipe.recipe_kind == _METADATA_RECIPE_KIND:
        plan = build_metadata_inspection_plan(recipe)
        tooling = client or resolve_tooling_client(session, environment_id)
        return execute_metadata_inspection(
            plan, client=tooling, environment_id=environment_id)

    if recipe.recipe_kind == _DATA_RECIPE_KIND:
        plan = build_data_recipe_plan(recipe)
        data_client = client or resolve_data_mutation_client(session, environment_id)
        # Any plan that begins with an ORDINARY create constructs a world and
        # so needs S1 requiredness (k16 padding): the positive vertical (D-115)
        # and the 2-step negative's setup create (D-203). Only the 1-step
        # create-rejected negative (steps[0] flagged) needs no world. SYNC: build
        # the read-through port from the run-path's own connection (the S6
        # interpret idiom). ASYNC (D-230.2): ``world_plans`` is pre-resolved under
        # the select bracket, so no S1 read happens here (session is None).
        s1 = None
        if world_plans is None and plan.steps[0].expect_rejection is None:
            from primeqa.semantic.query import SemanticOrgModel
            s1 = SemanticOrgModel(session.connection())
        return execute_data_recipe(
            plan, client=data_client, environment_id=environment_id, s1=s1,
            world_plans=world_plans, record_sink=record_sink)

    raise PlanTranslationError(
        f"run path has no executor for recipe_kind={recipe.recipe_kind!r} "
        f"(only metadata-recipe + data-recipe are wired)",
        recipe_id=recipe.recipe_id,
    )


def _interpret_and_persist(session, evidence: RunEvidence) -> Optional[Interpretation]:
    """The S6 interpret stage for a finalized run — eager + best-effort (D-111.2).

    ``interpret_run`` (pure) → ``attribute_run`` (deeper cause, reading
    *contemporaneous* S1 through the run-path's own connection — so the
    attribution reflects the org model the run executed against) →
    ``persist_interpretation``, **all inside one SAVEPOINT** (``begin_nested``) +
    a ``try/except``.

    Evidence-first (B): any failure — an S1-read SELECT error, a persist flush
    violation, or an interpreter defect — rolls back to the savepoint and
    returns ``None``. The run truth ``finalize_run`` already flushed (the
    ``s4_execution_runs`` row + the S2 posture callback) lives *outside* the
    savepoint, so it survives and the outer transaction still commits. The
    savepoint is load-bearing: a persist flush failure would otherwise poison
    the whole transaction and lose the captured truth. The failure is logged
    loudly; the caller surfaces the ``None`` on ``RunPathResult.interpretation``.

    ``attribute_run`` self-limits the S1 read to the failed behavioral verdicts;
    the reader is constructed always (cheap — no query) but only queries when a
    failed behavioral verdict needs it.
    """
    # Lazy imports (call-time) break an execution_engine <-> interpretation
    # module cycle — the same convention run_recipe_execution_for_tenant uses
    # for Session/get_tenant_connection. See the TYPE_CHECKING note up top.
    from primeqa.interpretation.attribution import attribute_run
    from primeqa.interpretation.interpreter import interpret_run
    from primeqa.interpretation.result_store import persist_interpretation
    from primeqa.interpretation.s1_reader import S1ValidationRuleReader
    from primeqa.semantic.query import SemanticOrgModel

    try:
        with session.begin_nested():
            interpretation = interpret_run(
                evidence, claim_kind=_claim_kind_of(session, evidence.claim_test_id))
            s1_reader = S1ValidationRuleReader(
                SemanticOrgModel(session.connection()))
            interpretation = attribute_run(interpretation, evidence, s1=s1_reader)
            persist_interpretation(session, interpretation)
        return interpretation
    except Exception:
        _log.exception(
            "S6 interpretation failed for run %s; run truth preserved, "
            "interpretation=None (D-111.2 best-effort)", evidence.run_id)
        return None


def _claim_kind_of(session, claim_test_id) -> Optional[str]:
    """The claim's current kind — the interpreter's verdict-vocabulary selector
    (D-210). Best-effort: any read failure returns None (the value-claim
    default vocabulary), never blocks interpretation."""
    from sqlalchemy import text as _text
    try:
        return session.execute(_text(
            "SELECT claim_kind FROM test_claims "
            "WHERE test_id = :tid AND valid_to IS NULL"
        ), {"tid": str(claim_test_id)}).scalar()
    except Exception:
        return None


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

    # D-230: the production entry owns tenant identity, so it builds the
    # write-ahead durability sink (bound to tenant + env; run_id is supplied
    # per call by the executor's tracker). The sink writes in its OWN committed
    # transactions, so a created record is durable before the run can crash —
    # the crash-recovery reaper then reclaims anything teardown never reached.
    from primeqa.execution_engine.stranded_cleanup import StrandedRecordSink
    sink = StrandedRecordSink(tenant_id, environment_id)

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
                record_sink=sink,
            )
        finally:
            session.close()
    # get_tenant_connection commits on clean exit (atomic); rolls back on raise.


# ---------------------------------------------------------------------------
# The async run path (D-129) — brief transactions around the live read, so no
# DB connection is held across Salesforce I/O. The production-trigger
# foundation (B1/B2 queue + consumer drive this); the sync path above is
# unchanged (boundary A, the right call for a one-off call).
# ---------------------------------------------------------------------------

@contextmanager
def _default_session_scope(tenant_id: int):
    """A brief tenant-scoped session: open ``get_tenant_connection`` (search_path
    = tenant_N, public), bind a ``Session``, yield it, close it; the context
    **commits on clean exit** (atomic) and rolls back on raise — the
    ``_for_tenant`` idiom, scoped to one bracket. Injectable so a test drives the
    async path with a fake scope (no PG)."""
    from sqlalchemy.orm import Session

    from primeqa.semantic.connection import get_tenant_connection

    with get_tenant_connection(tenant_id) as conn:
        session = Session(bind=conn)
        try:
            yield session
        finally:
            session.close()


def run_recipe_execution_async(
    tenant_id: int,
    test_id: UUID,
    *,
    environment_id: int,
    client=None,
    available_environment: Optional[ExecutionEnvironmentBody] = None,
    coordinator=None,
    session_scope=None,
) -> RunPathResult:
    """Async-safe execution: bracket the live read with **brief transactions** so
    no DB connection is held across Salesforce I/O (D-129; data path D-230.2). Three
    brackets:

      1. **select + snapshot** (read-only TX) → the ``RecipeRead`` detaches as plain
         data; the client is resolved kind-aware; and — for a data recipe with an
         ordinary create — the operational world is pre-resolved into a detached
         ``{step_id: WorldPlan}`` map, all under THIS connection;
      2. **execute** holding NO DB connection (metadata needs only the client; data
         builds from the pre-resolved world plans, and the write-ahead sink writes in
         its own per-call transactions — not the run connection) — the load-bearing
         invariant;
      3. **persist + posture + interpret** (TX) → committed.

    ``client`` is **optional**: when ``None`` it is resolved kind-aware INSIDE bracket
    1 (which holds the connection a resolver needs — never the execute bracket); an
    injected client (the tests) is honored as-is. ``session_scope`` defaults to
    :func:`_default_session_scope`; a test injects a fake scope to assert the
    no-connection-during-read invariant.

    Both recipe kinds are bracketed now — the prior data-path deferral (D-129) is
    lifted by D-230.2's WorldPlan pre-resolution (`construct_world`'s reads are walked
    read-only up front, so execute holds no connection).
    """
    from primeqa.execution_engine.stranded_cleanup import StrandedRecordSink

    coord = coordinator or SemanticTransactionCoordinator()
    scope = session_scope or _default_session_scope

    # 1. select + snapshot — brief, read-only. The recipe detaches as plain data;
    #    the client + (for a data recipe) the WorldPlan map are resolved HERE, under
    #    the open connection, so the execute bracket holds none.
    with scope(tenant_id) as session:
        recipe = coord.select_recipe_for_execution(
            session,
            test_id,
            available_environment=available_environment or _MIN_AVAILABLE_ENV,
            replay_mode="live",
        )
        prep = (_prepare_async_execute(recipe, session, environment_id, client)
                if recipe is not None else None)
    if recipe is None:
        return RunPathResult(ran=False, reason="no_eligible_recipe")
    resolved_client, world_plans = prep

    # 2. execute — NO DB connection held across the live read (the invariant). The
    #    write-ahead durability sink (D-230) writes in its own per-call transactions,
    #    so it never holds the run connection across SF I/O.
    sink = StrandedRecordSink(tenant_id, environment_id)
    evidence = _execute_for_kind(
        recipe, None, environment_id, resolved_client,
        record_sink=sink, world_plans=world_plans)

    # 3. persist + posture + interpret — a fresh brief transaction.
    with scope(tenant_id) as session:
        state = finalize_run(session, evidence, coordinator=coord)
        interpretation = _interpret_and_persist(session, evidence)

    return RunPathResult(
        ran=True,
        selected_recipe_id=recipe.recipe_id,
        evidence=evidence,
        runtime_state=state,
        interpretation=interpretation,
    )


def _prepare_async_execute(recipe, session, environment_id: int, client):
    """Bracket-1 prep for the async execute (D-230.2): resolve the client kind-aware
    and, for a data recipe with an ordinary create, pre-resolve the operational world
    into a detached ``{step_id: WorldPlan}`` map — all under the (open) select-bracket
    connection, so the execute bracket needs none. Returns ``(client, world_plans)``:
    ``world_plans`` is ``None`` for a metadata recipe, ``{}`` for a 1-step
    create-rejected data recipe (no world to build), else the pre-resolved map. An
    injected ``client`` is honored; a missing one is resolved by kind. An unknown kind
    fails loud (same surface as the sync :func:`_execute_for_kind`)."""
    if recipe.recipe_kind == _METADATA_RECIPE_KIND:
        return (client or resolve_tooling_client(session, environment_id), None)
    if recipe.recipe_kind == _DATA_RECIPE_KIND:
        data_client = client or resolve_data_mutation_client(session, environment_id)
        plan = build_data_recipe_plan(recipe)
        if plan.steps[0].expect_rejection is None:
            from primeqa.semantic.query import SemanticOrgModel
            world_plans = plan_data_recipe_world(
                plan, SemanticOrgModel(session.connection()))
        else:
            world_plans = {}    # 1-step create-rejected — no world, but async-prepared
        return (data_client, world_plans)
    raise PlanTranslationError(
        f"run path has no executor for recipe_kind={recipe.recipe_kind!r} "
        f"(only metadata-recipe + data-recipe are wired)",
        recipe_id=recipe.recipe_id,
    )
