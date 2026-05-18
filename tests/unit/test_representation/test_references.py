"""Tests for substrate-2 reference types.

Per SPEC §4.7.3 + D-058 §5 + D-060 §4.7.6. Covers:

  - Construction + serialization round-trips for all three ref
    types (LogicalRef, PinnedRef, IdentityBearingRef).
  - isinstance dispatch — the IdentityBearingRef subclass
    relationship is the Coordinator's hook into "is this slot
    identity-bearing?" per D-060.
  - JSON-shape equality between PinnedRef and IdentityBearingRef
    (the subclass exists for Python-side type discipline only;
    the on-the-wire shape is identical to PinnedRef).
  - Discriminated-union dispatch — wire payloads with
    ref_kind="pinned" land as PinnedRef; ref_kind="logical" as
    LogicalRef. IdentityBearingRef is intentionally NOT a union
    participant.
  - Frozen-model invariant — references can't be mutated in place
    per D-061 §7.2.
  - is_identity_bearing() helper — explicit predicate.
"""
from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from primeqa.test_representation.models.references import (
    IdentityBearingRef,
    LogicalRef,
    OperationalRef,
    PinnedRef,
    is_identity_bearing,
)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestConstruction:
    def test_logical_ref_minimal(self) -> None:
        ref = LogicalRef(entity_type="Object", external_id="Account")
        assert ref.ref_kind == "logical"
        assert ref.entity_type == "Object"
        assert ref.external_id == "Account"

    def test_pinned_ref_minimal(self) -> None:
        eid = uuid4()
        ref = PinnedRef(
            entity_type="Object",
            entity_id=eid,
            version_seq=7,
            external_id="Account",
        )
        assert ref.ref_kind == "pinned"
        assert ref.entity_id == eid
        assert ref.version_seq == 7

    def test_identity_bearing_ref_minimal(self) -> None:
        eid = uuid4()
        ref = IdentityBearingRef(
            entity_type="Object",
            entity_id=eid,
            version_seq=7,
            external_id="Account",
        )
        assert ref.ref_kind == "pinned"
        assert ref.entity_id == eid

    def test_logical_ref_rejects_extras(self) -> None:
        """extra='forbid' so typos surface at construction time."""
        with pytest.raises(ValidationError):
            LogicalRef(  # type: ignore[call-arg]
                entity_type="Object", external_id="Account",
                version_seq=1,  # extra
            )

    def test_pinned_ref_rejects_extras(self) -> None:
        with pytest.raises(ValidationError):
            PinnedRef(  # type: ignore[call-arg]
                entity_type="Object",
                entity_id=uuid4(),
                version_seq=1,
                external_id="Account",
                unexpected="x",
            )


# ---------------------------------------------------------------------------
# isinstance + subclass discipline (the IdentityBearingRef pattern)
# ---------------------------------------------------------------------------

class TestSubclassDiscipline:
    def _eid(self) -> UUID:
        return uuid4()

    def test_identity_bearing_is_pinned(self) -> None:
        ib = IdentityBearingRef(
            entity_type="Object", entity_id=self._eid(),
            version_seq=1, external_id="Account",
        )
        assert isinstance(ib, PinnedRef)
        assert isinstance(ib, IdentityBearingRef)

    def test_pinned_is_not_identity_bearing(self) -> None:
        p = PinnedRef(
            entity_type="Object", entity_id=self._eid(),
            version_seq=1, external_id="Account",
        )
        assert isinstance(p, PinnedRef)
        assert not isinstance(p, IdentityBearingRef)

    def test_logical_is_not_pinned(self) -> None:
        l = LogicalRef(entity_type="Object", external_id="Account")
        assert not isinstance(l, PinnedRef)
        assert not isinstance(l, IdentityBearingRef)

    def test_is_identity_bearing_helper(self) -> None:
        eid = self._eid()
        p = PinnedRef(
            entity_type="Object", entity_id=eid,
            version_seq=1, external_id="Account",
        )
        ib = IdentityBearingRef(
            entity_type="Object", entity_id=eid,
            version_seq=1, external_id="Account",
        )
        l = LogicalRef(entity_type="Object", external_id="Account")
        assert is_identity_bearing(ib) is True
        assert is_identity_bearing(p) is False
        assert is_identity_bearing(l) is False
        assert is_identity_bearing("not a ref") is False
        assert is_identity_bearing(None) is False


# ---------------------------------------------------------------------------
# JSON shape — PinnedRef and IdentityBearingRef serialize identically
# ---------------------------------------------------------------------------

