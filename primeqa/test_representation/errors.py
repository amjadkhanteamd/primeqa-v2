"""Substrate-2 error hierarchy.

Per SPEC §4.7.8. All substrate-2 validation, ontology, mutation,
and schema-compatibility failures derive from :class:`SubstrateError`
so callers can catch the substrate boundary uniformly. Each subclass
carries structured context (kind / body_schema_version / actor /
referent identifier as appropriate) — callers and observability
layers read these attributes directly rather than parsing message
text.

Lives at ``primeqa.test_representation.errors`` (substrate-wide;
NOT under ``models/`` since several non-model code paths raise these
— Coordinator at write time per D-060 §4.7.6, mutation enforcement
per D-061, schema upgrades per D-059 §6.3.7).
"""
from __future__ import annotations

from typing import Any, Optional


class SubstrateError(Exception):
    """Base class for substrate-2 errors.

    All substrate-2 failures derive from this class so callers can
    catch the substrate boundary in one place. The ``context`` dict
    is an open-ended carrier for observability — subclasses populate
    it with the structured fields most relevant to their failure
    mode, but downstream code can also attach context post-hoc.
    """

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__(message)
        self.message = message
        self.context: dict[str, Any] = dict(context)

    def __repr__(self) -> str:
        if self.context:
            return f"{type(self).__name__}({self.message!r}, **{self.context!r})"
        return f"{type(self).__name__}({self.message!r})"


class SchemaIncompatibilityError(SubstrateError):
    """A persisted body's ``body_schema_version`` is outside the
    range of Pydantic models the registry can decode, or the
    registry has no model for the requested ``(kind, version)``.

    Per D-059 §6.3.7: hashes are only directly comparable within
    the same identity_hash_version. Per SPEC §4.4 + §4.7.1: every
    body carries ``body_schema_version`` so the registry can
    dispatch to the right Pydantic model; a missing entry means
    we've encountered a body that this build can't safely decode.

    Structured context:
      - ``kind``: the body's declared ``kind`` field (e.g.,
        ``"value-claim"``)
      - ``body_schema_version``: the integer version observed on
        the body
      - ``available_versions``: list of versions the registry
        currently knows for this kind (may be empty)
    """

    def __init__(
        self,
        message: str,
        *,
        kind: Optional[str] = None,
        body_schema_version: Optional[int] = None,
        available_versions: Optional[list[int]] = None,
        **extra: Any,
    ) -> None:
        super().__init__(
            message,
            kind=kind,
            body_schema_version=body_schema_version,
            available_versions=available_versions,
            **extra,
        )


class BodyCorruptionError(SubstrateError):
    """A persisted JSONB body fails Pydantic validation against the
    registry-resolved model for its declared ``(kind,
    body_schema_version)``.

    Distinct from :class:`SchemaIncompatibilityError`: there the
    registry doesn't know how to decode the body. Here the registry
    has a model and it rejects the content. Per D-060 §4.7.1 the
    Pydantic layer enforces deep structural validity; this error
    fires when that layer fails on read (typically indicating
    corruption, partial migration, or a write that bypassed the
    Coordinator).

    Structured context:
      - ``kind``: the body's declared kind
      - ``body_schema_version``: the version that was attempted
      - ``referent``: identifier of the row whose body failed
        (e.g., ``test_id``, ``recipe_id``) — opaque string, format
        depends on which table the body came from
      - ``validation_errors``: Pydantic's ``errors()`` output if
        available (list of dicts)
    """

    def __init__(
        self,
        message: str,
        *,
        kind: Optional[str] = None,
        body_schema_version: Optional[int] = None,
        referent: Optional[str] = None,
        validation_errors: Optional[list[dict[str, Any]]] = None,
        **extra: Any,
    ) -> None:
        super().__init__(
            message,
            kind=kind,
            body_schema_version=body_schema_version,
            referent=referent,
            validation_errors=validation_errors,
            **extra,
        )


class OntologyViolationError(SubstrateError):
    """A reference's ``entity_type`` is not in the allow-list for
    the field where it appears, or a SemanticField's value violates
    the ontology contract for its declared role.

    Per D-058 §5.7: each model declares which entity types are
    valid for each reference field; the Coordinator validates this
    at write time. Per SPEC §4.7.4: SemanticField descriptors carry
    ontology hints (allowed entity types, allowed scalar enums)
    that the Coordinator enforces. This error fires when a write
    presents a reference whose ``entity_type`` isn't permitted in
    its slot.

    Structured context:
      - ``field_path``: dotted path to the offending field (e.g.,
        ``"asserted_truth.subject"``)
      - ``actual_entity_type``: the type that was presented
      - ``allowed_entity_types``: list of permitted types for this
        slot
      - ``referent``: identifier of the parent row, if known
    """

    def __init__(
        self,
        message: str,
        *,
        field_path: Optional[str] = None,
        actual_entity_type: Optional[str] = None,
        allowed_entity_types: Optional[list[str]] = None,
        referent: Optional[str] = None,
        **extra: Any,
    ) -> None:
        super().__init__(
            message,
            field_path=field_path,
            actual_entity_type=actual_entity_type,
            allowed_entity_types=allowed_entity_types,
            referent=referent,
            **extra,
        )


