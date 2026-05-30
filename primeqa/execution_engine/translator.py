"""edge→SOQL translation — operational realization, no semantic injection.

Per SPEC §2.3 + D-108.1. The translator turns a :class:`PlannedRead` (edge
vocabulary — a `LogicalRef` + the S1 edge it captures) into a live Tooling SOQL
query. It is **S4-owned, finite, edge-keyed**: it adds *operational mechanics*
(the `FROM` object, the `WHERE` scoping, the columns) but **never a semantic
predicate**. The query reflects only what the recipe's assertion carries.

Slice 2 supports the one edge this vertical needs — **`APPLIES_TO`**
(ValidationRule → Object). The recipe asserts plain `exists` over the edge, so
the query scopes to the subject Object with **no `Active` filter**: adding one
would inject a predicate the recipe never asserted (active-ness is parked as
S4-Q-001, S3-owned). An unknown edge raises (fail-loud) — never a silent
empty query.
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
    edge: str                   # the S1 edge realized (e.g. "APPLIES_TO")
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


def translate_read(read: PlannedRead) -> ToolingQuery:
    """Translate one :class:`PlannedRead` into a scoped Tooling query.

    The read's ``fields_to_capture`` names the S1 edge to verify (e.g.
    ``APPLIES_TO``); the registry maps it to the live query. Raises
    :class:`UnsupportedEdgeError` for an edge this vertical doesn't translate
    yet, and for a read that doesn't capture exactly one edge (the inspection
    vertical reads a single edge per step)."""
    edges = read.fields_to_capture
    if len(edges) != 1:
        raise UnsupportedEdgeError(
            f"metadata-inspection read {read.step_id!r} must capture exactly "
            f"one edge; got {list(edges)}"
        )
    edge = edges[0]
    builder = _EDGE_TRANSLATORS.get(edge)
    if builder is None:
        raise UnsupportedEdgeError(
            f"no edge→SOQL translation for edge {edge!r} "
            f"(slice 2 supports: {sorted(_EDGE_TRANSLATORS)})"
        )
    return builder(read)


def _soql_literal(value: str) -> str:
    """Escape a string for a SOQL single-quoted literal.

    Salesforce SOQL escapes backslash + single-quote with a backslash. API
    names are alphanumeric/underscore in practice, but the subject external_id
    flows from S2 data, so it is never embedded raw."""
    return value.replace("\\", "\\\\").replace("'", "\\'")
