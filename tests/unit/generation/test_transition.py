"""Transition IR — TransitionState + evaluate/satisfy (the VR10/VR05 arc).

One explicit representation for transition semantics (AK's instruction): org-state
functions become decidable predicates over (prior, next); the satisfaction
operation realizes a transition-shaped prohibition as setup + changes, violating
exactly one approval branch while satisfying the rest and every sibling control.
"""
from primeqa.generation.transition import (
    FAR_FUTURE_DATE,  # noqa: F401 — the legacy axiom keeps a compat test

    TransitionState,
    evaluate_transition,
    has_transition_semantics,
    satisfy_transition,
)
from primeqa.generation.fixture import (
    ROLE_SIBLING_ISOLATION, ROLE_TARGET_ACTIVATION, ROLE_TARGET_WITNESS,
)
from primeqa.semantic.formula import parse
from primeqa.test_representation.temporal import relative_date
from tests.unit.generation.test_control_relevance import ALL_VRS, VR05, VR10

RAIL = {
    "PLS_BM_Stage__c": {"field_type": "picklist",
                        "picklist_values": ["Draft", "Approved", "Rejected"],
                        "is_createable": True, "is_updateable": True},
    "PLS_BM_Deal_Type__c": {"field_type": "picklist",
                            "picklist_values": ["Enterprise", "SMB"],
                            "is_createable": True, "is_updateable": True},
    "PLS_BM_Risk_Level__c": {"field_type": "picklist",
                             "picklist_values": ["Low", "Medium", "High", "Critical"],
                             "is_createable": True, "is_updateable": True},
    "PLS_BM_Deal_Value__c": {"field_type": "currency", "scale": 2,
                             "is_createable": True, "is_updateable": True},
    "PLS_BM_Discount__c": {"field_type": "percent", "scale": 2,
                           "is_createable": True, "is_updateable": True},
    "PLS_BM_Compliance_Approved__c": {"field_type": "boolean",
                                      "is_createable": True, "is_updateable": True},
    "PLS_BM_Contract_Number__c": {"field_type": "string", "length": 80,
                                  "is_createable": True, "is_updateable": True},
    "PLS_BM_Contract_Start_Date__c": {"field_type": "date",
                                      "is_createable": True, "is_updateable": True},
    "PLS_BM_Approval_Reason__c": {"field_type": "textarea", "length": 32768,
                                  "is_createable": True, "is_updateable": True},
}
SIBLINGS = [(n, t) for n, t in ALL_VRS if n != "VR10"]


def _ts(setup, changes):
    prior = {k.lower(): v for k, v in setup.items()}
    return TransitionState(prior=prior,
                           next={**prior, **{k.lower(): v for k, v in changes.items()}})


# -- evaluate_transition: org-state functions decidable over the pair -----------

def test_ischanged_decidable_over_the_pair():
    ast = parse("ISCHANGED(PLS_BM_Stage__c)")
    assert evaluate_transition(ast, _ts({"PLS_BM_Stage__c": "Draft"},
                                        {"PLS_BM_Stage__c": "Approved"})) is True
    assert evaluate_transition(ast, _ts({"PLS_BM_Stage__c": "Draft"}, {})) is False


def test_priorvalue_reads_the_prior_phase():
    ast = parse('ISPICKVAL(PRIORVALUE(PLS_BM_Stage__c), "Approved")')
    assert evaluate_transition(ast, _ts({"PLS_BM_Stage__c": "Approved"},
                                        {"PLS_BM_Stage__c": "Rejected"})) is True
    assert evaluate_transition(ast, _ts({"PLS_BM_Stage__c": "Draft"},
                                        {"PLS_BM_Stage__c": "Approved"})) is False


def test_vr05_fires_on_the_lock_violation_and_only_then():
    ast = parse(VR05)
    # prior Approved + Deal_Value changed → the lock fires
    fires = _ts({"PLS_BM_Stage__c": "Approved", "PLS_BM_Deal_Value__c": 1},
                {"PLS_BM_Deal_Value__c": 2})
    assert evaluate_transition(ast, fires) is True
    # prior Draft (the VR10 witness shape) → provably silent
    silent = _ts({"PLS_BM_Stage__c": "Draft", "PLS_BM_Deal_Value__c": 1},
                 {"PLS_BM_Stage__c": "Approved"})
    assert evaluate_transition(ast, silent) is False


