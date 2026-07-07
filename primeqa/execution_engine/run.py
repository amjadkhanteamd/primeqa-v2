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
from uuid import UUID, uuid4

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
from primeqa.execution_engine.errors import (
    PlanTranslationError, PolicyError,
)
from primeqa.execution_engine.evidence import RunEvidence
from primeqa.core.authz import AuthorizationError, Tier
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


def _resolve_env_gate(session, environment_id: int):
    """Read ``(execution_policy, is_production)`` for the env from a live session.

    The ``environments`` row lives in ``public`` (reachable on the tenant
    search_path). Returns ``(None, None)`` when no session is available (the
    async execute bracket holds none — its caller resolves the gate up front in
    the open select bracket and passes it in)."""
    if session is None:
        return (None, None)
    try:
        from primeqa.core.models import Environment
        # SAVEPOINT-guard the read: if `environments` is unreadable (a
        # substrate-only test session with no such table), the failed SELECT
        # aborts the Postgres transaction. begin_nested() lets us roll that back
        # to the savepoint so the OUTER transaction stays usable — otherwise the
        # next statement (finalize_run's flush) hits InFailedSqlTransaction.
        with session.begin_nested():
            env = (session.query(Environment)
                   .filter(Environment.id == environment_id).first())
    except Exception:
        # The env row is unreadable from this session (e.g. a substrate-only
        # test session with no `environments` table). Degrade the dispatch gate
        # to skip — the route + enqueue layers still gate, and a production
        # session always resolves the row (public is on the search_path).
        return (None, None)
    if env is None:
        return (None, None)
    return (env.execution_policy, bool(env.is_production))


def _authorize_dispatch(recipe, *, execution_policy, is_production, caller_tier):
    """The production / resource-policy dispatch gate (D-245, Phase 4) — applied
    at the single chokepoint :func:`_execute_for_kind`. Three distinct errors,
    never conflated.

    The **resource-policy** rule (i, below) is role-independent and fires on every
    entry (sync, async/queued, system). The **production-role** rule (ii) needs a
    human ``caller_tier``; it is supplied on the sync path
    (``/claims/<id>/run`` passes ``rank(role)``) but is ``None`` on the async path
    (the worker runs as *system*). The async production decision is therefore made
    at the **enqueue boundary** instead — ``api_s4_execution_enqueue`` rejects a
    non-Admin enqueue against a production env (D-245 review fix) — because the
    authenticated caller is known there, not in the worker.

    ``recipe_kind == metadata-recipe`` IS the read-only inspection vertical: the
    bridge enforces ``mode == metadata_read`` (``build_metadata_inspection_plan``
    raises otherwise), so the kind is the reliable read-mode discriminator. **When
    a metadata-write/deploy recipe kind is added, this gate MUST also check the
    mode** — a write-mode metadata recipe mutates and is not inspection.

    Order:
      (i)  Resource policy (``env.execution_policy``) — env state, applies to
           EVERYONE incl. admins/system: ``disabled`` → reject all (PolicyError);
           ``read_only`` → reject any non-inspection recipe (PolicyError).
      (ii) Production role — only when a user ``caller_tier`` is supplied: a
           non-Admin against a production env may run ONLY the inspection
           vertical; a data-recipe is hard-rejected (AuthorizationError).
    """
    read_only_inspection = recipe.recipe_kind == _METADATA_RECIPE_KIND

    if execution_policy == "disabled":
        raise PolicyError(
            "environment execution_policy=disabled — no runs permitted "
            f"(recipe_kind={recipe.recipe_kind!r})")
    if execution_policy == "read_only" and not read_only_inspection:
        raise PolicyError(
            "environment execution_policy=read_only — only the read-only "
            "metadata inspection vertical may run; data-recipes are rejected "
            "regardless of role")

    if (is_production and caller_tier is not None
            and int(caller_tier) < int(Tier.ADMIN) and not read_only_inspection):
        raise AuthorizationError(
            "production environment — only an Admin may run a data-recipe "
            "(mutating) here; your role may run only the read-only inspection")


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
    field_overrides=None,
    caller_tier=None,
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
        recipe, session, environment_id, client, record_sink=record_sink,
        field_overrides=field_overrides, caller_tier=caller_tier,
        coordinator=coord)
    state = finalize_run(session, evidence, coordinator=coord)
    interpretation = _interpret_and_persist(session, evidence)

    return RunPathResult(
        ran=True,
        selected_recipe_id=recipe.recipe_id,
        evidence=evidence,
        runtime_state=state,
        interpretation=interpretation,
    )


