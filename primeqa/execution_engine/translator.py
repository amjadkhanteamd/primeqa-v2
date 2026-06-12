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
from typing import Optional

from primeqa.execution_engine.errors import (
    UnsupportedEdgeError,
    UnsupportedPropertyError,
)
from primeqa.execution_engine.plan import PlannedRead


@dataclass(frozen=True)
class ToolingQuery:
    """A translated SOQL read: the SOQL string **plus the structured filter
    it encodes**, so evidence records *what was asked* (object + edge), not just
    an opaque string — the basis for S6 to later tell an absent subject from a
    present-but-unconstrained one."""

    soql: str
    sobject: str                # the Tooling object queried (e.g. "ValidationRule")
    edge: str                   # the S1 surface realized: an edge ("APPLIES_TO")
                                # or a self-read capture ("sf_api_name", "length")
    subject_entity_type: str    # the LogicalRef's entity_type (e.g. "Object")
    subject_external_id: str    # the LogicalRef's external_id (e.g. "Lead")
    capture_column: Optional[str] = None  # the SELECTed column whose value a
                                # value-predicate (equals/is_null) reads; None for
                                # existence/edge reads (exists ignores it). D-128.
    api: str = "tooling"        # D-224: which query endpoint runs it —
                                # "tooling" (/tooling/query) or "data"
                                # (/query; ObjectPermissions/FieldPermissions
                                # are Data-API sObjects, not Tooling).


@dataclass(frozen=True)
class LayoutMembershipProbe:
    """The INCLUDES_FIELD realization (D-224) — NOT a single SOQL: resolve the
    layout's Id (Tooling SOQL over ``Layout``), fetch its ``Metadata`` blob
    (single-row Tooling read), and walk ``layoutSections→layoutRows→
    layoutItems→layoutComponents`` for the field. The executor runs it; the
    recipe's ``exists`` assert evaluates over the synthesized row set
    (``[{"field": …}]`` when placed, ``[]`` when not)."""

    layout_external_id: str     # the S1 Layout name, "Object-Layout Name"
    object_api: str             # parsed prefix ("Account")
    layout_name: str            # parsed remainder ("Account Layout")
    field_api: str              # the BARE field name to find ("AnnualRevenue")
    edge: str = "INCLUDES_FIELD"
    sobject: str = "Layout"
    subject_entity_type: str = "Layout"
    capture_column: Optional[str] = None


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


# D-224: capability → permission-flag column maps. The recipe asserts
# equals-true over the flag (capture-column semantics, the D-128 pattern) —
# a permissions row EXISTS even when every flag is false, so presence alone
# verifies nothing. FieldPermissions carries only read/edit.
_OBJECT_GRANT_COLUMNS = {
    "create": "PermissionsCreate",
    "read": "PermissionsRead",
    "edit": "PermissionsEdit",
    "delete": "PermissionsDelete",
    "view_all": "PermissionsViewAllRecords",
    "modify_all": "PermissionsModifyAllRecords",
}
_FIELD_GRANT_COLUMNS = {
    "read": "PermissionsRead",
    "edit": "PermissionsEdit",
}


def _grantee_scope(read: PlannedRead) -> str:
    """The WHERE fragment scoping ObjectPermissions/FieldPermissions to the
    granting subject. Profiles surface through their owning PermissionSet row
    (``Parent.IsOwnedByProfile`` + ``Parent.Profile.Name``); PermissionSets
    scope by their own API name."""
    granter = read.target_entity
    name = _soql_literal(granter.external_id)
    if granter.entity_type == "Profile":
        return ("Parent.IsOwnedByProfile = true "
                f"AND Parent.Profile.Name = '{name}'")
    return f"Parent.IsOwnedByProfile = false AND Parent.Name = '{name}'"


def _require_edge_scope(read: PlannedRead, edge: str, *, qualifier: bool):
    """D-224 reads need the far endpoint (+ qualifier for grants); a read
    missing them is a pre-D-224 recipe — fail loud (the D-223 gate keeps such
    claims at the enqueue door; regenerating the claim re-authors the scope)."""
    if read.edge_target is None or (qualifier and read.edge_qualifier is None):
        raise UnsupportedEdgeError(
            f"{edge} read {read.step_id!r} carries no edge scope "
            f"(edge_target{'/edge_qualifier' if qualifier else ''}) — a "
            f"pre-D-224 recipe; regenerate the claim to re-author it"
        )


def _grants_object_access(read: PlannedRead) -> ToolingQuery:
    """``GRANTS_OBJECT_ACCESS`` (Profile/PermissionSet → Object): read the
    grantee's ObjectPermissions row for the target object and capture the
    capability flag. Data-API SOQL (Tooling does not expose the table)."""
    _require_edge_scope(read, "GRANTS_OBJECT_ACCESS", qualifier=True)
    column = _OBJECT_GRANT_COLUMNS.get(read.edge_qualifier)
    if column is None:
        raise UnsupportedEdgeError(
            f"GRANTS_OBJECT_ACCESS has no permission column for capability "
            f"{read.edge_qualifier!r} (mapped: {sorted(_OBJECT_GRANT_COLUMNS)})"
        )
    obj = read.edge_target.external_id
    soql = (
        f"SELECT {column} FROM ObjectPermissions "
        f"WHERE {_grantee_scope(read)} "
        f"AND SobjectType = '{_soql_literal(obj)}'"
    )
    return ToolingQuery(
        soql=soql, sobject="ObjectPermissions", edge="GRANTS_OBJECT_ACCESS",
        subject_entity_type=read.target_entity.entity_type,
        subject_external_id=read.target_entity.external_id,
        capture_column=column, api="data",
    )


