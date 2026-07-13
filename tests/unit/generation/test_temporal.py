"""The temporal capability (VR06 arc): RelativeDate protocol, IR decidability,
the S4 materialisation boundary, and the emitted four-arm experiment.

Three layers stay separate: semantic `< TODAY()` / test-design
RelativeDate(RUN_DATE, k) / transport ISO date — the semantic layer never holds
a calendar literal, the recipe persists the symbolic value, only the execution
boundary materialises.
"""
from datetime import date
from uuid import uuid4

import pytest

from primeqa.test_representation.temporal import (
    TemporalReference, is_relative_date, materialise, relative_date,
    relative_date_offset,
)
from primeqa.execution_engine.temporal import (
    TemporalBoundaryClient, capture_temporal_reference,
)
from primeqa.generation.transition import (
    TransitionState, evaluate_transition, satisfy_temporal_boundary,
)
from primeqa.semantic.formula import parse
from tests.unit.generation.test_control_relevance import ALL_VRS

VR06 = next(t for n, t in ALL_VRS if n == "VR06")
RAIL = {
    "PLS_BM_Stage__c": {"field_type": "picklist",
                        "picklist_values": ["Draft", "Contract Review",
                                            "Approved", "Rejected"],
                        "is_createable": True, "is_updateable": True},
    "PLS_BM_Contract_Start_Date__c": {"field_type": "date",
                                      "is_createable": True, "is_updateable": True},
    "PLS_BM_Contract_Number__c": {"field_type": "string", "length": 80,
                                  "is_createable": True, "is_updateable": True},
    "PLS_BM_Deal_Type__c": {"field_type": "picklist",
                            "picklist_values": ["Enterprise", "SMB"],
                            "is_createable": True, "is_updateable": True},
    "PLS_BM_Deal_Value__c": {"field_type": "currency", "scale": 2,
                             "is_createable": True, "is_updateable": True},
    "PLS_BM_Discount__c": {"field_type": "percent", "scale": 2,
                           "is_createable": True, "is_updateable": True},
    "PLS_BM_Risk_Level__c": {"field_type": "picklist",
                             "picklist_values": ["Low", "High", "Critical"],
                             "is_createable": True, "is_updateable": True},
    "PLS_BM_Compliance_Approved__c": {"field_type": "boolean",
                                      "is_createable": True, "is_updateable": True},
    "PLS_BM_Approval_Reason__c": {"field_type": "textarea", "length": 32768,
                                  "is_createable": True, "is_updateable": True},
}
SIBS = [(n, t) for n, t in ALL_VRS if n != "VR06"]


# -- the protocol ---------------------------------------------------------------

def test_relative_date_wire_shape_and_materialisation():
    v = relative_date(-1)
    assert is_relative_date(v) and relative_date_offset(v) == -1
    assert materialise(v, date(2026, 7, 10)) == "2026-07-09"
    assert materialise(relative_date(0), date(2026, 7, 10)) == "2026-07-10"
    assert materialise(relative_date(1), date(2026, 7, 10)) == "2026-07-11"


def test_concrete_values_pass_the_boundary_untouched():
    for v in ("2026-01-01", 42, None, True, {"a": 1}):
        assert materialise(v, date(2026, 7, 10)) == v


def test_unrecognized_symbolic_refuses_before_salesforce():
    with pytest.raises(ValueError):
        materialise({"$something_else": {}}, date(2026, 7, 10))
    with pytest.raises(ValueError):
        materialise({"$relative_date": {"anchor": "EPOCH", "offset_days": 1}},
                    date(2026, 7, 10))


# -- the S4 boundary --------------------------------------------------------------

class _Client:
    def __init__(self):
        self.created = []
        self.updated = []

    def query(self, soql):
        return [{"TimeZoneSidKey": "America/Los_Angeles"}]

    def create(self, sobject, field_values):
        self.created.append(field_values)
        return {"ok": True}

    def update(self, sobject, record_id, field_changes):
        self.updated.append(field_changes)
        return {"ok": True}


def test_capture_uses_org_timezone_with_utc_fallback():
    ref = capture_temporal_reference(_Client())
    assert ref.source == "organization_timezone"
    assert ref.reference_timezone == "America/Los_Angeles"

    class _NoQuery:
        pass
    ref2 = capture_temporal_reference(_NoQuery())
    assert ref2.source == "utc_fallback" and ref2.reference_timezone == "UTC"


def test_boundary_client_materialises_payloads():
    inner = _Client()
    ref = TemporalReference(reference_date=date(2026, 7, 10),
                            reference_timezone="UTC",
                            captured_at="", source="test")
    client = TemporalBoundaryClient(inner, ref)
    client.create("X", {"D__c": relative_date(-1), "N__c": 5})
    client.update("X", "id1", {"D__c": relative_date(1)})
    assert inner.created[0] == {"D__c": "2026-07-09", "N__c": 5}
    assert inner.updated[0] == {"D__c": "2026-07-11"}


# -- IR decidability ----------------------------------------------------------------

def test_relative_date_vs_today_decides_by_offset_sign():
    ast = parse("PLS_BM_Contract_Start_Date__c < TODAY()")

    def ts(v):
        return TransitionState(prior={}, next={"pls_bm_contract_start_date__c": v})
    assert evaluate_transition(ast, ts(relative_date(-1)), absent="blank") is True
    assert evaluate_transition(ast, ts(relative_date(0)), absent="blank") is False
    assert evaluate_transition(ast, ts(relative_date(1)), absent="blank") is False


def test_relative_date_is_never_blank():
    ast = parse("ISBLANK(PLS_BM_Contract_Start_Date__c)")
    ts = TransitionState(prior={}, next={
        "pls_bm_contract_start_date__c": relative_date(0)})
    assert evaluate_transition(ast, ts, absent="blank") is False


