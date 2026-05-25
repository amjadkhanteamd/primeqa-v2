"""Unit tests for the semantic-completeness registry (D-097.3 / D-098.5) and
the verbatim S2 binding of the config metadata-relationship claim_kind.

No PG. Guards: the caveat reads ONLY from the registry; the registry is
claim-kind-general (not config-special-cased); every key is a real S2
claim_kind; and the config debut body binds its kind/archetype verbatim."""
from __future__ import annotations

from primeqa.generation.semantic_completeness import (
    _HAS_LAYER_2, caveat_kind, has_layer_2, requires_caveat,
)


# ---------------------------------------------------------------------------
# Caveat authority (D-097.3) — caveat present iff has_layer_2
# ---------------------------------------------------------------------------

def test_config_metadata_relationship_no_caveat():
    # Layer-1-complete (D-079): reading S1 IS the verification -> no caveat.
    assert has_layer_2("metadata-relationship-claim") is False
    assert requires_caveat("metadata-relationship-claim") is False


def test_data_behavior_negatives_carry_caveat():
    # Layer-2 defined-but-unbuilt (parser-deferred) -> mandatory caveat.
    assert requires_caveat("prohibition-claim") is True
    assert requires_caveat("state-transition-claim") is True


def test_value_claim_no_caveat():
    assert requires_caveat("value-claim") is False


def test_unknown_claim_kind_defaults_to_caveat():
    # Fail toward honesty: oversell-avoidance default is "caveat".
    assert requires_caveat("some-future-behavioral-kind") is True


# ---------------------------------------------------------------------------
# caveat_kind — typed posture, sole authority alongside requires_caveat (D-101.3)
# ---------------------------------------------------------------------------

def test_caveat_kind_typed_posture():
    from primeqa.generation.enums import CaveatKind
    # Layer-1-plausible -> the typed deeper-verification-layer-unparsed posture.
    assert caveat_kind("prohibition-claim") is CaveatKind.DEEPER_VERIFICATION_LAYER_UNPARSED
    assert caveat_kind("state-transition-claim") is CaveatKind.DEEPER_VERIFICATION_LAYER_UNPARSED
    # Layer-1-complete -> no posture.
    assert caveat_kind("metadata-relationship-claim") is None
    assert caveat_kind("value-claim") is None


def test_caveat_kind_set_iff_required():
    # The persistence CHECK invariant (caveat_required = caveat_kind IS NOT NULL)
    # holds at the authority: a kind is present exactly when a caveat is required.
    for ck in ("prohibition-claim", "state-transition-claim", "automation-effect-claim",
               "metadata-relationship-claim", "value-claim", "existence-claim",
               "some-future-behavioral-kind"):
        assert (caveat_kind(ck) is not None) == requires_caveat(ck)


# ---------------------------------------------------------------------------
# Per-emission verified discharge (D-107) — verified=True drops the caveat
# ---------------------------------------------------------------------------

def test_verified_drops_caveat_for_layer2_kind():
    # The deeper layer (Layer 2) was discharged for THIS emission (a violating
    # value derived with certainty) -> no caveat, even though the claim_kind
    # structurally HAS a Layer 2.
    assert requires_caveat("prohibition-claim", verified=True) is False
    assert caveat_kind("prohibition-claim", verified=True) is None
    # has_layer_2 is the structural fact and is unchanged by the per-emission axis.
    assert has_layer_2("prohibition-claim") is True


def test_unverified_keeps_caveat_is_the_default():
    from primeqa.generation.enums import CaveatKind
    # Default verified=False preserves the Layer-1-plausible caveat (the existing
    # caveated-negative behavior — backward compatible).
    assert requires_caveat("prohibition-claim", verified=False) is True
    assert caveat_kind("prohibition-claim", verified=False) is \
        CaveatKind.DEEPER_VERIFICATION_LAYER_UNPARSED


def test_verified_is_a_noop_for_layer1_complete_kind():
    # A Layer-1-complete kind has no Layer 2 to discharge: verified must not
    # invert anything (still no caveat either way).
    assert requires_caveat("metadata-relationship-claim", verified=True) is False
    assert requires_caveat("metadata-relationship-claim", verified=False) is False
    assert caveat_kind("value-claim", verified=True) is None


def test_caveat_kind_set_iff_required_holds_on_verified_axis():
    # The persistence CHECK invariant survives the new axis: a kind is present
    # exactly when a caveat is required, for every (claim_kind, verified) pair.
    for ck in ("prohibition-claim", "state-transition-claim", "value-claim",
               "metadata-relationship-claim", "some-future-behavioral-kind"):
        for verified in (True, False):
            assert (caveat_kind(ck, verified) is not None) == requires_caveat(ck, verified)


# ---------------------------------------------------------------------------
# Claim-kind-general, not config-special-cased (D-098.5 gravity guard)
# ---------------------------------------------------------------------------

def test_registry_spans_archetypes():
    assert "metadata-relationship-claim" in _HAS_LAYER_2   # configuration
    assert "value-claim" in _HAS_LAYER_2                    # data_behavior
    assert "prohibition-claim" in _HAS_LAYER_2              # data_behavior
    # behavioral kinds are present-and-True, so they slot in without a
    # config-shaped retrofit.
    assert _HAS_LAYER_2["prohibition-claim"] is True


def test_registry_keys_are_real_s2_claim_kinds():
    """Drift-guard: every registry key is a real S2 claim_kind (never minted)."""
    from primeqa.test_representation.models_db import CLAIM_KIND_ENUM
    s2_kinds = set(CLAIM_KIND_ENUM.enums)
    for k in _HAS_LAYER_2:
        assert k in s2_kinds, f"{k!r} is not an S2 claim_kind (registry drift)"


# ---------------------------------------------------------------------------
# Config debut body binds S2 strings verbatim (never mint)
# ---------------------------------------------------------------------------

def test_config_body_binds_s2_strings_verbatim():
    from primeqa.test_representation.models_db import ARCHETYPE_ENUM, CLAIM_KIND_ENUM
    from primeqa.test_representation.models.claims.configuration import (
        MetadataRelationshipClaimBody,
    )
    inst = MetadataRelationshipClaimBody(
        edge_type="APPLIES_TO",
        source={"ref_kind": "pinned", "entity_type": "ValidationRule",
                "entity_id": "11111111-1111-1111-1111-111111111111",
                "version_seq": 1, "external_id": "VR"},
        target={"ref_kind": "pinned", "entity_type": "Object",
                "entity_id": "22222222-2222-2222-2222-222222222222",
                "version_seq": 1, "external_id": "Account"},
    )
    assert inst.kind == "metadata-relationship-claim"
    assert "metadata-relationship-claim" in CLAIM_KIND_ENUM.enums   # verbatim, never minted
    assert "configuration" in ARCHETYPE_ENUM.enums


def test_config_body_registered():
    from primeqa.test_representation.models.registry import get_body_model
    from primeqa.test_representation.models.claims.configuration import (
        MetadataRelationshipClaimBody,
    )
    assert get_body_model("metadata-relationship-claim", 1) is MetadataRelationshipClaimBody