def _grants_field_access(read: PlannedRead) -> ToolingQuery:
    """``GRANTS_FIELD_ACCESS`` (Profile/PermissionSet → Field): same shape over
    FieldPermissions; ``Field`` is the qualified ``Object.Field`` name."""
    _require_edge_scope(read, "GRANTS_FIELD_ACCESS", qualifier=True)
    column = _FIELD_GRANT_COLUMNS.get(read.edge_qualifier)
    if column is None:
        raise UnsupportedEdgeError(
            f"GRANTS_FIELD_ACCESS has no permission column for capability "
            f"{read.edge_qualifier!r} (mapped: {sorted(_FIELD_GRANT_COLUMNS)}; "
            f"FieldPermissions carries only read/edit)"
        )
    field = read.edge_target.external_id
    if "." not in field:
        raise UnsupportedEdgeError(
            f"GRANTS_FIELD_ACCESS needs a qualified 'Object.Field' "
            f"edge_target; got {field!r}"
        )
    obj = field.partition(".")[0]
    soql = (
        f"SELECT {column} FROM FieldPermissions "
        f"WHERE {_grantee_scope(read)} "
        f"AND SobjectType = '{_soql_literal(obj)}' "
        f"AND Field = '{_soql_literal(field)}'"
    )
    return ToolingQuery(
        soql=soql, sobject="FieldPermissions", edge="GRANTS_FIELD_ACCESS",
        subject_entity_type=read.target_entity.entity_type,
        subject_external_id=read.target_entity.external_id,
        capture_column=column, api="data",
    )


def _includes_field(read: PlannedRead) -> LayoutMembershipProbe:
    """``INCLUDES_FIELD`` (Layout → Field): a membership probe, not a SOQL —
    the executor resolves the layout Id, fetches its Metadata blob, and walks
    the layout items for the field (see :class:`LayoutMembershipProbe`)."""
    _require_edge_scope(read, "INCLUDES_FIELD", qualifier=False)
    layout_ext = read.target_entity.external_id
    obj, sep, layout_name = layout_ext.partition("-")
    if not sep or not obj or not layout_name:
        raise UnsupportedEdgeError(
            f"INCLUDES_FIELD needs the S1 'Object-Layout Name' layout "
            f"external_id; got {layout_ext!r}"
        )
    field_ext = read.edge_target.external_id
    field_api = field_ext.partition(".")[2] or field_ext
    return LayoutMembershipProbe(
        layout_external_id=layout_ext, object_api=obj,
        layout_name=layout_name, field_api=field_api,
    )


# Finite, edge-keyed registry. One entry per TIER_1 edge this vertical realizes.
_EDGE_TRANSLATORS = {
    "APPLIES_TO": _applies_to_validation_rule,
    "GRANTS_OBJECT_ACCESS": _grants_object_access,
    "GRANTS_FIELD_ACCESS": _grants_field_access,
    "INCLUDES_FIELD": _includes_field,
}

# The capture that denotes an existence-claim self-read: the subject's own
# canonical api name.
_EXISTENCE_CAPTURE = "sf_api_name"

# property-claim self-read (D-128): the finite, **cleanly equals-mappable** Field
# properties — the S1 detail-column → Tooling `FieldDefinition` column whose value
# has the SAME semantics. is_required (page-layout-derived, no FieldDefinition
# column) and field_type (describe vocabulary vs DataType display string) are
# intentionally ABSENT → `UnsupportedPropertyError` (never a guessed column).
_FIELD_PROPERTY_COLUMNS = {
    "length": "Length",
    "precision": "Precision",
    "scale": "Scale",
}


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
    EntityDefinition.QualifiedApiName = …``).

    Two captures:
      - ``sf_api_name`` → existence (SELECT the api name; assert ``exists``; no
        value column to read);
      - a **mapped property** (length/precision/scale) → SELECT the Tooling
        column for an ``equals``/``is_null`` assertion, recorded as
        ``capture_column``. An unmapped property raises
        :class:`UnsupportedPropertyError` (D-128 — never a guessed column)."""
    ext = read.target_entity.external_id
    obj, sep, field = ext.partition(".")
    if not sep or not obj or not field:
        raise UnsupportedEdgeError(
            f"Field self-read needs a qualified 'Object.Field' external_id; "
            f"got {ext!r}"
        )
    where = (
        f"WHERE EntityDefinition.QualifiedApiName = '{_soql_literal(obj)}' "
        f"AND QualifiedApiName = '{_soql_literal(field)}'"
    )
    if capture == _EXISTENCE_CAPTURE:
        column, capture_column = "QualifiedApiName", None
    else:
        column = _FIELD_PROPERTY_COLUMNS.get(capture)
        if column is None:
            raise UnsupportedPropertyError(
                f"no faithful FieldDefinition column for property {capture!r} on "
                f"{ext!r} (mapped: {sorted(_FIELD_PROPERTY_COLUMNS)}; "
                f"is_required / field_type have no faithful Tooling column — deferred)"
            )
        capture_column = column
    return ToolingQuery(
        soql=f"SELECT {column} FROM FieldDefinition {where}",
        sobject="FieldDefinition", edge=capture,
        subject_entity_type=read.target_entity.entity_type,
        subject_external_id=ext, capture_column=capture_column,
    )


# Finite, entity_type-keyed self-read registry (the subject's own metadata).
_SELF_READ_BUILDERS = {
    "Object": _self_read_object,
    "Field": _self_read_field,
}


def translate_read(read: PlannedRead) -> "ToolingQuery | LayoutMembershipProbe":
    """Translate one :class:`PlannedRead` into a scoped read realization —
    a :class:`ToolingQuery` (tooling or data SOQL) or, for ``INCLUDES_FIELD``,
    a :class:`LayoutMembershipProbe` (D-224).

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
