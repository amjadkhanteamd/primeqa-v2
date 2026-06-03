"""Unit tests for the S4 edge->SOQL translator (D-108.1) — pure, no org.

The translator is operational realization only: it maps a `PlannedRead` (edge
vocabulary) to a scoped Tooling query, adding mechanics (FROM, WHERE scope,
columns) but **never a semantic predicate**. The `APPLIES_TO` translation
carries **no `Active` filter** — the recipe asserts plain `exists` (S4-Q-001).
"""
from __future__ import annotations

import pytest

from primeqa.execution_engine.errors import UnsupportedEdgeError
from primeqa.execution_engine.plan import PlannedRead
from primeqa.execution_engine.translator import ToolingQuery, translate_read
from primeqa.test_representation.models.references import LogicalRef


def _read(external_id="Lead", *, edge="APPLIES_TO", entity_type="Object"):
    return PlannedRead(
        step_id="read-subject",
        target_entity=LogicalRef(entity_type=entity_type, external_id=external_id),
        fields_to_capture=(edge,) if edge is not None else (),
    )


def test_applies_to_translates_to_scoped_validation_rule_query():
    q = translate_read(_read("Lead"))
    assert isinstance(q, ToolingQuery)
    assert q.sobject == "ValidationRule"
    assert q.edge == "APPLIES_TO"
    assert q.subject_entity_type == "Object"
    assert q.subject_external_id == "Lead"
    assert "FROM ValidationRule" in q.soql
    assert "EntityDefinition.QualifiedApiName = 'Lead'" in q.soql


def test_applies_to_query_has_no_active_filter():
    # The operational-realization principle: the recipe asserts plain `exists`,
    # so the translator injects NO active-ness predicate (S4-Q-001).
    q = translate_read(_read("Account"))
    assert "Active" not in q.soql
    assert "active" not in q.soql


def test_subject_external_id_is_soql_escaped():
    # external_id flows from S2 data, so it is never embedded raw.
    q = translate_read(_read("O'Brien__c"))
    assert "EntityDefinition.QualifiedApiName = 'O\\'Brien__c'" in q.soql
    # the structured filter keeps the unescaped value for evidence legibility.
    assert q.subject_external_id == "O'Brien__c"


def test_unknown_edge_fails_loud():
    with pytest.raises(UnsupportedEdgeError, match="BELONGS_TO"):
        translate_read(_read(edge="BELONGS_TO"))


def test_read_must_capture_exactly_one_edge():
    with pytest.raises(UnsupportedEdgeError, match="exactly"):
        translate_read(_read(edge=None))  # empty fields_to_capture


# ---------------------------------------------------------------------------
# Self-reads (D-127): existence reads the subject's OWN metadata, not an edge.
# The capture is `sf_api_name`; the query realizes the subject's own surface.
# ---------------------------------------------------------------------------

def test_existence_object_self_read_queries_entity_definition():
    q = translate_read(_read("Account", edge="sf_api_name", entity_type="Object"))
    assert q.sobject == "EntityDefinition"
    assert q.edge == "sf_api_name"
    assert q.subject_entity_type == "Object"
    assert q.subject_external_id == "Account"
    assert "FROM EntityDefinition" in q.soql
    assert "QualifiedApiName = 'Account'" in q.soql


def test_existence_field_self_read_queries_field_definition_scoped_by_object():
    # the Field external_id is the qualified Object.Field; the query scopes by
    # the parent object AND the field's own api name.
    q = translate_read(_read("Account.Industry", edge="sf_api_name", entity_type="Field"))
    assert q.sobject == "FieldDefinition"
    assert q.edge == "sf_api_name"
    assert q.subject_external_id == "Account.Industry"
    assert "FROM FieldDefinition" in q.soql
    assert "EntityDefinition.QualifiedApiName = 'Account'" in q.soql
    assert "QualifiedApiName = 'Industry'" in q.soql


def test_existence_self_read_has_no_extra_predicate():
    # operational realization only — existence asserts plain `exists`, so the
    # self-read scopes to the subject and adds nothing else.
    q = translate_read(_read("Account", edge="sf_api_name", entity_type="Object"))
    assert "Active" not in q.soql and "IsRequired" not in q.soql


def test_field_self_read_requires_qualified_object_field():
    with pytest.raises(UnsupportedEdgeError, match="qualified"):
        translate_read(_read("BareField", edge="sf_api_name", entity_type="Field"))


def test_self_read_unknown_entity_type_fails_loud():
    # `sf_api_name` on a subject type with no self-read builder → fail-loud,
    # never a silent empty query (Layout self-read arrives with cap+layout, deferred).
    with pytest.raises(UnsupportedEdgeError, match="Layout"):
        translate_read(_read("X", edge="sf_api_name", entity_type="Layout"))


def test_field_self_read_soql_escapes_both_parts():
    # both halves of the Object.Field flow from S2 data → both are escaped.
    q = translate_read(_read("O'Brien__c.Quote'd__c", edge="sf_api_name", entity_type="Field"))
    assert "EntityDefinition.QualifiedApiName = 'O\\'Brien__c'" in q.soql
    assert "AND QualifiedApiName = 'Quote\\'d__c'" in q.soql
