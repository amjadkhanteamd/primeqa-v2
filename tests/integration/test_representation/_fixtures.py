"""Scaffolding helpers for Track D-γ.2 resolution-operation tests.

The resolution operations (``get_current_approved_claim``,
``get_test_runtime_status``, ``select_recipe_for_execution``)
exercise lifecycle states the Coordinator's write path can't
yet produce on its own at v1:
  - ``status='approved'`` requires a promote_claim method —
    that lands in Track D-ε.
  - ``status='deprecated'`` requires a deprecate method — same.
  - ``test_recipe_runtime_state`` rows are written by S4 via a
    Coordinator callback — that's a Track D-δ method.

These helpers fill the gap with direct DB UPDATE / INSERT so the
D-γ.2 tests can exercise the resolution semantics today. Each
helper carries a docstring noting which D-δ / D-ε Coordinator
method will eventually replace its direct-SQL implementation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional
from uuid import UUID

from sqlalchemy import text

from primeqa.test_representation import (
    ActorKind,
    AuthAssumption,
    ExecutionEnvironmentBody,
    FeatureAssumption,
    InfrastructureAssumption,
    SemanticTransactionCoordinator,
)

from ._builders import (
    build_minimal_data_recipe_body,
    build_minimal_data_mutation_trigger_body,
    build_minimal_execution_environment_body,
    empty_conditions,
    make_value_claim,
)


# ---------------------------------------------------------------------------
# Claim arrangement
# ---------------------------------------------------------------------------


def arrange_approved_claim(
    session,
    coordinator: SemanticTransactionCoordinator,
    *,
    actor: ActorKind = "human",
    claim_kind: str = "value-claim",
    value: str = "Tech",
) -> tuple[UUID, int]:
    """Write a value-claim and promote to ``status='approved'``
    via direct UPDATE.

    Track D-ε will add ``promote_claim`` and this helper will
    delegate to it.

    Returns ``(test_id, version_seq)``.
    """
    if claim_kind != "value-claim":
        raise NotImplementedError(
            "arrange_approved_claim only knows value-claim today; "
            "extend when richer fixtures are needed"
        )
    body = make_value_claim(value=value)
    result = coordinator.write_claim(
        session,
        actor=actor, test_id=None,
        archetype="data_behavior", claim_kind=claim_kind,
        asserted_truth=body, semantic_conditions=empty_conditions(),
    )
    session.execute(text(
        "UPDATE test_claims SET status = 'approved' "
        "WHERE test_id = :t AND version_seq = :v"
    ), {"t": str(result.test_id), "v": result.version_seq})
    session.flush()
    return result.test_id, result.version_seq


def update_claim_status(
    session,
    *,
    test_id: UUID,
    version_seq: int,
    new_status: Literal["draft", "approved", "deprecated"],
) -> None:
    """Update a specific claim version's status via direct
    UPDATE. Track D-ε's promote_claim / deprecate_claim methods
    will replace this."""
    session.execute(text(
        "UPDATE test_claims SET status = :s "
        "WHERE test_id = :t AND version_seq = :v"
    ), {"s": new_status, "t": str(test_id), "v": version_seq})
    session.flush()


# ---------------------------------------------------------------------------
# Recipe arrangement
# ---------------------------------------------------------------------------


def arrange_recipe_with_status(
    session,
    coordinator: SemanticTransactionCoordinator,
    *,
    claim_test_id: UUID,
    status: Literal[
        "active", "approved", "deprecated", "generated_unapproved",
    ] = "active",
    priority: int = 0,
    actor: ActorKind = "human",
    trigger_kind: str = "data-mutation-trigger",
    recipe_kind: str = "data-recipe",
    execution_environment: Optional[ExecutionEnvironmentBody] = None,
) -> tuple[UUID, int]:
    """Write a minimal recipe via Coordinator (lands in
    ``generated_unapproved``) and optionally update status +
    priority via direct UPDATE.

    Track D-ε will add ``promote_recipe`` /
    ``deprecate_recipe``; Track D-δ may add a priority setter.
    This helper combines the operations until those land.

    Returns ``(recipe_id, version_seq)``.
    """
    if trigger_kind != "data-mutation-trigger":
        # Builder coverage for other trigger kinds exists, but
        # the simplest pairing for resolution tests is
        # data-mutation + data-recipe.
        raise NotImplementedError(
            f"arrange_recipe_with_status currently only supports "
            f"trigger_kind='data-mutation-trigger'; got {trigger_kind!r}"
        )
    if recipe_kind != "data-recipe":
        raise NotImplementedError(
            f"arrange_recipe_with_status currently only supports "
            f"recipe_kind='data-recipe'; got {recipe_kind!r}"
        )
    env = execution_environment or build_minimal_execution_environment_body()
    result = coordinator.write_recipe(
        session,
        actor=actor, recipe_id=None,
        claim_test_id=claim_test_id,
        trigger_kind=trigger_kind, recipe_kind=recipe_kind,
        causal_initiation=build_minimal_data_mutation_trigger_body(),
        observation_realization=build_minimal_data_recipe_body(),
        execution_environment=env,
    )
    if status != "generated_unapproved" or priority != 0:
        session.execute(text(
            "UPDATE test_recipes SET status = :s, priority = :p "
            "WHERE recipe_id = :r AND version_seq = :v"
        ), {
            "s": status, "p": priority,
            "r": str(result.recipe_id), "v": result.version_seq,
        })
        session.flush()
    return result.recipe_id, result.version_seq


# ---------------------------------------------------------------------------
# Runtime state arrangement
# ---------------------------------------------------------------------------


def arrange_runtime_state(
    session,
    *,
    recipe_id: UUID,
    outcome: Literal["passed", "failed", "errored", "skipped"],
    last_run_recipe_version_seq: int = 1,
    last_run_at: Optional[datetime] = None,
    last_run_id: Optional[UUID] = None,
) -> None:
    """Insert a ``test_recipe_runtime_state`` row directly.

    Track D-δ's :meth:`SemanticTransactionCoordinator.report_run_outcome`
    is now the production path for runtime-state writes. This
    helper remains as an **escape hatch** for tests that need
    to seed state without going through the Coordinator
    authority check — useful for tests that want a runtime row
    on a recipe whose actor mix would otherwise need careful
    s4-only setup. Production code paths use
    ``report_run_outcome``; tests setting up scenarios use
    this helper for brevity.

    Note per SPEC §8.2: runtime state is keyed by ``recipe_id``
    only (one row per recipe regardless of which version_seq was
    last run). Re-calling this helper with the same recipe_id
    UPSERTs.
    """
    import uuid as _u
    now = last_run_at or datetime.now(timezone.utc)
    run_id = last_run_id or _u.uuid4()
    pass_ts = now if outcome == "passed" else None
    fail_ts = (
        now if outcome in ("failed", "errored") else None
    )
    # UPSERT — test_recipe_runtime_state has recipe_id as its PK,
    # so use ON CONFLICT to support re-arrangement.
    session.execute(text(
        "INSERT INTO test_recipe_runtime_state "
        "(recipe_id, last_run_id, last_run_at, last_run_outcome, "
        " last_run_recipe_version_seq, last_pass_at, "
        " last_failure_at, updated_at) "
        "VALUES (:r, :run, :now, :outcome, :vs, :p, :f, :now) "
        "ON CONFLICT (recipe_id) DO UPDATE SET "
        "  last_run_id = EXCLUDED.last_run_id, "
        "  last_run_at = EXCLUDED.last_run_at, "
        "  last_run_outcome = EXCLUDED.last_run_outcome, "
        "  last_run_recipe_version_seq = "
        "    EXCLUDED.last_run_recipe_version_seq, "
        "  last_pass_at = EXCLUDED.last_pass_at, "
        "  last_failure_at = EXCLUDED.last_failure_at, "
        "  updated_at = EXCLUDED.updated_at"
    ), {
        "r": str(recipe_id),
        "run": str(run_id),
        "now": now,
        "outcome": outcome,
        "vs": last_run_recipe_version_seq,
        "p": pass_ts,
        "f": fail_ts,
    })
    session.flush()


# ---------------------------------------------------------------------------
# Environment builder
# ---------------------------------------------------------------------------


def make_minimal_execution_environment(
    *,
    feature_names: Optional[list[str]] = None,
    org_kind: Optional[
        Literal["sandbox", "production", "scratch_org", "dev_hub"]
    ] = None,
    salesforce_edition: Optional[
        Literal["enterprise", "unlimited", "developer", "performance"]
    ] = None,
    auth_kinds: Optional[list[str]] = None,
    infra_kinds: Optional[list[str]] = None,
) -> ExecutionEnvironmentBody:
    """Build an :class:`ExecutionEnvironmentBody` for tests.

    All features in ``feature_names`` are added as
    ``required=True``. Tests needing negative-assumption coverage
    (``required=False``) construct the body directly rather than
    using this helper.
    """
    return ExecutionEnvironmentBody(
        org_kind=org_kind,
        salesforce_edition=salesforce_edition,
        feature_assumptions=[
            FeatureAssumption(feature_name=name, required=True)
            for name in (feature_names or [])
        ],
        auth_assumptions=[
            AuthAssumption(auth_kind=kind)  # type: ignore[arg-type]
            for kind in (auth_kinds or [])
        ],
        infrastructure_assumptions=[
            InfrastructureAssumption(infra_kind=kind)  # type: ignore[arg-type]
            for kind in (infra_kinds or [])
        ],
    )
