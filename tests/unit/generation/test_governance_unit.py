"""Pure unit tests for governance-core internals (no PG): the S1 edge-type
drift-guard, dismissal phase-tagging (D-077), and explanation_hash mechanics
(D-075) — determinism, prose-freeness, typed-field sensitivity."""
from __future__ import annotations

import json

from primeqa.generation import governance_core as gc
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
