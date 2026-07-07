"""Unit tests for the D-333 approval-arc S3 layer — grounding helper +
emission authors, pure (no PG, no LLM).
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from primeqa.generation import governance_core as gc
from primeqa.generation.emission import (
    GroundedAcceptance,
    GroundedNegative,
    _Endpoint,
    _GroundedCondition,
    author_emission,
)
from primeqa.test_representation.identity_hash import compute_identity_hash
from primeqa.test_representation.models.claims.data_behavior.acceptance_claim import (
    AcceptanceClaimArcBody,
)
from primeqa.test_representation.models.claims.data_behavior.prohibition_claim import (
    ProhibitionClaimArcBody,
)
from primeqa.test_representation.models.recipes.data_recipe import (
    ApprovalActionStep,
    CreateStep,
    UpdateStep,
)


def _ep(external_id, entity_type="Field"):
    return _Endpoint(entity_id=uuid4(), entity_type=entity_type,
                     external_id=external_id)


def _cond(field, value):
    return _GroundedCondition(field=_ep(field), predicate="equals",
                              value=value)


# ---------------------------------------------------------------------------
# Emission — the arc prohibition author
# ---------------------------------------------------------------------------

def _arc_negative(actions=("submit",)):
    return GroundedNegative(
        archetype="data_behavior", claim_kind="prohibition-claim",
        operation_hint="modify_record", version_seq=7,
        subject=_ep("Opportunity", "Object"),
        requirement_excerpt="the move to Approved is blocked while pending",
        conditions=(_cond("Opportunity.Loan_Amount__c", 6000000),),
        approval_actions=tuple(actions),
        attempted_change=(_ep("Opportunity.StageName"), "Approved"))


def test_arc_negative_authors_v2_body_and_arc_recipe():
    b = author_emission(_arc_negative())
    assert isinstance(b.asserted_truth, ProhibitionClaimArcBody)
    assert b.asserted_truth.approval_actions == ["submit"]
    assert b.asserted_truth.attempted_change == {
        "Opportunity.StageName": "Approved"}
    steps = b.observation_realization.steps
    assert [type(s).__name__ for s in steps] == [
        "CreateStep", "ApprovalActionStep", "UpdateStep"]
    assert steps[0].field_values == {"Opportunity.Loan_Amount__c": 6000000}
    assert steps[1].action == "submit"
    assert steps[2].expect_rejection is not None
    assert steps[2].field_changes == {"Opportunity.StageName": "Approved"}
    # Layer-1 + the honest caveat: the run is the verification.
    assert b.caveat_required is True


def test_arc_negative_message_binds_when_one_formula():
    g = GroundedNegative(
        archetype="data_behavior", claim_kind="prohibition-claim",
        operation_hint="modify_record", version_seq=7,
        subject=_ep("Opportunity", "Object"),
        requirement_excerpt="x",
        vr_formulas=("F1",), vr_messages={"F1": "Needs Manager Approval"},
        conditions=(), approval_actions=("submit",),
        attempted_change=(_ep("Opportunity.StageName"), "Approved"))
    b = author_emission(g)
    upd = b.observation_realization.steps[-1]
    assert upd.expect_rejection.error_message_pattern == (
        "Needs\\ Manager\\ Approval")


def test_arc_vs_plain_prohibition_hash_apart():
    arc = author_emission(_arc_negative())
    h_arc = compute_identity_hash(arc.archetype, arc.claim_kind,
                                  arc.asserted_truth, arc.semantic_conditions)
    arc2 = author_emission(_arc_negative(actions=("submit", "reject")))
    h_arc2 = compute_identity_hash(
        arc2.archetype, arc2.claim_kind, arc2.asserted_truth,
        arc2.semantic_conditions)
    assert h_arc != h_arc2       # pending-blocks vs rejected-still-blocked


# ---------------------------------------------------------------------------
# Emission — the arc acceptance author
# ---------------------------------------------------------------------------

def test_arc_acceptance_authors_v3_body_and_interposed_steps():
    g = GroundedAcceptance(
        archetype="data_behavior", claim_kind="acceptance-claim",
        version_seq=7, subject=_ep("Opportunity", "Object"),
        requirement_excerpt="once approved it can move to Approved",
        conditions=(_cond("Opportunity.Loan_Amount__c", 6000000),),
        update_conditions=(_cond("Opportunity.StageName", "Approved"),),
        approval_actions=("submit", "approve"))
    b = author_emission(g)
    assert isinstance(b.asserted_truth, AcceptanceClaimArcBody)
    assert b.asserted_truth.approval_actions == ["submit", "approve"]
    steps = b.observation_realization.steps
    assert [type(s).__name__ for s in steps] == [
        "CreateStep", "ApprovalActionStep", "ApprovalActionStep",
        "UpdateStep", "ReadStep", "AssertStep"]
    assert steps[3].expect_acceptance is True
    assert steps[3].field_changes == {"Opportunity.StageName": "Approved"}


def test_arcless_acceptance_is_byte_identical_v2():
    g = GroundedAcceptance(
        archetype="data_behavior", claim_kind="acceptance-claim",
        version_seq=7, subject=_ep("Opportunity", "Object"),
        requirement_excerpt="x",
        conditions=(_cond("Opportunity.KYC_Complete__c", True),),
        update_conditions=(_cond("Opportunity.StageName",
                                 "Credit Assessment"),))
    b = author_emission(g)
    assert b.asserted_truth.body_schema_version == 2
    assert not any(isinstance(s, ApprovalActionStep)
                   for s in b.observation_realization.steps)


# ---------------------------------------------------------------------------
# Governance — the arc grounding helper
# ---------------------------------------------------------------------------

def _field_rel(sf_api_name):
    return SimpleNamespace(
        edge_type="BELONGS_TO",
        entity=SimpleNamespace(entity_type="Field", sf_api_name=sf_api_name,
                               id=uuid4(), attributes={}))


_NB = [_field_rel("Opportunity.StageName"),
       _field_rel("Opportunity.Loan_Amount__c")]
_META = {"StageName": {"field_type": "picklist",
                       "picklist_values": ["Prospecting", "Approved"],
                       "is_updateable": True},
         "Loan_Amount__c": {"field_type": "currency"}}
_ONE_APPROVAL = [SimpleNamespace(sf_api_name="HL_High_Value_Loan")]


def test_ground_arc_prohibition_happy_path_binds_picklist():
    actions, change, err = gc._ground_arc_prohibition(
        {"approval_actions": ["submit"],
         "attempted_change": {"field_name": "Opportunity.StageName",
                              "value": "approved"}},   # ci-bound to org casing
        _NB, _META, _ONE_APPROVAL)
    assert err is None and actions == ("submit",)
    ep, value = change
    assert ep.external_id == "Opportunity.StageName" and value == "Approved"


def test_ground_arc_prohibition_refusals():
    # not submit-first
    _, _, err = gc._ground_arc_prohibition(
        {"approval_actions": ["approve"],
         "attempted_change": {"field_name": "Opportunity.StageName",
                              "value": "Approved"}},
        _NB, _META, _ONE_APPROVAL)
    assert err and "beginning with 'submit'" in err
    # zero active approvals
    _, _, err = gc._ground_arc_prohibition(
        {"approval_actions": ["submit"],
         "attempted_change": {"field_name": "Opportunity.StageName",
                              "value": "Approved"}},
        _NB, _META, [])
    assert err and "exactly ONE active approval" in err
    # missing attempted_change
    _, _, err = gc._ground_arc_prohibition(
        {"approval_actions": ["submit"]}, _NB, _META, _ONE_APPROVAL)
    assert err and "attempted_change" in err
    # unknown field
    _, _, err = gc._ground_arc_prohibition(
        {"approval_actions": ["submit"],
         "attempted_change": {"field_name": "Opportunity.Nope__c",
                              "value": "X"}},
        _NB, _META, _ONE_APPROVAL)
    assert err and "does not BELONG_TO" in err
    # unbindable picklist value
    _, _, err = gc._ground_arc_prohibition(
        {"approval_actions": ["submit"],
         "attempted_change": {"field_name": "Opportunity.StageName",
                              "value": "Closed Won"}},
        _NB, _META, _ONE_APPROVAL)
    assert err and "not an active picklist value" in err
