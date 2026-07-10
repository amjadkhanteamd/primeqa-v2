"""ContextDifferential (RECORD dimension) — Amendment B §4, composed with
BoundaryPair around the shared minimal-violating witness.

The control arm: the SAME above-boundary value the in-context arm rejects,
under the ALTERNATIVE record classification, expected ACCEPTED — proving the
rule is context-scoped. Derived BY REFERENCE from the firing member (exactly
one mutation: RecordTypeId), so the single-dimension guarantee is structural
and the treatment arm dedups by construction (it IS the primary's proof).
"""
from uuid import uuid4

from primeqa.generation.emission import _author_negative, GroundedNegative, _Endpoint
from primeqa.generation.verified_negative import (
    BoundaryMember, derive_boundary_set, derive_context_control,
)
from primeqa.semantic.formula import parse
from tests.unit.generation.test_control_relevance import ALL_VRS, VR08

RAIL = {
    "PLS_BM_Discount__c": {"field_type": "percent", "scale": 2,
                           "is_createable": True, "is_updateable": True},
    "PLS_BM_Approval_Reason__c": {"field_type": "textarea", "length": 32768,
                                  "is_createable": True, "is_updateable": True},
    "__record_types__": {"PLS_BM_Enterprise": "012Ent000000000AAA",
                         "PLS_BM_Standard": "012Std000000000AAA"},
}


def _firing():
    members = derive_boundary_set(parse(VR08), RAIL)
    return next(m for m in members if m.expect_reject)


# -- the primitive --------------------------------------------------------------

def test_control_flips_exactly_the_record_type_dimension():
    firing = _firing()
    control = derive_context_control(firing, RAIL)
    assert control.expect_reject is False and control.edge == "context-control"
    # exactly ONE mutation: RecordTypeId → the alternative (Standard)
    assert control.payload["RecordTypeId"] == "012Std000000000AAA"
    # every other field byte-identical — the single-dimension guarantee
    assert {k: v for k, v in control.payload.items() if k != "RecordTypeId"} \
        == {k: v for k, v in firing.payload.items() if k != "RecordTypeId"}
    # the held value is the ABOVE-boundary witness (0.2501), not the just-inside
    assert control.payload["PLS_BM_Discount__c"] == 0.2501
    assert control.boundary_field == firing.boundary_field


def test_no_record_type_staged_no_control():
    plain = BoundaryMember(payload={"Amount": 10001}, expect_reject=True,
                           edge="firing", boundary_field="Amount")
    assert derive_context_control(plain, RAIL) is None


def test_no_alternative_record_type_no_control():
    rail = {**RAIL, "__record_types__": {"PLS_BM_Enterprise": "012Ent000000000AAA"}}
    assert derive_context_control(_firing(), rail) is None


def test_alternative_choice_is_deterministic():
    rail = {**RAIL, "__record_types__": {
        "PLS_BM_Enterprise": "012Ent000000000AAA",
        "PLS_BM_Zeta": "012Zet000000000AAA",
        "PLS_BM_Alpha": "012Alp000000000AAA"}}
    control = derive_context_control(_firing(), rail)
    # sorted by devname → Alpha wins, order-independent of rail construction
    assert control.payload["RecordTypeId"] == "012Alp000000000AAA"


# -- the emitted composition ----------------------------------------------------

def _grounded_vr08():
    return GroundedNegative(
        archetype="data_behavior", claim_kind="prohibition-claim",
        operation_hint="modify_record", version_seq=1,
        subject=_Endpoint(entity_id=uuid4(), entity_type="Object",
                          external_id="PLS_BM_Deal__c"),
        requirement_excerpt="Enterprise deals stricter discount controls",
        vr_formulas=(VR08,),
        field_metadata=RAIL,
        vr_messages={text: f"msg {name}" for name, text in ALL_VRS},
    )


def test_bundle_carries_boundary_probe_plus_context_control():
    bundle = _author_negative(_grounded_vr08(), enable_bva_boundaries=True)
    assert bundle.strategy_kind == "bva"
    edges = {}
    for probe in bundle.boundary_recipes:
        detail = probe.execution_environment.auth_assumptions[0].details
        create = probe.observation_realization.steps[0]
        edges["context-control" if "context-differential control" in detail
              else "just-inside"] = create.field_values
    assert set(edges) == {"just-inside", "context-control"}
    # just-inside: Enterprise at the boundary (25), VR02-isolated
    assert edges["just-inside"] == {
        "PLS_BM_Deal__c.RecordTypeId": "012Ent000000000AAA",
        "PLS_BM_Deal__c.PLS_BM_Discount__c": 25,
        "PLS_BM_Deal__c.PLS_BM_Approval_Reason__c": "PQA"}
    # control: Standard at the SAME above-boundary value (25.01), VR02-isolated
    assert edges["context-control"] == {
        "PLS_BM_Deal__c.RecordTypeId": "012Std000000000AAA",
        "PLS_BM_Deal__c.PLS_BM_Discount__c": 25.01,
        "PLS_BM_Deal__c.PLS_BM_Approval_Reason__c": "PQA"}


def test_control_provenance_marks_the_varied_dimension():
    bundle = _author_negative(_grounded_vr08(), enable_bva_boundaries=True)
    control = next(p for p in bundle.boundary_recipes
                   if "context-differential control"
                   in p.execution_environment.auth_assumptions[0].details)
    detail = control.execution_environment.auth_assumptions[0].details
    assert "varied: the differential's control arm" in detail
    assert "sibling isolation" in detail          # the VR02 fill is recorded


def test_no_alternative_record_type_two_member_fallback():
    g = _grounded_vr08()
    rail = {**RAIL, "__record_types__": {"PLS_BM_Enterprise": "012Ent000000000AAA"}}
    g = GroundedNegative(**{**g.__dict__, "field_metadata": rail})
    bundle = _author_negative(g, enable_bva_boundaries=True)
    # boundary probe only — a control arm is never fabricated
    assert len(bundle.boundary_recipes) == 1
    detail = bundle.boundary_recipes[0].execution_environment.auth_assumptions[0].details
    assert "bva boundary probe" in detail