def test_absence_modes_mirror_the_ir_split():
    ast = parse("ISCHANGED(PLS_BM_Stage__c)")
    empty = TransitionState(prior={}, next={})
    assert evaluate_transition(ast, empty, absent="unknown") is None  # proof mode
    assert evaluate_transition(ast, empty, absent="blank") is False   # run-time mode


def test_today_comparisons_stay_unknown():
    ast = parse("PLS_BM_Contract_Start_Date__c < TODAY()")
    ts = _ts({"PLS_BM_Contract_Start_Date__c": "2000-01-01"}, {})
    assert evaluate_transition(ast, ts, absent="blank") is None       # no clock


# -- satisfy_transition: the VR10 witness ----------------------------------------

def test_vr10_witness_violates_exactly_one_branch():
    w = satisfy_transition(VR10, RAIL, sibling_items=SIBLINGS)
    assert w is not None
    assert w.violated_branch == "PLS_BM_Discount__c > 0.2"
    # the witness value is MINIMALLY violating (D-352 composing through)
    assert w.setup["PLS_BM_Discount__c"] == 0.2001
    # the update IS the transition — nothing else changes
    assert w.changes == {"PLS_BM_Stage__c": "Approved"}
    # the prior state is a real non-Approved stage
    assert w.setup["PLS_BM_Stage__c"] == "Draft"


def test_vr10_witness_satisfies_every_other_branch_and_gate():
    w = satisfy_transition(VR10, RAIL, sibling_items=SIBLINGS)
    s = w.setup
    assert s["PLS_BM_Deal_Type__c"] == "Enterprise"          # gate
    assert s["PLS_BM_Deal_Value__c"] == 2000000.01           # gate, minimal
    assert s["PLS_BM_Risk_Level__c"] == "Low"                # D2/D3 false
    assert s["PLS_BM_Compliance_Approved__c"] is True        # D4 false
    assert s["PLS_BM_Contract_Number__c"] == "PQA"           # D5 false
    # D6/D7 reconcile on the SAME replay-stable tomorrow (a past date would
    # arm D7; the far-future bridge is retired from production)
    assert s["PLS_BM_Contract_Start_Date__c"] == relative_date(1)
    # VR02 (armed by 0.2001 > 0.20) silenced on the free dimension
    assert s["PLS_BM_Approval_Reason__c"] == "PQA"


def test_vr10_witness_fires_target_and_silences_siblings():
    w = satisfy_transition(VR10, RAIL, sibling_items=SIBLINGS)
    ts = _ts(w.setup, w.changes)
    assert evaluate_transition(parse(VR10), ts, absent="blank") is True
    for name, text in SIBLINGS:
        r = evaluate_transition(parse(text), ts, absent="blank")
        # VR06 is unknown (TODAY, runtime-safe via the far-future date);
        # everything else provably silent — incl. VR05 via the prior state.
        assert r is not True, name


def test_vr10_provenance_roles():
    w = satisfy_transition(VR10, RAIL, sibling_items=SIBLINGS)
    roles = {f: r for f, (r, _s) in w.provenance.items()}
    assert roles["PLS_BM_Discount__c"] == ROLE_TARGET_WITNESS
    assert roles["PLS_BM_Approval_Reason__c"] == ROLE_SIBLING_ISOLATION
    assert roles["PLS_BM_Stage__c"] == ROLE_TARGET_ACTIVATION
    assert roles["PLS_BM_Compliance_Approved__c"] == ROLE_TARGET_ACTIVATION


# -- the SAME representation carries VR05 ----------------------------------------

def test_vr05_witness_same_representation():
    w = satisfy_transition(VR05, RAIL, sibling_items=None)
    assert w is not None
    # prior state Approved (the PRIORVALUE constraint), Deal_Value changes
    assert w.setup["PLS_BM_Stage__c"] == "Approved"
    assert w.setup["PLS_BM_Deal_Value__c"] == 1
    assert w.changes == {"PLS_BM_Deal_Value__c": 2}
    ts = _ts(w.setup, w.changes)
    assert evaluate_transition(parse(VR05), ts, absent="blank") is True


# -- refuse-not-guess bounds ------------------------------------------------------

def test_non_transition_formula_refuses():
    assert satisfy_transition("PLS_BM_Deal_Value__c <= 0", RAIL) is None


def test_unparseable_refuses():
    assert satisfy_transition("((broken", RAIL) is None


