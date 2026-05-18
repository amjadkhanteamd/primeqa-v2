"""Canonicalization — the substrate's semantic compiler.

Per D-059 + SPEC §6.3 (identity_hash semantics, canonicalization,
governance contract). Canonicalization compiles a Pydantic body
into a deterministic Python tree (dicts / lists / scalars) that
:func:`canonical_serialize` then turns into a stable UTF-8 JSON
string. The resulting bytes are SHA-256'd by
:func:`identity_hash.compute_identity_hash` to produce the
``identity_hash`` fingerprint.

This module is **governance-critical infrastructure** per SPEC
§6.3.9 Rule 6: changing any rule here changes the canonical form
of every body in the substrate, which mechanically invalidates
all prior approvals computed under the old rules. Changes require
an ``IDENTITY_HASH_VERSION`` bump and a documented migration —
see :mod:`primeqa.test_representation.identity_hash`.

The rules implemented here are v1 (``IDENTITY_HASH_VERSION = 1``),
sourced from SPEC §6.3.2:

  - Dict keys are sorted alphabetically (UTF-8 lexicographic).
  - JSON output carries no whitespace.
  - ``null`` / ``true`` / ``false`` are lowercase.
  - Integers serialize in canonical numeric form (no leading
    zeros, no trailing ``.0``).
  - **Floats raise** :class:`FloatInIdentityBearingContentError`
    — platform-dependent IEEE-754 representation would make hashes
    unstable.
  - Empty arrays / objects serialize as ``[]`` / ``{}``.
  - Strings preserved verbatim (no normalization, no folding); the
    JSON-string escape set follows RFC 8259.
  - Body-level: ``body_schema_version`` is excluded from the
    canonical form per §6.3.5 (schema-version drift must not
    change identity).
  - References (per §6.3.3): :class:`IdentityBearingRef` instances
    canonicalize to ``{entity_id, entity_type}`` — version_seq and
    external_id are stripped so version supersession + entity
    renames don't change identity (the C0/C1 drift invariants).
  - Arrays (per §6.3.4): typed ``list[T]`` fields on Pydantic
    models MUST carry an :class:`ArraySemantics` marker. Untyped
    lists inside ``Any``-typed content default to ORDERED
    (canonical-JSON convention).
  - Semantic projection markers (:class:`SemanticField` /
    :class:`ProjectionField`) are reserved for v2 and raise
    :class:`UnresolvedSemanticProjectionError` if encountered.

Body classes that need NON-default canonicalization — most
prominently
:class:`StateTransitionClaimBody` and
:class:`AutomationEffectClaimBody`, which re-key state dicts by
``entity_id`` rather than ``external_id`` per the C1-B
resolution — register a custom canonicalizer with the
:data:`default_canonicalizer_registry`. The :func:`canonicalize`
entry point dispatches to the registered canonicalizer before
falling back to the generic walk.
"""
from __future__ import annotations

