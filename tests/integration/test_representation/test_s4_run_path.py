"""Integration: the S4 run path end-to-end on the governance DB (D-108.4).

Reuses this package's ``session`` fixture (transactional rollback, the migrated
substrate test DB). Seeds a real **approved metadata-inspection recipe** in
``tenant_1`` via the Coordinator (the test_representation builders make
*data*-recipes, so the inspection bodies are written directly with
``author_emission``'s output + an UPDATE to 'approved'), then runs
``run_recipe_execution`` with a **stub client** (the local test DB has no
``environments``/``connections`` for ``resolve_tooling_client``) and asserts the
full chain writes BOTH the ``s4_execution_runs`` row and the
``test_recipe_runtime_state`` row in one transaction.
"""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy import text

from primeqa.execution_engine.result_store import S4ExecutionRun
from primeqa.execution_engine.run import run_recipe_execution
from primeqa.generation.emission import (
    GroundedNegative,
    _Endpoint,
    author_emission,
)
from primeqa.test_representation import SemanticTransactionCoordinator

from ._fixtures import arrange_approved_claim


class _StubClient:
    def __init__(self, rows=None):
        self._rows = list(rows or [])
        self.queries = []

    def query(self, soql):
        self.queries.append(soql)
        return list(self._rows)


def _seed_approved_inspection_recipe(session, coord, *, subject="Lead"):
    """Approved claim + an approved inspection recipe (real emitted bodies)."""
    test_id, _ = arrange_approved_claim(session, coord)
    bundle = author_emission(GroundedNegative(
        archetype="data_behavior", claim_kind="prohibition-claim",
        operation_hint="delete", version_seq=7,
        subject=_Endpoint(entity_id=uuid4(), entity_type="Object", external_id=subject),
        requirement_excerpt=f"Users must not delete a {subject} without a reason."))
    written = coord.write_recipe(
        session, actor="human", recipe_id=None, claim_test_id=test_id,
        trigger_kind="inspection-trigger", recipe_kind="metadata-recipe",
        causal_initiation=bundle.causal_initiation,
        observation_realization=bundle.observation_realization,
        execution_environment=bundle.execution_environment)
    # Promote to 'approved' so select_recipe_for_execution finds it (escape-hatch
    # UPDATE — the resolution path only needs the state, not the call chain).
    session.execute(text(
        "UPDATE test_recipes SET status = 'approved' "
        "WHERE recipe_id = :r AND version_seq = :v"),
        {"r": str(written.recipe_id), "v": written.version_seq})
    session.flush()
    return test_id, written.recipe_id


def test_run_path_writes_both_rows(session):
    coord = SemanticTransactionCoordinator()
    test_id, recipe_id = _seed_approved_inspection_recipe(session, coord, subject="Lead")

    result = run_recipe_execution(
        session, test_id, environment_id=7,
        client=_StubClient(rows=[{"Id": "03d", "ValidationName": "Req"}]),
        coordinator=coord)

    # ran, selected the seeded recipe, grounded outcome.
    assert result.ran is True
    assert result.selected_recipe_id == recipe_id
    assert result.evidence.outcome == "passed"

    # 1) s4_execution_runs row landed.
    run_row = (session.query(S4ExecutionRun)
               .filter(S4ExecutionRun.run_id == result.evidence.run_id).one())
    assert run_row.recipe_id == recipe_id
    assert run_row.outcome == "passed"
    assert "EntityDefinition.QualifiedApiName = 'Lead'" in run_row.evidence["steps"][0]["query"]

    # 2) test_recipe_runtime_state row landed (posture), keyed on the recipe.
    state = coord.get_recipe_runtime_state(session, recipe_id)
    assert state.last_run_id == result.evidence.run_id
    assert state.last_run_outcome == "passed"
    assert state.last_run_at == result.evidence.finished_at


def test_run_path_failed_outcome_persists_and_reports(session):
    coord = SemanticTransactionCoordinator()
    test_id, recipe_id = _seed_approved_inspection_recipe(session, coord, subject="Account")

    result = run_recipe_execution(
        session, test_id, environment_id=7, client=_StubClient(rows=[]),
        coordinator=coord)

    assert result.ran is True
    assert result.evidence.outcome == "failed"
    state = coord.get_recipe_runtime_state(session, recipe_id)
    assert state.last_run_outcome == "failed"
    assert state.last_failure_at == result.evidence.finished_at
    assert state.last_pass_at is None


def test_run_path_no_eligible_recipe(session):
    # An approved claim with NO approved recipe → select returns None → no run.
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)   # claim only, no recipe

    result = run_recipe_execution(
        session, test_id, environment_id=7, client=_StubClient(rows=[{"Id": "1"}]),
        coordinator=coord)

    assert result.ran is False
    assert result.reason == "no_eligible_recipe"
    assert result.evidence is None
    # nothing persisted.
    assert session.query(S4ExecutionRun).count() == 0
