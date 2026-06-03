"""edge→SOQL translation — operational realization, no semantic injection.

Per SPEC §2.3 + D-108.1. The translator turns a :class:`PlannedRead` (a
`LogicalRef` + the single S1 surface it captures) into a live Tooling SOQL
query. It is **S4-owned, finite, capture-keyed**: it adds *operational mechanics*
(the `FROM` object, the `WHERE` scoping, the columns) but **never a semantic
predicate**. The query reflects only what the recipe's assertion carries.

Two **read shapes** (D-127.A), dispatched on the captured surface:

  - **edge-read** — the capture is an S1 edge (e.g. **`APPLIES_TO`**,
    ValidationRule → Object). The query realizes the edge from the subject;
    e.g. APPLIES_TO scopes to the subject Object with **no `Active` filter**
    (adding one would inject a predicate the recipe never asserted — active-ness
    is parked as S4-Q-001, S3-owned).
  - **self-read** — the capture is the subject's *own* surface
    (`sf_api_name` for an existence-claim; a property name in A2). The query
    reads the subject's own metadata (Object → `EntityDefinition`, Field →
    `FieldDefinition`), reusing the proven Tooling SOQL vocabulary from v1 sync
    (`metadata/service.py`) — the query *knowledge*, not its fetchers.

An unknown edge / unknown subject entity_type raises (fail-loud) — never a
silent empty query.
"""
from __future__ import annotations

from dataclasses import dataclass

from primeqa.execution_engine.errors import UnsupportedEdgeError
from primeqa.execution_engine.plan import PlannedRead


@dataclass(frozen=True)
class ToolingQuery:
    """A translated Tooling read: the SOQL string **plus the structured filter
    it encodes**, so evidence records *what was asked* (object + edge), not just
    an opaque string — the basis for S6 to later tell an absent subject from a
    present-but-unconstrained one."""

    soql: str
    sobject: str                # the Tooling object queried (e.g. "ValidationRule")
    edge: str                   # the S1 surface realized: an edge ("APPLIES_TO")
                                # or a self-read capture ("sf_api_name")
    subject_entity_type: str    # the LogicalRef's entity_type (e.g. "Object")
    subject_external_id: str    # the LogicalRef's external_id (e.g. "Lead")


def _applies_to_validation_rule(read: PlannedRead) -> ToolingQuery:
    """`APPLIES_TO` (ValidationRule → Object): which ValidationRules apply to
    the subject Object. Scoped by ``EntityDefinition.QualifiedApiName``.

    **No `Active` filter** — the recipe asserts plain `exists`; active-ness is
    not part of what this realizes (S4-Q-001). Columns are minimal (`Id`,
    `ValidationName`): enough to evidence *which* rules matched; the assertion
    only needs presence."""
    obj = read.target_entity.external_id
    soql = (
        "SELECT Id, ValidationName FROM ValidationRule "
        f"WHERE EntityDefinition.QualifiedApiName = '{_soql_literal(obj)}'"
    )
    return ToolingQuery(
        soql=soql,
        sobject="ValidationRule",
        edge="APPLIES_TO",
        subject_entity_type=read.target_entity.entity_type,
        subject_external_id=obj,
    )


# Finite, edge-keyed registry. One entry per TIER_1 edge this vertical realizes.
_EDGE_TRANSLATORS = {
    "APPLIES_TO": _applies_to_validation_rule,
}

# The capture that denotes an existence-claim self-read: the subject's own
# canonical api name. (A2 adds property-name captures via the same builders.)
_EXISTENCE_CAPTURE = "sf_api_name"


def _self_read_object(read: PlannedRead, capture: str) -> ToolingQuery:
    """Object self-read: read the Object's own metadata from `EntityDefinition`.
    existence captures ``sf_api_name`` and asserts ``exists`` — a non-empty row
    set IS the verification (D-122). Reuses v1 sync's proven probe
    (`metadata/service.py`: ``SELECT … FROM EntityDefinition WHERE
    QualifiedApiName = …``)."""
    if capture != _EXISTENCE_CAPTURE:
        raise UnsupportedEdgeError(
            f"Object self-read supports capture {_EXISTENCE_CAPTURE!r} "
            f"(existence); got {capture!r} (property captures arrive in A2)"
        )
    obj = read.target_entity.external_id
    soql = (
        "SELECT QualifiedApiName FROM EntityDefinition "
        f"WHERE QualifiedApiName = '{_soql_literal(obj)}'"
    )
    return ToolingQuery(
        soql=soql, sobject="EntityDefinition", edge=capture,
        subject_entity_type=read.target_entity.entity_type,
        subject_external_id=obj,
    )


