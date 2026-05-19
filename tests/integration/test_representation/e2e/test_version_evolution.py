"""End-to-end version-evolution scenarios.

Track E category β. Each scenario walks multi-version claim
histories — drafts, approvals, hash-preserving S8 evolution,
S3 no-ops. Demonstrates the current-vs-current-approved
distinction and the no-autonomous-semantic-divergence
governance commitment.
"""
from __future__ import annotations

from uuid import uuid4

from primeqa.test_representation import (
    IdentityBearingRef,
    LiteralValue,
    SemanticTransactionCoordinator,
    TestProvenance,
    ValueClaimBody,
)

from .._builders import empty_conditions, ib, make_value_claim


def _value_claim(subject: IdentityBearingRef, value: str) -> ValueClaimBody:
    return ValueClaimBody(
        subject=subject,
        expected_value=LiteralValue(value=value),
    )


# ---------------------------------------------------------------------------
# β-1: Hash-changing edit preserves prior approval
# ---------------------------------------------------------------------------


def test_hash_changing_edit_preserves_prior_approval(session) -> None:
    """Human writes claim v1 → promotes to approved. Writes v2
    with different content (hash changes; v2 lands draft per
    D-061 §6.3.9 Rule 2).
    :meth:`get_current_approved_claim` walks past the v2 draft
    to find the still-approved v1; :meth:`get_latest_claim`
    surfaces the v2 draft. The two reads correctly express the
    'current vs current-approved' distinction.
    """
    coord = SemanticTransactionCoordinator()
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

    # Hash-changing edit → v2 lands draft.
    body_v2 = _value_claim(body_v1.subject, value="Finance")
    r2 = coord.write_claim(
        session,
        actor="human", test_id=r1.test_id,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body_v2, semantic_conditions=empty_conditions(),
    )
    assert r2.version_seq == 2
    assert r2.status == "draft"
    assert r2.identity_hash != r1.identity_hash

    # current-approved walks past the v2 draft to find v1.
    approved = coord.get_current_approved_claim(session, r1.test_id)
    assert approved is not None
    assert approved.version_seq == 1
    assert approved.status == "approved"

    # current-valid is the v2 draft.
    latest = coord.get_latest_claim(session, r1.test_id)
    assert latest is not None
    assert latest.version_seq == 2
    assert latest.status == "draft"


# ---------------------------------------------------------------------------
# β-2: S8 hash-preserving evolution inherits approval
# ---------------------------------------------------------------------------


def test_s8_hash_preserving_evolution_inherits_approval(session) -> None:
    """Human writes v1 referencing entity X@version_seq=5,
    promotes. S8 writes v2 with the same body content (same
    canonical form → same identity_hash). Per write_claim's
    authority logic: S8 + prior + hash-unchanged → success,
    status preserved. v1 closes; v2 is the new current
    approved version.

    Note: the C1-B canonicalization handles version_seq drift on
    references natively — same entity_id at different
    version_seqs produces the same hash. This scenario uses
    identical bodies to avoid coupling to the C1-B test fixture
    setup; the principle exercised is "S8 hash-preserving →
    approval transfers."
    """
    coord = SemanticTransactionCoordinator()
    body = make_value_claim(value="Tech")
    r1 = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body, semantic_conditions=empty_conditions(),
    )
    coord.promote_claim_to_approved(
        session, actor="human",
        test_id=r1.test_id, version_seq=1,
    )

    # S8 writes the SAME body content → same hash. Authority
    # passes (hash-preserving operational evolution); status
    # preserved per the D-061 rule.
    r2 = coord.write_claim(
        session,
        actor="s8", test_id=r1.test_id,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body, semantic_conditions=empty_conditions(),
    )
    assert r2.version_seq == 2
    assert r2.status == "approved"  # preserved
    assert r2.identity_hash == r1.identity_hash

    # current-approved returns v2 (the new approved version);
    # v1 is closed.
    approved = coord.get_current_approved_claim(session, r1.test_id)
    assert approved.version_seq == 2

    v1 = coord.get_claim_version(session, r1.test_id, 1)
    assert v1.valid_to is not None  # closed
    v2 = coord.get_claim_version(session, r1.test_id, 2)
    assert v2.valid_to is None      # current


# ---------------------------------------------------------------------------
# β-3: S3 same-hash regeneration is no-op
# ---------------------------------------------------------------------------


