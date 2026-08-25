"""3A-2 registration completeness (LLD §d) — every kind-keyed registry
resolves the two new kinds; the LLM vocabulary EXCLUDES conformance-claim
(enumerated-only) and the exclusion is asserted, not incidental."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_body_registry_resolves_both_kinds():
    from primeqa.test_representation.models.registry import default_registry
    from primeqa.test_representation.models.claims.ui.conformance_claim import (
        ConformanceClaimBody)
    from primeqa.test_representation.models.recipes.ui_inspection import (
        UiInspectionBody)
    assert default_registry.get_body_model("conformance-claim", 1) is ConformanceClaimBody
    assert default_registry.get_body_model("ui-inspection", 1) is UiInspectionBody


def test_readable_body_renders_conformance():
    from primeqa.intelligence import readable_body as rb
    assert "conformance-claim" in rb._REGISTERED_KINDS
    assert "conformance check" in rb._CONFORMANCE_GLOSS


def test_claim_title_renders_conformance():
    from primeqa.intelligence.claim_presentation import claim_title
    t = claim_title("conformance-claim", {
        "plimsol_rule_id": "PLM-A11Y-001",
        "surface": {"site": "portal.example.com", "path": "/s/home",
                    "persona_scope": "customer"}})
    assert t == "Conforms to PLM-A11Y-001 on portal.example.com/s/home as customer"


def test_evidence_contract_declares_conformance():
    from primeqa.generation.evidence_contract import EvidenceTier, required_evidence
    assert required_evidence("conformance-claim") is EvidenceTier.STRUCTURAL


def test_llm_vocabulary_excludes_conformance_enumerated_only():
    from primeqa.generation import tools as T
    from primeqa.test_representation.models_db import CLAIM_KIND_ENUM
    assert "conformance-claim" in CLAIM_KIND_ENUM.enums     # in the taxonomy
    assert "conformance-claim" not in T._CLAIM_KINDS        # never LLM-offered
