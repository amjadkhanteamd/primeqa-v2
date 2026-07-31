"""D-424 (the D-400 closure): the assert-evidence value envelope.

Pins the five contract cases — value present, value absent (a pre-change
run's step dict), a type-coerced D-211 match, a multi-row read, and a
zero-row read — plus: every predicate on both producers sets
``observed_kind`` (the fail-loud law), the temporal materialise path keeps
the symbolic form, scope stays the asserted field only (never the row),
and the envelope serializes JSONB-safe through the existing trace builder.
No org, no PG.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

from primeqa.execution_engine.data_executor import _run_ground
from primeqa.execution_engine.evidence import DataReadEvidence
from primeqa.execution_engine.executor import _run_assert
from primeqa.execution_engine.plan import PlannedAssertion
from primeqa.execution_engine.result_store import _jsonable
from primeqa.intelligence.claim_presentation import step_plain
from primeqa.test_representation.models.primitives import AssertionPredicate


def _read_ev(rows, *, sobject="Opportunity", step_id="read-created",
             fields=("Risk_Rating__c",)):
    now = datetime.now(timezone.utc)
    return DataReadEvidence(
        step_id=step_id, ordinal=1, soql="SELECT ...", sobject=sobject,
        fields_captured=tuple(fields), row_count=len(rows), rows=tuple(rows),
        started_at=now, finished_at=now, duration_ms=1)


def _assertion(predicate, value=None, *, field="Risk_Rating__c",
               read_step="read-created"):
    subject = f"{read_step}.{field}"
    return PlannedAssertion(
        step_id="assert-value",
        predicate=AssertionPredicate(
            subject_ref=subject, predicate=predicate, value=value))


# ---------------------------------------------------------------------------
# Pin 1 — value present: equals persists both sides, scoped to the field
# ---------------------------------------------------------------------------

def test_equals_persists_asserted_and_observed_value():
    read = _read_ev([{"Id": "001A", "Risk_Rating__c": "Medium",
                      "StageName": "Prospecting"}],
                    fields=("Risk_Rating__c", "StageName"))
    ev = _run_ground(_assertion("equals", "High"), read, ordinal=2)
    assert ev.held is False
    assert ev.asserted_field == "Risk_Rating__c"
    assert ev.asserted_value == "High"
    assert ev.observed_value == "Medium"
    assert ev.observed_kind == "field_value"
    assert ev.asserted_value_symbolic is None


def test_scope_is_the_asserted_field_only_never_the_row():
    # The row carries sibling captured fields + Id; the envelope must not.
    read = _read_ev([{"Id": "001A", "Risk_Rating__c": "High",
                      "StageName": "Prospecting", "Amount": 5000.0}],
                    fields=("Risk_Rating__c", "StageName", "Amount"))
    ev = _run_ground(_assertion("equals", "High"), read, ordinal=2)
    payload = dataclasses.asdict(ev)
    blob = str(payload)
    assert "Prospecting" not in blob and "5000" not in blob and "001A" not in blob
    assert payload["observed_value"] == "High"


# ---------------------------------------------------------------------------
# Pin 2 — value absent: a pre-change step dict renders "not captured"
# ---------------------------------------------------------------------------

def test_pre_change_failed_assert_renders_not_captured():
    old = {"kind": "assert", "predicate": "equals", "held": False,
           "subject_ref": "read-created.Risk_Rating__c",
           "evaluated_row_count": 1}
    text = step_plain(old)
    assert "not captured" in text
    assert "did NOT hold" in text
    # Never an empty or zero value masquerading as captured.
    assert "= 0" not in text and "=  " not in text and "None" not in text


def test_pre_change_held_assert_keeps_the_established_sentence():
    old = {"kind": "assert", "predicate": "equals", "held": True,
           "subject_ref": "read-created.Risk_Rating__c",
           "evaluated_row_count": 1}
    assert step_plain(old) == "Checked the records — the assertion held"


# ---------------------------------------------------------------------------
# Pin 3 — D-211 type-coerced match: both sides persist RAW
# ---------------------------------------------------------------------------

def test_coerced_match_persists_raw_types_on_both_sides():
    read = _read_ev([{"Amount": 5000.0}], fields=("Amount",))
    ev = _run_ground(_assertion("equals", "5000", field="Amount"),
                     read, ordinal=2)
    assert ev.held is True                      # D-211 tolerant match
    assert ev.asserted_value == "5000" and isinstance(ev.asserted_value, str)
    assert ev.observed_value == 5000.0 and isinstance(ev.observed_value, float)


# ---------------------------------------------------------------------------
# Pin 4 — multi-row read: the graded row (rows[0]) is what persists
# ---------------------------------------------------------------------------

def test_multi_row_read_persists_the_graded_first_row_value():
    read = _read_ev([{"Risk_Rating__c": "High"}, {"Risk_Rating__c": "Low"}])
    ev = _run_ground(_assertion("equals", "High"), read, ordinal=2)
    assert ev.held is True
    assert ev.evaluated_row_count == 2
    assert ev.observed_value == "High" and ev.observed_kind == "field_value"


# ---------------------------------------------------------------------------
# Pin 5 — zero-row read: "no_row", never None-as-captured-blank
# ---------------------------------------------------------------------------

def test_zero_row_equals_records_no_row_not_a_blank():
    ev = _run_ground(_assertion("equals", "High"), _read_ev([]), ordinal=2)
    assert ev.held is False
    assert ev.observed_kind == "no_row" and ev.observed_value is None
    assert ev.asserted_field == "Risk_Rating__c" and ev.asserted_value == "High"


def test_zero_row_not_null_records_no_row():
    ev = _run_ground(_assertion("not_null"), _read_ev([]), ordinal=2)
    assert ev.held is False and ev.observed_kind == "no_row"


# ---------------------------------------------------------------------------
# Fail-loud law: every data predicate sets observed_kind
# ---------------------------------------------------------------------------

def test_every_data_predicate_sets_observed_kind():
    cases = [
        (_assertion("exists"), _read_ev([{"Id": "1"}]), "row_count", 1),
        (_assertion("not_exists"), _read_ev([]), "row_count", 0),
        (_assertion("count_equals", 2),
         _read_ev([{"Id": "1"}, {"Id": "2"}]), "row_count", 2),
        (_assertion("not_null"),
         _read_ev([{"Risk_Rating__c": "High"}]), "field_value", "High"),
    ]
    for assertion, read, kind, observed in cases:
        ev = _run_ground(assertion, read, ordinal=2)
        assert ev.observed_kind == kind, assertion.predicate.predicate
        assert ev.observed_value == observed, assertion.predicate.predicate
        assert ev.held is True, assertion.predicate.predicate


def test_count_equals_persists_the_asserted_count():
    ev = _run_ground(_assertion("count_equals", 3),
                     _read_ev([{"Id": "1"}]), ordinal=2)
    assert ev.held is False
    assert ev.asserted_value == 3 and ev.observed_value == 1
    assert ev.observed_kind == "row_count"


# ---------------------------------------------------------------------------
# Temporal (C4): materialised value persists, symbolic form kept alongside
# ---------------------------------------------------------------------------

def test_materialised_expected_keeps_the_symbolic_form():
    symbolic = {"$relative_date": {"anchor": "RUN_DATE", "offset_days": 30}}
    read = _read_ev([{"Close_Date__c": "2026-08-30"}], fields=("Close_Date__c",))
    ev = _run_ground(
        _assertion("equals", symbolic, field="Close_Date__c"), read,
        ordinal=2, materialise=lambda v: "2026-08-30")
    assert ev.held is True
    assert ev.asserted_value == "2026-08-30"
    assert ev.asserted_value_symbolic == symbolic


def test_untransformed_materialise_sets_no_symbolic():
    read = _read_ev([{"Risk_Rating__c": "High"}])
    ev = _run_ground(_assertion("equals", "High"), read, ordinal=2,
                     materialise=lambda v: v)
    assert ev.asserted_value == "High" and ev.asserted_value_symbolic is None


# ---------------------------------------------------------------------------
# The inspection producer (executor._run_assert) obeys the same envelope
# ---------------------------------------------------------------------------

def _inspection_step(predicate, value=None):
    return PlannedAssertion(
        step_id="assert-1",
        predicate=AssertionPredicate(
            subject_ref="read-1", predicate=predicate, value=value))


def test_inspection_equals_persists_values():
    ev = _run_assert(_inspection_step("equals", "5"), 1,
                     {"read-1": [{"Length": 10}]}, {"read-1": "Length"})
    assert ev.held is False
    assert ev.asserted_field == "Length" and ev.asserted_value == "5"
    assert ev.observed_value == 10 and ev.observed_kind == "field_value"


def test_inspection_is_null_and_exists_set_observed_kind():
    is_null = _run_assert(_inspection_step("is_null"), 1,
                          {"read-1": [{"Formula": None}]},
                          {"read-1": "Formula"})
    assert is_null.held is True and is_null.observed_kind == "field_value"
    assert is_null.observed_value is None and is_null.asserted_field == "Formula"
    exists = _run_assert(_inspection_step("exists"), 1,
                         {"read-1": [{"f": 1}, {"f": 2}]}, {"read-1": None})
    assert exists.observed_kind == "row_count" and exists.observed_value == 2


def test_inspection_zero_rows_records_no_row():
    ev = _run_assert(_inspection_step("equals", "5"), 1,
                     {"read-1": []}, {"read-1": "Length"})
    assert ev.held is False and ev.observed_kind == "no_row"
    assert ev.observed_value is None


# ---------------------------------------------------------------------------
# Persistence: the envelope is JSONB-safe through the existing trace builder
# ---------------------------------------------------------------------------

def test_envelope_serializes_jsonb_safe():
    import json
    read = _read_ev([{"Amount": 5000.0}], fields=("Amount",))
    ev = _run_ground(_assertion("equals", "5000", field="Amount"),
                     read, ordinal=2)
    payload = _jsonable(dataclasses.asdict(ev))
    json.dumps(payload)                          # must not raise
    assert payload["asserted_value"] == "5000"
    assert payload["observed_value"] == 5000.0
    assert payload["observed_kind"] == "field_value"


# ---------------------------------------------------------------------------
# Render: post-change step dicts phrase the comparison from the evidence
# ---------------------------------------------------------------------------

def test_step_plain_phrases_field_value_comparison():
    step = {"kind": "assert", "predicate": "equals", "held": False,
            "asserted_field": "Risk_Rating__c", "asserted_value": "High",
            "observed_value": "Medium", "observed_kind": "field_value"}
    text = step_plain(step)
    assert "expected Risk_Rating__c = High" in text
    assert "observed Medium" in text


def test_step_plain_phrases_no_row_and_counts():
    no_row = {"kind": "assert", "predicate": "equals", "held": False,
              "asserted_field": "F__c", "asserted_value": "X",
              "observed_value": None, "observed_kind": "no_row"}
    assert "no record was there to observe" in step_plain(no_row)
    count = {"kind": "assert", "predicate": "count_equals", "held": False,
             "asserted_value": 2, "observed_value": 1,
             "observed_kind": "row_count"}
    text = step_plain(count)
    assert "expected 2" in text and "found 1 matching record" in text


def test_step_plain_renders_observed_blank_not_empty():
    step = {"kind": "assert", "predicate": "equals", "held": False,
            "asserted_field": "F__c", "asserted_value": "X",
            "observed_value": None, "observed_kind": "field_value"}
    text = step_plain(step)
    assert "observed blank" in text and "None" not in text
