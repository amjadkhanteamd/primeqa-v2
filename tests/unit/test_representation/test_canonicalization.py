"""Tests for the canonicalization core.

Per SPEC §6.3.2 (strict rules) + §6.3.3 (reference
canonicalization) + §6.3.4 (array semantics) + §6.3.5
(body_schema_version exclusion) + D-059.

Covers:
  - :func:`canonical_serialize` rules: dict key ordering, no
    whitespace, lowercase null/true/false, integer canonical form,
    nested round-trip, empty arrays / objects, UTF-8 strings.
  - :func:`canonicalize` generic walk: IdentityBearingRef stripping
    to ``{entity_id, entity_type}``, dict default key sort,
    SET-marked array sorting, ORDERED preservation,
    body_schema_version excluded, defensive PinnedRef/LogicalRef
    rejection.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from primeqa.test_representation import (
    ArraySemantics,
    BodyBase,
    IdentityBearingRef,
    LiteralValue,
    LogicalRef,
    PinnedRef,
    StateDescriptor,
    SubstrateError,
    ValueClaimBody,
    canonical_serialize,
    canonicalize,
)


def _ib(name: str = "Account.Industry", entity_id: UUID = None) -> IdentityBearingRef:
    return IdentityBearingRef(
        entity_type="Field",
        entity_id=entity_id or UUID("00000000-0000-0000-0000-000000000001"),
        version_seq=1,
        external_id=name,
    )


# ---------------------------------------------------------------------------
# canonical_serialize
# ---------------------------------------------------------------------------

class TestCanonicalSerializeRules:
    def test_dict_keys_alphabetical(self) -> None:
        # Insert in non-alphabetical order; output must be sorted.
        s = canonical_serialize({"b": 1, "a": 2, "c": 3})
        assert s == '{"a":2,"b":1,"c":3}'

    def test_no_whitespace(self) -> None:
        s = canonical_serialize({"a": [1, 2, {"b": 3}]})
        assert " " not in s
        assert "\n" not in s
        assert "\t" not in s

    def test_null_lowercase(self) -> None:
        assert canonical_serialize(None) == "null"
        assert canonical_serialize({"x": None}) == '{"x":null}'

    def test_booleans_lowercase(self) -> None:
        assert canonical_serialize(True) == "true"
        assert canonical_serialize(False) == "false"
        assert canonical_serialize({"x": True, "y": False}) == '{"x":true,"y":false}'

    def test_integers_canonical(self) -> None:
        assert canonical_serialize(0) == "0"
        assert canonical_serialize(42) == "42"
        assert canonical_serialize(-7) == "-7"
        # No trailing .0:
        assert canonical_serialize(100) == "100"

    def test_empty_array(self) -> None:
        assert canonical_serialize([]) == "[]"

    def test_empty_object(self) -> None:
        assert canonical_serialize({}) == "{}"

    def test_nested_structures(self) -> None:
        value = {
            "outer": [
                {"a": 1, "b": [True, None]},
                {"c": "x"},
            ],
            "key": "value",
        }
        out = canonical_serialize(value)
        # The "outer" list preserves its element order; each dict
        # gets alphabetical key sort.
        assert out == (
            '{"key":"value","outer":'
            '[{"a":1,"b":[true,null]},{"c":"x"}]}'
        )

    def test_unicode_passthrough(self) -> None:
        # ensure_ascii=False: UTF-8 strings serialize verbatim.
        s = canonical_serialize({"name": "Açaí"})
        assert s == '{"name":"Açaí"}'

    def test_nan_inf_rejected(self) -> None:
        # canonical_serialize uses allow_nan=False — NaN/Inf raise
        # ValueError. Floats are also rejected upstream by
        # canonicalize, but defence-in-depth at the serialization
        # layer matters because hand-built canonical-form dicts
        # could include them.
        with pytest.raises(ValueError):
            canonical_serialize(float("nan"))
        with pytest.raises(ValueError):
            canonical_serialize(float("inf"))


# ---------------------------------------------------------------------------
# canonicalize: IdentityBearingRef handling
# ---------------------------------------------------------------------------

class TestCanonicalizeIdentityBearingRef:
    def test_ref_strips_to_entity_id_and_type(self) -> None:
        body = ValueClaimBody(
            subject=_ib(),
            expected_value=LiteralValue(value="x"),
        )
        out = canonicalize(body)
        assert out["subject"] == {
            "entity_id": "00000000-0000-0000-0000-000000000001",
            "entity_type": "Field",
        }
        # external_id and version_seq stripped per D-059 §6.3.3.
        assert "external_id" not in out["subject"]
        assert "version_seq" not in out["subject"]
        assert "ref_kind" not in out["subject"]

    def test_canonical_form_excludes_body_schema_version(self) -> None:
        body = ValueClaimBody(
            subject=_ib(),
            expected_value=LiteralValue(value="x"),
        )
        out = canonicalize(body)
        # §6.3.5: body_schema_version excluded.
        assert "body_schema_version" not in out

    def test_canonical_form_includes_kind(self) -> None:
        """``kind`` is part of the body's identity; it stays in
        the canonical form (and appears redundantly at the top
        level of the hash input, but that's deterministic and
        intentional)."""
        body = ValueClaimBody(
            subject=_ib(),
            expected_value=LiteralValue(value="x"),
        )
        out = canonicalize(body)
        assert out["kind"] == "value-claim"


# ---------------------------------------------------------------------------
# Test fixtures for generic-walk introspection
# ---------------------------------------------------------------------------

class _OrderedListBody(BodyBase):
    """Test fixture: a body with an ORDERED-marked list field."""

    model_config = ConfigDict(extra="forbid", frozen=False)
    body_schema_version: Literal[1] = 1
    kind: Literal["_test_ordered"] = "_test_ordered"
    items: Annotated[list[str], ArraySemantics.ORDERED] = []


class _SetListBody(BodyBase):
    """Test fixture: a body with a SET-marked list field."""

    model_config = ConfigDict(extra="forbid", frozen=False)
    body_schema_version: Literal[1] = 1
    kind: Literal["_test_set"] = "_test_set"
    items: Annotated[list[str], ArraySemantics.SET] = []


# ---------------------------------------------------------------------------
# canonicalize: array semantics
# ---------------------------------------------------------------------------

class TestCanonicalizeArraySemantics:
    def test_ordered_preserves_order(self) -> None:
        body = _OrderedListBody(items=["c", "a", "b"])
        out = canonicalize(body)
        assert out["items"] == ["c", "a", "b"]

    def test_set_sorts_items(self) -> None:
        body = _SetListBody(items=["c", "a", "b"])
        out = canonicalize(body)
        # Items sort by their canonical-JSON serialization (which
        # for plain strings = the JSON-quoted string).
        assert out["items"] == ["a", "b", "c"]

    def test_set_with_mixed_string_content_sorts_stably(self) -> None:
        body = _SetListBody(items=["B", "a", "10", "2"])
        out = canonicalize(body)
        # Sort by canonical JSON: '"10" < "2" < "B" < "a"' under
        # UTF-8 lexicographic comparison of the JSON-quoted form.
        # The quotes themselves are equal, so this is just
        # character-wise comparison.
        assert out["items"] == sorted(["B", "a", "10", "2"])

    def test_set_duplicates_preserved(self) -> None:
        """SET semantics sort but don't deduplicate. The body
        author is responsible for unique content (or the
        Coordinator enforces uniqueness at write time)."""
        body = _SetListBody(items=["a", "a", "b"])
        out = canonicalize(body)
        # Sorted but duplicates retained.
        assert out["items"] == ["a", "a", "b"]


# ---------------------------------------------------------------------------
# canonicalize: dict handling
# ---------------------------------------------------------------------------

class TestCanonicalizeDicts:
    def test_dict_inside_any_value_keys_sorted(self) -> None:
        """``LiteralValue.value: Any`` may hold a dict — default
        rule is alphabetical key sort."""
        body = ValueClaimBody(
            subject=_ib(),
            expected_value=LiteralValue(
                value={"z": 1, "a": 2, "m": 3},
            ),
        )
        out = canonicalize(body)
        keys = list(out["expected_value"]["value"].keys())
        assert keys == ["a", "m", "z"]

    def test_nested_dicts_recursive_sort(self) -> None:
        body = ValueClaimBody(
            subject=_ib(),
            expected_value=LiteralValue(
                value={"outer": {"z": 1, "a": {"q": 1, "b": 2}}},
            ),
        )
        out = canonicalize(body)
        outer = out["expected_value"]["value"]["outer"]
        assert list(outer.keys()) == ["a", "z"]
        assert list(outer["a"].keys()) == ["b", "q"]

    def test_state_descriptor_field_values_sorted_by_default(self) -> None:
        """Generic walk for :class:`StateDescriptor` (when used
        outside :class:`StateTransitionClaimBody`) sorts keys
        alphabetically. The custom canonicalizer overrides this
        for state-transition bodies."""
        sd = StateDescriptor(field_values={
            "Z.Field": LiteralValue(value=1),
            "A.Field": LiteralValue(value=2),
            "M.Field": LiteralValue(value=3),
        })
        # Wrap into a body for canonicalize entry — use a
        # ValueClaimBody won't accept StateDescriptor; we'll call
        # the generic walk indirectly by serializing a model that
        # contains the StateDescriptor. Easier: invoke
        # _generic_canonicalize_body on the StateDescriptor directly.
        from primeqa.test_representation.canonicalization import (
            _generic_canonicalize_body,
        )
        out = _generic_canonicalize_body(sd)
        keys = list(out["field_values"].keys())
        assert keys == ["A.Field", "M.Field", "Z.Field"]


# ---------------------------------------------------------------------------
# canonicalize: defensive ref-type rejection
# ---------------------------------------------------------------------------

class TestCanonicalizeDefensiveRefRejection:
    def test_plain_pinned_ref_rejected_in_identity_bearing_content(
        self,
    ) -> None:
        """Identity-bearing content should never contain a plain
        PinnedRef (only IdentityBearingRef). If somehow one ends
        up there, the canonicalizer fails loudly rather than
        silently producing a wrong hash."""
        from primeqa.test_representation.canonicalization import (
            _canonicalize_value,
        )

        plain_pinned = PinnedRef(
            entity_type="Field",
            entity_id=UUID("00000000-0000-0000-0000-000000000099"),
            version_seq=1,
            external_id="Account.Industry",
        )
        with pytest.raises(SubstrateError) as exc_info:
            _canonicalize_value(plain_pinned, None, "_Test", "path")
        assert "PinnedRef" in str(exc_info.value)

    def test_logical_ref_rejected_in_identity_bearing_content(self) -> None:
        from primeqa.test_representation.canonicalization import (
            _canonicalize_value,
        )

        logical = LogicalRef(
            entity_type="Field", external_id="Account.Industry",
        )
        with pytest.raises(SubstrateError) as exc_info:
            _canonicalize_value(logical, None, "_Test", "path")
        assert "LogicalRef" in str(exc_info.value)


# ---------------------------------------------------------------------------
# canonicalize: scalar passthrough
# ---------------------------------------------------------------------------

class TestCanonicalizeScalarPassthrough:
    @pytest.mark.parametrize("value", [
        "string", 42, 0, -7, True, False, None,
    ])
    def test_scalars_passthrough(self, value: Any) -> None:
        """LiteralValue carries scalar values; canonicalize emits
        them verbatim."""
        body = ValueClaimBody(
            subject=_ib(),
            expected_value=LiteralValue(value=value),
        )
        out = canonicalize(body)
        assert out["expected_value"]["value"] == value


# ---------------------------------------------------------------------------
# canonicalize entry-point semantics
# ---------------------------------------------------------------------------

class TestCanonicalizeEntryPoint:
    def test_canonicalize_returns_dict(self) -> None:
        body = ValueClaimBody(
            subject=_ib(),
            expected_value=LiteralValue(value="x"),
        )
        out = canonicalize(body)
        assert isinstance(out, dict)

    def test_canonical_serialize_is_deterministic(self) -> None:
        body = ValueClaimBody(
            subject=_ib(),
            expected_value=LiteralValue(value="x"),
        )
        s1 = canonical_serialize(canonicalize(body))
        s2 = canonical_serialize(canonicalize(body))
        assert s1 == s2

    def test_canonical_form_serializes_round_trip(self) -> None:
        """The output of :func:`canonicalize` is pure dict/list/
        scalar — :func:`canonical_serialize` can always serialize
        it without raising."""
        body = ValueClaimBody(
            subject=_ib(),
            expected_value=LiteralValue(value={"x": [1, 2, 3]}),
        )
        s = canonical_serialize(canonicalize(body))
        assert isinstance(s, str)
        assert len(s) > 0
