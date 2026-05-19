"""Coverage extraction — semantic linkage from claims to S1 entities.

Per SPEC §4.5 (coverage derivation) + §4.7.6 step 9 (write-flow
coverage rederivation) + D-058 §5.4. Coverage is **fully derived**
from the claim's identity-bearing content — there is no
hand-authored coverage list. The Coordinator calls
:func:`extract_coverage` during every claim write to compute the
fresh set of ``(entity_type, entity_id, reference_kind)`` triples
that the new version covers, then DELETE-and-INSERTs them into
:class:`TestClaimCoverage` per the current-only rederivation rule
(SPEC §6.5).

Reference-kind classification per SPEC §4.5:
  - References inside ``asserted_truth`` are classified as
    ``"subject"`` — they identify the entities the claim is
    about.
  - References inside ``semantic_conditions`` are classified as
    ``"condition"`` — they identify entities scoping the
    claim's applicability.

The walk is type-aware (mirrors :mod:`canonicalization`) but
extracts :class:`IdentityBearingRef` instances instead of
producing a canonical-form dict. It only handles
identity-bearing content; operational refs
(:class:`PinnedRef` / :class:`LogicalRef`) appearing in input
are a defensive error.

Deduplication: same
``(entity_type, entity_id, reference_kind)`` triple appearing
twice in input produces one :class:`CoverageEntry`. The output
is sorted by ``str(entity_id)`` for deterministic order — useful
for the write-flow's DELETE+INSERT step (stable ordering = stable
test assertions + stable diffs in audit logs).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from primeqa.test_representation.errors import SubstrateError
from primeqa.test_representation.models.common import BodyBase
from primeqa.test_representation.models.references import (
    IdentityBearingRef,
    LogicalRef,
    PinnedRef,
)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


ReferenceKind = Literal["subject", "condition"]


@dataclass(frozen=True)
class CoverageEntry:
    """A single coverage row: one S1 entity referenced from one
    slot of the claim."""

    entity_type: str
    entity_id: UUID
    reference_kind: ReferenceKind


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_coverage(
    asserted_truth: BodyBase,
    semantic_conditions: BodyBase,
) -> list[CoverageEntry]:
    """Walk the identity-bearing content of a claim and return
    its coverage triples.

    The asserted_truth body's refs are tagged ``reference_kind="subject"``;
    the semantic_conditions body's refs are tagged ``"condition"``.
    Refs are deduplicated by ``(entity_type, entity_id,
    reference_kind)``. Output is sorted by ``str(entity_id)`` for
    deterministic order.

    Raises:
      :class:`SubstrateError` — defensively, if the walk
      encounters a :class:`PinnedRef` (not
      :class:`IdentityBearingRef`) or :class:`LogicalRef` in
      identity-bearing content. Operational refs aren't allowed
      in claim bodies.
    """
    accumulated: set[tuple[str, UUID, ReferenceKind]] = set()
    _walk(asserted_truth, "subject", accumulated, "asserted_truth")
    _walk(semantic_conditions, "condition", accumulated, "semantic_conditions")
    entries = [
        CoverageEntry(
            entity_type=entity_type,
            entity_id=entity_id,
            reference_kind=ref_kind,
        )
        for (entity_type, entity_id, ref_kind) in accumulated
    ]
    entries.sort(key=lambda e: (str(e.entity_id), e.entity_type, e.reference_kind))
    return entries


# ---------------------------------------------------------------------------
# Walk implementation
# ---------------------------------------------------------------------------


def _walk(
    value: Any,
    ref_kind: ReferenceKind,
    accumulated: set,
    path: str,
) -> None:
    """Recursive descent. Collect IdentityBearingRef instances
    into ``accumulated`` as ``(entity_type, entity_id,
    ref_kind)`` tuples."""

    if value is None:
        return

    # IdentityBearingRef BEFORE PinnedRef — the subclass check.
    if isinstance(value, IdentityBearingRef):
        accumulated.add((value.entity_type, value.entity_id, ref_kind))
        return

    if isinstance(value, PinnedRef):
        raise SubstrateError(
            f"PinnedRef (operational ref) encountered at {path}: "
            f"identity-bearing content requires IdentityBearingRef "
            f"per D-058 §5",
            field_path=path,
        )

    if isinstance(value, LogicalRef):
        raise SubstrateError(
            f"LogicalRef (operational ref) encountered at {path}: "
            f"identity-bearing content requires IdentityBearingRef "
            f"per D-058 §5",
            field_path=path,
        )

    if isinstance(value, BaseModel):
        for field_name in type(value).model_fields:
            if field_name == "body_schema_version":
                continue
            sub_value = getattr(value, field_name)
            _walk(sub_value, ref_kind, accumulated, f"{path}.{field_name}")
        return

    if isinstance(value, list):
        for idx, item in enumerate(value):
            _walk(item, ref_kind, accumulated, f"{path}[{idx}]")
        return

    if isinstance(value, dict):
        # Keys are external_id strings (not refs); values may
        # contain refs (e.g., ExpectedValue inside StateDescriptor.field_values).
        for key, sub_value in value.items():
            _walk(sub_value, ref_kind, accumulated, f"{path}.{key}")
        return

    # Scalar types (str / int / bool / float / UUID-as-Python-uuid /
    # etc.) carry no refs and are silently skipped. The walk's only
    # job is finding IdentityBearingRef instances; non-ref leaf
    # values are uninteresting.
    return
