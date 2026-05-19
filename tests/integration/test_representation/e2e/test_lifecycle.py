"""End-to-end lifecycle scenarios.

Track E category α. Each scenario walks a single test from
creation through retirement, exercising the Coordinator's full
surface in realistic compositions. Assertions land at each
significant step so a regression surfaces with a clear pointer
to the broken transition.

The substrate's flush-on-write contract makes explicit
``session.commit()`` calls unnecessary in these tests: each
Coordinator method ``session.flush()``-es before returning, so
subsequent reads / writes within the same session see the
prior method's effects. The per-test transactional rollback at
teardown cleans up regardless.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from primeqa.test_representation import (
    AuthorityViolationError,
    SemanticTransactionCoordinator,
    TestProvenance,
)

from .._builders import (
    build_minimal_data_mutation_trigger_body,
    build_minimal_data_recipe_body,
    build_minimal_execution_environment_body,
    empty_conditions,
    ib,
    make_value_claim,
)
from .._fixtures import (
    arrange_test_in_failing_state,
)


# ---------------------------------------------------------------------------
# α-1: Full happy-path test authoring
# ---------------------------------------------------------------------------


def test_full_happy_path_test_authoring(session) -> None:
    """Author writes a claim, promotes it, writes two recipes,
    promotes both, S4 reports passing runs — every read returns
    the expected composition outcome."""
    coord = SemanticTransactionCoordinator()

    # Step 1: human writes claim (draft).
    body = make_value_claim()
    claim_write = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body, semantic_conditions=empty_conditions(),
    )
    assert claim_write.status == "draft"

    # Step 2: human promotes the claim.
    promoted_claim = coord.promote_claim_to_approved(
        session, actor="human",
        test_id=claim_write.test_id, version_seq=1,
    )
    assert promoted_claim.status == "approved"

    # Step 3: write two recipes (lands as generated_unapproved each).
    recipe_writes = []
    for _ in range(2):
        recipe_write = coord.write_recipe(
            session,
            actor="human", recipe_id=None,
            claim_test_id=claim_write.test_id,
            trigger_kind="data-mutation-trigger",
            recipe_kind="data-recipe",
            causal_initiation=build_minimal_data_mutation_trigger_body(),
            observation_realization=build_minimal_data_recipe_body(),
            execution_environment=build_minimal_execution_environment_body(),
        )
        assert recipe_write.status == "generated_unapproved"
        recipe_writes.append(recipe_write)

    # Step 4: human promotes each recipe.
    for rw in recipe_writes:
        promoted_recipe = coord.promote_recipe_to_approved(
            session, actor="human",
            recipe_id=rw.recipe_id, version_seq=1,
        )
        assert promoted_recipe.status == "approved"

    # Step 5: S4 reports passing runs on both recipes.
    for rw in recipe_writes:
        coord.report_run_outcome(
            session,
            actor="s4",
            recipe_id=rw.recipe_id,
            last_run_id=uuid4(),
            last_run_at=datetime.now(timezone.utc),
            last_run_outcome="passed",
            last_run_recipe_version_seq=1,
        )

    # Step 6: composed reads all return the expected state.
    assert coord.get_test_runtime_status(
        session, claim_write.test_id,
    ) == "passing"

    approved = coord.get_current_approved_claim(
        session, claim_write.test_id,
    )
    assert approved is not None
    assert approved.version_seq == 1
    assert approved.status == "approved"

    active = coord.list_active_recipes(session, claim_write.test_id)
    assert len(active) == 2
    assert {r.recipe_id for r in active} == {
        rw.recipe_id for rw in recipe_writes
    }


# ---------------------------------------------------------------------------
# α-2: Failing test recovery workflow
# ---------------------------------------------------------------------------


def test_failing_test_recovery_workflow(session) -> None:
    """Approved test → failing run → deprecate failing recipe →
    write replacement → promote → passing run. Verifies the
    full recovery loop including provenance trail."""
    coord = SemanticTransactionCoordinator()
    test_id, failing_recipe_id = arrange_test_in_failing_state(
        session, coord,
    )

    # The setup leaves us in 'failing' state.
    assert coord.get_test_runtime_status(session, test_id) == "failing"

    # Human deprecates the failing recipe.
    deprecated = coord.deprecate_recipe(
        session, actor="human",
        recipe_id=failing_recipe_id, version_seq=1,
        reason="root cause: selector drift; superseded by new recipe",
    )
    assert deprecated.status == "deprecated"

    # With the failing recipe deprecated, no eligible recipes
    # remain → status reverts to 'untested'.
    assert coord.get_test_runtime_status(session, test_id) == "untested"

    # Author writes a replacement recipe (lands generated_unapproved).
    replacement = coord.write_recipe(
        session,
        actor="human", recipe_id=None,
        claim_test_id=test_id,
        trigger_kind="data-mutation-trigger",
        recipe_kind="data-recipe",
        causal_initiation=build_minimal_data_mutation_trigger_body(),
        observation_realization=build_minimal_data_recipe_body(),
        execution_environment=build_minimal_execution_environment_body(),
    )
    # Still untested — replacement not yet approved.
    assert coord.get_test_runtime_status(session, test_id) == "untested"

    # Promote the replacement.
    coord.promote_recipe_to_approved(
        session, actor="human",
        recipe_id=replacement.recipe_id, version_seq=1,
    )

    # S4 reports passing run on the replacement.
    coord.report_run_outcome(
        session,
        actor="s4",
        recipe_id=replacement.recipe_id,
        last_run_id=uuid4(),
        last_run_at=datetime.now(timezone.utc),
        last_run_outcome="passed",
        last_run_recipe_version_seq=1,
    )

    # Test status recovers to 'passing'.
    assert coord.get_test_runtime_status(session, test_id) == "passing"

    # Provenance trail captures the explicit workflow steps. The
    # arrange_test_in_failing_state fixture uses direct-SQL
    # UPDATEs for the approved-status setup (its docstring flags
    # this as the escape-hatch path), so those state changes
    # don't produce provenance events — only the Coordinator
    # method calls in this test do.
    events = session.query(TestProvenance).filter(
        (TestProvenance.claim_test_id == test_id)
        | (TestProvenance.recipe_id == failing_recipe_id)
        | (TestProvenance.recipe_id == replacement.recipe_id),
    ).all()
    event_kinds = sorted(e.event_kind for e in events)
    # Expected events (Coordinator-emitted only):
    #   claim_created (write_claim)
    #   recipe_created (failing recipe, write_recipe)
    #   recipe_deprecated (this test's deprecate_recipe)
    #   recipe_created (replacement, write_recipe)
    #   recipe_approved (replacement, promote_recipe_to_approved)
    assert "recipe_deprecated" in event_kinds
    assert "recipe_approved" in event_kinds
    assert event_kinds.count("recipe_created") == 2
    # The deprecate event was for the FAILING recipe; the approve
    # event was for the REPLACEMENT recipe. Verify by recipe_id.
    by_recipe = {(e.recipe_id, e.event_kind) for e in events}
    assert (failing_recipe_id, "recipe_deprecated") in by_recipe
    assert (replacement.recipe_id, "recipe_approved") in by_recipe


# ---------------------------------------------------------------------------
# α-3: Test obsolescence
# ---------------------------------------------------------------------------


def test_test_obsolescence(session) -> None:
    """Approved test with passing recipes → human deprecates the
    claim. Runtime status reverts to 'untested' because no
    approved claim resolves; recipe runtime state rows still
    exist (deprecation is claim-level)."""
    coord = SemanticTransactionCoordinator()
    test_id, recipe_id = arrange_test_in_failing_state(session, coord)
    # Flip the recipe outcome to passed so we start from 'passing'.
    coord.report_run_outcome(
        session, actor="s4", recipe_id=recipe_id,
        last_run_id=uuid4(),
        last_run_at=datetime.now(timezone.utc),
        last_run_outcome="passed",
        last_run_recipe_version_seq=1,
    )
    assert coord.get_test_runtime_status(session, test_id) == "passing"

    # Human deprecates the claim.
    coord.deprecate_claim(
        session, actor="human",
        test_id=test_id, version_seq=1,
        reason="superseded by new claim covering the same assertion",
    )

    # With no approved claim, runtime resolution → untested.
    assert coord.get_test_runtime_status(session, test_id) == "untested"

    # The recipe's raw runtime state row STILL EXISTS — deprecation
    # of the claim doesn't sweep recipe data. S6 / S8 can still
    # inspect it for historical context.
    recipe_state = coord.get_recipe_runtime_state(session, recipe_id)
    assert recipe_state is not None
    assert recipe_state.last_run_outcome == "passed"


# ---------------------------------------------------------------------------
# α-4: Intermediate authority failure does not corrupt state
# ---------------------------------------------------------------------------


def test_intermediate_authority_failure_does_not_corrupt_state(
    session,
) -> None:
    """Approved test → S8 attempts a hash-changing write (rejected
    by D-061 §7.2's no-autonomous-semantic-divergence rule).
    Verify: no orphan version, no orphan provenance event, no
    partial coverage. The substrate's storage stays clean across
    authority failures. Retry with correct actor succeeds."""
    coord = SemanticTransactionCoordinator()

    # Human writes claim + promotes to approved.
    body_v1 = make_value_claim(value="Tech")
    r1 = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body_v1, semantic_conditions=empty_conditions(),
    )
    coord.promote_claim_to_approved(
        session, actor="human",
        test_id=r1.test_id, version_seq=1,
    )

    # Snapshot pre-failure state.
    from sqlalchemy import text as _t
    claim_count_before = session.execute(_t(
        "SELECT COUNT(*) FROM test_claims WHERE test_id = :t"
    ), {"t": str(r1.test_id)}).scalar()
    provenance_count_before = session.execute(_t(
        "SELECT COUNT(*) FROM test_provenance "
        "WHERE claim_test_id = :t"
    ), {"t": str(r1.test_id)}).scalar()

    # S8 attempts a hash-changing edit (semantic divergence) — must
    # be rejected.
    body_v2 = make_value_claim(subject=body_v1.subject, value="Finance")
    with pytest.raises(AuthorityViolationError) as exc_info:
        coord.write_claim(
            session,
            actor="s8", test_id=r1.test_id,
            archetype="data_behavior", claim_kind="value-claim",
            asserted_truth=body_v2,
            semantic_conditions=empty_conditions(),
        )
    assert "autonomous" in str(exc_info.value).lower()

    # No state changes after the rejection.
    claim_count_after = session.execute(_t(
        "SELECT COUNT(*) FROM test_claims WHERE test_id = :t"
    ), {"t": str(r1.test_id)}).scalar()
    provenance_count_after = session.execute(_t(
        "SELECT COUNT(*) FROM test_provenance "
        "WHERE claim_test_id = :t"
    ), {"t": str(r1.test_id)}).scalar()
    assert claim_count_after == claim_count_before
    assert provenance_count_after == provenance_count_before

    # Retry with the correct actor succeeds.
    r2 = coord.write_claim(
        session,
        actor="human", test_id=r1.test_id,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body_v2,
        semantic_conditions=empty_conditions(),
    )
    assert r2.version_seq == 2
    assert r2.status == "draft"