def test_s3_same_hash_regeneration_no_op(session) -> None:
    """S3 writes claim → draft. Human approves. S3 regenerates
    with identical canonical form. write_claim returns
    ``was_noop=True``; no new version row; no new provenance
    event. The §7.7 no-op skip in production workflow."""
    coord = SemanticTransactionCoordinator()
    body = make_value_claim()
    r_s3 = coord.write_claim(
        session,
        actor="s3", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body, semantic_conditions=empty_conditions(),
    )
    coord.promote_claim_to_approved(
        session, actor="human",
        test_id=r_s3.test_id, version_seq=1,
    )

    # Snapshot state before the S3 re-write.
    from sqlalchemy import text
    rows_before = session.execute(text(
        "SELECT COUNT(*) FROM test_claims WHERE test_id = :t"
    ), {"t": str(r_s3.test_id)}).scalar()
    events_before = session.execute(text(
        "SELECT COUNT(*) FROM test_provenance "
        "WHERE claim_test_id = :t"
    ), {"t": str(r_s3.test_id)}).scalar()

    # S3 regenerates with the same body → same hash → no-op.
    r_s3_again = coord.write_claim(
        session,
        actor="s3", test_id=r_s3.test_id,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body, semantic_conditions=empty_conditions(),
    )
    assert r_s3_again.was_noop is True
    assert r_s3_again.version_seq == 1
    assert r_s3_again.status == "approved"  # current state preserved

    # No new rows, no new events.
    rows_after = session.execute(text(
        "SELECT COUNT(*) FROM test_claims WHERE test_id = :t"
    ), {"t": str(r_s3.test_id)}).scalar()
    events_after = session.execute(text(
        "SELECT COUNT(*) FROM test_provenance "
        "WHERE claim_test_id = :t"
    ), {"t": str(r_s3.test_id)}).scalar()
    assert rows_after == rows_before
    assert events_after == events_before


# ---------------------------------------------------------------------------
# β-4: Complex version chain with multiple actors
# ---------------------------------------------------------------------------


def test_complex_version_chain_with_multiple_actors(session) -> None:
    """Human writes v1 → human promotes → S8 writes v2 (hash
    unchanged) → human writes v3 (hash changed) → human
    promotes v3. Verify all three versions are retrievable,
    current-approved resolves correctly, and provenance has
    the expected event sequence.
    """
    coord = SemanticTransactionCoordinator()
    body_a = make_value_claim(value="Tech")
    # v1 — human authors.
    r1 = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body_a, semantic_conditions=empty_conditions(),
    )
    # human promotes v1 to approved.
    coord.promote_claim_to_approved(
        session, actor="human",
        test_id=r1.test_id, version_seq=1,
    )

    # v2 — S8 hash-preserving rewrite. Status preserves.
    r2 = coord.write_claim(
        session,
        actor="s8", test_id=r1.test_id,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body_a, semantic_conditions=empty_conditions(),
    )
    assert r2.status == "approved"

    # v3 — human hash-changing edit.
    body_b = _value_claim(body_a.subject, value="Finance")
    r3 = coord.write_claim(
        session,
        actor="human", test_id=r1.test_id,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body_b, semantic_conditions=empty_conditions(),
    )
    assert r3.status == "draft"

    # human promotes v3.
    coord.promote_claim_to_approved(
        session, actor="human",
        test_id=r1.test_id, version_seq=3,
    )

    # current-approved now resolves to v3.
    approved = coord.get_current_approved_claim(session, r1.test_id)
    assert approved.version_seq == 3

    # All three versions retrievable individually.
    for vs in (1, 2, 3):
        row = coord.get_claim_version(session, r1.test_id, vs)
        assert row is not None
        assert row.version_seq == vs

    # Provenance has: claim_created (v1), claim_approved (v1),
    # claim_edited (v2 — S8 hash-preserving), claim_edited (v3 —
    # human hash-changing), claim_approved (v3).
    events = session.query(TestProvenance).filter(
        TestProvenance.claim_test_id == r1.test_id,
    ).order_by(TestProvenance.event_at).all()
    kinds = [e.event_kind for e in events]
    assert kinds == [
        "claim_created",
        "claim_approved",
        "claim_edited",
        "claim_edited",
        "claim_approved",
    ]
