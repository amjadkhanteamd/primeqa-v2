"""Generation Convergence — snapshot, variants, classifier (pure, no DB).

The taxonomy law under test: every failed path gets EXACTLY ONE category,
by smallest-distance-from-convergence precedence; EMISSION_GAP outranks a
meaning-altering kind-swap rescue; lexical classes carry recovery audit."""
from __future__ import annotations

from primeqa.generation.convergence import (
    HONEST_LIMIT, MODEL_SIDE, SUBSTRATE_SIDE, TAXONOMY, ReplayResult,
    classify, outcome_funnel, snapshot, variants_for)


def _desc(**kw):
    h = {"entity_type": "Object", "sf_api_name": "PLS_FB_Order__c"}
    h.update(kw.pop("hint", {}))
    d = {"ac_ref": 5, "archetype_hint": "data_behavior",
         "claim_kind_hint": kw.pop("kind", "automation-effect-claim"),
         "polarity_hint": "positive", "requirement_excerpt": "x",
         "target_subject_hint": h}
    d.update(kw)
    return d


# ── snapshot ─────────────────────────────────────────────────────────

def test_snapshot_normalizes_both_persisted_shapes():
    d = _desc(hint={"field_name": "F", "expected_value": "Gold"})
    s1 = snapshot(d)                                   # bare descriptor
    s2 = snapshot({"intent_descriptor": d})            # legacy singular
    assert s1.field_name == s2.field_name == "F"
    assert s1.has_expected_value and s1.expected_value == "Gold"
    assert s1.ac_ref == 5 and s1.claim_kind == "automation-effect-claim"


def test_snapshot_tolerates_bad_ac_ref():
    assert snapshot(_desc(ac_ref=True)).ac_ref is None
    assert snapshot(_desc(ac_ref=-2)).ac_ref is None


# ── variants ─────────────────────────────────────────────────────────

def test_variants_value_drop_and_bounds():
    s = snapshot(_desc(hint={"field_name": "F", "expected_value": "X"}))
    vs = dict(variants_for(s, ReplayResult(stage="resolved",
                                           refusal_kind="emission-deferred")))
    assert "value_drop" in vs
    assert "expected_value" not in vs["value_drop"]["target_subject_hint"]
    assert len(vs) <= 5


def test_variants_offer_swaps_clause_references_too():
    d = _desc(kind="acceptance-claim", hint={
        "acceptance_conditions": [
            {"field": "PLS_FB_Order__c.External_Reference__c",
             "predicate": "equals", "value": "x"}]})
    s = snapshot(d)
    ap = ReplayResult(stage="resolved", refusal_kind="emission-deferred",
                      offer_entity_type="Field",
                      offer_top="PLS_FB_Order__c.PLS_FB_External_Ref__c",
                      offer_proposed="PLS_FB_Order__c.External_Reference__c")
    vs = dict(variants_for(s, ap))
    cond = vs["offer"]["target_subject_hint"]["acceptance_conditions"][0]
    assert cond["field"] == "PLS_FB_Order__c.PLS_FB_External_Ref__c"


def test_variants_kind_swap_only_for_swappable_kinds():
    st = snapshot(_desc(kind="state-transition-claim",
                        hint={"field_name": "F", "expected_value": "V"}))
    names = [n for n, _ in variants_for(
        st, ReplayResult(stage="resolved", refusal_kind="x"))]
    assert "kind_swap" in names and "kind_swap+value_drop" in names
    pro = snapshot(_desc(kind="prohibition-claim", hint={"field_name": "F"}))
    names = [n for n, _ in variants_for(
        pro, ReplayResult(stage="resolved", refusal_kind="x"))]
    assert "kind_swap" not in names


# ── classifier precedence ────────────────────────────────────────────

def _snap(**kw):
    return snapshot(_desc(**kw))


def test_classify_converged_and_self_refusal():
    ok = ReplayResult(stage="resolved", grounded_n=4)
    assert classify(_snap(), ok, {})[0] == "CONVERGED"
    s = _snap()
    s.no_admissible_test = True
    assert classify(s, ok, {})[0] == "MODEL_SELF_REFUSAL"


def test_classify_lexical_subject_with_recovery_audit():
    ap = ReplayResult(stage="layer_a", offer_top="PLS_FB_Order__c",
                      offer_entity_type="Object")
    code, note = classify(_snap(), ap, {
        "offer": ReplayResult(stage="resolved", grounded_n=1)})
    assert code == "LEXICAL_SUBJECT" and note == "recovered"
    code, note = classify(_snap(), ap, {
        "offer": ReplayResult(stage="resolved", refusal_kind="x")})
    assert note == "offer_present_not_convergent"