class TestSerializationShape:
    def test_pinned_and_identity_bearing_have_identical_json(self) -> None:
        """The subclass is a Python-side distinction only. Wire
        shape must be identical so persisting + reading goes
        through the same path."""
        eid = uuid4()
        kwargs = dict(
            entity_type="Object",
            entity_id=eid,
            version_seq=3,
            external_id="Account",
        )
        p = PinnedRef(**kwargs)
        ib = IdentityBearingRef(**kwargs)
        assert p.model_dump() == ib.model_dump()
        assert p.model_dump(mode="json") == ib.model_dump(mode="json")
        assert p.model_dump_json() == ib.model_dump_json()

    def test_logical_ref_serialization(self) -> None:
        ref = LogicalRef(entity_type="Object", external_id="Account")
        dumped = ref.model_dump()
        assert dumped == {
            "ref_kind": "logical",
            "entity_type": "Object",
            "external_id": "Account",
        }

    def test_pinned_ref_serialization(self) -> None:
        eid = uuid4()
        ref = PinnedRef(
            entity_type="Object", entity_id=eid,
            version_seq=3, external_id="Account",
        )
        dumped = ref.model_dump(mode="json")
        assert dumped == {
            "ref_kind": "pinned",
            "entity_type": "Object",
            "entity_id": str(eid),
            "version_seq": 3,
            "external_id": "Account",
        }


# ---------------------------------------------------------------------------
# Discriminated union — OperationalRef
# ---------------------------------------------------------------------------

class TestDiscriminatedUnion:
    @pytest.fixture
    def adapter(self) -> TypeAdapter:
        return TypeAdapter(OperationalRef)

    def test_pinned_payload_lands_as_pinned_ref(self, adapter) -> None:
        eid = uuid4()
        result = adapter.validate_python({
            "ref_kind": "pinned",
            "entity_type": "Object",
            "entity_id": str(eid),
            "version_seq": 3,
            "external_id": "Account",
        })
        assert type(result) is PinnedRef
        assert result.entity_id == eid

    def test_logical_payload_lands_as_logical_ref(self, adapter) -> None:
        result = adapter.validate_python({
            "ref_kind": "logical",
            "entity_type": "Object",
            "external_id": "Account",
        })
        assert type(result) is LogicalRef

    def test_identity_bearing_payload_deserializes_as_pinned(
        self, adapter,
    ) -> None:
        """IdentityBearingRef payloads share ref_kind="pinned" with
        PinnedRef. The discriminated union has only PinnedRef +
        LogicalRef; an IdentityBearingRef on the wire deserializes
        as a plain PinnedRef. The Coordinator promotes it back to
        IdentityBearingRef at slot-validation time per D-060
        §4.7.6 — not via the union."""
        eid = uuid4()
        ib = IdentityBearingRef(
            entity_type="Object", entity_id=eid,
            version_seq=3, external_id="Account",
        )
        result = adapter.validate_python(ib.model_dump(mode="json"))
        assert type(result) is PinnedRef
        assert not isinstance(result, IdentityBearingRef)

    def test_unknown_discriminator_rejected(self, adapter) -> None:
        with pytest.raises(ValidationError):
            adapter.validate_python({
                "ref_kind": "mystery",
                "entity_type": "Object",
                "external_id": "Account",
            })

    def test_missing_discriminator_rejected(self, adapter) -> None:
        with pytest.raises(ValidationError):
            adapter.validate_python({
                "entity_type": "Object",
                "external_id": "Account",
            })


# ---------------------------------------------------------------------------
# Coordinator-side promotion (plain PinnedRef -> IdentityBearingRef)
# ---------------------------------------------------------------------------

class TestPromotion:
    def test_construct_identity_bearing_from_pinned_dict(self) -> None:
        """Coordinator path: wire deserializes as PinnedRef, slot
        validation passes, Coordinator wraps as IdentityBearingRef
        using the PinnedRef's fields. Demonstrates that the
        promotion is a one-line construction."""
        eid = uuid4()
        p = PinnedRef(
            entity_type="Object", entity_id=eid,
            version_seq=3, external_id="Account",
        )
        ib = IdentityBearingRef(**p.model_dump())
        assert isinstance(ib, IdentityBearingRef)
        assert ib.entity_id == eid
        assert ib.model_dump() == p.model_dump()


# ---------------------------------------------------------------------------
# Frozen invariant (D-061 §7.2 — references are immutable)
# ---------------------------------------------------------------------------

class TestFrozenInvariant:
    def test_logical_ref_is_frozen(self) -> None:
        ref = LogicalRef(entity_type="Object", external_id="Account")
        with pytest.raises(ValidationError):
            ref.external_id = "Other"  # type: ignore[misc]

    def test_pinned_ref_is_frozen(self) -> None:
        ref = PinnedRef(
            entity_type="Object", entity_id=uuid4(),
            version_seq=1, external_id="Account",
        )
        with pytest.raises(ValidationError):
            ref.version_seq = 2  # type: ignore[misc]

    def test_identity_bearing_ref_is_frozen(self) -> None:
        ref = IdentityBearingRef(
            entity_type="Object", entity_id=uuid4(),
            version_seq=1, external_id="Account",
        )
        with pytest.raises(ValidationError):
            ref.external_id = "Other"  # type: ignore[misc]
