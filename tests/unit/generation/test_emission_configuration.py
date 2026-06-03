"""Emission probes: config existence-claim + property-claim (D-122, slice 1).

Deterministic — construct the grounded shape and assert the authored
``EmissionBundle`` (claim body, inspection recipe, Layer-1-no-caveat). No DB, no
S1; the resolver→ground path is exercised by the integration draft vertical.
"""
from __future__ import annotations

from uuid import uuid4

from primeqa.generation.emission import (
    GroundedExistence,
    GroundedProperty,
    _Endpoint,
    author_emission,
)
from primeqa.generation.enums import AdmissibilityLayer
from primeqa.test_representation.models.claims.configuration import (
    ExistenceClaimBody,
    PropertyClaimBody,
)


def _subj(entity_type="Field", external_id="Account.Industry"):
    return _Endpoint(entity_id=uuid4(), entity_type=entity_type, external_id=external_id)


# --- existence-claim ---

def test_existence_authors_metadata_read_exists():
    b = author_emission(GroundedExistence(
        archetype="configuration", claim_kind="existence-claim", version_seq=7,
        subject=_subj(), requirement_excerpt="Account has an Industry field"))
    assert isinstance(b.asserted_truth, ExistenceClaimBody)
    assert b.asserted_truth.subject.external_id == "Account.Industry"
    assert b.trigger_kind == "inspection-trigger" and b.recipe_kind == "metadata-recipe"
    read, assertion = b.observation_realization.steps
    assert read.step_id == "read-subject"
    assert assertion.predicate.predicate == "exists" and assertion.predicate.value is None


def test_existence_is_layer1_uncaveated():
    b = author_emission(GroundedExistence(
        archetype="configuration", claim_kind="existence-claim", version_seq=7,
        subject=_subj(), requirement_excerpt="x"))
    assert b.admissibility_layer == AdmissibilityLayer.LAYER_1
    assert b.caveat_required is False and b.caveat_kind is None


# --- property-claim ---

def test_property_authors_equals_assert_on_the_value():
    b = author_emission(GroundedProperty(
        archetype="configuration", claim_kind="property-claim", version_seq=7,
        subject=_subj(), property_name="is_required", expected_value=True,
        requirement_excerpt="Industry is required"))
    assert isinstance(b.asserted_truth, PropertyClaimBody)
    assert b.asserted_truth.property_name == "is_required"
    assert b.asserted_truth.expected_value.value is True       # LiteralValue
    read, assertion = b.observation_realization.steps
    assert read.fields_to_capture == ["is_required"]
    assert assertion.predicate.predicate == "equals" and assertion.predicate.value is True
    assert b.admissibility_layer == AdmissibilityLayer.LAYER_1
    assert b.caveat_required is False


def test_property_null_value_authors_is_null():
    b = author_emission(GroundedProperty(
        archetype="configuration", claim_kind="property-claim", version_seq=7,
        subject=_subj(), property_name="default_value", expected_value=None,
        requirement_excerpt="no default"))
    assert b.asserted_truth.expected_value.kind == "null"      # NullValue
    _, assertion = b.observation_realization.steps
    assert assertion.predicate.predicate == "is_null" and assertion.predicate.value is None
