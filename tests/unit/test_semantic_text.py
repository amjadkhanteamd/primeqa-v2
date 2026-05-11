"""Unit tests for primeqa/semantic/semantic_text.py.

Per Phase 2 plan §4.3 and decision D-046 (deterministic templating,
no LLM call).

For each entity type, three tests:
  1. Determinism — same input twice → same output string
  2. Field-shape — output includes the expected name and key signals
  3. Graceful defaults — None/missing values do not produce literal "None"

Plus dispatcher tests, type-coverage parametrization, and length/output
sanity checks.
"""
from __future__ import annotations

import pytest

from primeqa.semantic.semantic_text import (
    _TEMPLATERS,
    _strip_terminal_punct,
    to_semantic_text,
)


pytestmark = pytest.mark.unit


# ----------------------------------------------------------------------
# Per-type fixtures (clean-shape dicts)
# ----------------------------------------------------------------------

def _object_fixture() -> dict:
    return {
        "name": "Account",
        "label": "Account",
        "is_custom": False,
        "description": "Standard Salesforce account object.",
        "key_field_names": ["Industry", "AnnualRevenue", "OwnerId"],
    }


def _field_fixture() -> dict:
    return {
        "name": "Industry",
        "object_name": "Account",
        "type": "picklist",
        "label": "Industry",
        "help_text": "Customer industry classification.",
        "is_custom": False,
        "is_required": False,
        "reference_to": None,
        "picklist_value_set_name": "IndustryValues",
    }


def _field_lookup_fixture() -> dict:
    return {
        "name": "OwnerId",
        "object_name": "Account",
        "type": "reference",
        "label": "Owner",
        "help_text": "Record owner.",
        "is_custom": False,
        "is_required": True,
        "reference_to": "User",
        "picklist_value_set_name": None,
    }


def _record_type_fixture() -> dict:
    return {
        "name": "Customer",
        "object_name": "Account",
        "label": "Customer Account",
        "is_active": True,
        "description": "Account record type for direct customers.",
    }


def _layout_fixture() -> dict:
    return {
        "name": "Account-Account Layout",
        "object_name": "Account",
        "section_count": 3,
        "section_names": ["Address", "Information", "System Information"],
    }


def _picklist_value_set_fixture() -> dict:
    return {
        "name": "IndustryValues",
        "label": "Industry Values",
        "is_restricted": True,
        "is_global_value_set": False,
        "description": "Standard industry classifications.",
    }


def _picklist_value_fixture() -> dict:
    return {
        "value": "Technology",
        "set_name": "IndustryValues",
        "is_active": True,
        "is_default": False,
    }


def _profile_fixture() -> dict:
    return {
        "name": "System Administrator",
        "license": "Salesforce",
        "description": "Full administrative access.",
        "obj_perm_count": 47,
        "field_perm_count": 312,
    }


def _permission_set_fixture() -> dict:
    return {
        "name": "DataReader",
        "license": "Salesforce",
        "description": "Read-only access to all data.",
        "obj_perm_count": 12,
        "field_perm_count": 87,
        "parent_profile": "Standard User",
    }


def _user_fixture() -> dict:
    return {
        "username": "admin@example.com.demo",
        "full_name": "Demo Admin",
        "profile_name": "System Administrator",
        "is_active": True,
        "email": "admin@example.com",
    }


def _validation_rule_fixture() -> dict:
    return {
        "name": "AmountPositive",
        "object_name": "Opportunity",
        "is_active": True,
        "error_message": "Amount must be greater than zero on closed opportunities.",
        "formula_text": "IsClosed && Amount <= 0",
        "error_display_field": "Amount",
    }


def _flow_fixture() -> dict:
    return {
        "name": "AccountUpdateFlow",
        "process_type": "AutoLaunchedFlow",
        "is_active": True,
        "description": "Updates parent account on contact change.",
        "triggers_on_object": "Contact",
        "trigger_type": "AfterSave",
    }


_FIXTURES: dict[str, callable] = {
    "Object":           _object_fixture,
    "Field":            _field_fixture,
    "RecordType":       _record_type_fixture,
    "Layout":           _layout_fixture,
    "PicklistValueSet": _picklist_value_set_fixture,
    "PicklistValue":    _picklist_value_fixture,
    "Profile":          _profile_fixture,
    "PermissionSet":    _permission_set_fixture,
    "User":             _user_fixture,
    "ValidationRule":   _validation_rule_fixture,
    "Flow":             _flow_fixture,
}

ALL_TYPES = sorted(_FIXTURES.keys())


