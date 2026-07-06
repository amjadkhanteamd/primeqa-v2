"""D-318 — bind the automation-effect Flow by its EFFECT, not by the LLM naming
the org's internal Flow.

The seeded ``Loan__c`` island (conftest) mirrors env-59: the Flow
``HL_Auto_Risk_Rating`` stamps ``Risk_Rating__c`` = High/Medium/Low (recordUpdates)
AND creates a ``Loan_Task__c`` (recordCreates); ``HL_Risk_Override`` is a second
Flow that ALSO stamps Medium (the ambiguity fixture). The LLM cannot know these
internal Flow api-names from a business requirement, so the resolver binds the
Flow whose parsed Metadata actually PRODUCES the claimed effect.
"""
from __future__ import annotations

from uuid import uuid4

from primeqa.generation.enums import OutcomeKind
from primeqa.generation.governance_core import GovernanceCore
from primeqa.generation.persistence import LedgerPersister

from .conftest import FakeTurn, FakeToolTurn, TEST_TENANT_ID, make_request, propose_turn


def _intent(sf_api_name="Loan__c", **hint_extra):
    tsh = {"entity_type": "Object", "sf_api_name": sf_api_name, **hint_extra}
    return {"requirement_excerpt": "the org automatically sets it",
            "intent_descriptor": {"archetype_hint": "data_behavior",
                                  "polarity_hint": "positive",
                                  "claim_kind_hint": "automation-effect-claim",
                                  "target_subject_hint": tsh}}


def _emit_turn() -> FakeTurn:
    return FakeTurn([{"type": "tool_use", "id": f"tu_{uuid4().hex[:6]}",
                      "name": "emit_outcome",
                      "input": {"outcome_kind": "draft",
                                "payload": {"admissibility_layer": "layer_1"}}}])


def _run(seeded, intent_input, *, expect_emit=True):
    from primeqa.semantic.connection import get_tenant_connection
    from primeqa.semantic.query import SemanticOrgModel
    from primeqa.generation.runtime import GenerationRuntime

    req = make_request(s1_version_seq=seeded["v1"])
    turns = [propose_turn(intent_input)]
    if expect_emit:
        turns.append(_emit_turn())
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        gov = GovernanceCore(SemanticOrgModel(conn))
        result = GenerationRuntime().run(
            request=req, seam=gov, tool_turn_fn=FakeToolTurn(turns),
            persister=LedgerPersister(TEST_TENANT_ID))
    return result.results[0]


def test_no_name_binds_the_flow_that_produces_the_effect(seeded):
    # No automation_name at all — the resolver finds HL_Auto_Risk_Rating by the
    # effect its Metadata stamps (Risk_Rating__c=High). This is exactly what the
    # LLM cannot name.
    r = _run(seeded, _intent(field_name="Loan__c.Risk_Rating__c", expected_value="High"))
    assert r.outcome.outcome_kind == OutcomeKind.DRAFT
    body = r.emission.asserted_truth
    assert body.automation.external_id == "HL_Auto_Risk_Rating"
    assert body.automation_primitive == "flow"


def test_invented_name_still_binds_by_effect(seeded):
    # The req-302 case: the LLM invents "RiskRatingAssignment" (no such Flow) but
    # names the real effect — bind the Flow that produces it, don't refuse.
    r = _run(seeded, _intent(field_name="Loan__c.Risk_Rating__c", expected_value="Low",
                             automation_name="RiskRatingAssignment"))
    assert r.outcome.outcome_kind == OutcomeKind.DRAFT
    assert r.emission.asserted_truth.automation.external_id == "HL_Auto_Risk_Rating"


def test_effect_no_flow_produces_still_refuses(seeded):
    # No fake-green: an effect value no Flow on the subject produces still refuses,
    # even though the subject HAS flows.
    r = _run(seeded, _intent(field_name="Loan__c.Risk_Rating__c",
                             expected_value="Platinum",
                             automation_name="RiskRatingAssignment"),
             expect_emit=False)
    assert r.outcome.outcome_kind == OutcomeKind.REFUSAL


def test_no_name_effect_no_flow_produces_refuses(seeded):
    # SUB-3 (repro): the NO-NAME counterpart of test_effect_no_flow_produces_
    # still_refuses. No automation_name + an effect value no Flow on the subject
    # produces (empty producer set) + a NON-calculated observed field must REFUSE,
    # not bind the blind flows[0] (a Flow that produces a DIFFERENT effect) — a
    # wrong-green. The named branch already refuses this; the no-name branch must
    # be symmetric. Against the old code this DRAFTS (bound to flows[0]).
    r = _run(seeded, _intent(field_name="Loan__c.Risk_Rating__c",
                             expected_value="Platinum"),   # no automation_name
             expect_emit=True)
    assert r.outcome.outcome_kind == OutcomeKind.REFUSAL
    assert "produces the claimed effect" in r.outcome.refusals[0].payload["detail"]


def test_no_name_calculated_field_still_binds_formula(seeded):
    # No-regression: a CALCULATED observed field legitimately has no Flow producer
    # (the formula engine is the mechanism); the coherence-guard re-binds
    # primitive='formula'. The SUB-3 no-producer refusal is scoped to non-calc
    # fields, so this still DRAFTS. (Order__c.Total_With_Tax__c is is_calculated.)
    r = _run(seeded, _intent(sf_api_name="Order__c",
                             field_name="Order__c.Total_With_Tax__c",
                             expected_value="110"))
    assert r.outcome.outcome_kind == OutcomeKind.DRAFT
    assert r.emission.asserted_truth.automation_primitive == "formula"


def test_cross_object_binds_the_flow_that_creates_the_object(seeded):
    # The Flow's recordCreates makes a Loan_Task__c — bind by the created object,
    # no name needed.
    r = _run(seeded, _intent(effect_object="Loan_Task__c",
                             effect_lookup_field="Loan_Task__c.Loan__c",
                             automation_name="HighRiskTaskCreation"))
    assert r.outcome.outcome_kind == OutcomeKind.DRAFT
    assert r.emission.asserted_truth.automation.external_id == "HL_Auto_Risk_Rating"


def test_ambiguous_effect_refuses_and_asks_for_the_name(seeded):
    # Two Flows stamp Risk_Rating__c=Medium — the effect no longer disambiguates;
    # refuse (never bind the wrong automation).
    r = _run(seeded, _intent(field_name="Loan__c.Risk_Rating__c",
                             expected_value="Medium"),
             expect_emit=False)
    assert r.outcome.outcome_kind == OutcomeKind.REFUSAL
    assert "produce the claimed effect" in r.outcome.refusals[0].payload["detail"]


def test_naming_one_of_the_ambiguous_flows_binds_it(seeded):
    # When the model DOES name one of the two Medium-producers, the name binds it
    # (the fast path) — no ambiguity refusal.
    r = _run(seeded, _intent(field_name="Loan__c.Risk_Rating__c", expected_value="Medium",
                             automation_name="HL_Risk_Override"))
    assert r.outcome.outcome_kind == OutcomeKind.DRAFT
    assert r.emission.asserted_truth.automation.external_id == "HL_Risk_Override"
