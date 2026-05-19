"""Plain claim-read tests for ``SemanticTransactionCoordinator``.

Per Track D-γ.1 + SPEC §10.2 + §6.6. Covers:
  - :meth:`get_latest_claim` returns the current valid version
    (``valid_to IS NULL``) or ``None``.
  - :meth:`get_claim_version` returns a specific historical
    version regardless of ``valid_to`` state.
  - JSONB → Pydantic body deserialization round-trips correctly
    across all four data-behavior claim kinds.
  - :class:`SchemaIncompatibilityError` raised when JSONB
    declares an unknown ``(kind, body_schema_version)``.
  - The ``deprecated`` status / ``valid_to`` distinction is
    documented in a dedicated test.
"""
from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from primeqa.test_representation import (
    AutomationEffectClaimBody,
    ClaimRead,
    ProhibitionClaimBody,
    SchemaIncompatibilityError,
    SemanticConditionsBody,
    SemanticTransactionCoordinator,
    StateTransitionClaimBody,
    TestClaim,
    ValueClaimBody,
)

from ._builders import (
    empty_conditions,
    make_automation_effect,
    make_prohibition,
    make_state_transition,
    make_value_claim,
)


# ---------------------------------------------------------------------------
# get_latest_claim
# ---------------------------------------------------------------------------


def test_get_latest_claim_none_for_unknown_test_id(session) -> None:
    coord = SemanticTransactionCoordinator()
    result = coord.get_latest_claim(session, uuid4())
    assert result is None


def test_get_latest_claim_returns_v1(session) -> None:
    coord = SemanticTransactionCoordinator()
    body = make_value_claim(value="Tech")
    written = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body, semantic_conditions=empty_conditions(),
    )
    read = coord.get_latest_claim(session, written.test_id)
    assert read is not None
    assert isinstance(read, ClaimRead)
    assert read.test_id == written.test_id
    assert read.version_seq == 1
    assert read.valid_to is None
    assert read.archetype == "data_behavior"
    assert read.claim_kind == "value-claim"
    assert read.identity_hash == written.identity_hash


def test_get_latest_claim_returns_v2_after_supersession(session) -> None:
    coord = SemanticTransactionCoordinator()
    body_v1 = make_value_claim(value="Tech")
    r1 = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body_v1, semantic_conditions=empty_conditions(),
    )
    body_v2 = make_value_claim(subject=body_v1.subject, value="Finance")
    r2 = coord.write_claim(
        session,
        actor="human", test_id=r1.test_id,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body_v2, semantic_conditions=empty_conditions(),
    )
    read = coord.get_latest_claim(session, r1.test_id)
    assert read is not None
    assert read.version_seq == 2
    assert read.identity_hash == r2.identity_hash
    assert read.valid_to is None


def test_get_latest_claim_deserializes_body_as_pydantic_instance(
    session,
) -> None:
    """The JSONB stored asserted_truth comes back as a typed
    Pydantic body instance, not a raw dict."""
    coord = SemanticTransactionCoordinator()
    body = make_value_claim(value="Tech")
    written = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body, semantic_conditions=empty_conditions(),
    )
    read = coord.get_latest_claim(session, written.test_id)
    assert isinstance(read.asserted_truth, ValueClaimBody)
    assert isinstance(read.semantic_conditions, SemanticConditionsBody)
    # Round-trip equivalence: the deserialized body == the original.
    assert read.asserted_truth == body


def test_get_latest_claim_returns_deprecated_current_version(
    session,
) -> None:
    """``valid_to`` and ``status`` are orthogonal axes per SPEC
    §6.6. A ``deprecated`` claim version still has
    ``valid_to IS NULL`` until a NEW version supersedes it.
    :meth:`get_latest_claim` returns it; consumers check
    ``status`` separately if they care."""
    coord = SemanticTransactionCoordinator()
    body = make_value_claim()
    written = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body, semantic_conditions=empty_conditions(),
    )
    # Manually deprecate v1 (D-γ.2 adds promote/deprecate; for
    # now: direct SQL).
    session.execute(text(
        "UPDATE test_claims SET status = 'deprecated' "
        "WHERE test_id = :t AND version_seq = 1"
    ), {"t": str(written.test_id)})
    session.flush()
    read = coord.get_latest_claim(session, written.test_id)
    assert read is not None
    assert read.status == "deprecated"
    assert read.valid_to is None  # still current despite deprecated


