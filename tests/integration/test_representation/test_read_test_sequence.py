"""Tests for ``_read_test_sequence`` — the lean ordered walk-the-plan read.

The requirement-workspace strip (claim + run pages) and the live-chips poll
both position a test inside its requirement's live plan via this read, so its
contract is: membership + ordering EXACTLY mirror ``_read_claims`` (the
requirement page's plan) — ``generated_from`` links only, deprecated excluded,
sorted archetype → claim_kind → test_id — with none of the recipe/label cost.
"""
from __future__ import annotations

from primeqa.intelligence.s3_generation_console import (
    _read_claims,
    _read_test_sequence,
)
from primeqa.test_representation import SemanticTransactionCoordinator

from ._builders import empty_conditions, make_value_claim


def _linked_claim(session, coord, key, value="Tech"):
    r = coord.write_claim(
        session, actor="s3", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=make_value_claim(value=value),
        semantic_conditions=empty_conditions())
    coord.link_requirement(
        session, actor="s3", test_id=r.test_id,
        external_system="jira", external_key=key, link_kind="generated_from")
    return r


def test_sequence_orders_and_scopes_to_key(session) -> None:
    coord = SemanticTransactionCoordinator()
    a = _linked_claim(session, coord, "SEQ-1", value="A")
    b = _linked_claim(session, coord, "SEQ-1", value="B")
    _linked_claim(session, coord, "SEQ-2", value="other-key")
    session.flush()

    seq = _read_test_sequence(session, "SEQ-1")
    ids = [t["test_id"] for t in seq]
    assert sorted([str(a.test_id), str(b.test_id)]) == ids
    assert all(t["status"] == "draft" for t in seq)
    assert str(a.test_id) in ids and str(b.test_id) in ids


def test_sequence_excludes_deprecated(session) -> None:
    coord = SemanticTransactionCoordinator()
    keep = _linked_claim(session, coord, "SEQ-3", value="keep")
    gone = _linked_claim(session, coord, "SEQ-3", value="gone")
    coord.deprecate_claim(
        session, actor="human", test_id=gone.test_id,
        version_seq=gone.version_seq, reason="superseded in test")
    session.flush()

    ids = [t["test_id"] for t in _read_test_sequence(session, "SEQ-3")]
    assert ids == [str(keep.test_id)]


def test_sequence_mirrors_read_claims_order(session) -> None:
    # The strip's position N of M must be the requirement page's row N — the
    # two reads must agree on membership AND order.
    coord = SemanticTransactionCoordinator()
    for v in ("one", "two", "three"):
        _linked_claim(session, coord, "SEQ-4", value=v)
    session.flush()

    seq_ids = [t["test_id"] for t in _read_test_sequence(session, "SEQ-4")]
    page_ids = [c["test_id"] for c in _read_claims(session, "SEQ-4")]
    assert seq_ids == page_ids
