"""Step A — the repair-proposal three-verdict gate, as a pure table
(LLD_STEP_A_REPAIR_GATE §a, rulings D1/D4; SEMANTIC first, ratified).

No DB, no S1: :func:`classify` is total over :class:`GateInputs`. Every
row here pins one branch; the DB-real suite (tests/integration/
test_repair_gate.py) proves the wiring around it.
"""
from __future__ import annotations

from primeqa.intelligence import repair_gate as G
from primeqa.intelligence.llm.prompts.repair_proposal import REMOVE_SENTINEL

STAGED = ("Opportunity.Name", "Opportunity.StageName",
          "Opportunity.Loan_Type__c", "Opportunity.Amount")


def _edit(field_changes, **kw):
    base = dict(proposal_kind="recipe_edit", field_changes=field_changes,
                staged_keys=STAGED, sobject="Opportunity",
                recipe_readable=True, claim_readable=True,
                claim_kind="value-claim", asserted_fields=frozenset({"amount"}),
                destination={"key": "req-302", "url": "/requirements/302"})
    base.update(kw)
    return G.GateInputs(**base)


# ---- ruling D1: the deterministic kinds are DERIVED by construction ------

def test_rerun_is_derived_with_no_recipe_mutation_recorded():
    r = G.classify(G.GateInputs(proposal_kind="rerun", outcome="errored",
                                s6_verdict="not_evaluated",
                                failure_category="transport"))
    assert r.verdict == G.DERIVED
    assert r.grounding["rule"] == "K-rerun"
    assert r.grounding["no_recipe_mutation"] is True
    assert r.grounding["failure_category"] == "transport"


def test_regenerate_is_derived_with_no_recipe_mutation_recorded():
    r = G.classify(G.GateInputs(proposal_kind="regenerate_from_current_org",
                                cause_kind="vr_formula_drift",
                                claim_version_seq=3))
    assert r.verdict == G.DERIVED
    assert r.grounding["rule"] == "K-regen"
    assert r.grounding["no_recipe_mutation"] is True
    assert r.grounding["claim_version_seq"] == 3


def test_unknown_kind_fails_closed():
    assert G.classify(G.GateInputs(proposal_kind="mystery")).verdict == G.SEMANTIC


# ---- SEMANTIC first (ratified) -------------------------------------------

def test_touching_an_asserted_field_is_semantic_even_when_r1_would_hold():
    """Amount is asserted; the remedy removes it; S1 would attest R1 —
    SEMANTIC still wins because it is evaluated first."""
    r = G.classify(_edit(
        {"Amount": REMOVE_SENTINEL},
        error_code="INVALID_FIELD_FOR_INSERT_UPDATE", error_fields=("Amount",),
        s1_facts={"amount": G.FieldFact(exists=True, is_createable=False,
                                        entity_id="e1")}))
    assert r.verdict == G.SEMANTIC
    assert r.grounding["reason"] == "touches_asserted_field"
    assert r.grounding["fields"] == ["amount"]
    assert r.grounding["destination"]["url"] == "/requirements/302"


def test_object_prefix_is_stripped_before_the_intersection():
    r = G.classify(_edit({"Opportunity.Amount": "5"}))
    assert r.verdict == G.SEMANTIC and r.grounding["fields"] == ["amount"]


def test_bare_staged_key_fails_closed_to_semantic():
    r = G.classify(_edit({"StageName": "Prospecting"},
                         staged_keys=("StageName", "Opportunity.Name")))
    assert r.verdict == G.SEMANTIC
    assert r.grounding["reason"] == "bare_staged_key"
    assert r.grounding["fields"] == ["stagename"]


def test_empty_remedy_unreadable_recipe_and_unsupported_kind_fail_closed():
    assert G.classify(_edit({})).grounding["reason"] == "empty_remedy"
    assert G.classify(_edit({"Name": "x"}, recipe_readable=False)
                      ).grounding["reason"] == "recipe_unreadable"
    assert G.classify(_edit({"Name": "x"}, claim_readable=False)
                      ).grounding["reason"] == "claim_unreadable"
    r = G.classify(_edit({"Name": "x"}, claim_kind="conformance-claim"))
    assert r.verdict == G.SEMANTIC
    assert r.grounding["reason"] == "claim_kind_unsupported"


