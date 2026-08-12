"""D-449 pins: realized temporal payloads persist beside the symbolic tokens.

Direction pins: the TRANSPORT payload is byte-identical to before (capture
only — asserted against `materialise` directly, and identity-object for a
plain payload); the recording clears between calls (a stale realized payload
never leaks onto the next step); a symbolic payload crossing the boundary
without a recording FAILS LOUD; evidence carries BOTH values; the serialized
trace of a NON-temporal run is byte-identical (no new keys, no envelope
reference)."""
from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from primeqa.execution_engine.data_executor import (
    _realized, execute_data_recipe)
from primeqa.execution_engine.result_store import _evidence_trace
from primeqa.execution_engine.temporal import TemporalBoundaryClient
from primeqa.execution_engine.plan import DataRecipePlan, PlannedCreate
from primeqa.test_representation.models.primitives import RejectionExpectation
from primeqa.test_representation.models.references import LogicalRef
from primeqa.test_representation.temporal import (
    TemporalReference, materialise, relative_date)

pytestmark = pytest.mark.unit

_VR_CODE = "FIELD_CUSTOM_VALIDATION_EXCEPTION"
_REF = TemporalReference(reference_date=date(2026, 8, 12),
                         reference_timezone="Asia/Kolkata",
                         captured_at="2026-08-12T06:00:00+00:00",
                         source="organization_timezone")


class _Inner:
    def __init__(self, create_result):
        self._create_result = create_result
        self.creates = []
        self.deletes = []

    def create(self, sobject, field_values):
        self.creates.append((sobject, field_values))
        return self._create_result

    def delete(self, sobject, record_id):
        self.deletes.append((sobject, record_id))
        return {"success": True}


def _rejected():
    return {"api_response": {"status_code": 400,
                             "body": [{"errorCode": _VR_CODE,
                                       "message": "no", "fields": []}]},
            "http_status": 400, "success": False, "record_id": None}


def _neg_plan(field_values):
    return DataRecipePlan(
        recipe_id=uuid4(), recipe_version_seq=1, claim_test_id=uuid4(),
        claim_version_seq=None, api_choice="rest",
        steps=(PlannedCreate(
            step_id="create-violating",
            target_object=LogicalRef(entity_type="Object",
                                     external_id="PLS_BM_Deal__c"),
            field_values=field_values,
            expect_rejection=RejectionExpectation(error_code=_VR_CODE)),))


# ---------------------------------------------------------------------------
# The wrapper's recording + transport byte-identity
# ---------------------------------------------------------------------------

def test_symbolic_payload_transport_is_exactly_materialise():
    inner = _Inner(_rejected())
    client = TemporalBoundaryClient(inner, _REF)
    payload = {"D__c": relative_date(1), "N__c": 5}
    client.create("X__c", payload)
    sent = inner.creates[0][1]
    assert sent == materialise_all(payload)
    assert sent["D__c"] == "2026-08-13" and sent["N__c"] == 5
    assert client.last_realized == sent


def materialise_all(payload):
    return {k: materialise(v, _REF.reference_date) for k, v in payload.items()}


def test_plain_payload_passes_identity_and_records_none():
    inner = _Inner(_rejected())
    client = TemporalBoundaryClient(inner, _REF)
    payload = {"N__c": 5}
    client.create("X__c", payload)
    assert inner.creates[0][1] is payload      # the IDENTICAL object
    assert client.last_realized is None


def test_recording_clears_between_calls():
    inner = _Inner(_rejected())
    client = TemporalBoundaryClient(inner, _REF)
    client.create("X__c", {"D__c": relative_date(1)})
    assert client.last_realized is not None
    client.create("X__c", {"N__c": 1})
    assert client.last_realized is None        # no stale leak


# ---------------------------------------------------------------------------
# _realized: the fail-loud capture
# ---------------------------------------------------------------------------

def test_realized_none_for_plain_payloads():
    class _Bare:
        pass
    assert _realized(_Bare(), {"N__c": 1}) is None
    assert _realized(_Bare(), {}) is None
    assert _realized(_Bare(), None) is None


def test_realized_fails_loud_without_a_recording():
    class _Bare:
        pass
    with pytest.raises(RuntimeError, match="D-449 fail-loud"):
        _realized(_Bare(), {"D__c": relative_date(1)})


# ---------------------------------------------------------------------------
# End-to-end: evidence carries both; non-temporal traces byte-identical
# ---------------------------------------------------------------------------

def test_evidence_carries_symbolic_and_realized_plus_reference():
    inner = _Inner(_rejected())
    token = relative_date(1)
    ev = execute_data_recipe(
        _neg_plan({"PLS_BM_Deal__c.PLS_BM_Contract_Start_Date__c": token}),
        client=TemporalBoundaryClient(inner, _REF), environment_id=7)
    step = ev.steps[0]
    key = "PLS_BM_Deal__c.PLS_BM_Contract_Start_Date__c"
    assert step.field_values[key] == token                       # symbolic kept
    assert step.field_values_realized[key] == "2026-08-13"      # realized beside
    assert ev.temporal_reference == {
        "reference_date": "2026-08-12",
        "reference_timezone": "Asia/Kolkata",
        "captured_at": "2026-08-12T06:00:00+00:00",
        "source": "organization_timezone"}
    trace = _evidence_trace(ev)
    tstep = trace["steps"][0]
    assert tstep["field_values_realized"][key] == "2026-08-13"
    assert trace["temporal_reference"]["reference_date"] == "2026-08-12"


def test_non_temporal_trace_is_byte_identical():
    inner = _Inner(_rejected())
    ev = execute_data_recipe(
        _neg_plan({"PLS_BM_Deal__c.PLS_BM_Deal_Value__c": 5}),
        client=TemporalBoundaryClient(inner, _REF), environment_id=7)
    assert ev.temporal_reference is None       # nothing materialised
    trace = _evidence_trace(ev)
    assert "temporal_reference" not in trace
    for s in trace["steps"]:
        assert "field_values_realized" not in s
        assert "field_changes_realized" not in s
