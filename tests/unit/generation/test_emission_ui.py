"""Emission probes: ui layout-claim (D-124, slice 3).

Deterministic — construct the grounded shape and assert the authored
``EmissionBundle`` (claim body, inspection recipe over the INCLUDES_FIELD edge,
Layer-1-no-caveat). No DB, no S1; the resolver->ground path is exercised by the
integration draft vertical.
"""
from __future__ import annotations

from uuid import uuid4

from primeqa.generation.emission import (
    GroundedLayout,
    _Endpoint,
    author_emission,
)
from primeqa.generation.enums import AdmissibilityLayer
from primeqa.test_representation.models.claims.ui import LayoutClaimBody


def _ep(entity_type, external_id):
    return _Endpoint(entity_id=uuid4(), entity_type=entity_type, external_id=external_id)


def _grounded(layout=("Layout", "Account-Account Layout"),
              field=("Field", "Account.AnnualRevenue")):
    return GroundedLayout(
        archetype="ui", claim_kind="layout-claim", version_seq=7,
        layout=_ep(*layout), field=_ep(*field),
        requirement_excerpt="AnnualRevenue appears on the Account layout")


def test_layout_authors_includes_field_inspection():
    b = author_emission(_grounded())
    assert isinstance(b.asserted_truth, LayoutClaimBody)
    assert b.asserted_truth.layout.external_id == "Account-Account Layout"
    assert b.asserted_truth.field.external_id == "Account.AnnualRevenue"
    assert b.trigger_kind == "inspection-trigger" and b.recipe_kind == "metadata-recipe"
    read, assertion = b.observation_realization.steps
    # The recipe reads the LAYOUT's metadata (it holds the field placements).
    assert read.step_id == "read-subject"
    assert read.fields_to_capture == ["INCLUDES_FIELD"]
    # Layer-1: existence of the captured placement IS the verification.
    assert assertion.predicate.predicate == "exists" and assertion.predicate.value is None


def test_layout_is_layer1_uncaveated():
    # The D-124 design's key call: placement is a metadata fact (metadata-recipe),
    # NOT a ui-recipe — and Layer-1-complete, no caveat.
    b = author_emission(_grounded())
    assert b.recipe_kind == "metadata-recipe"   # not "ui-recipe"
    assert b.admissibility_layer == AdmissibilityLayer.LAYER_1
    assert b.caveat_required is False and b.caveat_kind is None
