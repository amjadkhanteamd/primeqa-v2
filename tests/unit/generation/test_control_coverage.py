"""Control read-model — Phase 0 read-only telemetry.

Verifies the lifecycle derivation against the ten real VRB-V1 rules
(EXPECTED / NOMINATED / EMITTED at generation time), the D-297 signature
inversion, the inactive-rule exclusion (D-301 posture), the duck-typed
extraction over both pydantic-object and persisted-dict shapes, and — the
load-bearing invariant — that attaching the map CANNOT re-key an outcome
(explanation_hash neutrality)."""
import re
from types import SimpleNamespace

from primeqa.generation import control_coverage as cc
from primeqa.generation.explanation_hash import compute_explanation_hash
from tests.unit.generation.test_control_relevance import ALL_VRS

# The rules' user-facing messages (the org fixture's, abbreviated per rule).
MESSAGES = {
    "VR01": "Deal Value must be greater than zero.",
    "VR02": "Approval Reason is required when Discount exceeds 20%.",
    "VR03": "High-value risky or highly discounted deals require Compliance Approval.",
    "VR04": "Contract Number is required during Contract Review and Approved stages.",
    "VR05": "Deal Value cannot be changed after the deal has been approved.",
    "VR06": "Approved deals require a Contract Start Date of today or later.",
    "VR07": "Critical Risk deals require Compliance Approval and an Override Reason.",
    "VR08": "Enterprise deals cannot have a Discount greater than 25%.",
    "VR09": "External Reference must use the format EXT-12345678.",
    "VR10": ("Enterprise deals above 2,000,000 must meet all approval "
             "conditions before moving to Approved."),
}
SUBJECT = "PLS_BM_Deal__c"


def _neighborhood(active=None):
    """Ten ValidationRule rows in the governance neighborhood shape, both
    stored attribute generations exercised (designed vs raw Tooling)."""
    active = active or {}
    rows = []
    for i, (name, formula) in enumerate(ALL_VRS):
        if i % 2 == 0:  # the designed projection shape
            attrs = {"formula_text": formula, "error_message": MESSAGES[name],
                     "is_active": active.get(name, True)}
        else:           # the post-cutover raw Tooling shape
            attrs = {"Metadata": {"errorConditionFormula": formula},
                     "ErrorMessage": MESSAGES[name],
                     "Active": active.get(name, True)}
        rows.append(SimpleNamespace(
            edge_type="APPLIES_TO",
            entity=SimpleNamespace(entity_type="ValidationRule",
                                   sf_api_name=f"{SUBJECT}.{name}",
                                   attributes=attrs)))
    # a non-VR neighbor must be ignored
    rows.append(SimpleNamespace(
        edge_type="BELONGS_TO",
        entity=SimpleNamespace(entity_type="Field",
                               sf_api_name=f"{SUBJECT}.PLS_BM_Discount__c",
                               attributes={})))
    return rows


def _facts(active=None):
    return cc.controls_from_neighborhood(SUBJECT, _neighborhood(active))


# -- fact derivation ----------------------------------------------------------

def test_derives_all_ten_rules_both_attribute_shapes():
    facts = _facts()
    assert len(facts) == 10
    by_ref = {f.control_ref: f for f in facts}
    assert by_ref[f"{SUBJECT}.VR01"].firing_signature == MESSAGES["VR01"]
    assert by_ref[f"{SUBJECT}.VR02"].firing_signature == MESSAGES["VR02"]
    assert all(f.mechanism == cc.MECHANISM_VALIDATION_RULE for f in facts)
    assert all(f.subject_ref == SUBJECT for f in facts)


def test_non_vr_neighbors_and_duplicates_ignored():
    rows = _neighborhood() + _neighborhood()   # duplicated
    facts = cc.controls_from_neighborhood(SUBJECT, rows)
    assert len(facts) == 10


def test_formula_fields_mirrors_selector_side():
    fields = cc.formula_fields(dict(ALL_VRS)["VR04"])
    assert fields == frozenset({"pls_bm_stage__c", "pls_bm_contract_number__c"})
    assert cc.formula_fields(None) == frozenset()
    assert cc.formula_fields("~~~unparseable~~~") == frozenset()


# -- signature inversion (D-297) ----------------------------------------------

def test_match_signature_inverts_re_escape():
    facts = _facts()
    pattern = re.escape(MESSAGES["VR08"])
    hits = cc.match_signature(pattern, facts)
    assert [h.control_ref for h in hits] == [f"{SUBJECT}.VR08"]


def test_match_signature_invalid_pattern_matches_nothing():
    assert cc.match_signature("(unbalanced", _facts()) == []
    assert cc.match_signature(None, _facts()) == []


# -- lifecycle at generation time ----------------------------------------------

def test_stages_expected_nominated_emitted():
    facts = _facts()
    cmap = cc.build_coverage_map(
        facts,
        # one claim conditioned on Approval_Reason (VR02's field)
        condition_field_sets=[frozenset({"pls_bm_approval_reason__c"})],
        # one emitted recipe pinning VR06's message
        emitted_patterns=[re.escape(MESSAGES["VR06"])])
    ctl = cmap["controls"]
    assert ctl[f"{SUBJECT}.VR06"]["stage"] == cc.EMITTED
    assert ctl[f"{SUBJECT}.VR02"]["stage"] == cc.NOMINATED
    # VR09's fields overlap nothing proposed and no pattern pins it
    assert ctl[f"{SUBJECT}.VR09"]["stage"] == cc.EXPECTED
    assert cmap["counts"] == {"expected": 10, "nominated": 2,
                              "emitted": 1, "inactive_listed": 0}


