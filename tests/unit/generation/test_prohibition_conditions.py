"""D-293 Slice 1 — prohibition claims can carry an identity-bearing business
state (semantic_conditions). DORMANT plumbing: byte-identical when no state is
supplied; distinct states yield distinct identity_hashes (the AC1/2/4 collapse
fix); identity is stable across recipe-affecting inputs (D-110.3's Option-C
property preserved — the violating value stays operational).

Offline: constructed GroundedNegatives + author_emission + compute_identity_hash.
No LLM, no DB. Nothing populates GroundedNegative.conditions until the intent +
grounding slices arm it, so this slice cannot regress live behaviour.
"""
from __future__ import annotations

from uuid import uuid4

from primeqa.generation.emission import (
    GroundedNegative,
    _Endpoint,
    _GroundedCondition,
    author_emission,
)
from primeqa.test_representation.identity_hash import compute_identity_hash

# Stable entity ids — identity_hash canonicalises pinned refs to {entity_id,
# entity_type}, so the SUBJECT must be held constant across compared bundles or
# distinctness would be for the wrong reason. Fields differ where the business
# state differs.
OPP = uuid4()
LOAN_AMOUNT = uuid4()
ANNUAL_INCOME = uuid4()


def _cond(external_id, eid, predicate="is_null", value=None):
    return _GroundedCondition(
        field=_Endpoint(entity_id=eid, entity_type="Field", external_id=external_id),
        predicate=predicate, value=value)


def _grounded(conditions=(), formulas=()):
    return GroundedNegative(
        archetype="data_behavior", claim_kind="prohibition-claim",
        operation_hint="modify_record", version_seq=7,
        subject=_Endpoint(entity_id=OPP, entity_type="Object", external_id="Opportunity"),
        requirement_excerpt="the org must reject the save",
        vr_formulas=formulas, conditions=conditions)


def _hash(bundle):
    return compute_identity_hash(
        bundle.archetype, bundle.claim_kind, bundle.asserted_truth, bundle.semantic_conditions)


# --- dormant: byte-identical when no state is supplied ---

def test_no_conditions_authors_empty_body_byte_identical():
    b = author_emission(_grounded())
    assert b.semantic_conditions.conditions == []          # exactly the pre-D-293 condition-free body
    # And two condition-free prohibitions on the same subject still collapse to one
    # identity (the pre-D-293 behaviour — unchanged while dormant).
    assert _hash(author_emission(_grounded())) == _hash(author_emission(_grounded()))


# --- the collapse fix: distinct business states -> distinct claims ---

def test_distinct_business_states_yield_distinct_identity():
    # AC1 (Loan Amount blank) vs another mandatory-field rule (Annual Income blank):
    # same subject + same operation + same generic rejection — ONLY the state differs.
    a = author_emission(_grounded(conditions=(_cond("Opportunity.Loan_Amount__c", LOAN_AMOUNT),)))
    b = author_emission(_grounded(conditions=(_cond("Opportunity.Annual_Income__c", ANNUAL_INCOME),)))
    assert _hash(a) != _hash(b)                            # would have collapsed pre-D-293


def test_same_business_state_same_identity():
    a = author_emission(_grounded(conditions=(_cond("Opportunity.Loan_Amount__c", LOAN_AMOUNT),)))
    b = author_emission(_grounded(conditions=(_cond("Opportunity.Loan_Amount__c", LOAN_AMOUNT),)))
    assert _hash(a) == _hash(b)


# --- Option-C property preserved: identity ignores recipe-affecting inputs ---

def test_identity_stable_across_recipe_inputs():
    # Same business STATE; different vr_formulas drive a different recipe (the
    # violating value is operational). Identity must be unchanged (D-110.3).
    cond = (_cond("Opportunity.Loan_Amount__c", LOAN_AMOUNT),)
    a = author_emission(_grounded(conditions=cond, formulas=()))
    b = author_emission(_grounded(conditions=cond, formulas=("Amount  > 10000",)))
    assert _hash(a) == _hash(b)


# --- the state is faithfully authored into the claim body ---

def test_conditions_authored_into_semantic_conditions():
    b = author_emission(_grounded(conditions=(
        _cond("Opportunity.Loan_Amount__c", LOAN_AMOUNT, "is_null"),)))
    conds = b.semantic_conditions.conditions
    assert len(conds) == 1
    assert conds[0].subject.external_id == "Opportunity.Loan_Amount__c"
    assert conds[0].predicate == "is_null" and conds[0].value is None