def _resolve_run_org(session, environment_id: int) -> str:
    """The run's ``connected_org_id`` (str), FAIL-LOUD if unresolved (per-org
    Slice 3a, D-257). A run reads S1 *scoped to the org it executes against* — an
    env with no provisioned connected_org would otherwise fall back to a wrong /
    org-blind read, attributing the run's evidence against the wrong metadata.

    D-286: delegates to the SHARED resolver (``sync.credentials``) so S4 execution
    and S3 generation fail-loud through the ONE resolver. Behavior-neutral — same
    org id resolved, same ``OrgResolutionError`` raised (now neutral-based; S4
    classifies it as ``execution_error`` via the ``_classify_error`` fallthrough,
    identical to before)."""
    from primeqa.sync.credentials import resolve_connected_org_or_raise
    return resolve_connected_org_or_raise(session.connection(), environment_id)


def _execute_for_kind(recipe, session, environment_id: int, client,
                      record_sink=None, world_plans=None, field_overrides=None,
                      *, env_gate=None, caller_tier=None,
                      null_asserted_fields=None, coordinator=None):
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
    # D-245 Phase 4 — the production / resource-policy gate, at the single
    # dispatch chokepoint (covers sync + async). Sync passes a live `session`
    # (resolve the env policy here); async passes a pre-resolved `env_gate`
    # (its execute bracket holds no DB connection — `session` is None).
    policy, is_prod = (env_gate if env_gate is not None
                       else _resolve_env_gate(session, environment_id))
    _authorize_dispatch(recipe, execution_policy=policy,
                        is_production=is_prod, caller_tier=caller_tier)

    if recipe.recipe_kind == _METADATA_RECIPE_KIND:
        plan = build_metadata_inspection_plan(recipe)
        tooling = client or resolve_tooling_client(session, environment_id)
        return execute_metadata_inspection(
            plan, client=tooling, environment_id=environment_id)

    if recipe.recipe_kind == _DATA_RECIPE_KIND:
        plan = build_data_recipe_plan(recipe)
        data_client = client or resolve_data_mutation_client(session, environment_id)
        # D-338: resolve the claim's asserted-blank fields (SYNC — session
        # open) — only for plans that stage an ordinary create (positive +
        # 2-step negative; the 1-step create-rejected negative stages nothing,
        # so the set has no consumer). ASYNC passes them pre-resolved from its
        # select bracket; a direct caller with neither (unit seams) gets the
        # empty set.
        if null_asserted_fields is None:
            null_asserted_fields = (
                _null_asserted_fields_of(session, recipe,
                                         coordinator=coordinator)
                if session is not None
                and plan.steps[0].expect_rejection is None
                else frozenset())
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
            # per-org Slice 3a: scope the world-build S1 read to the run's org.
            org_id = _resolve_run_org(session, environment_id)
            s1 = SemanticOrgModel(session.connection(), connected_org_id=org_id)
        return execute_data_recipe(
            plan, client=data_client, environment_id=environment_id, s1=s1,
            world_plans=world_plans, record_sink=record_sink,
            field_overrides=field_overrides,
            null_asserted_fields=null_asserted_fields)

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
    from primeqa.sync.credentials import get_connected_org_for_environment

    try:
        # per-org Slice 3a: attribute against the org the run executed against.
        # Best-effort (D-111.2): if the run's env has no connected_org, skip
        # interpretation rather than read the wrong / org-blind model — the run
        # truth is already flushed and survives.
        org_id = get_connected_org_for_environment(
            session.connection(), evidence.environment_id)
        if org_id is None:
            _log.warning(
                "S6: no connected_org for environment_id=%s (run %s); "
                "interpretation skipped, run truth preserved (D-111.2)",
                evidence.environment_id, evidence.run_id)
            return None
        with session.begin_nested():
            claim_kind, claim_automation = _claim_context_of(
                session, evidence.claim_test_id)
            interpretation = interpret_run(evidence, claim_kind=claim_kind)
            s1_reader = S1ValidationRuleReader(
                SemanticOrgModel(session.connection(), connected_org_id=org_id))
            interpretation = attribute_run(interpretation, evidence, s1=s1_reader,
                                           claim_automation=claim_automation)
            persist_interpretation(session, interpretation)
        return interpretation
    except Exception:
        _log.exception(
            "S6 interpretation failed for run %s; run truth preserved, "
            "interpretation=None (D-111.2 best-effort)", evidence.run_id)
        return None


def _claim_context_of(session, claim_test_id):
    """The claim's current kind + its automation binding, one read.

    The kind is the interpreter's verdict-vocabulary selector (D-210). The
    binding ``{name, primitive}`` (from the automation-effect body's pinned
    ``automation`` ref + ``automation_primitive``) lets S6 attribution name
    the automation the CLAIM grounds on instead of scanning S1 for any active
    Flow on the object — the scan blamed an unrelated Flow on multi-flow
    objects (run 4b8cbe84 blamed HL_Auto_Risk_Rating for an approval-process
    claim). Best-effort: any read failure returns ``(None, None)`` (the
    value-claim default vocabulary, scan-fallback attribution), never blocks
    interpretation."""
    from sqlalchemy import text as _text
    try:
        row = session.execute(_text(
            "SELECT claim_kind, "
            "asserted_truth->'automation'->>'external_id' AS automation_name, "
            "asserted_truth->>'automation_primitive' AS automation_primitive "
            "FROM test_claims "
            "WHERE test_id = :tid AND valid_to IS NULL"
        ), {"tid": str(claim_test_id)}).mappings().first()
        if row is None:
            return None, None
        automation = None
        if row["automation_name"]:
            automation = {"name": row["automation_name"],
                          "primitive": row["automation_primitive"]}
        return row["claim_kind"], automation
    except Exception:
        return None, None