def test_nominated_counts_all_field_overlaps():
    # Deal_Value conditions overlap VR01, VR03, VR05, VR10 — the 4-way tie the
    # regression analysis traced; all four are NOMINATED, none EMITTED.
    cmap = cc.build_coverage_map(
        _facts(), [frozenset({"pls_bm_deal_value__c"})], [])
    nominated = {ref.rsplit(".", 1)[-1] for ref, e in cmap["controls"].items()
                 if e["stage"] == cc.NOMINATED}
    assert nominated == {"VR01", "VR03", "VR05", "VR10"}


def test_inactive_rule_listed_but_never_expected():
    cmap = cc.build_coverage_map(
        _facts(active={"VR07": False}),
        [frozenset({"pls_bm_risk_level__c"})],
        [re.escape(MESSAGES["VR07"])])
    e = cmap["controls"][f"{SUBJECT}.VR07"]
    assert e["active"] is False
    assert e["stage"] is None          # cannot fire (D-301) -> never advances
    assert cmap["counts"]["expected"] == 9
    assert cmap["counts"]["inactive_listed"] == 1


def test_ambiguous_signature_flagged_on_both():
    rows = _neighborhood()
    # clone VR02's message onto VR04 -> one pattern pins two rules
    rows[3].entity.attributes["ErrorMessage"] = MESSAGES["VR02"]
    facts = cc.controls_from_neighborhood(SUBJECT, rows)
    cmap = cc.build_coverage_map(facts, [], [re.escape(MESSAGES["VR02"])])
    assert cmap["controls"][f"{SUBJECT}.VR02"]["signature_ambiguous"] is True
    assert cmap["controls"][f"{SUBJECT}.VR04"]["signature_ambiguous"] is True


# -- duck-typed extraction ------------------------------------------------------

def _obj_bundle():
    cond = SimpleNamespace(
        subject=SimpleNamespace(external_id=f"{SUBJECT}.PLS_BM_Stage__c"),
        predicate="equals", value="Approved")
    step = SimpleNamespace(expect_rejection=SimpleNamespace(
        error_message_pattern=re.escape(MESSAGES["VR05"])))
    body = SimpleNamespace(steps=[step])
    boundary = SimpleNamespace(observation_realization=SimpleNamespace(steps=[]))
    return SimpleNamespace(semantic_conditions=SimpleNamespace(conditions=[cond]),
                           observation_realization=body,
                           secondary_recipes=(), boundary_recipes=(boundary,))


def _dict_bundle():
    return {
        "semantic_conditions": {"conditions": [
            {"subject": {"external_id": f"{SUBJECT}.PLS_BM_Stage__c"},
             "predicate": "equals", "value": "Approved"}]},
        "observation_realization": {"steps": [
            {"expect_rejection":
                 {"error_message_pattern": re.escape(MESSAGES["VR05"])}}]},
        "secondary_recipes": [], "boundary_recipes": [],
    }


def test_coverage_from_bundles_object_and_dict_agree():
    for bundle in (_obj_bundle(), _dict_bundle()):
        cmap = cc.coverage_from_bundles(_facts(), [bundle])
        assert cmap["controls"][f"{SUBJECT}.VR05"]["stage"] == cc.EMITTED


def test_condition_fields_v2_cross_field_contributes_both():
    body = {"conditions": [{
        "subject": {"external_id": f"{SUBJECT}.PLS_BM_Deal_Value__c"},
        "predicate": "exceeds",
        "compared_to": {"external_id": f"{SUBJECT}.PLS_BM_Discount__c"}}]}
    assert cc.condition_fields(body) == frozenset(
        {"pls_bm_deal_value__c", "pls_bm_discount__c"})


# -- the load-bearing invariant: hash neutrality --------------------------------

def test_control_coverage_key_never_rekeys_the_outcome():
    ai = {
        "scoped_neighborhood": [{"entity_type": "Object",
                                 "sf_api_name": SUBJECT}],
        "candidate_paths": [{"path_id": "c0", "archetype": "data_behavior",
                             "claim_kind": "prohibition-claim",
                             "subject_refs": [{"sf_api_name": SUBJECT}],
                             "admissibility_status": "admissibly_grounded"}],
        "dismissed_alternatives_by_reason": {},
        "selected_path_id": "c0",
    }
    before = compute_explanation_hash(ai)
    ai["control_coverage"] = cc.build_coverage_map(
        _facts(), [frozenset({"pls_bm_stage__c"})],
        [re.escape(MESSAGES["VR05"])])
    assert compute_explanation_hash(ai) == before


# -- stage ordering ---------------------------------------------------------------

def test_stage_rank_orders_the_lifecycle():
    assert [cc.stage_rank(s) for s in cc.STAGES] == list(range(6))
    assert cc.stage_rank("bogus") == -1
