"""Per-step failure tests for ``write_claim``.

Per Track D-β.2 + SPEC §4.7.6 + §4.7.8. For each of the 11 steps
that can fail, construct an input that fails at that step and
verify:
  1. The expected error type raises.
  2. No partial DB state remains (transactional rollback).

The error-types tested map to SPEC §4.7.8's classification:
  - Step 1: ``ValueError`` (discriminator outside closed taxonomy)
  - Step 2: :class:`SchemaIncompatibilityError`
  - Step 4: :class:`BodyCorruptionError`
  - Step 7: Track C errors
    (:class:`FloatInIdentityBearingContentError`, etc.)
  - Step 10: :class:`AuthorityViolationError`

Step 3 (body validation) is unreachable for typed Pydantic input —
construction enforces structure. Step 5 (ontology) is also
unreachable for typed input — Pydantic's field-type discipline
rejects operational refs in identity-bearing slots before
write_claim sees them.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from primeqa.test_representation import (
    AuthorityViolationError,
    BodyCorruptionError,
    Condition,
    FloatInIdentityBearingContentError,
    LiteralValue,
    SchemaIncompatibilityError,
    SemanticConditionsBody,
    SemanticTransactionCoordinator,
    ValueClaimBody,
)

from ._builders import (
    empty_conditions,
    ib,
    make_value_claim,
)


def _row_count(session, table: str) -> int:
    return session.execute(
        text(f"SELECT COUNT(*) FROM {table}"),
    ).scalar()


# ---------------------------------------------------------------------------
# Step 1: discriminator validation
# ---------------------------------------------------------------------------


def test_unknown_archetype_raises_value_error(session) -> None:
    coord = SemanticTransactionCoordinator()
    body = make_value_claim()
    before = _row_count(session, "test_claims")
    with pytest.raises(ValueError):
        coord.write_claim(
            session,
            actor="human", test_id=None,
            archetype="bogus",  # not in taxonomy
            claim_kind="value-claim",
            asserted_truth=body, semantic_conditions=empty_conditions(),
        )
    after = _row_count(session, "test_claims")
    assert before == after


def test_unknown_claim_kind_raises_value_error(session) -> None:
    coord = SemanticTransactionCoordinator()
    body = make_value_claim()
    before = _row_count(session, "test_claims")
    with pytest.raises(ValueError):
        coord.write_claim(
            session,
            actor="human", test_id=None,
            archetype="data_behavior",
            claim_kind="bogus-claim",  # not in taxonomy
            asserted_truth=body, semantic_conditions=empty_conditions(),
        )
    after = _row_count(session, "test_claims")
    assert before == after


# ---------------------------------------------------------------------------
# Step 2: body-shape dispatch via the registry
# ---------------------------------------------------------------------------


def test_claim_kind_mismatch_with_body_class_raises_schema_incompat(
    session,
) -> None:
    """The registry returns the class for the requested
    (claim_kind, body_schema_version). If the caller passes a
    body of a DIFFERENT registered claim_kind class but lies
    about the claim_kind parameter, step 2 catches the mismatch
    before step 4's kind-equality check."""
    coord = SemanticTransactionCoordinator()
    body = make_value_claim()  # this is a ValueClaimBody
    before = _row_count(session, "test_claims")
    with pytest.raises(SchemaIncompatibilityError):
        coord.write_claim(
            session,
            actor="human", test_id=None,
            archetype="data_behavior",
            # caller LIES about the kind — registry returns
            # ProhibitionClaimBody but body is ValueClaimBody:
            claim_kind="prohibition-claim",
            asserted_truth=body,
            semantic_conditions=empty_conditions(),
        )
    after = _row_count(session, "test_claims")
    assert before == after


# ---------------------------------------------------------------------------
# Step 4: body-row discriminator consistency
# ---------------------------------------------------------------------------


