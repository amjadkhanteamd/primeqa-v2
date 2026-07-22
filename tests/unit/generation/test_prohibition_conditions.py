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


# A derivable numeric VR by default — D-293 authors a prohibition only when a
# behavioural reject recipe derives; the formula drives the RECIPE (operational),
# never the claim identity, so it does not affect the identity assertions below.
def _grounded(conditions=(), formulas=("Amount__c > 10000",)):
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
    # Same business STATE; different (both derivable) vr_formulas drive a different
    # recipe — the violating VALUE/threshold is operational. Identity must be
    # unchanged (D-110.3's preserved Option-C property). Both formulas must derive,
    # since D-293 refuses a non-derivable prohibition (no recipe to compare).
    cond = (_cond("Opportunity.Loan_Amount__c", LOAN_AMOUNT),)
    a = author_emission(_grounded(conditions=cond, formulas=("Amount__c > 10000",)))
    b = author_emission(_grounded(conditions=cond, formulas=("Amount__c > 99999",)))
    assert _hash(a) == _hash(b)


# --- the state is faithfully authored into the claim body ---

def test_conditions_authored_into_semantic_conditions():
    b = author_emission(_grounded(conditions=(
        _cond("Opportunity.Loan_Amount__c", LOAN_AMOUNT, "is_null"),)))
    conds = b.semantic_conditions.conditions
    assert len(conds) == 1
    assert conds[0].subject.external_id == "Opportunity.Loan_Amount__c"
    assert conds[0].predicate == "is_null" and conds[0].value is None


# ---------------------------------------------------------------------------
# D-384: matches_pattern grounds VALUE-FREE — invented spellings cannot mint
# distinct identities (the req-320 duplicate class: 13 spellings of ONE
# PLS_FB_Order behaviour). End-to-end pin through the live chokepoint:
# _ground_rejection_conditions (drop) -> author_emission -> identity_hash.
# ---------------------------------------------------------------------------

from types import SimpleNamespace                            # noqa: E402

from primeqa.generation import governance_core as gc         # noqa: E402

EXT_REF = uuid4()
ORDER_NO = uuid4()


def _fb_neighborhood():
    """A stable BELONGS_TO neighborhood — entity ids held constant so hash
    comparisons discriminate on the CONDITIONS, never on ref identity."""
    return [
        SimpleNamespace(edge_type="BELONGS_TO", entity=SimpleNamespace(
            entity_type="Field", id=EXT_REF, attributes={},
            sf_api_name="PLS_FB_Order__c.External_Reference__c")),
        SimpleNamespace(edge_type="BELONGS_TO", entity=SimpleNamespace(
            entity_type="Field", id=ORDER_NO, attributes={},
            sf_api_name="PLS_FB_Order__c.Order_Number__c")),
    ]


def _ground_mp(field, value=...):
    clause = {"field": field, "predicate": "matches_pattern"}
    if value is not ...:
        clause["value"] = value
    grounded, invalid = gc._ground_rejection_conditions(
        [clause], _fb_neighborhood(), 7)
    assert invalid == [] and len(grounded) == 1
    return tuple(grounded)


def test_matches_pattern_spellings_collapse_to_one_identity():
    """Every invented spelling grounds to the SAME value-free condition, so
    emission mints ONE identity_hash and the D-339 persister dedup collapses
    regenerations — the live corpus's 13-spelling group becomes 1 claim."""
    spellings = ["INVALID", "INVALID_FORMAT", "invalid-format",
                 "<invalid-format>", "<invalid format>",
                 "<not matching required format>"]
    hashes = {
        _hash(author_emission(_grounded(conditions=_ground_mp(
            "PLS_FB_Order__c.External_Reference__c", s))))
        for s in spellings}
    assert len(hashes) == 1


def test_matches_pattern_value_less_proposal_grounds_same_identity():
    """A model that already honours the division of responsibility (no value
    at all) grounds to the SAME identity — pre-D-384 this refused
    'requires a value', punishing the honest proposal."""
    valued = _hash(author_emission(_grounded(conditions=_ground_mp(
        "PLS_FB_Order__c.External_Reference__c", "INVALID"))))
    value_less = _hash(author_emission(_grounded(conditions=_ground_mp(
        "PLS_FB_Order__c.External_Reference__c"))))
    assert valued == value_less


def test_matches_pattern_field_still_discriminates_identity():
    """The drop removes NOISE, not signal — the same predicate on a DIFFERENT
    field is still a distinct business state and a distinct claim."""
    a = _hash(author_emission(_grounded(conditions=_ground_mp(
        "PLS_FB_Order__c.External_Reference__c"))))
    b = _hash(author_emission(_grounded(conditions=_ground_mp(
        "PLS_FB_Order__c.Order_Number__c"))))
    assert a != b
