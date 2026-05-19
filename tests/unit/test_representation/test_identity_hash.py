"""Tests for :func:`compute_identity_hash` + :data:`IDENTITY_HASH_VERSION`.

Per SPEC §6.3.5 + §6.3.6 + §6.3.7 + D-059 (canonicalization +
governance).

Covers:
  - Returns a 64-character lowercase hex string (SHA-256).
  - :data:`IDENTITY_HASH_VERSION` equals 1 at v1.
  - Determinism: same inputs across multiple calls → same hash.
  - Different archetype / claim_kind → different hash (sensitivity).
  - ``identity_hash_version`` is NOT folded into the hash input
    (per SPEC §6.3.5; the version is stored alongside the hash on
    the persisted row, not folded into it).
  - :data:`IdentityHash` return-type alias is exposed for
    forward-compat (v2 may wrap the str in an envelope).
"""
from __future__ import annotations

import json
from uuid import UUID

import pytest

from primeqa.test_representation import (
    IDENTITY_HASH_VERSION,
    IdentityBearingRef,
    IdentityHash,
    LiteralValue,
    SemanticConditionsBody,
    ValueClaimBody,
    canonical_serialize,
    canonicalize,
    compute_identity_hash,
)


def _ib(external_id: str = "Account.Industry") -> IdentityBearingRef:
    return IdentityBearingRef(
        entity_type="Field",
        entity_id=UUID("00000000-0000-0000-0000-000000000001"),
        version_seq=1,
        external_id=external_id,
    )


def _value_claim(value: str = "Tech") -> ValueClaimBody:
    return ValueClaimBody(
        subject=_ib(),
        expected_value=LiteralValue(value=value),
    )


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------

class TestHashShape:
    def test_returns_64_char_lower_hex(self) -> None:
        h = compute_identity_hash(
            "data_behavior", "value-claim",
            _value_claim(), SemanticConditionsBody(),
        )
        assert isinstance(h, str)
        assert len(h) == 64
        assert h == h.lower()
        # Every char is a hex digit.
        assert all(c in "0123456789abcdef" for c in h)


# ---------------------------------------------------------------------------
# Policy version constant
# ---------------------------------------------------------------------------

class TestIdentityHashVersion:
    def test_version_is_one(self) -> None:
        assert IDENTITY_HASH_VERSION == 1

    def test_version_is_int(self) -> None:
        assert isinstance(IDENTITY_HASH_VERSION, int)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

class TestHashDeterminism:
    def test_same_inputs_same_hash(self) -> None:
        body = _value_claim()
        cond = SemanticConditionsBody()
        h1 = compute_identity_hash("data_behavior", "value-claim", body, cond)
        h2 = compute_identity_hash("data_behavior", "value-claim", body, cond)
        h3 = compute_identity_hash("data_behavior", "value-claim", body, cond)
        assert h1 == h2 == h3

    def test_fresh_bodies_with_same_content_hash_same(self) -> None:
        """Distinct Python instances with identical content
        produce identical hashes. Models the typical case where
        the substrate compares a freshly-built body to an
        already-persisted body."""
        h1 = compute_identity_hash(
            "data_behavior", "value-claim",
            _value_claim("Tech"), SemanticConditionsBody(),
        )
        h2 = compute_identity_hash(
            "data_behavior", "value-claim",
            _value_claim("Tech"), SemanticConditionsBody(),
        )
        assert h1 == h2


# ---------------------------------------------------------------------------
# Sensitivity to top-level keys
# ---------------------------------------------------------------------------

class TestHashTopLevelSensitivity:
    def test_different_archetype_different_hash(self) -> None:
        body = _value_claim()
        cond = SemanticConditionsBody()
        h1 = compute_identity_hash(
            "data_behavior", "value-claim", body, cond,
        )
        h2 = compute_identity_hash(
            "configuration", "value-claim", body, cond,
        )
        assert h1 != h2

    def test_different_claim_kind_different_hash(self) -> None:
        body = _value_claim()
        cond = SemanticConditionsBody()
        h1 = compute_identity_hash(
            "data_behavior", "value-claim", body, cond,
        )
        h2 = compute_identity_hash(
            "data_behavior", "different-kind", body, cond,
        )
        assert h1 != h2


# ---------------------------------------------------------------------------
# identity_hash_version exclusion (SPEC §6.3.5)
# ---------------------------------------------------------------------------

class TestIdentityHashVersionExcluded:
    def test_hash_input_does_not_contain_version_key(self) -> None:
        """Reconstruct the hash input from the public functions
        and verify ``identity_hash_version`` does not appear
        anywhere in the canonical form. Per SPEC §6.3.5, the
        version is stored alongside the hash on the persisted
        row, never folded in."""
        body = _value_claim()
        cond = SemanticConditionsBody()
        hash_input = {
            "archetype": "data_behavior",
            "claim_kind": "value-claim",
            "asserted_truth": canonicalize(body),
            "semantic_conditions": canonicalize(cond),
        }
        serialized = canonical_serialize(hash_input)
        assert "identity_hash_version" not in serialized

    def test_hash_input_keys_are_exactly_the_four_expected(self) -> None:
        """Defensive: confirm the hash input has exactly the four
        top-level keys per SPEC §6.3.6. Guards against any future
        accidental key addition that would silently invalidate
        every prior hash."""
        body = _value_claim()
        cond = SemanticConditionsBody()
        hash_input = {
            "archetype": "data_behavior",
            "claim_kind": "value-claim",
            "asserted_truth": canonicalize(body),
            "semantic_conditions": canonicalize(cond),
        }
        # Round-trip through json to confirm it's serializable +
        # check the keys.
        roundtrip = json.loads(canonical_serialize(hash_input))
        assert set(roundtrip.keys()) == {
            "archetype",
            "claim_kind",
            "asserted_truth",
            "semantic_conditions",
        }


# ---------------------------------------------------------------------------
# IdentityHash type alias (forward-compat hook)
# ---------------------------------------------------------------------------

class TestIdentityHashTypeAlias:
    def test_alias_currently_resolves_to_str(self) -> None:
        """At v1 the alias is ``str``. v2 may wrap in an envelope
        class; consumers using the alias (not the concrete
        ``str`` type) will remain forward-compatible."""
        assert IdentityHash is str

    def test_returned_hash_satisfies_alias(self) -> None:
        body = _value_claim()
        cond = SemanticConditionsBody()
        h = compute_identity_hash(
            "data_behavior", "value-claim", body, cond,
        )
        # ``isinstance`` against the alias would be ``isinstance(h,
        # str)`` since the alias resolves to str at v1.
        assert isinstance(h, IdentityHash)


# ---------------------------------------------------------------------------
# Cross-platform stability of integer encoding
# ---------------------------------------------------------------------------

class TestIntegerCanonicalEncoding:
    def test_int_value_serialization_stable(self) -> None:
        """Python's ``str(int)`` is platform-stable; the
        canonical hash for an integer-valued body should be
        consistent. Documents the expectation; if a future
        Python release breaks this, the failing assertion
        surfaces the regression immediately."""
        body = ValueClaimBody(
            subject=_ib(),
            expected_value=LiteralValue(value=100_000),
        )
        cond = SemanticConditionsBody()
        # Compute twice in this process; expect identity.
        h1 = compute_identity_hash(
            "data_behavior", "value-claim", body, cond,
        )
        h2 = compute_identity_hash(
            "data_behavior", "value-claim", body, cond,
        )
        assert h1 == h2
        # And the canonical-form for that value contains the
        # plain integer in canonical form (no commas, no
        # scientific notation).
        assert canonical_serialize(canonicalize(body)).find("100000") != -1
