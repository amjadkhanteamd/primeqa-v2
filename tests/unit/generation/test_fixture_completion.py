"""Deterministic accept-fixture completion (AK Option-1 scope) — the D-347
satisfaction operation's first production consumer.

The live catch: VR08's just-inside accept probe (Enterprise + Discount 25%)
armed sibling VR02 (25% > 20% → Approval Reason required) and was setup-rejected
by a control not under test. Completion fills FREE dimensions with typed
minimal-valid values to silence provably-firing siblings; protected dimensions
(target witness / activation / context) are preserved exactly; UNSAT refuses.
"""
from primeqa.generation.emission import _author_negative, GroundedNegative, _Endpoint
from primeqa.generation.fixture import (
    FixtureCompletion, FixtureUnsat, ROLE_SIBLING_ISOLATION,
    complete_accept_fixture,
)
from tests.unit.generation.test_control_relevance import ALL_VRS, VR08
from uuid import uuid4

RAIL = {
    "PLS_BM_Discount__c": {"field_type": "percent", "scale": 2,
                           "is_createable": True, "is_updateable": True},
    "PLS_BM_Approval_Reason__c": {"field_type": "textarea", "length": 32768,
                                  "is_createable": True, "is_updateable": True},
    "__record_types__": {"PLS_BM_Enterprise": "012Ent000000000AAA",
                         "PLS_BM_Standard": "012Std000000000AAA"},
}
STAGED = {"RecordTypeId": "012Ent000000000AAA", "PLS_BM_Discount__c": 0.25}
VR_ITEMS = list(ALL_VRS)


def test_vr02_arming_is_silenced_on_a_free_dimension():
    comp = complete_accept_fixture(STAGED, set(STAGED), VR_ITEMS, RAIL)
    assert isinstance(comp, FixtureCompletion)
    # VR02 (Discount 25% > 20% + blank Approval_Reason) demands the ONE fill —
    # a typed minimal-valid textarea value, never a modification of Discount.
    assert comp.fills == {"PLS_BM_Approval_Reason__c": "PQA"}
    role, src = comp.provenance["PLS_BM_Approval_Reason__c"]
    assert role == ROLE_SIBLING_ISOLATION
    assert src == "VR02"


def test_protected_dimensions_never_modified():
    comp = complete_accept_fixture(STAGED, set(STAGED), VR_ITEMS, RAIL)
    assert not (set(comp.fills) & set(STAGED))


def test_non_firing_siblings_demand_nothing():
    # Discount at 15% (below every discount gate): no sibling provably fires.
    staged = {"RecordTypeId": "012Ent000000000AAA", "PLS_BM_Discount__c": 0.15}
    comp = complete_accept_fixture(staged, set(staged), VR_ITEMS, RAIL)
    assert comp.fills == {}


def test_unsat_when_sibling_only_silenceable_on_protected():
    # A contrived sibling that fires on the staged Discount alone: the only
    # falsification touches the protected witness → UNSAT, never modified.
    sib = ("VRX", "PLS_BM_Discount__c > 0.10")
    res = complete_accept_fixture(STAGED, set(STAGED), [sib], RAIL)
    assert isinstance(res, FixtureUnsat)
    assert "VRX" in res.reason and "protected" in res.reason


def test_unparseable_sibling_is_skipped_not_guessed():
    res = complete_accept_fixture(STAGED, set(STAGED),
                                  [("VRBAD", "((broken")], RAIL)
    assert isinstance(res, FixtureCompletion) and res.fills == {}


# -- end-to-end: the emitted VR08 probe stages the isolation fill --------------

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


def test_emitted_probe_carries_isolation_fill_and_provenance():
    bundle = _author_negative(_grounded_vr08(), enable_bva_boundaries=True)
    assert bundle.strategy_kind == "bva"
    probe = bundle.boundary_recipes[0]
    create = probe.observation_realization.steps[0]
    assert create.field_values == {
        "PLS_BM_Deal__c.RecordTypeId": "012Ent000000000AAA",
        "PLS_BM_Deal__c.PLS_BM_Discount__c": 25,       # transported just-inside
        "PLS_BM_Deal__c.PLS_BM_Approval_Reason__c": "PQA",  # sibling isolation
    }
    detail = probe.execution_environment.auth_assumptions[0].details
    assert "fixture provenance" in detail
    assert "target witness" in detail
    assert "context" in detail
    assert "sibling isolation" in detail


def test_emitted_probe_read_asserts_boundary_field_only():
    bundle = _author_negative(_grounded_vr08(), enable_bva_boundaries=True)
    probe = bundle.boundary_recipes[0]
    _create, read, assertion = probe.observation_realization.steps
    assert read.fields_to_capture == ["PLS_BM_Deal__c.PLS_BM_Discount__c"]
    assert assertion.predicate.value == 25


def test_unsat_authors_no_probe_claim_stays_single():
    # Add a sibling silenceable only on the protected Discount → no probe.
    g = _grounded_vr08()
    g = GroundedNegative(**{**g.__dict__,
                            "vr_messages": {**g.vr_messages,
                                            "PLS_BM_Discount__c > 0.10": "vrx msg"}})
    bundle = _author_negative(g, enable_bva_boundaries=True)
    assert bundle.boundary_recipes == ()
    assert bundle.strategy_kind is None
