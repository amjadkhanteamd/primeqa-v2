"""Tests for ``get_current_approved_claim`` resolution.

Per Track D-γ.2 + SPEC §7.5 + §6.6. The resolution walks
backward through a test's version history to find the latest
row with ``status='approved'``. Distinct from
:meth:`get_latest_claim`: the current valid version may be
``draft`` (after a hash-changing edit per D-059 §6.3.9 Rule 2),
and this resolution skips drafts AND deprecated versions.

Per SPEC §6.6: deprecation is ORTHOGONAL to supersession. A
deprecated row may still have ``valid_to IS NULL`` (current
valid); this resolution skips it because its status isn't
``approved``, not because of its valid_to state.
"""
from __future__ import annotations

from uuid import uuid4

from primeqa.test_representation import (
    SemanticTransactionCoordinator,
)

from ._builders import empty_conditions, make_value_claim
from ._fixtures import (
    arrange_approved_claim,
    update_claim_status,
)


def test_returns_none_for_unknown_test_id(session) -> None:
    coord = SemanticTransactionCoordinator()
    assert coord.get_current_approved_claim(session, uuid4()) is None


def test_returns_none_when_only_draft_versions_exist(session) -> None:
    """write_claim produces ``status='draft'``. With no
    promote_claim call (or direct UPDATE), no approved version
    exists."""
    coord = SemanticTransactionCoordinator()
    body = make_value_claim()
    result = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body, semantic_conditions=empty_conditions(),
    )
    assert result.status == "draft"
    assert coord.get_current_approved_claim(
        session, result.test_id,
    ) is None


def test_returns_approved_version_when_one_exists(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    read = coord.get_current_approved_claim(session, test_id)
    assert read is not None
    assert read.test_id == test_id
    assert read.status == "approved"
    assert read.version_seq == 1


def test_returns_latest_approved_when_multiple_exist(session) -> None:
    """v1 approved, write v2 (hash change → draft), write v3
    (hash change → draft). The only approved row is v1; the
    resolution returns it (the current valid row is v3 draft)."""
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord, value="Tech")
    # v2 (hash change → draft)
    body_v2 = make_value_claim(value="Finance")
    r2 = coord.write_claim(
        session,
        actor="human", test_id=test_id,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body_v2, semantic_conditions=empty_conditions(),
    )
    assert r2.status == "draft"
    # v3 (hash change → draft)
    body_v3 = make_value_claim(value="Energy")
    r3 = coord.write_claim(
        session,
        actor="human", test_id=test_id,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body_v3, semantic_conditions=empty_conditions(),
    )
    assert r3.status == "draft"

    approved = coord.get_current_approved_claim(session, test_id)
    assert approved is not None
    assert approved.version_seq == 1
    assert approved.status == "approved"


def test_returns_higher_version_when_both_approved(session) -> None:
    """v1 approved, v2 hash-changed (draft), approve v2 too.
    The resolution returns the HIGHEST approved version_seq."""
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord, value="Tech")
    body_v2 = make_value_claim(value="Finance")
    r2 = coord.write_claim(
        session,
        actor="human", test_id=test_id,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body_v2, semantic_conditions=empty_conditions(),
    )
    # Promote v2 to approved via the fixture helper.
    update_claim_status(
        session, test_id=test_id, version_seq=2, new_status="approved",
    )
    approved = coord.get_current_approved_claim(session, test_id)
    assert approved is not None
    assert approved.version_seq == 2


def test_skips_deprecated_current_valid_version(session) -> None:
    """Per SPEC §6.6: deprecating v1 sets ``status='deprecated'``
    but leaves ``valid_to IS NULL``. The resolution skips the
    deprecated row because status != 'approved', regardless of
    valid_to state."""
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    # Manually deprecate the only approved version. After this,
    # no approved row exists.
    update_claim_status(
        session, test_id=test_id, version_seq=1,
        new_status="deprecated",
    )
    assert coord.get_current_approved_claim(session, test_id) is None


def test_walks_past_deprecated_to_find_earlier_approved(
    session,
) -> None:
    """v1 approved, v2 hash-changed (draft), v3 hash-changed (draft);
    then deprecate v3. v1 is still the only approved row; the
    resolution returns it even though deprecation introduced a
    new lifecycle event."""
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord, value="Tech")
    body_v2 = make_value_claim(value="Finance")
    coord.write_claim(
        session,
        actor="human", test_id=test_id,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body_v2, semantic_conditions=empty_conditions(),
    )
    body_v3 = make_value_claim(value="Energy")
    coord.write_claim(
        session,
        actor="human", test_id=test_id,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body_v3, semantic_conditions=empty_conditions(),
    )
    update_claim_status(
        session, test_id=test_id, version_seq=3,
        new_status="deprecated",
    )
    approved = coord.get_current_approved_claim(session, test_id)
    assert approved is not None
    assert approved.version_seq == 1


def test_current_valid_draft_does_not_mask_prior_approval(
    session,
) -> None:
    """Documents the get_latest_claim vs get_current_approved_claim
    contrast: the current valid version is a draft (v2), but
    the approved row is the historical v1."""
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord, value="Tech")
    body_v2 = make_value_claim(value="Finance")
    coord.write_claim(
        session,
        actor="human", test_id=test_id,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body_v2, semantic_conditions=empty_conditions(),
    )
    latest = coord.get_latest_claim(session, test_id)
    approved = coord.get_current_approved_claim(session, test_id)
    assert latest.status == "draft"
    assert latest.version_seq == 2
    assert approved.status == "approved"
    assert approved.version_seq == 1