import json
import types
import typing
from typing import Any, Optional

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from primeqa.test_representation.errors import (
    FloatInIdentityBearingContentError,
    SubstrateError,
    UnmarkedArraySemanticsError,
    UnresolvedSemanticProjectionError,
)
from primeqa.test_representation.models.common import (
    ArraySemantics,
    BodyBase,
    ProjectionField,
    SemanticField,
)
from primeqa.test_representation.models.references import (
    IdentityBearingRef,
    LogicalRef,
    PinnedRef,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def canonicalize(body: BodyBase) -> dict[str, Any]:
    """Compile ``body`` to its canonical-form dict.

    Routes through the canonicalizer registry first:
      - If a custom canonicalizer is registered for the body
        class at the current ``IDENTITY_HASH_VERSION``, dispatch
        to it.
      - Otherwise apply the generic walk.

    Returns a Python ``dict`` whose contents are dicts / lists /
    strs / ints / bools / None — no Pydantic instances, no
    floats, no other types. The result is safe to pass to
    :func:`canonical_serialize` for stable byte-level output.

    Raises:
      :class:`FloatInIdentityBearingContentError` — if a float is
      encountered at any depth.
      :class:`UnmarkedArraySemanticsError` — if a typed list field
      lacks an :class:`ArraySemantics` marker.
      :class:`UnresolvedSemanticProjectionError` — if a field
      carries a :class:`SemanticField` or :class:`ProjectionField`
      marker.
      :class:`SubstrateError` — defensively, if an operational-ref
      (:class:`PinnedRef` that isn't :class:`IdentityBearingRef`,
      or :class:`LogicalRef`) appears in identity-bearing content.
    """
    # Lazy imports to avoid a circular import between this module,
    # ``identity_hash``, and the ``canonicalizers`` package (which
    # registers custom canonicalizers that themselves call back into
    # ``canonicalize`` and ``canonical_serialize``).
    from primeqa.test_representation.canonicalizers import (
        default_canonicalizer_registry,
    )
    from primeqa.test_representation.identity_hash import (
        IDENTITY_HASH_VERSION,
    )

    custom = default_canonicalizer_registry.get(
        type(body), IDENTITY_HASH_VERSION,
    )
    if custom is not None:
        return custom(body)
    return _generic_canonicalize_body(body)


def canonical_serialize(value: Any) -> str:
    """Serialize a canonical-form value (dict / list / scalar) to
    the canonical JSON string per SPEC §6.3.2.

    The output is suitable for ``.encode("utf-8")`` followed by
    SHA-256 to produce the ``identity_hash``. Uses
    ``json.dumps`` with strict options:
      - ``separators=(',', ':')`` — no whitespace.
      - ``sort_keys=True`` — alphabetical key order (defence
        against canonical-form dicts that weren't pre-sorted).
      - ``ensure_ascii=False`` — UTF-8 string passthrough.
      - ``allow_nan=False`` — NaN / Inf raise (defence: we
        already reject floats at canonicalize time).
    """
    return json.dumps(
        value,
        separators=(",", ":"),
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
    )


# ---------------------------------------------------------------------------
# Generic walk
# ---------------------------------------------------------------------------


def _generic_canonicalize_body(body: BaseModel) -> dict[str, Any]:
    """Generic recursive walk over a Pydantic body model.

    Skips ``body_schema_version`` (§6.3.5). Field iteration order
    is irrelevant for the hash because the output dict's keys are
    sorted by :func:`canonical_serialize`.
    """
    body_class = type(body)
    body_class_name = body_class.__name__
    result: dict[str, Any] = {}
    for field_name, field_info in body_class.model_fields.items():
        if field_name == "body_schema_version":
            continue
        _check_marker_violation(field_info, body_class_name, field_name)
        value = getattr(body, field_name)
        result[field_name] = _canonicalize_value(
            value, field_info, body_class_name, field_name,
        )
    return result


def _canonicalize_value(
    value: Any,
    field_info: Optional[FieldInfo],
    body_class_name: str,
    path: str,
) -> Any:
    """Type-aware canonical transformation of a single value.

    ``field_info`` is the Pydantic FieldInfo for the immediate
    parent field, or ``None`` when recursing into items of a list
    or values of a dict (where there's no per-element field
    declaration).
    """
    # None first — both ``None == False`` returns False but a
    # bool-isinstance check would also return False, so order isn't
    # critical here; explicit early return for clarity.
    if value is None:
        return None

    # Bools BEFORE ints — ``isinstance(True, int)`` is True in
    # Python, so int check would swallow bools.
    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        raise FloatInIdentityBearingContentError(
            f"float encountered in identity-bearing content at "
            f"{body_class_name}.{path}: {value!r}; v1 forbids "
            f"floats per SPEC §6.3.2 (use str or int instead)",
            body_class=body_class_name,
            field_path=path,
            value_repr=repr(value),
        )

    if isinstance(value, str):
        return value

    # IdentityBearingRef BEFORE PinnedRef — IdentityBearingRef is
    # a subclass of PinnedRef.
    if isinstance(value, IdentityBearingRef):
        return {
            "entity_id": str(value.entity_id),
            "entity_type": value.entity_type,
        }

    if isinstance(value, PinnedRef):
        raise SubstrateError(
            f"PinnedRef (operational ref) encountered in "
            f"identity-bearing content at "
            f"{body_class_name}.{path}: identity-bearing slots "
            f"require IdentityBearingRef per D-058 §5",
            body_class=body_class_name,
            field_path=path,
        )

    if isinstance(value, LogicalRef):
        raise SubstrateError(
            f"LogicalRef (operational ref) encountered in "
            f"identity-bearing content at "
            f"{body_class_name}.{path}: identity-bearing slots "
            f"require IdentityBearingRef per D-058 §5",
            body_class=body_class_name,
            field_path=path,
        )

    if isinstance(value, BaseModel):
        # Submodel — try the registry, fall back to the generic walk.
        # The lazy import is repeated here (vs. caching at module
        # top) to keep canonicalize safe under partial module-load
        # conditions during import.
        from primeqa.test_representation.canonicalizers import (
            default_canonicalizer_registry,
        )
        from primeqa.test_representation.identity_hash import (
            IDENTITY_HASH_VERSION,
        )

        custom = default_canonicalizer_registry.get(
            type(value), IDENTITY_HASH_VERSION,
        )
        if custom is not None:
            return custom(value)
        return _generic_canonicalize_body(value)

    if isinstance(value, list):
        semantics = _list_semantics(field_info)
        if semantics is None:
            if field_info is not None and _is_typed_list_field(field_info):
                raise UnmarkedArraySemanticsError(
                    f"typed list field {body_class_name}.{path} "
                    f"lacks ArraySemantics marker; identity-bearing "
                    f"list fields MUST declare ORDERED or SET per "
                    f"D-059 §6.3.4",
                    body_class=body_class_name,
                    field_path=path,
                )
            # Untyped list inside Any content → ORDERED by default
            # (canonical-JSON convention per SPEC §6.3.2).
            semantics = ArraySemantics.ORDERED

        items = [
            _canonicalize_value(item, None, body_class_name, f"{path}[{idx}]")
            for idx, item in enumerate(value)
        ]
        if semantics == ArraySemantics.SET:
            # Sort by canonical serialization so identical-content
            # lists hash identically regardless of input order.
            items.sort(key=canonical_serialize)
        return items

    if isinstance(value, dict):
        # Default: alphabetical key sort, recurse into values.
        out: dict[str, Any] = {}
        for k in sorted(value.keys()):
            out[k] = _canonicalize_value(
                value[k], None, body_class_name, f"{path}.{k}",
            )
        return out

    raise SubstrateError(
        f"unsupported value type {type(value).__name__} at "
        f"{body_class_name}.{path}: {value!r}",
        body_class=body_class_name,
        field_path=path,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_marker_violation(
    field_info: FieldInfo, body_class_name: str, path: str,
) -> None:
    """Raise :class:`UnresolvedSemanticProjectionError` if the
    field carries a SemanticField or ProjectionField marker."""
    for marker in field_info.metadata:
        if isinstance(marker, (SemanticField, ProjectionField)):
            marker_name = type(marker).__name__
            raise UnresolvedSemanticProjectionError(
                f"field {body_class_name}.{path} carries a "
                f"{marker_name} marker; semantic projection is "
                f"reserved for v2 of the canonicalization policy "
                f"per SPEC §6.3.10",
                body_class=body_class_name,
                field_path=path,
                marker_kind=marker_name,
            )


def _list_semantics(
    field_info: Optional[FieldInfo],
) -> Optional[ArraySemantics]:
    """Return the :class:`ArraySemantics` marker on the field, if
    any."""
    if field_info is None:
        return None
    for marker in field_info.metadata:
        if isinstance(marker, ArraySemantics):
            return marker
    return None


def _is_typed_list_field(field_info: FieldInfo) -> bool:
    """``True`` iff the field's annotation is ``list[T]`` (or an
    Optional wrapping a ``list[T]``).

    Distinguishes ``Any``-typed fields (whose Python value might
    coincidentally be a list) from declared-list fields (which
    MUST carry an ArraySemantics marker)."""
    return _annotation_is_list(field_info.annotation)


def _annotation_is_list(annotation: Any) -> bool:
    origin = typing.get_origin(annotation)
    if origin is list:
        return True
    if origin is typing.Union or origin is types.UnionType:
        return any(
            _annotation_is_list(arg) for arg in typing.get_args(annotation)
        )
    return False
