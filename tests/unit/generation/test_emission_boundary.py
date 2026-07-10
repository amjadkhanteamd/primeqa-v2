"""Unit tests for D-300 boundary-recipe authoring. Pure — no DB.

`author_boundary_recipes` maps a `derive_boundary_set` member tuple to
`BoundaryRecipe` carriers: ONLY the non-firing (accept) members author — the
firing member IS the bundle's primary reject recipe (the unit-pinned
member[0]==derive() invariant), so re-authoring it would double-execute the
same probe. The accept probe is the shipped positive round-trip shape
(create at the just-inside value -> read -> assert OUR value persisted) with
OBJECT-QUALIFIED payload keys (S4 world padding must never overwrite the
boundary value) and priority -1 (D-300.1: below the primary, above the -10
inspection fallback).
"""
from __future__ import annotations

import dataclasses

from primeqa.generation.emission import (
    BoundaryRecipe, EmissionBundle, _BOUNDARY_PROBE_PRIORITY,
    author_boundary_recipes,
)
from primeqa.generation.verified_negative import derive_boundary_set
from primeqa.semantic.formula import parse
from primeqa.test_representation.models.recipes.data_recipe import (
    AssertStep as DataAssertStep, CreateStep, ReadStep,
)


def _members(formula="Amount > 10000"):
    members = derive_boundary_set(parse(formula))
    assert len(members) == 2
    return members


def _authored(formula="Amount > 10000"):
    return author_boundary_recipes(
        subject_entity_type="Object", subject_external_id="Opportunity",
        members=_members(formula))


def test_authors_only_the_accept_member():
    out = _authored()
    assert len(out) == 1                       # the firing member is the primary
    assert isinstance(out[0], BoundaryRecipe)
    assert out[0].priority == _BOUNDARY_PROBE_PRIORITY == -1
    assert out[0].trigger_kind == "data-mutation-trigger"
    assert out[0].recipe_kind == "data-recipe"


def test_accept_probe_is_the_positive_round_trip_shape():
    recipe = _authored()[0].observation_realization
    create, read, assertion = recipe.steps
    assert isinstance(create, CreateStep)
    assert isinstance(read, ReadStep)
    assert isinstance(assertion, DataAssertStep)
    # the create expects SUCCESS — no expect_rejection (the accept polarity)
    assert create.expect_rejection is None
    # OBJECT-QUALIFIED key + the just-inside value (Amount > 10000 -> 10000)
    assert create.field_values == {"Opportunity.Amount": 10000}
    # round-trip assert on OUR OWN payload value — never a fabricated org value
    assert assertion.predicate.predicate == "equals"
    assert assertion.predicate.value == 10000
    assert read.fields_to_capture == ["Opportunity.Amount"]
    assert "WHERE Id = '$create-record.id'" in read.soql


def test_accept_probe_value_tracks_the_operator():
    # Amount <= 10000 fires AT 10000; just-inside (accept) is 10001.
    create = _authored("Amount <= 10000")[0].observation_realization.steps[0]
    assert create.field_values == {"Opportunity.Amount": 10001}


def test_edge_label_rides_the_env_detail():
    env = _authored()[0].execution_environment
    detail = env.auth_assumptions[0].details
    assert "just-inside" in detail and "boundary probe" in detail


def test_empty_members_author_nothing():
    assert author_boundary_recipes(
        subject_entity_type="Object", subject_external_id="Opportunity",
        members=()) == ()


def test_emission_bundle_boundary_slot_defaults_empty():
    fields = {f.name: f for f in dataclasses.fields(EmissionBundle)}
    assert fields["boundary_recipes"].default == ()


# -- P1 (Amendment B): the D-328 GATED accept probe -----------------------------
# A gated boundary member stages the gate fields alongside the boundary value
# (VR08: RecordTypeId + Discount). Pre-P1 the accept author crashed on the
# multi-field payload ("too many values to unpack") — generation must author,
# never crash. boundary_field names WHICH key carries the boundary value.

def test_gated_accept_probe_authors_full_payload():
    from primeqa.generation.verified_negative import derive_boundary_set
    from primeqa.semantic.formula import parse
    rail = {
        "PLS_BM_Discount__c": {"field_type": "percent", "scale": 2,
                               "is_createable": True, "is_updateable": True},
        "__record_types__": {"PLS_BM_Enterprise": "012Ent000000000AAA",
                             "PLS_BM_Standard": "012Std000000000AAA"},
    }
    vr08 = ("RecordType.DeveloperName = \"PLS_BM_Enterprise\" && "
            "PLS_BM_Discount__c > 0.25")
    members = derive_boundary_set(parse(vr08), rail)
    out = author_boundary_recipes(
        subject_entity_type="Object", subject_external_id="PLS_BM_Deal__c",
        members=members)
    assert len(out) == 1
    create, read, assertion = out[0].observation_realization.steps
    # FULL payload: the gate (RecordTypeId) + the boundary value, all qualified
    assert create.field_values == {
        "PLS_BM_Deal__c.RecordTypeId": "012Ent000000000AAA",
        "PLS_BM_Deal__c.PLS_BM_Discount__c": 0.25,
    }
    # read + assert target the BOUNDARY field, not the gate
    assert read.fields_to_capture == ["PLS_BM_Deal__c.PLS_BM_Discount__c"]
    assert assertion.predicate.value == 0.25


def test_hand_built_single_field_member_still_authors():
    # boundary_field=None (hand-built) keeps the single-key inference path.
    from primeqa.generation.verified_negative import BoundaryMember
    out = author_boundary_recipes(
        subject_entity_type="Object", subject_external_id="Opportunity",
        members=(BoundaryMember(payload={"Amount": 10000},
                                expect_reject=False, edge="just-inside"),))
    create = out[0].observation_realization.steps[0]
    assert create.field_values == {"Opportunity.Amount": 10000}