# ---- DERIVED R1: attested removal ----------------------------------------

def test_r1_removal_of_a_non_createable_field_named_by_the_error():
    r = G.classify(_edit(
        {"Loan_Type__c": REMOVE_SENTINEL},
        error_code="INVALID_FIELD_FOR_INSERT_UPDATE",
        error_fields=("Loan_Type__c",), s1_seq=249,
        s1_facts={"loan_type__c": G.FieldFact(exists=True, is_createable=False,
                                              entity_id="e9")}))
    assert r.verdict == G.DERIVED
    assert r.grounding["rule"] == "R1"
    assert r.grounding["s1_fact"] == "is_createable=false"
    assert r.grounding["attested_by"] == "error_fields"
    assert r.grounding["s1_seq"] == 249 and r.grounding["s1_as_of"] == "current"


def test_r1_removal_of_a_field_absent_from_s1_attested_by_the_message():
    r = G.classify(_edit(
        {"Loan_Type__c": REMOVE_SENTINEL},
        error_code="INVALID_FIELD",
        error_message="No such column 'Loan_Type__c' on sobject Opportunity",
        s1_facts={"loan_type__c": G.FieldFact(exists=False)}))
    assert r.verdict == G.DERIVED
    assert r.grounding["s1_fact"] == "absent"
    assert r.grounding["attested_by"] == "error_message"


def test_r1_needs_the_error_to_name_the_field():
    """S1 says non-createable, but nothing recorded ties the error to the
    field — the diagnosis is the model's, so SPECULATIVE."""
    r = G.classify(_edit(
        {"Loan_Type__c": REMOVE_SENTINEL},
        error_code="INVALID_FIELD_FOR_INSERT_UPDATE",
        s1_facts={"loan_type__c": G.FieldFact(exists=True, is_createable=False)}))
    assert r.verdict == G.SPECULATIVE


def test_r1_needs_s1_to_attest_the_field():
    """The error names the field but S1 records it createable — removing a
    writable field is a choice, not a derivation."""
    r = G.classify(_edit(
        {"Loan_Type__c": REMOVE_SENTINEL},
        error_code="INVALID_FIELD_FOR_INSERT_UPDATE",
        error_fields=("Loan_Type__c",),
        s1_facts={"loan_type__c": G.FieldFact(exists=True, is_createable=True)}))
    assert r.verdict == G.SPECULATIVE


def test_removal_without_any_s1_read_is_speculative():
    r = G.classify(_edit({"Loan_Type__c": REMOVE_SENTINEL},
                         error_code="INVALID_FIELD_FOR_INSERT_UPDATE",
                         error_fields=("Loan_Type__c",), s1_facts={}))
    assert r.verdict == G.SPECULATIVE


# ---- DERIVED R2: recorded picklist value (ruling D4) ----------------------

def _picklist(active, default=None):
    return {"loan_type__c": G.FieldFact(
        exists=True, is_createable=True, picklist_active_values=tuple(active),
        picklist_default=default, entity_id="e9")}


def test_r2_the_recorded_default_value_is_derived():
    r = G.classify(_edit(
        {"Loan_Type__c": "Home"}, error_code=G._PICKLIST_ERROR_CODE,
        error_fields=("Loan_Type__c",),
        s1_facts=_picklist(("Home", "Personal", "Business"), default="Home")))
    assert r.verdict == G.DERIVED
    assert r.grounding["rule"] == "R2" and r.grounding["matched"] == "default"


def test_r2_the_sole_active_value_is_derived_by_exhaustion():
    r = G.classify(_edit(
        {"Loan_Type__c": "Home"}, error_code=G._PICKLIST_ERROR_CODE,
        error_fields=("Loan_Type__c",), s1_facts=_picklist(("Home",))))
    assert r.verdict == G.DERIVED
    assert r.grounding["matched"] == "sole_active"


def test_r2_two_active_values_without_a_default_is_a_chosen_value():
    """Ruling D4's negative: two active values → the model CHOSE one."""
    r = G.classify(_edit(
        {"Loan_Type__c": "Home"}, error_code=G._PICKLIST_ERROR_CODE,
        error_fields=("Loan_Type__c",), s1_facts=_picklist(("Home", "Personal"))))
    assert r.verdict == G.SPECULATIVE
    assert r.grounding["reason"] == "chosen_picklist_value"
    assert r.grounding["active_count"] == 2