# ---------------------------------------------------------------------------
# get_claim_version
# ---------------------------------------------------------------------------


def test_get_claim_version_none_for_unknown_pair(session) -> None:
    coord = SemanticTransactionCoordinator()
    assert coord.get_claim_version(session, uuid4(), 1) is None


def test_get_claim_version_returns_historical_row(session) -> None:
    """Even after v2 supersedes v1 (v1's ``valid_to`` set),
    :meth:`get_claim_version` can fetch v1 by explicit
    version_seq. Useful for replay + audit + the D-γ.2
    resolution operations."""
    coord = SemanticTransactionCoordinator()
    body_v1 = make_value_claim(value="Tech")
    r1 = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body_v1, semantic_conditions=empty_conditions(),
    )
    body_v2 = make_value_claim(subject=body_v1.subject, value="Finance")
    coord.write_claim(
        session,
        actor="human", test_id=r1.test_id,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body_v2, semantic_conditions=empty_conditions(),
    )
    historical = coord.get_claim_version(session, r1.test_id, 1)
    assert historical is not None
    assert historical.version_seq == 1
    assert historical.valid_to is not None  # closed
    # Hash matches v1's body, not v2's.
    assert historical.identity_hash == r1.identity_hash


def test_get_claim_version_returns_current_via_explicit_pair(
    session,
) -> None:
    """Asking for version_seq=2 of a 2-version test returns the
    current row with ``valid_to IS NULL``."""
    coord = SemanticTransactionCoordinator()
    body = make_value_claim()
    r1 = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body, semantic_conditions=empty_conditions(),
    )
    body_v2 = make_value_claim(subject=body.subject, value="X")
    coord.write_claim(
        session,
        actor="human", test_id=r1.test_id,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body_v2, semantic_conditions=empty_conditions(),
    )
    read = coord.get_claim_version(session, r1.test_id, 2)
    assert read is not None
    assert read.valid_to is None


# ---------------------------------------------------------------------------
# Schema-incompatibility on unknown body schema
# ---------------------------------------------------------------------------


def test_unknown_body_schema_raises_schema_incompatibility(session) -> None:
    """Inject a TestClaim row directly with
    ``body_schema_version=99`` (an unregistered version) in the
    asserted_truth JSONB dict. The read path's
    ``_deserialize_body`` raises
    :class:`SchemaIncompatibilityError` — same contract as the
    write path (D-060 §4.7.8)."""
    coord = SemanticTransactionCoordinator()
    test_id = uuid4()
    row = TestClaim(
        test_id=test_id,
        version_seq=1,
        archetype="data_behavior",
        claim_kind="value-claim",
        asserted_truth={
            "body_schema_version": 99,  # not in registry
            "kind": "value-claim",
        },
        semantic_conditions={
            "body_schema_version": 1,
            "kind": "conditions",
            "conditions": [],
        },
        identity_hash="x" * 64,
        identity_hash_version=1,
        status="draft",
    )
    session.add(row)
    session.flush()
    with pytest.raises(SchemaIncompatibilityError):
        coord.get_latest_claim(session, test_id)


# ---------------------------------------------------------------------------
# Round-trip parametrized over all 4 data-behavior claim kinds
# ---------------------------------------------------------------------------


_CLAIM_FIXTURES = [
    ("value-claim", make_value_claim, ValueClaimBody),
    ("state-transition-claim", make_state_transition,
     StateTransitionClaimBody),
    ("automation-effect-claim", make_automation_effect,
     AutomationEffectClaimBody),
    ("prohibition-claim", make_prohibition, ProhibitionClaimBody),
]


@pytest.mark.parametrize("claim_kind,builder,body_class", _CLAIM_FIXTURES)
def test_round_trip_each_claim_kind(
    session, claim_kind: str, builder, body_class,
) -> None:
    coord = SemanticTransactionCoordinator()
    body = builder()
    written = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind=claim_kind,
        asserted_truth=body, semantic_conditions=empty_conditions(),
    )
    read = coord.get_latest_claim(session, written.test_id)
    assert read is not None
    assert isinstance(read.asserted_truth, body_class)
    assert read.asserted_truth == body