def _null_asserted_fields_of(session, recipe, coordinator=None) -> frozenset:
    """The claim's ``is_null``-conditioned field external_ids (D-338) — the
    business state the claim asserts as BLANK.

    The recipe cannot carry these: D-305 stages ``is_null`` clauses by ABSENCE
    (``field_values`` holds only the equals clauses), so the executor's k16
    semantic set — derived from the staged keys — under-counts the claim's
    identity, and the R1 VR-satisfaction filler (world.py) could DEMAND a value
    for an asserted-blank nillable field to satisfy an armed VR: the executed
    record then no longer matches the claim's state — a wrong-green. The set is
    read from the claim itself through the S2 coordinator, pinned to the
    recipe's ``claim_version_seq`` (latest when unpinned).

    A missing claim row → empty set (hand-built recipes; the pre-D-338
    behavior). A read/decode failure PROPAGATES: the run surfaces ``errored``
    pre-create — the executor never guesses, and failing open would silently
    reopen the wrong-green hole this closes."""
    coord = coordinator or SemanticTransactionCoordinator()
    claim = (coord.get_claim_version(
                 session, recipe.claim_test_id, recipe.claim_version_seq)
             if recipe.claim_version_seq is not None
             else coord.get_latest_claim(session, recipe.claim_test_id))
    if claim is None:
        return frozenset()
    conditions = getattr(claim.semantic_conditions, "conditions", None) or ()
    return frozenset(
        c.subject.external_id for c in conditions
        if c.predicate == "is_null" and c.subject.external_id)


