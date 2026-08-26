"""3A-2 conformance-claim identity tests (LLD §b) — pure, merge-gate.

The v1 identity contract: identity = plimsol_rule_id × the FROZEN
five-field surface natural key. These tests pin (1) hash stability across
dict-order permutations, (2) the exact v1 field composition (FAILS if a
field is added or dropped), (3) the frozen normalisation rules, and
(4) viewport's only-when-semantic participation.
"""
from __future__ import annotations

import pytest

from primeqa.test_representation.identity_hash import compute_identity_hash
from primeqa.test_representation.models.claims.ui.conformance_claim import (
    ConformanceClaimBody,
)
from primeqa.test_representation.models.conditions import SemanticConditionsBody
from primeqa.test_representation.models.surface import (
    SURFACE_KEY_FIELDS_V1,
    SurfaceNaturalKey,
    canonical_surface_key,
)

pytestmark = pytest.mark.unit

_SURFACE = {"site": "portal.example.com", "path": "/s/home",
            "persona_scope": "customer", "record_context_ref": None,
            "viewport": None}


def _hash(body_dict):
    body = ConformanceClaimBody.model_validate(body_dict)
    return compute_identity_hash("ui", "conformance-claim", body,
                                 SemanticConditionsBody())


def test_same_logical_claim_same_hash_across_dict_order_permutations():
    a = {"kind": "conformance-claim", "body_schema_version": 1,
         "plimsol_rule_id": "PLM-A11Y-001",
         "surface": {"site": "Portal.Example.com", "path": "/s/home/",
                     "persona_scope": "customer"}}
    # permuted key order + explicit None components + un-normalised host/path
    b = {"surface": {"persona_scope": "customer", "viewport": None,
                     "path": "s/home", "record_context_ref": None,
                     "site": "portal.example.com"},
         "plimsol_rule_id": "PLM-A11Y-001",
         "body_schema_version": 1, "kind": "conformance-claim"}
    assert _hash(a) == _hash(b)


def test_field_composition_is_exactly_the_v1_five():
    # FAILS if a field is added to or dropped from SurfaceNaturalKey —
    # the frozen IDENTITY_HASH_VERSION v1 composition (D2).
    assert SURFACE_KEY_FIELDS_V1 == (
        "site", "path", "persona_scope", "record_context_ref", "viewport")
    assert set(SurfaceNaturalKey.model_fields.keys()) == set(SURFACE_KEY_FIELDS_V1)


def test_frozen_normalisation_rules():
    key = canonical_surface_key(SurfaceNaturalKey(
        site="  Portal.Example.COM ", path="s/home/", persona_scope="customer"))
    assert key == "portal.example.com|/s/home|customer|-|-"


def test_rule_id_changes_hash_and_shape_is_enforced():
    base = {"kind": "conformance-claim", "body_schema_version": 1,
            "plimsol_rule_id": "PLM-A11Y-001", "surface": _SURFACE}
    other = {**base, "plimsol_rule_id": "PLM-A11Y-002"}
    assert _hash(base) != _hash(other)
    with pytest.raises(Exception):
        ConformanceClaimBody.model_validate({**base, "plimsol_rule_id": "axe:label"})


def test_viewport_participates_only_when_present():
    base = {"kind": "conformance-claim", "body_schema_version": 1,
            "plimsol_rule_id": "PLM-A11Y-001", "surface": _SURFACE}
    with_vp = {**base, "surface": {**_SURFACE, "viewport": "320x256"}}
    assert _hash(base) != _hash(with_vp)          # semantic viewport = identity
    absent = canonical_surface_key(SurfaceNaturalKey(**_SURFACE))
    assert absent.endswith("|-")                  # absent renders as '-'