def test_no_alternative_prior_value_refuses():
    rail = {**RAIL, "PLS_BM_Stage__c": {"field_type": "picklist",
                                        "picklist_values": ["Approved"],
                                        "is_createable": True, "is_updateable": True}}
    assert satisfy_transition(VR10, rail, sibling_items=None) is None


def test_has_transition_semantics():
    assert has_transition_semantics(VR10) is True
    assert has_transition_semantics(VR05) is True
    assert has_transition_semantics("PLS_BM_Deal_Value__c <= 0") is False


# -- end-to-end: the emitted VR10 update-rejected recipe --------------------------

def test_emitted_vr10_negative_is_the_transition_pair():
    from uuid import uuid4
    from primeqa.generation.emission import (
        _author_negative, GroundedNegative, _Endpoint)
    g = GroundedNegative(
        archetype="data_behavior", claim_kind="prohibition-claim",
        operation_hint="modify_record", version_seq=1,
        subject=_Endpoint(entity_id=uuid4(), entity_type="Object",
                          external_id="PLS_BM_Deal__c"),
        requirement_excerpt="Large Enterprise deal cannot move to Approved",
        vr_formulas=(VR10,),
        field_metadata=RAIL,
        vr_messages={t: f"msg {n}" for n, t in ALL_VRS},
    )
    bundle = _author_negative(g)
    steps = bundle.observation_realization.steps
    create, update = steps[0], steps[1]
    fv = {k.split(".")[-1]: v for k, v in create.field_values.items()}
    # the setup create: non-violating prior state incl. every gate + isolation
    assert fv["PLS_BM_Stage__c"] == "Draft"
    assert fv["PLS_BM_Deal_Type__c"] == "Enterprise"
    assert fv["PLS_BM_Compliance_Approved__c"] is True
    assert fv["PLS_BM_Contract_Start_Date__c"] == relative_date(1)
    assert fv["PLS_BM_Approval_Reason__c"] == "PQA"
    # transport: percent 0.2001 ships as 20.01; currency 1:1
    assert fv["PLS_BM_Discount__c"] == 20.01
    assert fv["PLS_BM_Deal_Value__c"] == 2000000.01
    # the update IS the transition, expected rejected with VR10's message
    fc = {k.split(".")[-1]: v for k, v in update.field_changes.items()}
    assert fc == {"PLS_BM_Stage__c": "Approved"}
    assert update.expect_rejection is not None
    # the message pattern is regex-escaped (D-297): 'msg\ VR10'
    assert "VR10" in (update.expect_rejection.error_message_pattern or "")
    # provenance rides the env detail
    detail = bundle.execution_environment.auth_assumptions[0].details
    assert "fixture provenance" in detail
    assert "target witness" in detail and "sibling isolation" in detail


# -- the T3 positive: the inverse experiment --------------------------------------

def test_acceptance_witness_falsifies_every_branch():
    from primeqa.generation.transition import satisfy_transition_acceptance
    w = satisfy_transition_acceptance(VR10, RAIL, sibling_items=SIBLINGS)
    assert w is not None and w.violated_branch == ""
    s = w.setup
    # every violation branch FALSE: discount at the boundary (0.2 allowed),
    # risk low, compliance true, contract number + future start date staged
    assert s["PLS_BM_Discount__c"] == 0.2
    assert s["PLS_BM_Risk_Level__c"] == "Low"
    assert s["PLS_BM_Compliance_Approved__c"] is True
    assert s["PLS_BM_Contract_Number__c"] == "PQA"
    assert s["PLS_BM_Contract_Start_Date__c"] == relative_date(1)
    # gates + transition intact
    assert s["PLS_BM_Deal_Type__c"] == "Enterprise"
    assert s["PLS_BM_Deal_Value__c"] == 2000000.01
    assert w.changes == {"PLS_BM_Stage__c": "Approved"}
    # the rule provably does NOT fire over the witness (the far-future axiom
    # closes the TODAY branch)
    ts = _ts(w.setup, w.changes)
    assert evaluate_transition(parse(VR10), ts, absent="blank") is False


def test_acceptance_witness_siblings_silent():
    from primeqa.generation.transition import satisfy_transition_acceptance
    w = satisfy_transition_acceptance(VR10, RAIL, sibling_items=SIBLINGS)
    ts = _ts(w.setup, w.changes)
    for name, text in SIBLINGS:
        assert evaluate_transition(parse(text), ts, absent="blank") is not True, name