class AuthorityViolationError(SubstrateError):
    """A mutation was attempted on a field outside the caller's
    authority over meaning.

    Per D-061 §7.2: not all fields are equally mutable from all
    callers. The Generation actor authors initial claim bodies;
    the Review actor approves/deprecates; the Evolution actor
    rewrites recipes under S8 control. Cross-actor mutations
    raise this error rather than silently succeeding.

    Structured context:
      - ``actor``: the actor identifier that attempted the mutation
        (string per the actor-naming convention; the substrate
        doesn't enforce a closed set)
      - ``field_path``: dotted path to the field the actor
        attempted to mutate
      - ``allowed_actors``: list of actor identifiers that are
        permitted to mutate this field, if known
      - ``referent``: identifier of the row being mutated, if known
    """

    def __init__(
        self,
        message: str,
        *,
        actor: Optional[str] = None,
        field_path: Optional[str] = None,
        allowed_actors: Optional[list[str]] = None,
        referent: Optional[str] = None,
        **extra: Any,
    ) -> None:
        super().__init__(
            message,
            actor=actor,
            field_path=field_path,
            allowed_actors=allowed_actors,
            referent=referent,
            **extra,
        )


# ---------------------------------------------------------------------------
# Canonicalization error types (Track C)
# ---------------------------------------------------------------------------

class UnresolvedSemanticProjectionError(SubstrateError):
    """Raised when canonicalization encounters a field marked with
    :class:`SemanticField` or :class:`ProjectionField`.

    Per SPEC §6.3.10: semantic projection fields are RESERVED in
    v1 of the canonicalization policy. The markers exist on body
    models so concrete bodies (B-β..ε) can reserve names without
    forward-declaring; v2 will define how the markers translate
    into the canonical form. v1 fails loud rather than silently
    omitting the field.

    Structured context:
      - ``body_class``: the body class containing the marked field
      - ``field_path``: dotted path to the marked field
      - ``marker_kind``: which marker (``"SemanticField"`` or
        ``"ProjectionField"``)
    """

    def __init__(
        self,
        message: str,
        *,
        body_class: Optional[str] = None,
        field_path: Optional[str] = None,
        marker_kind: Optional[str] = None,
        **extra: Any,
    ) -> None:
        super().__init__(
            message,
            body_class=body_class,
            field_path=field_path,
            marker_kind=marker_kind,
            **extra,
        )


class UnmarkedArraySemanticsError(SubstrateError):
    """Raised when canonicalization encounters a typed list field
    in identity-bearing content that lacks an explicit
    :class:`ArraySemantics` marker.

    Per D-059 §6.3.4: identity-bearing list fields MUST declare
    their semantics (ORDERED vs SET) explicitly. The canonicalizer
    cannot guess: ORDERED would silently preserve incidental order
    differences as semantic; SET would silently flatten meaningful
    order. v1 requires the author to choose at the body-model
    declaration site.

    Untyped lists inside ``Any``-typed content default to ORDERED
    (canonical-JSON convention); this error fires only for typed
    ``list[T]`` fields on Pydantic models without an
    :class:`ArraySemantics` marker.

    Structured context:
      - ``body_class``: the body class containing the unmarked field
      - ``field_path``: dotted path to the unmarked list field
    """

    def __init__(
        self,
        message: str,
        *,
        body_class: Optional[str] = None,
        field_path: Optional[str] = None,
        **extra: Any,
    ) -> None:
        super().__init__(
            message,
            body_class=body_class,
            field_path=field_path,
            **extra,
        )


class FloatInIdentityBearingContentError(SubstrateError):
    """Raised when canonicalization encounters a Python ``float``
    value in identity-bearing content.

    Per D-059 §6.3.2: v1 of the canonicalization policy FORBIDS
    floats in identity-bearing content. Float representation is
    platform-dependent (IEEE-754 rounding differs across runtimes
    and processors); allowing floats would make the
    ``identity_hash`` unstable. Authors who need numeric values
    use ``str`` (e.g., ``"3.14"``) or ``int`` (when the value is
    discrete). v2 may introduce a ``DecimalValue`` shape with an
    explicit precision contract if compelling.

    Structured context:
      - ``body_class``: the body class where the float was found
      - ``field_path``: dotted path to the offending value
      - ``value_repr``: ``repr(value)`` for diagnostic output
    """

    def __init__(
        self,
        message: str,
        *,
        body_class: Optional[str] = None,
        field_path: Optional[str] = None,
        value_repr: Optional[str] = None,
        **extra: Any,
    ) -> None:
        super().__init__(
            message,
            body_class=body_class,
            field_path=field_path,
            value_repr=value_repr,
            **extra,
        )
