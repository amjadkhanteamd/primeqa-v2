"""Unit tests for the 1c enrichment-gate pure helpers (no DB).

The DB-backed carry-forward + red-on-disable + divergence guard are in
tests/integration/semantic/test_enrichment_gate_live.py; here we pin the pure
hashing contract the gate's correctness rides on.
"""
from __future__ import annotations

import pytest

from primeqa.sync.enrichment_gate import (
    embed_input_hash,
    summary_input_signature,
    SUMMARY_ENABLED_ENTITY_TYPES,
)

pytestmark = pytest.mark.unit


class TestEmbedInputHash:
    def test_deterministic(self) -> None:
        assert embed_input_hash("the input", "voyage-3") == embed_input_hash("the input", "voyage-3")

    def test_distinguishes_different_inputs(self) -> None:
        # hash-unchanged ⟺ input-unchanged: a different string MUST differ.
        assert embed_input_hash("foo", "voyage-3") != embed_input_hash("bar", "voyage-3")

    def test_model_tag_changes_the_hash(self) -> None:
        # The PRODUCER is folded in: the same input under a different embedding
        # model MUST differ (so a model bump forces a re-embed, not stale carry).
        assert embed_input_hash("foo", "voyage-3") != embed_input_hash("foo", "voyage-4")

    def test_none_and_empty_are_equal_and_stable(self) -> None:
        assert embed_input_hash(None, "voyage-3") == embed_input_hash("", "voyage-3")

    def test_is_64_hex(self) -> None:
        h = embed_input_hash("x", "voyage-3")
        assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)


class TestSummaryInputSignature:
    def test_key_order_stable(self) -> None:
        # The context is hashed order-independently — two dicts that differ only in
        # key order produce the same signature (so a benign re-ordering never forces
        # a re-summarize).
        assert (summary_input_signature({"a": 1, "b": 2})
                == summary_input_signature({"b": 2, "a": 1}))

    def test_value_sensitive(self) -> None:
        assert summary_input_signature({"a": 1}) != summary_input_signature({"a": 2})

    def test_none_values_preserved(self) -> None:
        # A field going present→None is a real input change (must differ).
        assert summary_input_signature({"a": "x"}) != summary_input_signature({"a": None})


def test_summary_enabled_set_is_flow_and_vr() -> None:
    assert SUMMARY_ENABLED_ENTITY_TYPES == frozenset({"Flow", "ValidationRule"})
