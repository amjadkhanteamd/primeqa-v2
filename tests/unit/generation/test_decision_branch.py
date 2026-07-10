"""DecisionBranchCoverage (VR03 arc) — logical branch coverage over
``A AND (B OR C) AND D``: isolated firing witnesses per branch group +
necessity controls per gate, all minimally violating / boundary-honest,
sibling-isolated via the existing D-354 completion.
"""
from uuid import uuid4

from primeqa.generation.decision_branch import (
    decision_branch_shape, satisfy_decision_branches,
)
from tests.unit.generation.test_control_relevance import ALL_VRS

VR03 = next(t for n, t in ALL_VRS if n == "VR03")
RAIL = {
    "PLS_BM_Deal_Value__c": {"field_type": "currency", "scale": 2,
                             "is_createable": True, "is_updateable": True},
    "PLS_BM_Discount__c": {"field_type": "percent", "scale": 2,
                           "is_createable": True, "is_updateable": True},
    "PLS_BM_Risk_Level__c": {"field_type": "picklist",
                             "picklist_values": ["Low", "Medium", "High", "Critical"],
                             "is_createable": True, "is_updateable": True},
    "PLS_BM_Compliance_Approved__c": {"field_type": "boolean",
                                      "is_createable": True, "is_updateable": True},
    "PLS_BM_Stage__c": {"field_type": "picklist",
                        "picklist_values": ["Draft", "Contract Review", "Approved"],
                        "is_createable": True, "is_updateable": True},
    "PLS_BM_Contract_Number__c": {"field_type": "string", "length": 80,
                                  "is_createable": True, "is_updateable": True},
    "PLS_BM_Contract_Start_Date__c": {"field_type": "date",
                                      "is_createable": True, "is_updateable": True},
    "PLS_BM_Approval_Reason__c": {"field_type": "textarea", "length": 32768,
                                  "is_createable": True, "is_updateable": True},
    "PLS_BM_External_Reference__c": {"field_type": "string", "length": 40,
                                     "is_createable": True, "is_updateable": True},
}
SIBS = [(n, t) for n, t in ALL_VRS if n != "VR03"]


def _exp():
    return satisfy_decision_branches(VR03, RAIL, sibling_items=SIBS)


def test_shape_recognizer():
    assert decision_branch_shape(VR03) is not None
    assert decision_branch_shape("PLS_BM_Deal_Value__c <= 0") is None
    vr02 = next(t for n, t in ALL_VRS if n == "VR02")
    assert decision_branch_shape(vr02) is None          # no disjunction


def test_five_arms_two_firing_three_controls():
    exp = _exp()
    assert exp is not None
    labels = [(a.label, a.expect_reject) for a in exp.arms]
    assert labels == [
        ("branch:PLS_BM_Discount__c", True),
        ("branch:PLS_BM_Risk_Level__c", True),
        ("control:or-gate", False),
        ("control:PLS_BM_Deal_Value__c", False),
        ("control:PLS_BM_Compliance_Approved__c", False),
    ]


def test_discount_branch_fires_minimally_below_vr02():
    a = _exp().arms[0]
    # 15.01% — minimally violating, and naturally below VR02's 20% gate
    assert a.payload["PLS_BM_Discount__c"] == 0.1501
    assert a.payload["PLS_BM_Risk_Level__c"] == "Low"      # non-target held false
    assert a.payload["PLS_BM_Compliance_Approved__c"] is False
    assert a.payload["PLS_BM_Deal_Value__c"] == 1000000.01


def test_risk_branch_fires_with_discount_held_at_boundary():
    a = _exp().arms[1]
    assert a.payload["PLS_BM_Risk_Level__c"] == "High"
    assert a.payload["PLS_BM_Discount__c"] == 0.15         # exactly 15% = false


def test_gate_controls_are_boundary_honest():
    exp = _exp()
    or_ctl, dv_ctl, comp_ctl = exp.arms[2], exp.arms[3], exp.arms[4]
    # OR-gate: every branch false, gates true
    assert or_ctl.payload["PLS_BM_Discount__c"] == 0.15
    assert or_ctl.payload["PLS_BM_Risk_Level__c"] == "Low"
    assert or_ctl.payload["PLS_BM_Compliance_Approved__c"] is False
    # Deal_Value gate: exactly 1,000,000 ('greater than' false AT the boundary)
    assert dv_ctl.payload["PLS_BM_Deal_Value__c"] == 1000000
    assert dv_ctl.payload["PLS_BM_Discount__c"] == 0.1501  # firing config held
    # Compliance gate: true
    assert comp_ctl.payload["PLS_BM_Compliance_Approved__c"] is True
    assert comp_ctl.payload["PLS_BM_Deal_Value__c"] == 1000000.01


def test_no_sibling_provably_fires_in_any_arm():
    from primeqa.generation.transition import TransitionState, evaluate_transition, _bare
    from primeqa.semantic.formula import parse
    exp = _exp()
    for arm in exp.arms:
        ts = TransitionState(prior=_bare(arm.payload), next=_bare(arm.payload))
        assert evaluate_transition(parse(VR03), ts, absent="blank") \
            is arm.expect_reject, arm.label
        for name, text in SIBS:
            assert evaluate_transition(parse(text), ts, absent="blank") \
                is not True, (arm.label, name)


def test_emitted_experiment_primary_plus_four_probes():
    from primeqa.generation.emission import (
        _author_negative, GroundedNegative, _Endpoint)
    g = GroundedNegative(
        archetype="data_behavior", claim_kind="prohibition-claim",
        operation_hint="modify_record", version_seq=1,
        subject=_Endpoint(entity_id=uuid4(), entity_type="Object",
                          external_id="PLS_BM_Deal__c"),
        requirement_excerpt="high-value deals with significant discounts or risk",
        vr_formulas=(VR03,),
        field_metadata=RAIL,
        vr_messages={t: f"msg {n}" for n, t in ALL_VRS},
    )
    bundle = _author_negative(g, enable_bva_boundaries=True)
    assert bundle.strategy_kind == "bva"
    # PRIMARY: the Discount-branch create-rejected
    create = bundle.observation_realization.steps[0]
    fv = {k.split(".")[-1]: v for k, v in create.field_values.items()}
    assert fv["PLS_BM_Discount__c"] == 15.01               # transported
    assert create.expect_rejection is not None
    # 4 probes: 1 reject (Risk branch, attributed) + 3 accepts (read-back)
    assert len(bundle.boundary_recipes) == 4
    rejects = accepts = 0
    for p in bundle.boundary_recipes:
        steps = p.observation_realization.steps
        if steps[0].expect_rejection is not None:
            rejects += 1
            pfv = {k.split(".")[-1]: v for k, v in steps[0].field_values.items()}
            assert pfv["PLS_BM_Risk_Level__c"] == "High"
            assert "VR03" in (steps[0].expect_rejection.error_message_pattern or "")
        else:
            accepts += 1
            assert steps[-1].kind == "assert"              # the read-back
    assert (rejects, accepts) == (1, 3)
