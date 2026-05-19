"""End-to-end happy-path tests for ``SemanticTransactionCoordinator.write_claim``.

Per Track D-β.2. Each test exercises the full 11-step orchestration
against a real PostgreSQL test DB:
  - Pydantic body → JSONB serialization round-trip.
  - Canonicalization + identity_hash computation.
  - Coverage extraction → test_claim_coverage rows.
  - Provenance event → test_provenance row.

Parametrized over the four data-behavior claim kinds so a single
write_claim implementation is exercised across every body shape.
"""
from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import text

from primeqa.test_representation import (
    IDENTITY_HASH_VERSION,
    SemanticTransactionCoordinator,
    TestClaim,
    TestClaimCoverage,
    TestProvenance,
)

from ._builders import (
    empty_conditions,
    make_automation_effect,
    make_prohibition,
    make_state_transition,
    make_value_claim,
)


# ---------------------------------------------------------------------------
# Smoke: new value-claim, no prior, human actor
# ---------------------------------------------------------------------------


def test_write_new_value_claim_returns_v1_draft(session) -> None:
    coord = SemanticTransactionCoordinator()
    body = make_value_claim()
    result = coord.write_claim(
        session,
        actor="human",
        test_id=None,
        archetype="data_behavior",
        claim_kind="value-claim",
        asserted_truth=body,
        semantic_conditions=empty_conditions(),
    )
    assert result.version_seq == 1
    assert result.status == "draft"
    assert result.was_noop is False
    assert isinstance(result.test_id, UUID)
    assert len(result.identity_hash) == 64


def test_write_inserts_test_claims_row(session) -> None:
    coord = SemanticTransactionCoordinator()
    body = make_value_claim()
    result = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body, semantic_conditions=empty_conditions(),
    )
    rows = session.query(TestClaim).filter(
        TestClaim.test_id == result.test_id,
    ).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.version_seq == 1
    assert row.archetype == "data_behavior"
    assert row.claim_kind == "value-claim"
    assert row.status == "draft"
    assert row.valid_to is None
    assert row.identity_hash == result.identity_hash
    assert row.identity_hash_version == IDENTITY_HASH_VERSION
    # JSONB round-trip: asserted_truth dict has the universal keys.
    assert row.asserted_truth["body_schema_version"] == 1
    assert row.asserted_truth["kind"] == "value-claim"
    assert row.semantic_conditions["kind"] == "conditions"


def test_write_inserts_coverage_rows(session) -> None:
    coord = SemanticTransactionCoordinator()
    body = make_value_claim()
    result = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body, semantic_conditions=empty_conditions(),
    )
    coverage = session.query(TestClaimCoverage).filter(
        TestClaimCoverage.claim_test_id == result.test_id,
    ).all()
    assert len(coverage) == 1
    cov = coverage[0]
    assert cov.entity_id == body.subject.entity_id
    assert cov.entity_type == "Field"
    assert cov.reference_kind == "subject"


def test_write_inserts_provenance_event(session) -> None:
    coord = SemanticTransactionCoordinator()
    body = make_value_claim()
    result = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body, semantic_conditions=empty_conditions(),
    )
    events = session.query(TestProvenance).filter(
        TestProvenance.claim_test_id == result.test_id,
    ).all()
    assert len(events) == 1
    evt = events[0]
    assert evt.event_kind == "claim_created"
    assert evt.event_actor == "human"
    assert evt.recipe_id is None
    assert evt.event_data["actor"] == "human"
    assert evt.event_data["new_version_seq"] == 1
    assert evt.event_data["prior_version_seq"] is None
    assert evt.event_data["hash_changed"] is None
    assert evt.event_data["new_identity_hash"] == result.identity_hash


# ---------------------------------------------------------------------------
# Each of the four data-behavior claim kinds (parametrized)
# ---------------------------------------------------------------------------


_CLAIM_BUILDERS = [
    ("value-claim", make_value_claim),
    ("state-transition-claim", make_state_transition),
    ("automation-effect-claim", make_automation_effect),
    ("prohibition-claim", make_prohibition),
]


@pytest.mark.parametrize("claim_kind,builder", _CLAIM_BUILDERS)
def test_write_new_claim_each_kind(
    session, claim_kind: str, builder,
) -> None:
    coord = SemanticTransactionCoordinator()
    body = builder()
    result = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind=claim_kind,
        asserted_truth=body, semantic_conditions=empty_conditions(),
    )
    assert result.version_seq == 1
    assert result.was_noop is False
    # The JSONB-stored body is the model_dump(mode="json") form.
    row = session.query(TestClaim).filter(
        TestClaim.test_id == result.test_id,
    ).one()
    assert row.asserted_truth["kind"] == claim_kind


@pytest.mark.parametrize("claim_kind,builder", _CLAIM_BUILDERS)
def test_write_new_claim_produces_provenance(
    session, claim_kind: str, builder,
) -> None:
    coord = SemanticTransactionCoordinator()
    body = builder()
    result = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind=claim_kind,
        asserted_truth=body, semantic_conditions=empty_conditions(),
    )
    evt = session.query(TestProvenance).filter(
        TestProvenance.claim_test_id == result.test_id,
    ).one()
    assert evt.event_kind == "claim_created"


# ---------------------------------------------------------------------------
# Coverage classification: subject + condition kinds both produced
# ---------------------------------------------------------------------------


def test_write_with_conditions_produces_both_coverage_kinds(
    session,
) -> None:
    from primeqa.test_representation import Condition, SemanticConditionsBody

    body = make_value_claim()
    cond = SemanticConditionsBody(conditions=[
        Condition(
            subject=body.subject,  # reused: same entity, different slot
            predicate="is_not_null",
        ),
    ])
    coord = SemanticTransactionCoordinator()
    result = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body, semantic_conditions=cond,
    )
    coverage = session.query(TestClaimCoverage).filter(
        TestClaimCoverage.claim_test_id == result.test_id,
    ).all()
    # Same entity_id in both slots → 2 coverage rows (one per
    # reference_kind).
    kinds = sorted(c.reference_kind for c in coverage)
    assert kinds == ["condition", "subject"]
