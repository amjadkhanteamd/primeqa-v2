"""Emission probes: permission capability-claim (D-123, slice 2).

Deterministic — construct the grounded shape and assert the authored
``EmissionBundle`` (claim body, inspection recipe over the grant edge,
Layer-1-no-caveat). No DB, no S1; the resolver->ground path is exercised by the
integration draft vertical.
"""
from __future__ import annotations

from uuid import uuid4

from primeqa.generation.emission import (
    GroundedCapability,
    _Endpoint,
    author_emission,
)
from primeqa.generation.enums import AdmissibilityLayer
from primeqa.test_representation.models.claims.permission import CapabilityClaimBody


def _ep(entity_type, external_id):
    return _Endpoint(entity_id=uuid4(), entity_type=entity_type, external_id=external_id)


def _grounded(grant_type="field", capability="edit",
              grantee=("Profile", "Admin"), target=("Field", "Account.AnnualRevenue")):
    return GroundedCapability(
        archetype="permission", claim_kind="capability-claim", version_seq=7,
        granting_subject=_ep(*grantee), target=_ep(*target),
        granted_capability=capability, grant_type=grant_type,
        requirement_excerpt="The Admin profile can edit Account.AnnualRevenue")


def test_capability_authors_grant_edge_inspection():
    b = author_emission(_grounded())
    assert isinstance(b.asserted_truth, CapabilityClaimBody)
    assert b.asserted_truth.granting_subject.external_id == "Admin"
    assert b.asserted_truth.target.external_id == "Account.AnnualRevenue"
    assert b.asserted_truth.granted_capability == "edit"
    assert b.asserted_truth.grant_type == "field"
    assert b.trigger_kind == "inspection-trigger" and b.recipe_kind == "metadata-recipe"
    read, assertion = b.observation_realization.steps
    # The recipe reads the GRANTEE's metadata (it is the entity that holds grants).
    assert read.step_id == "read-subject"
    assert read.fields_to_capture == ["GRANTS_FIELD_ACCESS"]
    # Layer-1: existence of the captured grant IS the verification.
    assert assertion.predicate.predicate == "exists" and assertion.predicate.value is None


def test_capability_object_grant_captures_object_edge():
    b = author_emission(_grounded(grant_type="object", capability="read",
                                  target=("Object", "Case")))
    assert b.asserted_truth.grant_type == "object"
    read, _ = b.observation_realization.steps
    assert read.fields_to_capture == ["GRANTS_OBJECT_ACCESS"]


def test_capability_is_layer1_uncaveated():
    b = author_emission(_grounded())
    assert b.admissibility_layer == AdmissibilityLayer.LAYER_1
    assert b.caveat_required is False and b.caveat_kind is None