def test_emitted_t3_probe_expects_success_and_reads_back():
    from uuid import uuid4
    from primeqa.generation.emission import (
        _author_negative, GroundedNegative, _Endpoint)
    g = GroundedNegative(
        archetype="data_behavior", claim_kind="prohibition-claim",
        operation_hint="modify_record", version_seq=1,
        subject=_Endpoint(entity_id=uuid4(), entity_type="Object",
                          external_id="PLS_BM_Deal__c"),
        requirement_excerpt="Large Enterprise deal cannot move to Approved",
        vr_formulas=(VR10,),
        field_metadata=RAIL,
        vr_messages={t: f"msg {n}" for n, t in ALL_VRS},
    )
    bundle = _author_negative(g, enable_bva_boundaries=True)
    assert bundle.strategy_kind == "bva"
    assert len(bundle.boundary_recipes) == 1
    probe = bundle.boundary_recipes[0]
    create, update, read, assertion = probe.observation_realization.steps
    fv = {k.split(".")[-1]: v for k, v in create.field_values.items()}
    assert fv["PLS_BM_Discount__c"] == 20        # transport of 0.2
    assert fv["PLS_BM_Compliance_Approved__c"] is True
    # the transition update expects SUCCESS (D-306)
    assert update.expect_acceptance is True
    assert update.expect_rejection is None
    assert {k.split(".")[-1]: v for k, v in update.field_changes.items()} \
        == {"PLS_BM_Stage__c": "Approved"}
    # read-back asserts the to-state persisted
    assert assertion.predicate.predicate == "equals"
    assert assertion.predicate.value == "Approved"
    assert "PLS_BM_Stage__c" in read.fields_to_capture[0]
    # provenance preserved
    detail = probe.execution_environment.auth_assumptions[0].details
    assert "fixture provenance" in detail and "branch falsified" in detail


def test_t3_probe_absent_when_flag_off_or_witness_unsat():
    from uuid import uuid4
    from primeqa.generation.emission import (
        _author_negative, GroundedNegative, _Endpoint)
    g = GroundedNegative(
        archetype="data_behavior", claim_kind="prohibition-claim",
        operation_hint="modify_record", version_seq=1,
        subject=_Endpoint(entity_id=uuid4(), entity_type="Object",
                          external_id="PLS_BM_Deal__c"),
        requirement_excerpt="x", vr_formulas=(VR10,), field_metadata=RAIL,
        vr_messages={t: f"msg {n}" for n, t in ALL_VRS},
    )
    bundle = _author_negative(g)                 # flag off
    assert bundle.boundary_recipes == () and bundle.strategy_kind is None


# -- VR05: the PRIOR_STATE differential --------------------------------------------

RAIL5 = {**RAIL, "PLS_BM_Stage__c": {
    "field_type": "picklist",
    "picklist_values": ["Draft", "Contract Review", "Approved", "Rejected"],
    "is_createable": True, "is_updateable": True}}
SIBS5 = [(n, t) for n, t in ALL_VRS if n != "VR05"]


def test_whole_record_simultaneity_no_special_case():
    # AK's pre-emission verification: PRIORVALUE(Stage) and ISCHANGED(Deal_Value)
    # evaluate SIMULTANEOUSLY over one whole-record pair — Stage held, value changed.
    ts = TransitionState(
        prior={"pls_bm_stage__c": "Approved", "pls_bm_deal_value__c": 1000},
        next={"pls_bm_stage__c": "Approved", "pls_bm_deal_value__c": 1001})
    assert evaluate_transition(parse(VR05), ts, absent="blank") is True
    assert evaluate_transition(parse("ISCHANGED(PLS_BM_Stage__c)"), ts,
                               absent="blank") is False


def test_vr05_witness_reaches_approved_via_the_legitimate_path():
    w = satisfy_transition(VR05, RAIL5, sibling_items=SIBS5)
    assert w is not None
    # the prior state is NOT created directly — the entry transition (VR10's own
    # gate) establishes it: create Contract Review + the full T3 fixture, then
    # the org's own Stage->Approved update.
    assert w.setup["PLS_BM_Stage__c"] == "Contract Review"
    assert w.entry_changes == {"PLS_BM_Stage__c": "Approved"}
    assert w.setup["PLS_BM_Compliance_Approved__c"] is True   # T3 fixture intact
    # the mutation under test: Deal_Value stepped by the minimal increment
    assert w.changes == {"PLS_BM_Deal_Value__c": 2000000.02}
    # provenance: the prior state is the differential's CONTEXT dimension
    role, src = w.provenance["PLS_BM_Stage__c"]
    assert role == "context" and "org's own transition control" in src
    role, _ = w.provenance["PLS_BM_Deal_Value__c"]
    assert role == ROLE_TARGET_WITNESS