# ----------------------------------------------------------------------
# Test 1: Determinism
# ----------------------------------------------------------------------

@pytest.mark.parametrize("entity_type", ALL_TYPES)
def test_semantic_text_is_deterministic(entity_type: str) -> None:
    fixture = _FIXTURES[entity_type]
    a = to_semantic_text(entity_type, fixture())
    b = to_semantic_text(entity_type, fixture())
    assert a == b


# ----------------------------------------------------------------------
# Test 2: Field-shape (per type, verify key signals appear)
# ----------------------------------------------------------------------

class TestFieldShape:
    def test_object_includes_name_and_label(self) -> None:
        out = to_semantic_text("Object", _object_fixture())
        assert "Account" in out
        assert "standard" in out  # is_custom_word
        assert "Industry" in out  # key field appears

    def test_field_includes_name_and_object(self) -> None:
        out = to_semantic_text("Field", _field_fixture())
        assert "Industry" in out
        assert "Account" in out
        assert "picklist" in out
        assert "IndustryValues" in out  # picklist_value_set_name appears

    def test_field_lookup_emits_reference_clause(self) -> None:
        out = to_semantic_text("Field", _field_lookup_fixture())
        assert "OwnerId" in out
        assert "References User" in out
        assert "Required" in out

    def test_record_type_includes_name_and_object(self) -> None:
        out = to_semantic_text("RecordType", _record_type_fixture())
        assert "Customer" in out
        assert "Account" in out
        assert "yes" in out  # active

    def test_layout_includes_section_info(self) -> None:
        out = to_semantic_text("Layout", _layout_fixture())
        assert "Account-Account Layout" in out
        assert "3" in out  # section_count
        assert "Address" in out  # one of the section names

    def test_picklist_value_set_includes_name_and_label(self) -> None:
        out = to_semantic_text("PicklistValueSet", _picklist_value_set_fixture())
        assert "IndustryValues" in out
        assert "Industry Values" in out
        assert "yes" in out  # is_restricted

    def test_picklist_value_includes_value_and_set(self) -> None:
        out = to_semantic_text("PicklistValue", _picklist_value_fixture())
        assert "Technology" in out
        assert "IndustryValues" in out

    def test_profile_includes_name_and_counts(self) -> None:
        out = to_semantic_text("Profile", _profile_fixture())
        assert "System Administrator" in out
        assert "Salesforce" in out  # license
        assert "47" in out
        assert "312" in out

    def test_permission_set_includes_parent_profile(self) -> None:
        out = to_semantic_text("PermissionSet", _permission_set_fixture())
        assert "DataReader" in out
        assert "Standard User" in out  # parent_profile
        assert "12" in out
        assert "87" in out

    def test_user_includes_username_and_profile(self) -> None:
        out = to_semantic_text("User", _user_fixture())
        assert "admin@example.com.demo" in out
        assert "System Administrator" in out
        assert "Demo Admin" in out

    def test_validation_rule_includes_formula_verbatim(self) -> None:
        """High-signal content: formula text MUST appear verbatim because
        Substrate 4 attribution matches Salesforce error responses against
        these embeddings. (Trailing period stripped from user content per
        terminal-punct cleanup; the message body still appears verbatim.)"""
        out = to_semantic_text("ValidationRule", _validation_rule_fixture())
        assert "AmountPositive" in out
        assert "Opportunity" in out
        assert "IsClosed && Amount <= 0" in out
        assert "Amount must be greater than zero on closed opportunities" in out

    def test_flow_includes_trigger_info(self) -> None:
        out = to_semantic_text("Flow", _flow_fixture())
        assert "AccountUpdateFlow" in out
        assert "AutoLaunchedFlow" in out
        assert "Contact" in out  # triggers_on_object
        assert "AfterSave" in out  # trigger_type


# ----------------------------------------------------------------------
# Test 3: Graceful defaults — None values don't produce literal "None"
# ----------------------------------------------------------------------

@pytest.mark.parametrize("entity_type", ALL_TYPES)
def test_no_literal_none_in_output(entity_type: str) -> None:
    """Build a fixture where every field is None or missing.
    The output must not contain Python's literal 'None' as a token."""
    empty = {}
    out = to_semantic_text(entity_type, empty)
    # Look for the standalone 'None' word — uppercase, surrounded by
    # whitespace or punctuation. (The substring "None" could legitimately
    # appear inside larger words, but in our templates it would only
    # appear if a default is missing.)
    import re
    assert not re.search(r"\bNone\b", out), (
        f"{entity_type}: literal 'None' leaked into output: {out!r}"
    )