def test_classify_emission_gap_outranks_meaning_altering_swap():
    ap = ReplayResult(
        stage="resolved", refusal_kind="emission-deferred",
        detail="data_behavior/value-claim is groundable, but emission for "
               "this claim_kind is not yet built")
    variants = {"kind_swap": ReplayResult(stage="resolved", grounded_n=1)}
    code, note = classify(_snap(kind="value-claim"), ap, variants)
    assert code == "EMISSION_GAP"
    assert "meaning-altering" in note


def test_classify_kind_misframe_when_no_gap():
    ap = ReplayResult(stage="resolved", refusal_kind="emission-deferred",
                      detail="state-transition needs a verifiable to-state: "
                             "field_name (existing on the subject)")
    variants = {"kind_swap": ReplayResult(stage="resolved", grounded_n=1)}
    code, _ = classify(_snap(kind="state-transition-claim"), ap, variants)
    assert code == "KIND_MISFRAME"


def test_classify_structured_buckets():
    cases = [
        (ReplayResult(stage="resolved", refusal_kind="emission-deferred",
                      detail="the arm's guard interval on 'A' is empty or "
                             "outside the bounded synthesis grammar"),
         "GROUNDING_WITNESS"),
        (ReplayResult(stage="resolved", refusal_kind="emission-deferred",
                      detail="2 Flows verifiably stamp 'F' with a relative "
                             "date — cannot attribute"),
         "GROUNDING_AMBIGUITY"),
        (ReplayResult(stage="resolved", refusal_kind="emission-deferred",
                      detail="no Flow on the subject produces the claimed "
                             "effect — name the specific automation"),
         "NO_PRODUCER"),
        (ReplayResult(stage="resolved", refusal_kind="emission-deferred",
                      detail="no Flow on the subject verifiably produces the "
                             "claimed cross-object effect on 'Task'"),
         "CAPABILITY_LIMIT_CROSS_OBJECT"),
        (ReplayResult(stage="resolved", refusal_kind="ungrounded-claim",
                      detail=None),
         "ADMISSION_DISMISSAL"),
    ]
    for ap, want in cases:
        code, _ = classify(_snap(), ap, {})
        assert code == want, (want, code)


def test_every_code_is_in_the_taxonomy_and_partitioned():
    partitions = SUBSTRATE_SIDE | MODEL_SIDE | HONEST_LIMIT
    for code in partitions:
        assert code in TAXONOMY
    # the partitions are disjoint
    assert not (SUBSTRATE_SIDE & MODEL_SIDE)
    assert not (SUBSTRATE_SIDE & HONEST_LIMIT)
    assert not (MODEL_SIDE & HONEST_LIMIT)


# ── outcome funnel ───────────────────────────────────────────────────

def test_outcome_funnel_abandonment():
    ai = {
        "coverage_map": {
            "1": {"status": "covered"},
            "2": {"status": "refused", "reason": "x",
                  "reason_source": "model"},
            "3": {"status": "refused", "reason": "ungrounded_after_reprompt",
                  "reason_source": "substrate"},
            "4": {"status": "refused", "reason": "ungrounded_after_reprompt",
                  "reason_source": "substrate"},
        },
        "coverage_recovery": {
            "requested_refs": [2, 3, 4],
            "recovery_newly_covered": [],
            "recovery_newly_refused": [2],
            "recovery_zero_progress": False,
        },
    }
    f = outcome_funnel(ai, ["c1"], [])
    assert f["declared"] == 4 and f["covered"] == 1
    assert f["refused_model"] == 1 and f["refused_substrate"] == 2
    assert f["recovery_abandoned_acs"] == [3, 4]
    assert f["claims_written"] == 1


def test_classify_negative_underivable_and_no_producer_wordings():
    ap = ReplayResult(stage="resolved",
                      refusal_kind="no-admissible-negative-scenario-found",
                      detail=None)
    assert classify(_snap(kind="prohibition-claim"), ap, {})[0] == \
        "NEGATIVE_UNDERIVABLE"
    ap2 = ReplayResult(
        stage="resolved", refusal_kind="emission-deferred",
        detail="automation-effect needs a verifiable effect: field_name + "
               "expected_value on the subject (or effect_object + "
               "effect_lookup_field) — no transform, relative-date, or "
               "classification producer verifiably writes F")
    assert classify(_snap(), ap2, {})[0] == "NO_PRODUCER"