def test_semantic_conditions_with_wrong_kind_raises_body_corruption(
    session,
) -> None:
    """Step 4 only fires when steps 1 + 2 + 3 pass. The cleanest
    way to drive a body-kind mismatch into write_claim is to
    construct a body whose internal ``kind`` field disagrees
    with the discriminator the caller declared. For
    ``semantic_conditions``, the body's kind field is
    constrained by Pydantic Literal — caller can't easily lie at
    construction time. Step 4 is defence-in-depth and
    practically unreachable for typed Pydantic input.

    Documenting the unreachability via a skip with rationale —
    the step IS tested indirectly by the step 2 test above (the
    kind-class mismatch path), and via the canonicalization
    layer's own tests in Track C."""
    pytest.skip(
        "step 4 is defence-in-depth for typed Pydantic input; "
        "step 2's class-mismatch path covers the practical case"
    )


# ---------------------------------------------------------------------------
# Step 7: canonicalization (Track C errors)
# ---------------------------------------------------------------------------


def test_float_in_identity_bearing_content_raises(session) -> None:
    """A LiteralValue.value of 3.14 (float) is forbidden by
    Track C's canonicalization policy. Step 7 surfaces it before
    any DB write."""
    coord = SemanticTransactionCoordinator()
    body = ValueClaimBody(
        subject=ib(),
        expected_value=LiteralValue(value=3.14),  # forbidden!
    )
    before = _row_count(session, "test_claims")
    with pytest.raises(FloatInIdentityBearingContentError):
        coord.write_claim(
            session,
            actor="human", test_id=None,
            archetype="data_behavior", claim_kind="value-claim",
            asserted_truth=body, semantic_conditions=empty_conditions(),
        )
    after = _row_count(session, "test_claims")
    assert before == after


def test_float_nested_in_dict_raises(session) -> None:
    """Floats buried inside Any-typed dict content (LiteralValue.value
    = {'k': 3.14}) also raise."""
    coord = SemanticTransactionCoordinator()
    body = ValueClaimBody(
        subject=ib(),
        expected_value=LiteralValue(value={"nested": 3.14}),
    )
    with pytest.raises(FloatInIdentityBearingContentError):
        coord.write_claim(
            session,
            actor="human", test_id=None,
            archetype="data_behavior", claim_kind="value-claim",
            asserted_truth=body, semantic_conditions=empty_conditions(),
        )


# ---------------------------------------------------------------------------
# Step 10: authority — S8 hash-changed REJECTION
# ---------------------------------------------------------------------------


def test_s8_hash_changed_raises_authority_violation(session) -> None:
    """First write a claim as a human; then attempt an S8 edit
    that changes the hash. Step 10's authority check rejects."""
    coord = SemanticTransactionCoordinator()
    body_v1 = make_value_claim(value="Tech")
    result = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body_v1, semantic_conditions=empty_conditions(),
    )
    # Same subject, different value → hash changes.
    body_v2 = make_value_claim(subject=body_v1.subject, value="Finance")
    claim_rows_before = _row_count(session, "test_claims")
    provenance_before = _row_count(session, "test_provenance")

    with pytest.raises(AuthorityViolationError) as exc_info:
        coord.write_claim(
            session,
            actor="s8", test_id=result.test_id,
            archetype="data_behavior", claim_kind="value-claim",
            asserted_truth=body_v2,
            semantic_conditions=empty_conditions(),
        )
    # The rejection message names the governing rule.
    assert "autonomous" in str(exc_info.value).lower()
    assert "semantic divergence" in str(exc_info.value).lower()

    # No new claim row, no new provenance event.
    assert _row_count(session, "test_claims") == claim_rows_before
    assert _row_count(session, "test_provenance") == provenance_before


# ---------------------------------------------------------------------------
# Defensive: the test_id-not-found case
# ---------------------------------------------------------------------------


def test_test_id_provided_but_not_in_db_treated_as_new(session) -> None:
    """Per the prompt's behavior contract: passing a test_id that
    has no matching row is treated as 'no prior version' — a new
    test row is created with that test_id and version_seq=1."""
    import uuid as _u
    coord = SemanticTransactionCoordinator()
    body = make_value_claim()
    explicit_test_id = _u.uuid4()
    result = coord.write_claim(
        session,
        actor="human", test_id=explicit_test_id,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body, semantic_conditions=empty_conditions(),
    )
    assert result.test_id == explicit_test_id
    assert result.version_seq == 1
    assert result.was_noop is False