def run_recipe_execution_for_tenant(
    tenant_id: int,
    test_id: UUID,
    *,
    environment_id: int,
    available_environment: Optional[ExecutionEnvironmentBody] = None,
    client=None,
    coordinator=None,
    field_overrides=None,
    caller_tier=None,
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
                field_overrides=field_overrides,
                caller_tier=caller_tier,
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
    caller_tier=None,
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
        prep = (_prepare_async_execute(recipe, session, environment_id, client,
                                       coordinator=coord)
                if recipe is not None else None)
        # D-245 Phase 4: resolve the env policy HERE (session open) so the
        # execute bracket — which holds no connection — can gate without a read.
        env_gate = _resolve_env_gate(session, environment_id)
    if recipe is None:
        return RunPathResult(ran=False, reason="no_eligible_recipe")
    resolved_client, world_plans, null_asserted = prep

    # 2. execute — NO DB connection held across the live read (the invariant). The
    #    write-ahead durability sink (D-230) writes in its own per-call transactions,
    #    so it never holds the run connection across SF I/O.
    sink = StrandedRecordSink(tenant_id, environment_id)
    evidence = _execute_for_kind(
        recipe, None, environment_id, resolved_client,
        record_sink=sink, world_plans=world_plans,
        env_gate=env_gate, caller_tier=caller_tier,
        null_asserted_fields=null_asserted)

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


def _prepare_async_execute(recipe, session, environment_id: int, client,
                           coordinator=None):
    """Bracket-1 prep for the async execute (D-230.2): resolve the client kind-aware
    and, for a data recipe with an ordinary create, pre-resolve the operational world
    into a detached ``{step_id: WorldPlan}`` map — all under the (open) select-bracket
    connection, so the execute bracket needs none. Returns ``(client, world_plans,
    null_asserted_fields)``: ``world_plans`` is ``None`` for a metadata recipe, ``{}``
    for a 1-step create-rejected data recipe (no world to build), else the
    pre-resolved map; ``null_asserted_fields`` (D-338) is the claim's asserted-blank
    field set, resolved HERE (the execute bracket holds no connection) — both baked
    into the WorldPlans and returned for the executor's k16 grading/strip. An
    injected ``client`` is honored; a missing one is resolved by kind. An unknown kind
    fails loud (same surface as the sync :func:`_execute_for_kind`)."""
    if recipe.recipe_kind == _METADATA_RECIPE_KIND:
        return (client or resolve_tooling_client(session, environment_id), None,
                frozenset())
    if recipe.recipe_kind == _DATA_RECIPE_KIND:
        data_client = client or resolve_data_mutation_client(session, environment_id)
        plan = build_data_recipe_plan(recipe)
        if plan.steps[0].expect_rejection is None:
            from primeqa.semantic.query import SemanticOrgModel
            null_asserted = _null_asserted_fields_of(session, recipe,
                                                     coordinator=coordinator)
            # per-org Slice 3a: scope the pre-resolved world S1 read to the run's org.
            org_id = _resolve_run_org(session, environment_id)
            world_plans = plan_data_recipe_world(
                plan, SemanticOrgModel(session.connection(), connected_org_id=org_id),
                null_asserted_fields=null_asserted)
        else:
            # 1-step create-rejected — no world, no staged create, and so no
            # consumer for the asserted-blank set; still async-prepared.
            world_plans, null_asserted = {}, frozenset()
        return (data_client, world_plans, null_asserted)
    raise PlanTranslationError(
        f"run path has no executor for recipe_kind={recipe.recipe_kind!r} "
        f"(only metadata-recipe + data-recipe are wired)",
        recipe_id=recipe.recipe_id,
    )


# ---------------------------------------------------------------------------
# Run-all (D-277, S6 Slice 3.3) — execute EVERY applicable recipe of a claim as
# one batch, each run stamped with one batch_id, with per-probe failure
# isolation. The keystone the bva strategy (Slice 4) grades over. Additive +
# DORMANT: wired to no router (Slice 3.4 routes single vs run-all; nothing
# derives a bva probe set until §4a), so nothing reaches here yet — the singular
# run paths above are byte-identical. This layer only runs-and-persists; it does
# NOT interpret/aggregate or compute Verified (that is Slice 4). finalize_run +
# the best-effort S6 interpret per probe are the SAME as the singular's.
# ---------------------------------------------------------------------------

_RUNALL_SOURCE = "runall_probe"


@dataclass(frozen=True)
class ProbeRun:
    """One probe's run within a run-all batch — the recipe, its minted run, and
    the carried-verbatim S4 outcome (incl. ``errored``). No verdict / Verified."""

    recipe_id: UUID
    run_id: UUID
    outcome: str


@dataclass(frozen=True)
class RunAllResult:
    """The outcome of a run-all invocation (D-277). ``probes`` is one entry per
    APPLICABLE recipe — including every errored one (a probe that ran-and-errored
    leaves a batch-stamped row; this is NOT the same as a probe that never ran).
    ``ran`` is ``False`` only when the applicable set was empty (a real state, not
    an error — nothing persisted). The batch's completeness/aggregation → Slice 4."""

    batch_id: UUID
    ran: bool
    probes: tuple = ()
    reason: Optional[str] = None


def _synthesize_errored_evidence(recipe, environment_id: int, exc: BaseException) -> RunEvidence:
    """Build a minimal ``errored`` :class:`RunEvidence` for a probe whose execute
    RAISED before producing evidence (authorization / plan-translation / org
    resolution / an unexpected fault). The probe WAS attempted but could not be
    evaluated — so run-all must still persist a batch-stamped row (outcome=errored
    → Slice 1 classifies it indeterminate), never swallow it: Slice 4's
    completeness check distinguishes "ran-and-errored" (a row exists) from "never
    ran" (no row), and a swallowed probe with no row would break that."""
    from datetime import datetime, timezone

    from primeqa.execution_engine.evidence import ErrorSurface

    now = datetime.now(timezone.utc)
    return RunEvidence(
        run_id=uuid4(),
        recipe_id=recipe.recipe_id,
        recipe_version_seq=recipe.version_seq,
        claim_test_id=recipe.claim_test_id,
        claim_version_seq=recipe.claim_version_seq,
        environment_id=environment_id,
        api_choice="n/a",
        outcome="errored",
        started_at=now,
        finished_at=now,
        steps=(),
        error=ErrorSurface(phase="translate", error_type=type(exc).__name__,
                           message=str(exc)[:500]),
    )


class _PrepError:
    """Marks an async probe whose bracket-1 prep (:func:`_prepare_async_execute`)
    raised — so the batch loop synthesizes an errored evidence for THAT probe from
    ``exc`` instead of letting one recipe's prep failure abort the whole batch
    (run-all per-probe isolation, D-277)."""

    def __init__(self, exc: BaseException):
        self.exc = exc


def _write_batch_manifest(tenant_id, batch_id, claim_test_id, expected_recipe_ids,
                          env_key=None):
    """Record the run-all batch MANIFEST (C2, Slice 4c.0 / D-281): the EXPECTED
    applicable recipe set for this batch, committed EARLY in its OWN transaction
    (BEFORE any probe runs), so a batch that crashes before any probe persists is
    still readable as ``expected=N`` / ``present=0`` → INCOMPLETE → not-Verified
    (the completeness check, Slice 4c.1). The recorded membership is **drift-immune**
    — unlike recomputing the applicable set at read time, which depends on the
    mutable approved-claim version + recipe ``valid_to`` / status / priority.

    Own committed txn via ``get_tenant_connection`` (search_path = tenant_N), so it
    is durable independent of the probe loop's fate — the early-commit correctness
    requirement, not a nicety.

    DORMANT: only a run-all invocation calls this, and nothing routes to run-all
    until §4a. Skips when ``tenant_id`` is None (no tenant context — e.g. a run-all
    unit test that doesn't exercise the manifest); the loop still runs, and 4c.1
    reads a manifest-less batch as unknowable → not-Verified (the safe default)."""
    if tenant_id is None:
        return
    from datetime import datetime, timezone

    from sqlalchemy import bindparam, text
    from sqlalchemy.dialects.postgresql import ARRAY
    from sqlalchemy.dialects.postgresql import UUID as PG_UUID
    from sqlalchemy.orm import Session

    from primeqa.semantic.connection import get_tenant_connection

    stmt = text(
        "INSERT INTO s4_runall_batch_manifests "
        "(batch_id, claim_test_id, expected_recipe_ids, env_key, created_at) "
        "VALUES (:batch_id, :claim_test_id, :recipe_ids, :env_key, :created_at) "
        "ON CONFLICT (batch_id) DO NOTHING"
    ).bindparams(bindparam("recipe_ids", type_=ARRAY(PG_UUID(as_uuid=True))))

    with get_tenant_connection(tenant_id) as conn:     # own txn; commits on exit
        s = Session(bind=conn)
        try:
            s.execute(stmt, {
                "batch_id": batch_id,
                "claim_test_id": claim_test_id,
                "recipe_ids": list(expected_recipe_ids),
                "env_key": env_key,
                "created_at": datetime.now(timezone.utc),
            })
            s.flush()
        finally:
            s.close()
    # get_tenant_connection commits on clean exit — the manifest is durable now,
    # before the first probe runs.


def _probe_recipes(recipes: list) -> list:
    """D-300.1: the bva PROBE set — data-recipes only. ADR-001 defines bva over
    DATA probes; the D-228 inspection fallback is definitionally a metadata
    read and must never be strict-ANDed into a boundary verdict (a metadata
    read contributing a boundary Verified would be a wrong-green). Applied in
    BOTH run-all paths BETWEEN selection and the D-281 manifest write, so the
    batch and its manifest always agree on the probe set. The single path is
    untouched (its fallback selection legitimately serves depth diversity).
    A future data-shaped weaker-fallback would pass this filter — no such
    shape exists today; that is the named re-decision trigger (D-300.1)."""
    return [r for r in recipes if getattr(r, "recipe_kind", None) == "data-recipe"]


def run_all_recipes_execution(
    session,
    test_id: UUID,
    *,
    environment_id: int,
    available_environment: Optional[ExecutionEnvironmentBody] = None,
    client=None,
    coordinator=None,
    record_sink=None,
    field_overrides=None,
    caller_tier=None,
    execute_fn=None,
    tenant_id=None,
    manifest_writer=None,
) -> RunAllResult:
    """Run EVERY applicable recipe of ``test_id`` as one batch (sync; D-277).

    Mints one ``batch_id``; resolves the full env-satisfiable applicable set via
    :meth:`select_recipes_for_execution` (the plural, 3.1); runs each recipe
    through the recipe-agnostic :func:`_execute_for_kind`; and persists each
    result batch-stamped (``finalize_run(..., batch_id, source='runall_probe')``).
    An empty applicable set persists nothing and returns ``ran=False`` (a real
    state, not an error). Shares the caller's session (one commit by
    ``_for_tenant``), with a per-probe SAVEPOINT so a probe's failure can't poison
    the shared transaction for the others.

    **Per-probe failure isolation:** each probe runs inside its own
    ``begin_nested`` savepoint. A probe whose execute RAISES still persists a
    batch-stamped ``errored`` row (synthesized) and the loop continues; a probe
    whose executor RETURNS an errored evidence persists that real row; a probe
    whose PERSIST itself fails rolls back to its savepoint (the session stays
    clean for the next probe) and leaves no row — so the batch is incomplete ⇒
    Slice 4 reads it not-Verified ⇒ re-runnable (D-272). One probe failing never
    aborts the batch. (``probes`` may therefore be shorter than the applicable set
    when some probe's persist failed.)

    ``execute_fn`` defaults to :func:`_execute_for_kind` (injectable for tests);
    ``manifest_writer`` to :func:`_write_batch_manifest` (injectable for tests).
    ``tenant_id`` is needed only for the manifest's own committed txn (the router
    threads it); ``None`` skips the manifest (the loop still runs).
    """
    coord = coordinator or SemanticTransactionCoordinator()
    execute = execute_fn or _execute_for_kind
    write_manifest = manifest_writer or _write_batch_manifest
    batch_id = uuid4()

    recipes = _probe_recipes(coord.select_recipes_for_execution(
        session, test_id,
        available_environment=available_environment or _MIN_AVAILABLE_ENV,
        replay_mode="live"))
    if not recipes:
        return RunAllResult(batch_id=batch_id, ran=False, reason="no_eligible_recipes")

    # Slice 4c.0 (C2/D-281) + D-300 review-fix B2: the manifest records the
    # claim's AUTHORED data-probe membership (status- + env-blind), NOT the
    # approval/env-filtered selection — an unapproved / deprecated / env-
    # unsatisfiable member stays EXPECTED, never runs, and the completeness
    # reader marks the batch incomplete → not-Verified. Partial approval can
    # only produce an honest red, never a boundary verdict over a reduced set.
    # Committed EARLY (own txn) BEFORE the probe loop — a batch that crashes
    # mid-loop is still readable as incomplete (4c.1).
    write_manifest(tenant_id, batch_id, test_id,
                   coord.current_data_recipe_ids(session, test_id), env_key=None)

    probes = []
    for recipe in recipes:
        # Per-probe isolation via a SAVEPOINT: a probe whose execute raises a
        # session-poisoning error, OR whose persist (finalize) fails, rolls back to
        # its savepoint — leaving the shared session clean for the next probe (one
        # probe never aborts the batch). A persist failure leaves NO row for that
        # probe → the batch is incomplete ⇒ Slice 4 reads it not-Verified ⇒
        # re-runnable (D-272), the honest outcome of a mid-batch DB fault.
        try:
            with session.begin_nested():
                try:
                    evidence = execute(
                        recipe, session, environment_id, client,
                        record_sink=record_sink, field_overrides=field_overrides,
                        caller_tier=caller_tier, coordinator=coord)
                except Exception as exc:               # execute failed → errored probe
                    evidence = _synthesize_errored_evidence(recipe, environment_id, exc)
                finalize_run(session, evidence, coordinator=coord,
                             batch_id=batch_id, source=_RUNALL_SOURCE)
        except Exception:                              # persist/posture failed → no row
            _log.warning("run-all: probe %s did not persist (batch %s); the batch is "
                         "incomplete and re-runnable",
                         getattr(recipe, "recipe_id", None), batch_id, exc_info=True)
            continue
        _interpret_and_persist(session, evidence)      # best-effort, its own savepoint
        probes.append(ProbeRun(recipe.recipe_id, evidence.run_id, evidence.outcome))

    return RunAllResult(batch_id=batch_id, ran=True, probes=tuple(probes))


def run_all_recipes_execution_async(
    tenant_id: int,
    test_id: UUID,
    *,
    environment_id: int,
    client=None,
    available_environment: Optional[ExecutionEnvironmentBody] = None,
    coordinator=None,
    session_scope=None,
    caller_tier=None,
    execute_fn=None,
    manifest_writer=None,
) -> RunAllResult:
    """Async run-all (D-277) — same batch semantics as
    :func:`run_all_recipes_execution`, bracketed like
    :func:`run_recipe_execution_async` so no DB connection is held across the
    Salesforce I/O:

      1. **select + prep** (one read bracket): the full applicable set + each
         probe's client/world_plans + the env gate, resolved under the connection;
      2. **execute** holding NO DB connection — per probe, with failure isolation;
      3. **persist + posture + interpret** in a FRESH brief transaction PER PROBE,
         so each probe's row commits on its own (a probe's persist failure never
         touches the others) — the stronger isolation the production path uses.
    """
    from primeqa.execution_engine.stranded_cleanup import StrandedRecordSink

    coord = coordinator or SemanticTransactionCoordinator()
    scope = session_scope or _default_session_scope
    execute = execute_fn or _execute_for_kind
    write_manifest = manifest_writer or _write_batch_manifest
    batch_id = uuid4()

    # 1. select all + prep each — one brief read bracket.
    with scope(tenant_id) as session:
        recipes = _probe_recipes(coord.select_recipes_for_execution(
            session, test_id,
            available_environment=available_environment or _MIN_AVAILABLE_ENV,
            replay_mode="live"))
        # D-300 review-fix B2: the AUTHORED data-probe membership for the
        # manifest, read inside the same bracket (see the sync path's note).
        authored_ids = (coord.current_data_recipe_ids(session, test_id)
                        if recipes else [])
        # env_gate is resolved ONCE for the batch (assumed immutable for its
        # duration — the same as the singular async path, which resolves it once).
        env_gate = _resolve_env_gate(session, environment_id) if recipes else None
        preps = []
        for recipe in recipes:
            try:
                preps.append(_prepare_async_execute(
                    recipe, session, environment_id, client, coordinator=coord))
            except Exception as exc:           # a per-recipe prep failure must NOT
                preps.append(_PrepError(exc))  # abort the batch — defer it to its probe
    if not recipes:
        return RunAllResult(batch_id=batch_id, ran=False, reason="no_eligible_recipes")

    # Slice 4c.0 (C2/D-281) + D-300 review-fix B2: the manifest records the
    # AUTHORED membership (status- + env-blind data recipes), not the filtered
    # selection — a member that never runs marks the batch incomplete →
    # not-Verified (honest red, never a reduced-set boundary verdict).
    # Committed EARLY in its own txn BEFORE the probe loop (the select bracket
    # above already closed), so a mid-loop crash is readable as incomplete.
    write_manifest(tenant_id, batch_id, test_id, authored_ids, env_key=None)

    sink = StrandedRecordSink(tenant_id, environment_id)
    probes = []
    for recipe, prep in zip(recipes, preps):
        # 2. execute — NO DB connection held; per-probe isolation. A prep failure
        #    (bracket 1) or an execute raise both become this probe's errored
        #    evidence — never a batch-wide abort.
        if isinstance(prep, _PrepError):
            evidence = _synthesize_errored_evidence(recipe, environment_id, prep.exc)
        else:
            resolved_client, world_plans, null_asserted = prep
            try:
                evidence = execute(
                    recipe, None, environment_id, resolved_client,
                    record_sink=sink, world_plans=world_plans,
                    env_gate=env_gate, caller_tier=caller_tier,
                    null_asserted_fields=null_asserted)
            except Exception as exc:
                evidence = _synthesize_errored_evidence(recipe, environment_id, exc)
        # 3. persist + posture + interpret — a FRESH brief TX per probe, so a
        #    persist failure rolls back only THIS probe's scope (the others, each
        #    in their own scope, are untouched). A failed persist leaves no row →
        #    incomplete batch ⇒ not-Verified ⇒ re-runnable (D-272).
        try:
            with scope(tenant_id) as session:
                finalize_run(session, evidence, coordinator=coord,
                             batch_id=batch_id, source=_RUNALL_SOURCE)
                _interpret_and_persist(session, evidence)
        except Exception:
            _log.warning("run-all(async): probe %s did not persist (batch %s); the "
                         "batch is incomplete and re-runnable",
                         getattr(recipe, "recipe_id", None), batch_id, exc_info=True)
            continue
        probes.append(ProbeRun(recipe.recipe_id, evidence.run_id, evidence.outcome))

    return RunAllResult(batch_id=batch_id, ran=True, probes=tuple(probes))


# ---------------------------------------------------------------------------
# The run-all ROUTER (D-278, S6 Slice 3.4) — dispatch a claim to the single run
# path or the run-all path (Slice 3.3) by the claim's RECORDED strategy kind.
# READS a recorded kind; never DERIVES one (that inference is §4a, generation-
# time). Additive + DORMANT: today no claim records a kind (recon case A), so
# `_recorded_strategy_kind` is always None and every claim routes to single —
# byte-identical to before. Run-all is reachable ONLY by a claim that
# affirmatively records kind=='bva'; it is never the default and never inferred.
# ---------------------------------------------------------------------------

_ROUTE_SINGLE = "single"
_ROUTE_RUNALL = "runall"


def route_strategy(strategy_kind: Optional[str]) -> str:
    """The routing table — a claim's recorded strategy kind → which run path
    (``"single"`` / ``"runall"``). Pure; fail-loud on a kind we don't understand.

    - ``None`` (NO kind recorded — today's reality; no bva was ever intended) →
      single. The HONEST default for un-kinded claims: run the single path we have
      always run.
    - ``"single"`` → single.
    - ``"bva"`` → run-all (Slice 3.3) — the ONLY way to reach run-all.
    - any other NON-NULL value → **raise** ``ValueError``. A recorded kind we do
      not recognize is a wiring bug, surfaced — silently falling back to single
      would let a claim that was meant to get boundary testing quietly get one
      probe + a green check (the overstate-what-you-know failure ``apply_strategy``
      also refuses). **ABSENT (→single) and UNRECOGNIZED (→raise) are two distinct
      branches** — absent is fine (no bva intended); unrecognized is a fault.
    """
    if strategy_kind is None or strategy_kind == _ROUTE_SINGLE:
        return _ROUTE_SINGLE
    if strategy_kind == "bva":
        return _ROUTE_RUNALL
    raise ValueError(f"unrecognized strategy kind {strategy_kind!r}")


def _recorded_strategy_kind(claim) -> Optional[str]:
    """The claim's RECORDED evaluation-strategy kind, or ``None`` when none is
    recorded. **§4a SEAM (the single point):** today no kind field exists on the
    claim (recon case A — ``ClaimRead`` carries ``claim_kind`` = the assertion
    type, not a strategy kind), so this is always ``None`` → :func:`route_strategy`
    → single. When §4a records the kind on the claim, this reader returns it and
    run-all becomes reachable for ``bva`` claims — a one-line change here. NEVER
    derives the kind from claim shape (that derivation is §4a's, at generation)."""
    if claim is None:
        return None
    return getattr(claim, "strategy_kind", None)


def run_claim_execution_for_tenant(
    tenant_id: int,
    test_id: UUID,
    *,
    environment_id: int,
    available_environment: Optional[ExecutionEnvironmentBody] = None,
    client=None,
    coordinator=None,
    field_overrides=None,
    caller_tier=None,
    session_scope=None,
    single_fn=None,
    runall_fn=None,
):
    """Production sync entry — ROUTE by the claim's recorded strategy kind (D-278,
    Slice 3.4), then run + commit. Opens one tenant connection, resolves the claim's
    current approved version, routes via :func:`route_strategy`, and dispatches to
    the single run path (:func:`run_recipe_execution`) or the run-all path
    (:func:`run_all_recipes_execution`, Slice 3.3) on that session — committing on
    clean exit (boundary A), like :func:`run_recipe_execution_for_tenant`.

    **DORMANT today:** no claim records a kind (recon case A), so this ALWAYS takes
    the single branch — byte-identical to :func:`run_recipe_execution_for_tenant`
    (plus one cheap approved-claim read). Run-all is reached ONLY by a claim that
    records ``kind=='bva'``.

    Returns a :class:`RunPathResult` on the single branch (today, always) or a
    :class:`RunAllResult` on the run-all branch. (Callers that map the result —
    ``trigger_claim_run._map_run_result`` — handle ``RunPathResult`` today; the
    ``RunAllResult`` mapping lands with Slice 4, when run-all becomes reachable.)

    ``session_scope`` / ``single_fn`` / ``runall_fn`` are injectable seams for tests
    (assert WHICH path is selected without a live SF run); the async entry +
    consumer wiring is a Slice-4 companion (it lands with the RunAllResult
    result-handling the consumer will then need).
    """
    from primeqa.execution_engine.stranded_cleanup import StrandedRecordSink

    coord = coordinator or SemanticTransactionCoordinator()
    scope = session_scope or _default_session_scope
    single = single_fn or run_recipe_execution
    runall = runall_fn or run_all_recipes_execution
    sink = StrandedRecordSink(tenant_id, environment_id)

    with scope(tenant_id) as session:
        claim = coord.get_current_approved_claim(session, test_id)
        route = route_strategy(_recorded_strategy_kind(claim))
        if route == _ROUTE_RUNALL:
            return runall(
                session, test_id, environment_id=environment_id,
                available_environment=available_environment, client=client,
                coordinator=coord, record_sink=sink,
                field_overrides=field_overrides, caller_tier=caller_tier,
                tenant_id=tenant_id)        # 4c.0: for the batch manifest's own txn
        return single(
            session, test_id, environment_id=environment_id,
            available_environment=available_environment, client=client,
            coordinator=coord, record_sink=sink,
            field_overrides=field_overrides, caller_tier=caller_tier)


def async_run_claim_execution_for_tenant(
    tenant_id: int,
    test_id: UUID,
    *,
    environment_id: int,
    client=None,
    coordinator=None,
    session_scope=None,
    single_fn=None,
    runall_fn=None,
):
    """Async production entry — ROUTE by the claim's recorded strategy kind (D-284,
    Slice 4e), the ASYNC counterpart of the 3.4 sync router
    :func:`run_claim_execution_for_tenant`. Resolves the claim's recorded kind in a
    brief read (REUSING :func:`route_strategy` + :func:`_recorded_strategy_kind` —
    the ONE place single-vs-bva is decided, no second copy), then dispatches to the
    async single path (:func:`run_recipe_execution_async`) or the async run-all path
    (:func:`run_all_recipes_execution_async`, Slice 3.3). Each async fn opens its
    OWN brief brackets, so the dispatch runs AFTER the kind-resolve scope closes.

    **DORMANT today:** no claim records a kind (recon case A), so this ALWAYS routes
    single — byte-identical to calling :func:`run_recipe_execution_async` directly
    (plus one cheap approved-claim read, the same resolution the async run already
    does internally). Run-all is reached ONLY by a claim recording ``kind=='bva'``
    (the single §4a SEAM — ``_recorded_strategy_kind``, always None today). This is
    the HARD PREREQUISITE for §4a (4f): an async-enqueued bva claim reaches run-all
    HERE instead of crashing the consumer (which would read ``.evidence`` on a
    :class:`RunAllResult`).

    Returns a :class:`RunPathResult` on the single branch (today, always) or a
    :class:`RunAllResult` on the run-all branch. ``session_scope`` / ``single_fn`` /
    ``runall_fn`` are injectable seams for tests (assert WHICH path is selected
    without a live SF run)."""
    coord = coordinator or SemanticTransactionCoordinator()
    scope = session_scope or _default_session_scope
    single = single_fn or run_recipe_execution_async
    runall = runall_fn or run_all_recipes_execution_async

    with scope(tenant_id) as session:
        claim = coord.get_current_approved_claim(session, test_id)
        route = route_strategy(_recorded_strategy_kind(claim))

    if route == _ROUTE_RUNALL:
        return runall(tenant_id, test_id,
                      environment_id=environment_id, client=client)
    return single(tenant_id, test_id,
                  environment_id=environment_id, client=client)
