"""Pure unit tests for governance-core internals (no PG): the S1 edge-type
drift-guard, the emittable-set drift-guard (D-105), dismissal phase-tagging
(D-077), and explanation_hash mechanics (D-075) — determinism, prose-freeness,
typed-field sensitivity."""
from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

from primeqa.generation import governance_core as gc
from primeqa.generation.enums import AdmissibilityLayer, CaveatKind
from primeqa.generation.emission import (
    EMITTABLE,
    GroundedCapability,
    GroundedEmission,
    GroundedExistence,
    GroundedNegative,
    GroundedPositive,
    GroundedProperty,
    _Endpoint,
    author_emission,
)
from primeqa.generation.explanation_hash import canonicalize, compute_explanation_hash
from primeqa.semantic.edges import TIER_1_EDGES


# ---------------------------------------------------------------------------
# Edge-type drift-guard (D-096.1 — bind verbatim to TIER_1_EDGES)
# ---------------------------------------------------------------------------

def test_neighborhood_edges_are_tier1_keys():
    for et in gc.OBJECT_NEIGHBORHOOD_EDGES:
        assert et in TIER_1_EDGES, f"{et!r} is not a TIER_1_EDGES key (drift)"


def test_validation_rule_edge_shape():
    md = TIER_1_EDGES[gc.EDGE_VALIDATION_RULE]   # APPLIES_TO
    assert "ValidationRule" in md.source_entity_types
    assert "Object" in md.target_entity_types


def test_flow_and_grant_edge_shapes():
    assert "Flow" in TIER_1_EDGES[gc.EDGE_FLOW].source_entity_types
    assert "Object" in TIER_1_EDGES[gc.EDGE_FLOW].target_entity_types
    grant = TIER_1_EDGES[gc.EDGE_OBJECT_GRANT]
    assert "Object" in grant.target_entity_types


# ---------------------------------------------------------------------------
# Emittable-set drift-guard (D-105.1/.3) — EMITTABLE and author_emission lockstep
# ---------------------------------------------------------------------------
# This guard binds the source of truth to the authoring capability: EMITTABLE
# can't claim a kind author_emission can't handle (a constructable grounded shape
# per pair, and no extras). Since the gate admits only EMITTABLE pairs, a *gated*
# PROCEED therefore always reaches an authorable finalize. It does NOT prove the
# no-crash guarantee for an *ungated* resolution path (a gating gap) — that is the
# runtime backstop in finalize_outcome (D-105.4). Adding an emittable kind MUST
# update this map with a real shape author_emission handles.

def _ep(entity_type: str, external_id: str) -> _Endpoint:
    return _Endpoint(entity_id=uuid4(), entity_type=entity_type, external_id=external_id)


_EMITTABLE_SHAPES = {
    ("configuration", "metadata-relationship-claim"): lambda: GroundedEmission(
        archetype="configuration", claim_kind="metadata-relationship-claim",
        edge_type="APPLIES_TO", version_seq=1,
        source=_ep("ValidationRule", "Case.RequireReason"),
        target=_ep("Object", "Case"), requirement_excerpt="x"),
    ("configuration", "existence-claim"): lambda: GroundedExistence(
        archetype="configuration", claim_kind="existence-claim", version_seq=1,
        subject=_ep("Field", "Account.Industry"), requirement_excerpt="x"),
    ("configuration", "property-claim"): lambda: GroundedProperty(
        archetype="configuration", claim_kind="property-claim", version_seq=1,
        subject=_ep("Field", "Account.Industry"), property_name="is_required",
        expected_value=True, requirement_excerpt="x"),
    ("permission", "capability-claim"): lambda: GroundedCapability(
        archetype="permission", claim_kind="capability-claim", version_seq=1,
        granting_subject=_ep("Profile", "Admin"),
        target=_ep("Field", "Account.AnnualRevenue"),
        granted_capability="edit", grant_type="field", requirement_excerpt="x"),
    ("data_behavior", "prohibition-claim"): lambda: GroundedNegative(
        archetype="data_behavior", claim_kind="prohibition-claim",
        operation_hint=None, version_seq=1,
        subject=_ep("Object", "Case"), requirement_excerpt="x"),
    ("data_behavior", "value-claim"): lambda: GroundedPositive(
        archetype="data_behavior", claim_kind="value-claim", version_seq=1,
        target_object=_ep("Object", "Invoice"), field=_ep("Field", "Invoice.Amount"),
        value="100", requirement_excerpt="x"),
}


def test_emittable_set_matches_author_emission_shapes():
    # Lockstep: every EMITTABLE pair has a constructable grounded shape here,
    # and the map carries no pair absent from EMITTABLE. A new emittable kind
    # that forgets either side trips this.
    assert set(_EMITTABLE_SHAPES) == set(EMITTABLE)


def test_every_emittable_pair_is_authorable():
    # The PROCEED gate only lets EMITTABLE pairs through; each MUST author into a
    # bundle (no TypeError) whose (archetype, claim_kind) round-trips the pair —
    # so a gated PROCEED can never reach an unauthorable finalize.
    for pair, make in _EMITTABLE_SHAPES.items():
        bundle = author_emission(make())
        assert (bundle.archetype, bundle.claim_kind) == pair


# ---------------------------------------------------------------------------
# D-107 formula capture at grounding — _grounding_vr_formulas
# ---------------------------------------------------------------------------

def _rel(edge_type: str, entity_type: str, attributes: dict):
    """A minimal stand-in for a RelatedEntity (only the fields the capture reads)."""
    return SimpleNamespace(
        edge_type=edge_type,
        entity=SimpleNamespace(entity_type=entity_type, attributes=attributes))