def _self_read_field(read: PlannedRead, capture: str) -> ToolingQuery:
    """Field self-read: read the Field's own metadata from `FieldDefinition`.
    The Field external_id is the qualified ``Object.Field``; the query scopes by
    the parent object + the field's own api name. Reuses v1 sync's proven probe
    (`metadata/service.py`: ``FROM FieldDefinition WHERE
    EntityDefinition.QualifiedApiName = …``)."""
    if capture != _EXISTENCE_CAPTURE:
        raise UnsupportedEdgeError(
            f"Field self-read supports capture {_EXISTENCE_CAPTURE!r} "
            f"(existence); got {capture!r} (property captures arrive in A2)"
        )
    ext = read.target_entity.external_id
    obj, sep, field = ext.partition(".")
    if not sep or not obj or not field:
        raise UnsupportedEdgeError(
            f"Field self-read needs a qualified 'Object.Field' external_id; "
            f"got {ext!r}"
        )
    soql = (
        "SELECT QualifiedApiName FROM FieldDefinition "
        f"WHERE EntityDefinition.QualifiedApiName = '{_soql_literal(obj)}' "
        f"AND QualifiedApiName = '{_soql_literal(field)}'"
    )
    return ToolingQuery(
        soql=soql, sobject="FieldDefinition", edge=capture,
        subject_entity_type=read.target_entity.entity_type,
        subject_external_id=ext,
    )


# Finite, entity_type-keyed self-read registry (the subject's own metadata).
_SELF_READ_BUILDERS = {
    "Object": _self_read_object,
    "Field": _self_read_field,
}


def translate_read(read: PlannedRead) -> ToolingQuery:
    """Translate one :class:`PlannedRead` into a scoped Tooling query.

    The read's ``fields_to_capture`` names the single S1 surface to verify; the
    translator dispatches on its **read shape** (D-127.A):
      - **edge-read** — the capture is a known edge (e.g. ``APPLIES_TO``) →
        :data:`_EDGE_TRANSLATORS`;
      - **self-read** — the capture is the subject's own surface (e.g.
        ``sf_api_name``) → :data:`_SELF_READ_BUILDERS`, keyed on the subject's
        ``entity_type``.

    Raises :class:`UnsupportedEdgeError` for a read that doesn't capture exactly
    one surface, an unknown edge, or a subject ``entity_type`` with no self-read
    translation (fail-loud — never a silent empty query)."""
    captures = read.fields_to_capture
    if len(captures) != 1:
        raise UnsupportedEdgeError(
            f"metadata-inspection read {read.step_id!r} must capture exactly "
            f"one surface; got {list(captures)}"
        )
    capture = captures[0]

    # edge-read: the capture is a known S1 edge.
    edge_builder = _EDGE_TRANSLATORS.get(capture)
    if edge_builder is not None:
        return edge_builder(read)

    # self-read: the capture is the subject's own surface — dispatch on the
    # subject's entity_type.
    entity_type = read.target_entity.entity_type
    self_builder = _SELF_READ_BUILDERS.get(entity_type)
    if self_builder is None:
        raise UnsupportedEdgeError(
            f"no translation for capture {capture!r} on a {entity_type!r} "
            f"subject (edges: {sorted(_EDGE_TRANSLATORS)}; "
            f"self-read entity types: {sorted(_SELF_READ_BUILDERS)})"
        )
    return self_builder(read, capture)


def _soql_literal(value: str) -> str:
    """Escape a string for a SOQL single-quoted literal.

    Salesforce SOQL escapes backslash + single-quote with a backslash. API
    names are alphanumeric/underscore in practice, but the subject external_id
    flows from S2 data, so it is never embedded raw."""
    return value.replace("\\", "\\\\").replace("'", "\\'")