def test_vr06_provable_per_arm():
    ast = parse(VR06)

    def ts(v):
        n = {"pls_bm_stage__c": "Approved"}
        if v is not None:
            n["pls_bm_contract_start_date__c"] = v
        return TransitionState(prior={}, next=n)
    assert evaluate_transition(ast, ts(None), absent="blank") is True       # blank
    assert evaluate_transition(ast, ts(relative_date(-1)), absent="blank") is True
    assert evaluate_transition(ast, ts(relative_date(0)), absent="blank") is False
    assert evaluate_transition(ast, ts(relative_date(1)), absent="blank") is False


# -- the experiment -----------------------------------------------------------------

def test_vr06_experiment_shape():
    exp = satisfy_temporal_boundary(VR06, RAIL, sibling_items=SIBS)
    assert exp is not None
    assert exp.changes == {"PLS_BM_Stage__c": "Approved"}
    assert exp.date_field == "PLS_BM_Contract_Start_Date__c"
    # the simplest activating fixture: VR10's gate INACTIVE (Deal_Value
    # unstaged), VR04 isolated via the Contract Number fill
    assert exp.setup_base == {"PLS_BM_Stage__c": "Contract Review",
                              "PLS_BM_Contract_Number__c": "PQA"}
    assert [(a[0], a[2]) for a in exp.arms] == [
        ("blank", True), ("run_date-1", True),
        ("run_date", False), ("run_date+1", False)]


def test_non_temporal_shapes_refuse():
    assert satisfy_temporal_boundary(
        "PLS_BM_Deal_Value__c <= 0", RAIL, SIBS) is None
    vr02 = next(t for n, t in ALL_VRS if n == "VR02")
    assert satisfy_temporal_boundary(vr02, RAIL, SIBS) is None


def test_emitted_temporal_experiment():
    from primeqa.generation.emission import (
        _author_negative, GroundedNegative, _Endpoint)
    g = GroundedNegative(
        archetype="data_behavior", claim_kind="prohibition-claim",
        operation_hint="modify_record", version_seq=1,
        subject=_Endpoint(entity_id=uuid4(), entity_type="Object",
                          external_id="PLS_BM_Deal__c"),
        requirement_excerpt="contract start date must be today or later",
        vr_formulas=(VR06,),
        field_metadata=RAIL,
        vr_messages={t: f"msg {n}" for n, t in ALL_VRS},
    )
    bundle = _author_negative(g, enable_bva_boundaries=True)
    # PRIMARY = the blank arm: create (date ABSENT) -> transition rejected
    steps = bundle.observation_realization.steps
    assert [s.kind for s in steps] == ["create", "update"]
    fv = {k.split(".")[-1]: v for k, v in steps[0].field_values.items()}
    assert "PLS_BM_Contract_Start_Date__c" not in fv       # blank by absence
    assert steps[1].expect_rejection is not None
    # 3 probe arms: past-reject + today/tomorrow accepts
    assert bundle.strategy_kind == "bva"
    assert len(bundle.boundary_recipes) == 3
    kinds = []
    for p in bundle.boundary_recipes:
        psteps = p.observation_realization.steps
        create_fv = {k.split(".")[-1]: v for k, v in psteps[0].field_values.items()}
        update = psteps[1]
        dv = create_fv["PLS_BM_Contract_Start_Date__c"]
        assert is_relative_date(dv)             # the RECIPE persists the symbol
        kinds.append((relative_date_offset(dv),
                      bool(update.expect_rejection)))
    assert sorted(kinds) == [(-1, True), (0, False), (1, False)]


# -- C4: the assertion side ---------------------------------------------------------

def test_boundary_client_materialises_single_values():
    inner = _Client()
    ref = TemporalReference(reference_date=date(2026, 7, 10),
                            reference_timezone="UTC",
                            captured_at="", source="test")
    client = TemporalBoundaryClient(inner, ref)
    assert client.materialise_value(relative_date(5)) == "2026-07-15"
    assert client.materialise_value("Gold") == "Gold"     # pass-through
    assert client.materialise_value(None) is None


def test_run_ground_materialises_a_symbolic_expected():
    # the FL08 assert shape: field == RelativeDate(RUN_DATE, +5) anchors to
    # the SAME run date the payload boundary used
    from types import SimpleNamespace
    from primeqa.execution_engine.data_executor import _run_ground
    inner = _Client()
    ref = TemporalReference(reference_date=date(2026, 7, 10),
                            reference_timezone="UTC",
                            captured_at="", source="test")
    client = TemporalBoundaryClient(inner, ref)
    read_ev = SimpleNamespace(step_id="read-back", sobject="X__c",
                              row_count=1,
                              rows=[{"D__c": "2026-07-15", "Id": "x1"}])
    assertion = SimpleNamespace(
        step_id="assert-1",
        predicate=SimpleNamespace(predicate="equals",
                                  subject_ref="read-back.D__c",
                                  value=relative_date(5)))
    ev = _run_ground(assertion, read_ev, ordinal=3,
                     materialise=client.materialise_value)
    assert ev.held is True
    # a wrong observed date fails honestly
    read_ev.rows[0]["D__c"] = "2026-07-14"
    ev = _run_ground(assertion, read_ev, ordinal=3,
                     materialise=client.materialise_value)
    assert ev.held is False
    # absent materialiser + symbolic expected -> honest fail, never a pass
    read_ev.rows[0]["D__c"] = "2026-07-15"
    ev = _run_ground(assertion, read_ev, ordinal=3)
    assert ev.held is False