def test_vr05_control_same_base_same_mutation_entry_omitted():
    from primeqa.generation.transition import derive_prior_state_control
    w = satisfy_transition(VR05, RAIL5, sibling_items=SIBS5)
    c = derive_prior_state_control(VR05, w.setup, w.changes, RAIL5, SIBS5)
    assert c is not None
    assert c.setup == w.setup            # held constant
    assert c.changes == w.changes        # the SAME mutation
    assert c.entry_changes == {}         # the ONE varied dimension
    # over the un-entered state VR05 provably does NOT fire
    ts = _ts(c.setup, c.changes)
    assert evaluate_transition(parse(VR05), ts, absent="blank") is False


def test_vr05_control_refused_when_target_would_fire():
    from primeqa.generation.transition import derive_prior_state_control
    # a setup already in Approved: omitting the entry does NOT silence the lock
    assert derive_prior_state_control(
        VR05, {"PLS_BM_Stage__c": "Approved", "PLS_BM_Deal_Value__c": 1},
        {"PLS_BM_Deal_Value__c": 2}, RAIL5, SIBS5) is None


def test_prior_state_tie_break_selects_vr05():
    from types import SimpleNamespace as NS
    from uuid import uuid4
    import primeqa.generation.governance_core as gc
    cond = NS(field=NS(external_id="PLS_BM_Deal__c.PLS_BM_Stage__c",
                       entity_id=uuid4(), entity_type="Field"),
              predicate="equals", value="Approved", compared_to=None)
    tied = [t for n, t in ALL_VRS if n in ("VR04", "VR05", "VR06", "VR10")]
    assert gc._break_tie_by_prior_state(tied, [cond]) == VR05
    # no equals pin -> None (refuse floor intact)
    assert gc._break_tie_by_prior_state(tied, []) is None


def test_emitted_vr05_differential():
    from uuid import uuid4
    from primeqa.generation.emission import (
        _author_negative, GroundedNegative, _Endpoint)
    g = GroundedNegative(
        archetype="data_behavior", claim_kind="prohibition-claim",
        operation_hint="modify_field", version_seq=1,
        subject=_Endpoint(entity_id=uuid4(), entity_type="Object",
                          external_id="PLS_BM_Deal__c"),
        requirement_excerpt="approved commercial value protected",
        vr_formulas=(VR05,),
        field_metadata=RAIL5,
        vr_messages={t: f"msg {n}" for n, t in ALL_VRS},
    )
    bundle = _author_negative(g, enable_bva_boundaries=True)
    # PRIMARY: create -> entry update (expect accept) -> mutation (expect reject)
    steps = bundle.observation_realization.steps
    assert [s.kind for s in steps] == ["create", "update", "update"]
    entry, violating = steps[1], steps[2]
    assert entry.expect_acceptance is True and entry.expect_rejection is None
    assert {k.split(".")[-1]: v for k, v in entry.field_changes.items()} \
        == {"PLS_BM_Stage__c": "Approved"}
    assert violating.expect_rejection is not None
    assert {k.split(".")[-1]: v for k, v in violating.field_changes.items()} \
        == {"PLS_BM_Deal_Value__c": 2000000.02}
    assert "VR05" in (violating.expect_rejection.error_message_pattern or "")
    # CONTROL PROBE: same mutation, entry omitted, accept + read-back
    assert bundle.strategy_kind == "bva" and len(bundle.boundary_recipes) == 1
    probe = bundle.boundary_recipes[0]
    psteps = probe.observation_realization.steps
    assert [s.kind for s in psteps][:2] == ["create", "update"]
    pupdate = psteps[1]
    assert pupdate.expect_acceptance is True
    assert {k.split(".")[-1]: v for k, v in pupdate.field_changes.items()} \
        == {"PLS_BM_Deal_Value__c": 2000000.02}
    # read-back of the mutated value (AK: acceptance plus Deal Value read-back)
    assertion = psteps[-1]
    assert assertion.predicate.value == 2000000.02
    detail = probe.execution_environment.auth_assumptions[0].details
    assert "prior-state context varied" in detail
