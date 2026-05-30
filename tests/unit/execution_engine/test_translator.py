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