def test_r2_a_non_default_value_among_several_is_chosen():
    r = G.classify(_edit(
        {"Loan_Type__c": "Personal"}, error_code=G._PICKLIST_ERROR_CODE,
        error_fields=("Loan_Type__c",),
        s1_facts=_picklist(("Home", "Personal", "Business"), default="Home")))
    assert r.verdict == G.SPECULATIVE
    assert r.grounding["reason"] == "chosen_picklist_value"


def test_r2_a_value_the_picklist_does_not_record_is_speculative():
    """The prod specimen: the model proposed "Mortgage"; the set records
    Home / Personal / Business."""
    r = G.classify(_edit(
        {"Loan_Type__c": "Mortgage"}, error_code=G._PICKLIST_ERROR_CODE,
        error_fields=("Loan_Type__c",),
        s1_facts=_picklist(("Home", "Personal", "Business"))))
    assert r.verdict == G.SPECULATIVE
    assert r.grounding["reason"] == "value_not_recorded_in_picklist"


# ---- SPECULATIVE: inference or a chosen value -----------------------------

def test_no_platform_error_is_speculative_by_construction():
    """automation_effect_* proposals: the create SUCCEEDED, so no error
    string exists — the diagnosis can only be inference."""
    r = G.classify(_edit({"StageName": "Prospecting"},
                         cause_kind="automation_effect_absent"))
    assert r.verdict == G.SPECULATIVE
    assert r.grounding["reason"] == "no_platform_error"
    assert r.grounding["fields"] == ["StageName"]


def test_a_chosen_placeholder_value_is_speculative():
    """The prod specimen 465221: the model chose "1000"."""
    r = G.classify(_edit({"Amount2": "1000"}, error_code="JSON_PARSER_ERROR",
                         staged_keys=STAGED + ("Opportunity.Amount2",)))
    assert r.verdict == G.SPECULATIVE
    assert r.grounding["reason"] == "inference_or_chosen_value"


def test_two_field_remedies_never_derive():
    r = G.classify(_edit(
        {"Loan_Type__c": REMOVE_SENTINEL, "Name": REMOVE_SENTINEL},
        error_code="INVALID_FIELD_FOR_INSERT_UPDATE",
        error_fields=("Loan_Type__c", "Name"),
        s1_facts={"loan_type__c": G.FieldFact(exists=True, is_createable=False),
                  "name": G.FieldFact(exists=True, is_createable=False)}))
    assert r.verdict == G.SPECULATIVE


# ---- the asserted-field extractor + normalisation parity ------------------

def test_asserted_fields_walk_finds_pinned_field_refs_and_state_keys():
    class _Subj:
        external_id = "Opportunity.StageName"

    class _Cond:
        subject = _Subj()

    class _SC:
        conditions = [_Cond()]

    class _Body:
        def model_dump(self, mode="json"):
            return {"kind": "state-transition-claim",
                    "field": {"entity_type": "Field",
                              "external_id": "Opportunity.Status__c"},
                    "from_state": {"Opportunity.Amount": 1},
                    "target": {"entity_type": "Object",
                               "external_id": "Opportunity"}}

    class _Claim:
        semantic_conditions = _SC()
        asserted_truth = _Body()

    out = G.asserted_fields_of(_Claim())
    assert out == frozenset({"stagename", "status__c", "amount"})
    assert "opportunity" not in out                 # an Object ref is not a field


def test_normalisation_matches_the_d454_pins_builder():
    from primeqa.generation.governance_core import _coverage_pinned_fields

    class _Step:
        field_values = {"Opportunity.Loan_Type__c": "Home", "Amount": 5}
        field_changes = None

    class _Body:
        steps = [_Step()]

    class _Bundle:
        semantic_conditions = None
        observation_realization = _Body()

    pins = _coverage_pinned_fields(_Bundle())
    assert pins == frozenset({G.bare("Opportunity.Loan_Type__c"), G.bare("Amount")})
