"""D-228 — the multi-recipe vertical (MR-1): ONE verified prohibition claim,
TWO persisted realizations, with selection + governance composing over both.

Real S1 grounding + the real S2 Coordinator + the unified persistence
transaction over seeded local PG, driven through the runtime with a scripted
propose -> emit. The verified Lead negative (ISBLANK formula derives) persists
the behavioral data-recipe as primary (priority 0) AND the inspection
re-verify as the fallback secondary (priority -10). Asserts the replaceability
invariant end-to-end:

  - the console approve loop promotes the claim AND both recipes;
  - select_recipe_for_execution picks the behavioral primary on a full env
    (both auth capabilities) and the inspection secondary on a metadata-only
    env — same claim, env-appropriate realization;
  - the D-223 enqueue gate passes (>=1 executable realization);
  - identity dedup on a verbatim re-emit mints NO additional recipes (the
    equivalent claim keeps its 2-recipe set).
"""
from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from primeqa.generation.persistence import LedgerPersister
from primeqa.test_representation import (
    AuthAssumption,
    ExecutionEnvironmentBody,
    SemanticTransactionCoordinator,
)

from .conftest import (
    FakeTurn, FakeToolTurn, TEST_TENANT_ID, intent, make_request, propose_turn,
)

FULL_ENV = ExecutionEnvironmentBody(auth_assumptions=[
    AuthAssumption(auth_kind="metadata_api_user"),
    AuthAssumption(auth_kind="data_api_user"),
])
METADATA_ONLY_ENV = ExecutionEnvironmentBody(auth_assumptions=[
    AuthAssumption(auth_kind="metadata_api_user"),
])


def _verified_negative():
    # "Lead" carries the VR with the derivable ISBLANK formula (conftest seed).
    return intent(claim_kind="prohibition-claim", polarity="negative",
                  sf_api_name="Lead")


def _emit_draft_turn() -> FakeTurn:
    return FakeTurn([{"type": "tool_use", "id": f"tu_{uuid4().hex[:6]}",
                      "name": "emit_outcome",
                      "input": {"outcome_kind": "draft",
                                "payload": {"admissibility_layer": "layer_2"}}}])


def _emit_verified_negative(seeded):
    """Drive the verified Lead negative through the runtime + persister.
    Returns the claim test_id."""
    from primeqa.semantic.connection import get_tenant_connection
    from primeqa.semantic.query import SemanticOrgModel
    from primeqa.generation.governance_core import GovernanceCore
    from primeqa.generation.runtime import GenerationRuntime

    req = make_request(s1_version_seq=seeded["v1"])
    turns = [propose_turn(_verified_negative()), _emit_draft_turn()]
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        gov = GovernanceCore(SemanticOrgModel(conn))
        result = GenerationRuntime().run(
            request=req, seam=gov, tool_turn_fn=FakeToolTurn(turns),
            persister=LedgerPersister(TEST_TENANT_ID))
    outcome = result.results[0].outcome
    if outcome.claims_written:
        return UUID(str(outcome.claims_written[0].test_id))
    return UUID(str(outcome.equivalent_existing[0]))


def _approve(test_id) -> dict:
    """The console approve loop (claim + all non-deprecated current recipes)
    on a committed transaction."""
    from primeqa.semantic.connection import get_tenant_connection
    from primeqa.intelligence.s4_execution_console import _approve_claim
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        session = Session(bind=conn)
        try:
            out = _approve_claim(session, test_id)
            session.flush()
        finally:
            session.close()
    return out


def _recipe_rows():
    from primeqa.semantic.connection import get_tenant_connection
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        return conn.execute(text(
            "SELECT recipe_id, recipe_kind, trigger_kind, priority, status "
            "FROM test_recipes WHERE valid_to IS NULL ORDER BY priority DESC"
        )).mappings().all()


def test_approve_promotes_claim_and_both_recipes(seeded):
    test_id = _emit_verified_negative(seeded)
    rows = _recipe_rows()
    assert [r["status"] for r in rows] == ["generated_unapproved"] * 2

    out = _approve(test_id)
    assert out["ok"] is True
    assert out["recipes_approved"] == 2

    rows = _recipe_rows()
    assert len(rows) == 2
    assert all(r["status"] == "approved" for r in rows)


def test_selection_picks_env_appropriate_realization(seeded):
    test_id = _emit_verified_negative(seeded)
    _approve(test_id)

    from primeqa.semantic.connection import get_tenant_connection
    coord = SemanticTransactionCoordinator()
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        session = Session(bind=conn)
        try:
            # Full env (the production default, run.py _MIN_AVAILABLE_ENV
            # shape): the behavioral primary wins on priority.
            chosen = coord.select_recipe_for_execution(
                session, test_id, available_environment=FULL_ENV)
            assert chosen is not None
            assert chosen.recipe_kind == "data-recipe"
            assert chosen.priority == 0

            # Metadata-only env: the behavioral recipe's data_api_user
            # assumption is unsatisfied -> the inspection fallback is the
            # SAME claim's selected realization.
            fallback = coord.select_recipe_for_execution(
                session, test_id, available_environment=METADATA_ONLY_ENV)
            assert fallback is not None
            assert fallback.recipe_kind == "metadata-recipe"
            assert fallback.priority == -10
            assert fallback.recipe_id != chosen.recipe_id
        finally:
            session.close()


def test_enqueue_gate_passes_on_multi_recipe_claim(seeded):
    test_id = _emit_verified_negative(seeded)
    _approve(test_id)

    from primeqa.semantic.connection import get_tenant_connection
    from primeqa.execution_engine.executability import gate_enqueue
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        session = Session(bind=conn)
        try:
            gate_enqueue(session, test_id)   # raises on unexecutable
        finally:
            session.close()


def test_dedup_reemit_keeps_the_two_recipe_set(seeded):
    first_id = _emit_verified_negative(seeded)
    second_id = _emit_verified_negative(seeded)
    assert first_id == second_id            # same-hash no-op resolved the claim
    rows = _recipe_rows()
    assert len(rows) == 2                   # nothing re-minted
    assert [r["priority"] for r in rows] == [0, -10]
