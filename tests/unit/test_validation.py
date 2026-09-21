"""Unit: ``primeqa/shared/validation.py`` — the boundary a create/update
service applies BEFORE any row is built (AUD-011, triage round 2 batch 2).

Pure. The column bounds are read from the SQLAlchemy models themselves, so
these tests also pin that reading: ``String(255)`` refuses 256 characters,
``Integer`` refuses "x" and 10**30, ``Boolean`` refuses "maybe", ``Date``
refuses "not-a-date", ``JSON`` refuses a bare string, ``Text`` refuses a
NUL byte, a non-nullable text column refuses "".
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from primeqa.shared import validation as V

pytestmark = pytest.mark.unit


# --- scalars ------------------------------------------------------------------

@pytest.mark.parametrize("value", ["", "   ", None])
def test_an_empty_required_text_is_refused_naming_the_field(value):
    with pytest.raises(ValueError, match="^name is required$"):
        V.text_value(value, "name", required=True)


@pytest.mark.parametrize("value", ["", "   ", None])
def test_an_empty_optional_text_is_none(value):
    assert V.text_value(value, "version_tag") is None


def test_text_is_trimmed_and_bounded():
    assert V.text_value("  ok  ", "name", max_chars=5) == "ok"
    with pytest.raises(ValueError, match="name too long \\(max 5 characters\\)"):
        V.text_value("A" * 6, "name", max_chars=5)


@pytest.mark.parametrize("value", [12345, 1.5, True, ["a"], {"a": 1}])
def test_a_non_string_is_not_text(value):
    with pytest.raises(ValueError, match="^name must be text$"):
        V.text_value(value, "name")


def test_a_nul_byte_is_refused():
    with pytest.raises(ValueError, match="must not contain NUL"):
        V.text_value("nul\x00byte", "name")


@pytest.mark.parametrize("value,expected", [(7, 7), ("7", 7), (" 42 ", 42), ("-3", -3)])
def test_an_integer_from_json_or_a_form(value, expected):
    assert V.int_value(value, "n") == expected


@pytest.mark.parametrize("value", ["x", "not-an-int", 1.5, True, [1], "9" * 30, 10 ** 30])
def test_a_non_integer_or_an_out_of_range_integer_is_refused(value):
    with pytest.raises(ValueError, match="^n (must be an integer|is out of range)$"):
        V.int_value(value, "n")


@pytest.mark.parametrize("value", [0, -1, "0", "-5"])
def test_an_id_must_be_positive(value):
    with pytest.raises(ValueError, match="^requirement_id must be a positive integer$"):
        V.int_value(value, "requirement_id", positive=True)


def test_an_empty_required_integer_is_refused():
    with pytest.raises(ValueError, match="^section_id is required$"):
        V.int_value(None, "section_id", required=True)
    assert V.int_value("", "position") is None


@pytest.mark.parametrize("value,expected", [(True, True), (False, False), (1, True), (0, False), ("true", True),
                                            ("FALSE", False), ("on", True), ("0", False), ("yes", True)])
def test_a_boolean_from_json_or_a_form(value, expected):
    assert V.bool_value(value, "is_active") is expected


@pytest.mark.parametrize("value", ["maybe", 2, "A" * 20, [True]])
def test_a_non_boolean_is_refused(value):
    with pytest.raises(ValueError, match="^is_active must be true or false$"):
        V.bool_value(value, "is_active")


def test_a_date_from_iso_or_a_date():
    assert V.date_value("2026-09-21", "target_date") == date(2026, 9, 21)
    assert V.date_value(date(2026, 1, 2), "target_date") == date(2026, 1, 2)
    assert V.date_value(datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc), "target_date") == date(2026, 1, 2)
    assert V.date_value("", "target_date") is None


@pytest.mark.parametrize("value", ["not-a-date", "2026-13-45", 20260921, "9999-12-31T"])
def test_a_non_date_is_refused(value):
    with pytest.raises(ValueError, match="^target_date must be a date \\(YYYY-MM-DD\\)$"):
        V.date_value(value, "target_date")


def test_a_datetime_from_iso():
    assert V.datetime_value("2026-09-21T10:00:00Z", "expires_at") == datetime(2026, 9, 21, 10, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="expires_at must be a date-time"):
        V.datetime_value("soon", "expires_at")


@pytest.mark.parametrize("value", ["not-a-dict", 5, True])
def test_json_must_be_an_object_or_a_list(value):
    with pytest.raises(ValueError, match="^decision_criteria must be an object or a list$"):
        V.json_value(value, "decision_criteria")
    assert V.json_value({"a": 1}, "decision_criteria") == {"a": 1}
    assert V.json_value([], "decision_criteria") == []


# --- columns(): the bounds come from the model ----------------------------------------

def test_columns_reads_the_release_models_bounds():
    from primeqa.release.models import Release
    out = V.columns(Release, {"name": " r ", "version_tag": "", "target_date": "2026-09-21", "decision_criteria": {"x": 1},
                              "status": "planning", "not_a_column": "kept"}, required=("name",))
    assert out == {"name": "r", "version_tag": None, "target_date": date(2026, 9, 21), "decision_criteria": {"x": 1},
                   "status": "planning", "not_a_column": "kept"}


@pytest.mark.parametrize("values,field", [
    ({"name": "A" * 256}, "name too long \\(max 255"), ({"name": ""}, "name is required"), ({"name": 123}, "name must be text"),
    ({"version_tag": "A" * 101}, "version_tag too long \\(max 100"), ({"target_date": "x"}, "target_date must be a date"),
    ({"description": 5}, "description must be text"), ({"decision_criteria": "x"}, "decision_criteria must be an object"),
    ({"status": ""}, "status is required"),
])
def test_columns_refuses_what_the_release_columns_refuse(values, field):
    from primeqa.release.models import Release
    with pytest.raises(ValueError, match=field):
        V.columns(Release, values)


def test_columns_treats_an_id_column_as_a_positive_integer_and_a_boolean_as_a_boolean():
    from primeqa.core.models import Environment
    with pytest.raises(ValueError, match="connection_id must be a positive integer"):
        V.columns(Environment, {"connection_id": 0})
    with pytest.raises(ValueError, match="is_active must be true or false"):
        V.columns(Environment, {"is_active": "maybe"})
    with pytest.raises(ValueError, match="max_execution_slots must be an integer"):
        V.columns(Environment, {"max_execution_slots": "x"})
    assert V.columns(Environment, {"is_active": "false", "max_execution_slots": "3", "connection_id": ""}) == \
        {"is_active": False, "max_execution_slots": 3, "connection_id": None}


def test_columns_never_touches_the_identity_columns():
    from primeqa.release.models import Release
    assert V.columns(Release, {"id": "x", "tenant_id": "y", "created_by": "z"}) == {"id": "x", "tenant_id": "y", "created_by": "z"}


def test_columns_requires_a_mapping():
    from primeqa.release.models import Release
    with pytest.raises(ValueError, match="the body must be an object"):
        V.columns(Release, ["not", "a", "mapping"])


def test_the_bound_is_the_column_not_a_second_table():
    """A String(n) column anywhere is bounded by ITS n — the reading, not a list."""
    from primeqa.test_management.models import Requirement
    with pytest.raises(ValueError, match="jira_key too long \\(max 50"):
        V.columns(Requirement, {"jira_key": "A" * 51})
    with pytest.raises(ValueError, match="jira_summary too long \\(max 500"):
        V.columns(Requirement, {"jira_summary": "A" * 501})
    assert V.columns(Requirement, {"jira_description": "A" * 100000})["jira_description"] == "A" * 100000     # Text: unbounded