def test_grounding_vr_formulas_captures_matched_vrs_only():
    # Same (edge_type, far_type) the Layer-1 dimension matches: APPLIES_TO ->
    # ValidationRule. A Field on a different edge is ignored; a matched VR with no
    # formula_text contributes nothing; order is neighborhood order.
    nb = [
        _rel("APPLIES_TO", "ValidationRule", {"formula_text": "Amount < 0"}),
        _rel("BELONGS_TO", "Field", {"formula_text": "ignored — wrong edge/type"}),
        _rel("APPLIES_TO", "ValidationRule", {}),                 # VR, no formula
        _rel("APPLIES_TO", "ValidationRule", {"formula_text": "ISBLANK(R__c)"}),
    ]
    assert gc._grounding_vr_formulas("prohibition-claim", nb) == ("Amount < 0", "ISBLANK(R__c)")


def test_grounding_vr_formulas_unknown_dim_is_empty():
    # A claim_kind without a negative Layer-1 dimension has nothing to capture.
    assert gc._grounding_vr_formulas("value-claim", [_rel("APPLIES_TO", "ValidationRule",
                                                          {"formula_text": "Amount < 0"})]) == ()


# ---------------------------------------------------------------------------
# D-107 verified-vs-caveated drift-guard: LAYER_2 <=> caveat-dropped (Option C)
# ---------------------------------------------------------------------------

def _neg(formulas: tuple[str, ...]) -> GroundedNegative:
    return GroundedNegative(
        archetype="data_behavior", claim_kind="prohibition-claim",
        operation_hint=None, version_seq=1,
        subject=_ep("Object", "Case"), requirement_excerpt="x", vr_formulas=formulas)


def test_verified_negative_is_layer2_no_caveat():
    # A derivable grounding formula discharges Layer 2 -> verified marker, caveat
    # dropped.
    b = author_emission(_neg(("Amount < 0",)))
    assert b.admissibility_layer is AdmissibilityLayer.LAYER_2
    assert b.caveat_required is False and b.caveat_kind is None


def test_caveated_negative_is_layer1_with_caveat():
    # No derivable formula (empty; field-to-field; or unparsed) -> the Layer-1-
    # plausible caveated fallback (D-101), unchanged.
    for formulas in ((), ("Amount < Other__c",), ('REGEX(Name, "x")',)):
        b = author_emission(_neg(formulas))
        assert b.admissibility_layer is AdmissibilityLayer.LAYER_1
        assert b.caveat_required is True
        assert b.caveat_kind is CaveatKind.DEEPER_VERIFICATION_LAYER_UNPARSED


def test_layer2_iff_caveat_dropped_invariant():
    # The slice-4 drift-guard: across the verified/caveated split the marker and
    # the caveat move together — LAYER_2 exactly when no caveat. Under Option C
    # the gate is derivability ALONE (no payload-present clause). Includes a
    # multi-VR object where one of two formulas derives (at-least-one).
    for formulas in ((), ("Amount < 0",), ("Amount < Other__c",), ("ISBLANK(R__c)",),
                     ('REGEX(Name, "x")',), ("Amount < 0", 'Name < "M"')):
        b = author_emission(_neg(formulas))
        is_l2 = b.admissibility_layer is AdmissibilityLayer.LAYER_2
        assert is_l2 == (not b.caveat_required)
        assert is_l2 == (b.caveat_kind is None)


# ---------------------------------------------------------------------------
# Dismissal phase-tagging (D-077d)
# ---------------------------------------------------------------------------

def test_phase_for_reason():
    assert gc.phase_for_reason("no_constraint_supports_negative") == "grounding"
    assert gc.phase_for_reason("insufficient_grounding") == "grounding"
    assert gc.phase_for_reason("ambiguous_target_resolution") == "interpretation"
    assert gc.phase_for_reason("lower_specificity") == "interpretation"
    assert gc.phase_for_reason("policy_threshold_not_met") == "governance"


# ---------------------------------------------------------------------------
# explanation_hash (D-075) — mechanical, prose-free
# ---------------------------------------------------------------------------

def _ai(anchor="some excerpt prose", claim_kind="prohibition-claim"):
    return {
        "candidate_paths": [{
            "path_id": "c0", "archetype": "data_behavior", "claim_kind": claim_kind,
            "subject_refs": [{"entity_type": "Object", "sf_api_name": "Account"}],
            "requirement_anchor": anchor,
            "admissibility_status": "dismissed", "admissibility_layer": None,
        }],
        "dismissed_alternatives_by_reason": {"no_constraint_supports_negative": ["c0"]},
        "selected_path_id": None,
        "scoped_neighborhood": [],
    }


def test_hash_deterministic():
    assert compute_explanation_hash(_ai()) == compute_explanation_hash(_ai())
    assert len(compute_explanation_hash(_ai())) == 64


def test_hash_is_prose_free():
    # Changing the free-form requirement_anchor must NOT change the hash.
    assert compute_explanation_hash(_ai(anchor="prose A")) == compute_explanation_hash(_ai(anchor="prose B"))
    # And the canonical form carries no requirement_anchor at all.
    assert "requirement_anchor" not in json.dumps(canonicalize(_ai()))


def test_hash_sensitive_to_typed_substance():
    # Changing a typed field (claim_kind) MUST change the hash.
    assert compute_explanation_hash(_ai(claim_kind="prohibition-claim")) != \
           compute_explanation_hash(_ai(claim_kind="value-claim"))