def test_object_with_none_description_uses_default() -> None:
    fixture = _object_fixture()
    fixture["description"] = None
    out = to_semantic_text("Object", fixture)
    assert "no description provided" in out
    assert "None" not in out


def test_field_with_no_help_text_uses_default() -> None:
    fixture = _field_fixture()
    fixture["help_text"] = None
    out = to_semantic_text("Field", fixture)
    assert "no help text provided" in out


def test_validation_rule_with_no_formula_uses_default() -> None:
    fixture = _validation_rule_fixture()
    fixture["formula_text"] = None
    out = to_semantic_text("ValidationRule", fixture)
    assert "no formula provided" in out
    assert "None" not in out


def test_object_with_empty_string_description_uses_default() -> None:
    """Whitespace-stripped empty string also triggers default."""
    fixture = _object_fixture()
    fixture["description"] = "   "
    out = to_semantic_text("Object", fixture)
    assert "no description provided" in out


# ----------------------------------------------------------------------
# Dispatcher
# ----------------------------------------------------------------------

class TestDispatcher:
    def test_unknown_type_raises_value_error(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            to_semantic_text("NotARealType", {})
        assert "Unknown entity_type" in str(exc_info.value)
        assert "NotARealType" in str(exc_info.value)

    def test_known_types_count(self) -> None:
        assert len(_TEMPLATERS) == 11

    @pytest.mark.parametrize("entity_type", ALL_TYPES)
    def test_dispatches_each_known_type(self, entity_type: str) -> None:
        """Each registered type can be dispatched and returns a string."""
        out = to_semantic_text(entity_type, _FIXTURES[entity_type]())
        assert isinstance(out, str)
        assert len(out) > 0


# ----------------------------------------------------------------------
# Output sanity
# ----------------------------------------------------------------------

class TestOutputSanity:
    @pytest.mark.parametrize("entity_type", ALL_TYPES)
    def test_output_is_str_not_none(self, entity_type: str) -> None:
        out = to_semantic_text(entity_type, _FIXTURES[entity_type]())
        assert out is not None
        assert isinstance(out, str)

    @pytest.mark.parametrize("entity_type", ALL_TYPES)
    def test_output_under_token_target(self, entity_type: str) -> None:
        """Rough length sanity: under 1000 chars to catch runaway templates.
        Templates should produce 50-200 tokens typically."""
        out = to_semantic_text(entity_type, _FIXTURES[entity_type]())
        assert len(out) < 1000, f"{entity_type}: output too long ({len(out)} chars)"

    @pytest.mark.parametrize("entity_type", ALL_TYPES)
    def test_output_has_some_substance(self, entity_type: str) -> None:
        """Lower-bound sanity: at least 30 chars.
        Anything under that suggests the template lost most of its content."""
        out = to_semantic_text(entity_type, _FIXTURES[entity_type]())
        assert len(out) >= 30


# ----------------------------------------------------------------------
# _strip_terminal_punct helper
# ----------------------------------------------------------------------

class TestStripTerminalPunct:
    def test_removes_trailing_period(self) -> None:
        assert _strip_terminal_punct("Account object.") == "Account object"

    def test_removes_trailing_exclamation(self) -> None:
        assert _strip_terminal_punct("Watch out!") == "Watch out"

    def test_removes_trailing_question_mark(self) -> None:
        assert _strip_terminal_punct("Are you sure?") == "Are you sure"

    def test_preserves_middle_punctuation(self) -> None:
        """Only TRAILING punctuation is stripped; mid-sentence punctuation stays."""
        assert _strip_terminal_punct("abc. def.") == "abc. def"

    def test_preserves_inner_period(self) -> None:
        """A single mid-string period must survive."""
        assert _strip_terminal_punct("e.g., this") == "e.g., this"

    def test_handles_none(self) -> None:
        assert _strip_terminal_punct(None) == ""

    def test_handles_empty_string(self) -> None:
        assert _strip_terminal_punct("") == ""

    def test_handles_whitespace_only(self) -> None:
        # rstrip whitespace first, then no terminal punct → "" stripped of whitespace
        assert _strip_terminal_punct("   ").strip() == ""

    def test_handles_trailing_whitespace_before_punctuation(self) -> None:
        """Pattern like 'abc .' should strip period AND surrounding whitespace."""
        assert _strip_terminal_punct("abc .") == "abc"
        assert _strip_terminal_punct("abc.   ") == "abc"
        assert _strip_terminal_punct("abc .  ") == "abc"

    def test_handles_multiple_terminal_punct(self) -> None:
        """Repeated trailing periods/exclamations all stripped."""
        assert _strip_terminal_punct("hi!!!") == "hi"
        assert _strip_terminal_punct("hi...") == "hi"

    def test_no_punctuation_unchanged(self) -> None:
        assert _strip_terminal_punct("plain text") == "plain text"


# ----------------------------------------------------------------------
# Double-period regression — descriptions ending in '.' produce single
# template-terminator, not "..".
# ----------------------------------------------------------------------

class TestDoublePeriodRegression:
    def test_object_description_with_terminal_period_clean(self) -> None:
        """User content ending in '.' should not produce '..' in output."""
        d = {
            "name": "Account",
            "label": "Account",
            "is_custom": False,
            "description": "Account object.",
            "key_field_names": ["Industry"],
        }
        out = to_semantic_text("Object", d)
        assert "Description: Account object." in out
        assert ".." not in out

    def test_field_help_text_with_terminal_period_clean(self) -> None:
        d = {
            "name": "Industry",
            "object_name": "Account",
            "type": "picklist",
            "label": "Industry",
            "help_text": "Customer industry classification.",
            "is_custom": False,
            "is_required": False,
            "picklist_value_set_name": "IndustryValues",
        }
        out = to_semantic_text("Field", d)
        assert "Help text: Customer industry classification." in out
        assert ".." not in out

    def test_validation_rule_error_message_clean(self) -> None:
        d = {
            "name": "AmountPositive",
            "object_name": "Opportunity",
            "is_active": True,
            "error_message": "Amount must be greater than zero on closed opportunities.",
            "formula_text": "IsClosed && Amount <= 0",
            "error_display_field": "Amount",
        }
        out = to_semantic_text("ValidationRule", d)
        assert "'.'." not in out  # period-inside-quote + period-after-quote
        assert "Error message: 'Amount must be greater than zero on closed opportunities'." in out

    def test_validation_rule_formula_with_period_clean(self) -> None:
        d = {
            "name": "Sample",
            "object_name": "Account",
            "is_active": True,
            "error_message": "msg",
            "formula_text": "Account.Industry = 'Tech'.",
            "error_display_field": "Industry",
        }
        out = to_semantic_text("ValidationRule", d)
        # Formula's terminal period stripped; template adds its own period
        assert "Formula: Account.Industry = 'Tech'." in out
        # Inner period (Account.Industry) preserved
        assert "Account.Industry" in out
        # No double-period at end of formula
        assert "Tech'.." not in out

    def test_flow_description_with_terminal_period_clean(self) -> None:
        d = {
            "name": "MyFlow",
            "process_type": "AutoLaunchedFlow",
            "is_active": True,
            "description": "Updates parent account on contact change.",
            "triggers_on_object": "Contact",
            "trigger_type": "AfterSave",
        }
        out = to_semantic_text("Flow", d)
        assert "Description: Updates parent account on contact change." in out
        assert ".." not in out

    def test_record_type_description_clean(self) -> None:
        d = {
            "name": "Customer",
            "object_name": "Account",
            "label": "Customer Account",
            "is_active": True,
            "description": "Account record type for direct customers.",
        }
        out = to_semantic_text("RecordType", d)
        assert "Description: Account record type for direct customers." in out
        assert ".." not in out

    def test_profile_description_clean(self) -> None:
        d = {
            "name": "System Administrator",
            "license": "Salesforce",
            "description": "Full administrative access.",
            "obj_perm_count": 47,
            "field_perm_count": 312,
        }
        out = to_semantic_text("Profile", d)
        assert "Description: Full administrative access." in out
        assert ".." not in out

    def test_picklist_value_set_description_clean(self) -> None:
        d = {
            "name": "IndustryValues",
            "label": "Industry Values",
            "is_restricted": True,
            "is_global_value_set": False,
            "description": "Standard industry classifications.",
        }
        out = to_semantic_text("PicklistValueSet", d)
        assert "Description: Standard industry classifications." in out
        assert ".." not in out

    def test_permission_set_description_clean(self) -> None:
        d = {
            "name": "DataReader",
            "license": "Salesforce",
            "description": "Read-only access to all data.",
            "obj_perm_count": 12,
            "field_perm_count": 87,
            "parent_profile": "Standard User",
        }
        out = to_semantic_text("PermissionSet", d)
        assert "Description: Read-only access to all data." in out
        assert ".." not in out
