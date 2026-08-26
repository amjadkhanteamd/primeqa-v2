"""3A-5 pure merge-gate tests — SF-08 normalizer version semantics, the
DE-11 mapping + corrected ownership rule, registry drift guards, and
the claim-identity pin (the canonicalizer's fields are unchanged by the
entity work)."""
from __future__ import annotations

import pytest

from primeqa.interpretation.ui_conformance import bundle_developer_name
from primeqa.semantic.normalization import hash_normalized, normalize

pytestmark = pytest.mark.unit


def _bundle(source="let a = 1;\n", path="lwc/loanWidget/loanWidget.js"):
    return {"Id": "0Rb000000000001", "DeveloperName": "loanWidget",
            "NamespacePrefix": None, "ApiVersion": 63.0,
            "Description": "d",
            "_resources": [{"FilePath": path, "Format": "js",
                            "Source": source}]}


def test_same_source_same_hash_crlf_normalised():
    h1 = hash_normalized(normalize("LightningComponentBundle", _bundle()))
    h2 = hash_normalized(normalize("LightningComponentBundle",
                                   _bundle(source="let a = 1;\r\n")))
    assert h1 == h2          # no-op resync (CRLF noise) → no new version


def test_one_line_edit_changes_hash():
    h1 = hash_normalized(normalize("LightningComponentBundle", _bundle()))
    h2 = hash_normalized(normalize("LightningComponentBundle",
                                   _bundle(source="let a = 2;\n")))
    assert h1 != h2          # a source edit IS a new S1 version


def test_tag_to_developer_name_mapping():
    assert bundle_developer_name("c-loan-widget") == "loanWidget"
    assert bundle_developer_name("c-widget") == "widget"
    assert bundle_developer_name("c-my-big-form-x") == "myBigFormX"


def test_sync_registries_carry_the_bundle_type():
    from primeqa.semantic.semantic_text import to_semantic_text
    from primeqa.sync.fk_assertion import ENTITY_ORDER, FINAL_PHASE
    from primeqa.sync.phases import PHASE_REGISTRY
    from primeqa.sync.presentation import to_presentation

    assert ENTITY_ORDER[-1] == "LightningComponentBundle"
    assert FINAL_PHASE == "LightningComponentBundle"       # D-308.1 sentinel
    assert "LightningComponentBundle" in PHASE_REGISTRY
    assert "Surface" not in PHASE_REGISTRY                 # declared, never synced
    assert "Surface" not in ENTITY_ORDER
    n = normalize("LightningComponentBundle", _bundle())
    p = to_presentation("LightningComponentBundle", n)
    assert p["name"] == "loanWidget"
    assert "loanWidget" in to_semantic_text("LightningComponentBundle", p)


def test_migration_sync_check_excludes_surface():
    from pathlib import Path
    ddl = (Path(__file__).parents[3] / "alembic" / "versions" / "tenant" /
           "20260825_0040_3a5_s1_entities.py").read_text("utf-8")
    upgrade_section = ddl.split("def downgrade")[0]
    assert "'LightningComponentBundle'" in upgrade_section
    # Surface must never be legal in the SYNCED lists (its only mention
    # is the downgrade's entity-row cleanup).
    assert "'Surface'" not in upgrade_section


def test_claim_identity_unchanged_by_entities():
    # The 3A-2 pin, re-asserted across 3A-5: the canonicalizer's output
    # fields are exactly {kind, plimsol_rule_id, surface_key} — no
    # entity id enters identity.
    from primeqa.test_representation.canonicalization import canonicalize
    from primeqa.test_representation.models.claims.ui.conformance_claim import (
        ConformanceClaimBody)
    body = ConformanceClaimBody(
        plimsol_rule_id="PLM-A11Y-001",
        surface={"site": "s.example.com", "path": "/p",
                 "persona_scope": "guest"})
    assert set(canonicalize(body).keys()) == {
        "kind", "plimsol_rule_id", "surface_key"}
