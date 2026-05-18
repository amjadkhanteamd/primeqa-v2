"""Reference type hierarchy.

Per D-058 §5 (reference model) and D-060 §4.7.6 (write-flow
orchestration; identity-bearing references must be pinned).

Two on-the-wire shapes:
  - :class:`LogicalRef` — entity_type + external_id; the reference
    is resolved by name, not pinned to a specific version.
  - :class:`PinnedRef` — entity_type + entity_id + version_seq +
    external_id; the reference is pinned to a specific S1 entity
    row at a specific bitemporal version.

One Python-side distinction:
  - :class:`IdentityBearingRef` — a subclass of :class:`PinnedRef`
    with no field changes. Identical JSON serialization. Carries a
    stricter Python type signature for slots where D-060 requires
    "this reference must be identity-bearing" (the subject of a
    claim, for instance). The Coordinator promotes a plain
    :class:`PinnedRef` to an :class:`IdentityBearingRef` at write
    time once it has confirmed the slot's ontology + pinning
    requirements per D-060 §4.7.6.

:data:`OperationalRef` is the on-the-wire discriminated union over
:class:`PinnedRef` + :class:`LogicalRef` only — :class:`IdentityBearingRef`
is NOT a discriminator participant. The union deserializes a
``"pinned"``-discriminated payload as :class:`PinnedRef`; the
Coordinator decides whether to wrap it as
:class:`IdentityBearingRef` based on the slot. This was empirically
verified during B-α implementation: shared discriminator value +
subclass isinstance dispatch + identical JSON shape all behave as
designed in Pydantic 2.13.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# LogicalRef
# ---------------------------------------------------------------------------

class LogicalRef(BaseModel):
    """A reference resolved by name (``entity_type`` + ``external_id``),
    not pinned to a specific S1 entity-row version.

    Per D-058 §5.2: logical references are appropriate when the
    body should track the current state of the referenced entity
    even as it supersedes (e.g., a recipe references the Object
    "Account" — the recipe stays meaningful when the Account
    entity's metadata changes).

    Distinct from :class:`PinnedRef` via the ``ref_kind``
    discriminator.
    """

    model_config = ConfigDict(
        extra="forbid",
        # References are frozen post-construction. A mutation to
        # a reference's identity would corrupt the body's
        # identity_hash; D-061 §7.2 disallows in-place mutation.
        frozen=True,
    )

    ref_kind: Literal["logical"] = "logical"
    """Discriminator. See :data:`OperationalRef`."""

    entity_type: str
    """The S1 entity type the reference targets (e.g., ``"Object"``,
    ``"Field"``)."""

    external_id: str
    """The S1 entity's external identifier (e.g., ``"Account"`` for
    an Object, or ``"Account.Name"`` for a Field). Resolution is
    by ``(entity_type, external_id)`` against S1's active set."""


# ---------------------------------------------------------------------------
# PinnedRef
# ---------------------------------------------------------------------------

class PinnedRef(BaseModel):
    """A reference pinned to a specific S1 entity-row version.

    Per D-058 §5.3: pinned references carry both the entity_id
    (the S1 row's UUID) and the version_seq, plus the
    entity_type + external_id for human readability and as a
    cross-check at read time.

    Per D-060 §4.7.6: identity-bearing slots (claim subjects,
    primarily) REQUIRE a pinned reference; the Coordinator
    rejects logical references in those positions. See
    :class:`IdentityBearingRef` for the stricter Python type.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    ref_kind: Literal["pinned"] = "pinned"
    """Discriminator. Shared with :class:`IdentityBearingRef`
    (which is a Python-side subclass, not a separate
    discriminator value)."""

    entity_type: str
    """The S1 entity type the reference targets."""

    entity_id: UUID
    """The S1 entity row's UUID. Cross-substrate logical FK per
    D-058 §5.3 — not DB-enforced; the Coordinator validates at
    write time."""

    version_seq: int
    """The S1 bitemporal version this reference pins to."""

    external_id: str
    """Human-readable cross-check. Resolution authority is
    (entity_id, version_seq); the external_id is for display +
    drift detection (if it disagrees with the entity's current
    external_id, something has shifted)."""


# ---------------------------------------------------------------------------
# IdentityBearingRef
# ---------------------------------------------------------------------------

class IdentityBearingRef(PinnedRef):
    """A pinned reference appearing in an identity-bearing slot.

    Per D-060 §4.7.6: certain body fields (most prominently the
    subject of a claim) participate in the body's
    ``identity_hash`` computation. References in those slots
    MUST be pinned (not logical) AND MUST satisfy the slot's
    ontology requirements. The Coordinator validates both
    invariants at write time, then promotes the on-the-wire
    :class:`PinnedRef` to an :class:`IdentityBearingRef`
    instance so downstream code sees a Python type that
    enforces "this reference passed the identity-bearing
    checks."

    No field changes vs :class:`PinnedRef`. Identical JSON
    serialization. The subclass exists purely as a type-system
    discipline for the Coordinator + body-model boundaries.

    NOT a separate discriminator participant in
    :data:`OperationalRef`. A wire-level payload with
    ``ref_kind="pinned"`` deserializes via the union as a
    plain :class:`PinnedRef`; the Coordinator wraps it in
    :class:`IdentityBearingRef` if and only if the slot
    requires identity-bearing semantics and the wire payload
    satisfied those checks.
    """


# ---------------------------------------------------------------------------
# Discriminated union
# ---------------------------------------------------------------------------

OperationalRef = Annotated[
    Union[PinnedRef, LogicalRef],
    Field(discriminator="ref_kind"),
]
"""On-the-wire reference shape: either pinned or logical.

Pydantic dispatches by the ``ref_kind`` discriminator value. The
union does NOT include :class:`IdentityBearingRef` — it shares the
``"pinned"`` discriminator with :class:`PinnedRef`, and identity-
bearing promotion is a Coordinator-side concern per D-060 §4.7.6.

Concrete body models declare fields of this type when they need
either-or reference semantics. Identity-bearing slots declare
:class:`IdentityBearingRef` directly (no union); the Coordinator
ensures the wire payload satisfies the ``ref_kind="pinned"`` +
ontology + pinning requirements before constructing the
:class:`IdentityBearingRef` instance."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_identity_bearing(ref: object) -> bool:
    """Return True iff ``ref`` is an :class:`IdentityBearingRef`
    instance.

    Sole purpose: explicit + greppable predicate for code that
    needs to branch on "did the Coordinator promote this
    reference to identity-bearing?" without writing the raw
    ``isinstance`` check. Returns False for plain
    :class:`PinnedRef` (the Python-type distinction is the
    point), :class:`LogicalRef`, and anything else.
    """
    return isinstance(ref, IdentityBearingRef)
