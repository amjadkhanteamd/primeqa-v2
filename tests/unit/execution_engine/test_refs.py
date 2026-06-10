"""Unit tests for the data-recipe read-resolution convention (D-115 side B).

``resolve_step_refs`` substitutes ``$<step_id>.<attr>`` tokens in a read's SOQL
with the run's captured state (the create's record Id), preserving the authored
quotes, and fail-loud on an unresolved reference."""
from __future__ import annotations

import pytest

from primeqa.execution_engine.errors import StepRefResolutionError
from primeqa.execution_engine.refs import resolve_step_refs


def test_substitutes_quoted_ref_preserving_quotes():
    soql = "SELECT Status__c FROM Account WHERE Id = '$create-record.id'"
    out = resolve_step_refs(soql, {"create-record": {"id": "006XX"}})
    assert out == "SELECT Status__c FROM Account WHERE Id = '006XX'"


def test_substitutes_multiple_refs():
    out = resolve_step_refs(
        "WHERE Id = '$a.id' OR ParentId = '$b.id'",
        {"a": {"id": "1"}, "b": {"id": "2"}})
    assert out == "WHERE Id = '1' OR ParentId = '2'"


def test_no_refs_is_identity():
    soql = "SELECT Name FROM Account WHERE Id = '006'"
    assert resolve_step_refs(soql, {}) == soql


def test_hyphenated_step_id_supported():
    assert resolve_step_refs("'$create-record.id'", {"create-record": {"id": "z"}}) == "'z'"


def test_unresolved_step_raises():
    with pytest.raises(StepRefResolutionError, match="create-record"):
        resolve_step_refs("WHERE Id = '$create-record.id'", {})


def test_unresolved_attr_raises():
    with pytest.raises(StepRefResolutionError, match="create-record"):
        resolve_step_refs(
            "WHERE Id = '$create-record.id'", {"create-record": {"name": "x"}})


def test_none_value_raises():
    with pytest.raises(StepRefResolutionError):
        resolve_step_refs(
            "WHERE Id = '$create-record.id'", {"create-record": {"id": None}})


# ---------------------------------------------------------------------------
# D-205 — resolve_field_value_refs (cross-step refs in create field VALUES)
# ---------------------------------------------------------------------------

from primeqa.execution_engine.refs import resolve_field_value_refs  # noqa: E402


def test_field_value_ref_resolves_against_state():
    out = resolve_field_value_refs(
        {"Contact.AccountId": "$create-account.id", "Contact.LastName": "PQA"},
        {"create-account": {"id": "001ABC"}})
    assert out == {"Contact.AccountId": "001ABC", "Contact.LastName": "PQA"}


def test_field_value_non_strings_pass_through():
    out = resolve_field_value_refs(
        {"Amount": 10000, "Active__c": True, "Note__c": None},
        {})
    assert out == {"Amount": 10000, "Active__c": True, "Note__c": None}


def test_field_value_without_token_is_verbatim():
    out = resolve_field_value_refs({"Name": "no refs here, $ alone is fine"}, {})
    assert out == {"Name": "no refs here, $ alone is fine"}


def test_field_value_unresolved_ref_fails_loud():
    import pytest
    from primeqa.execution_engine.errors import StepRefResolutionError
    with pytest.raises(StepRefResolutionError, match="create-account"):
        resolve_field_value_refs(
            {"AccountId": "$create-account.id"}, {"other-step": {"id": "x"}})


def test_field_value_input_dict_not_mutated():
    src = {"AccountId": "$s.id"}
    resolve_field_value_refs(src, {"s": {"id": "001"}})
    assert src == {"AccountId": "$s.id"}
