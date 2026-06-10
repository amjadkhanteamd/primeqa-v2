"""D-207 — the multi-claim vertical: one requirement -> N intents -> N claims,
one outcome.

Real S1 grounding + the real S2 Coordinator + the unified persistence
transaction over seeded local PG, driven through the runtime with a scripted
multi-intent propose. Asserts:

  - two grounded intents emit TWO claims + TWO recipes under ONE outcome row
    (claims_written / recipes_written carry both refs; D-072 intact);
  - the outcome-level epistemic posture aggregates conservatively (LAYER_2
    only when every bundle verified; any caveated bundle caveats the outcome);
  - partial grounding is a DRAFT (failed intent recorded as a dismissal under
    its own re-indexed path id), refusal only at zero grounded;
  - per-bundle identity dedup: a verbatim re-run dedups BOTH claims
    (equivalent_existing carries both, nothing new minted);
  - the generated_from requirement link lands per claim.
"""
from __future__ import annotations

import json
from uuid import uuid4

import pytest
from sqlalchemy import text

from primeqa.generation.enums import AdmissibilityLayer, OutcomeKind
from primeqa.generation.governance_core import GovernanceCore
from primeqa.generation.persistence import LedgerPersister

from .conftest import (
    FakeTurn, FakeToolTurn, TEST_TENANT_ID, intent, make_request, propose_turn, rel_intent,
)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _flat(legacy: dict) -> dict:
    """Convert a conftest legacy intent input to a D-207 flat array item."""
    item = dict(legacy["intent_descriptor"])
    item["requirement_excerpt"] = legacy["requirement_excerpt"]
    return item


def multi_propose(legacy_intents: list[dict]) -> dict:
    return {"intent_descriptors": [_flat(li) for li in legacy_intents]}


def _emit_draft_turn() -> FakeTurn:
    return FakeTurn([{"type": "tool_use", "id": f"tu_{uuid4().hex[:6]}",
                      "name": "emit_outcome",
                      "input": {"outcome_kind": "draft",
                                "payload": {"admissibility_layer": "layer_1"}}}])


def _run_one_requirement(seeded, propose_input: dict, *, persist=True):
    """Drive ONE requirement through propose(multi) -> emit."""
    from primeqa.semantic.connection import get_tenant_connection
    from primeqa.semantic.query import SemanticOrgModel
    from primeqa.generation.runtime import GenerationRuntime

    req = make_request(s1_version_seq=seeded["v1"])
    turns = [propose_turn(propose_input), _emit_draft_turn()]
    persister = LedgerPersister(TEST_TENANT_ID) if persist else None
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        gov = GovernanceCore(SemanticOrgModel(conn))
        result = GenerationRuntime().run(
            request=req, seam=gov, tool_turn_fn=FakeToolTurn(turns), persister=persister)
    return result.results[0]


def _query():
    from primeqa.semantic.connection import get_tenant_connection
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        claims = conn.execute(text(
            "SELECT test_id, archetype, claim_kind FROM test_claims")).mappings().all()
        recipes = conn.execute(text(
            "SELECT recipe_id, recipe_kind, trigger_kind FROM test_recipes")).mappings().all()
        outcomes = conn.execute(text(
            "SELECT outcome_kind, admissibility_layer, caveat_required, caveat_kind, "
            "claims_written, recipes_written, equivalent_existing, attempted_interpretation "
            "FROM generation_outcomes")).mappings().all()
        links = conn.execute(text(
            "SELECT test_id, external_key, link_kind FROM test_requirement_links")).mappings().all()
    return {"claims": claims, "recipes": recipes, "outcomes": outcomes, "links": links}


# The two grounded intents used throughout: the config relationship (Case VR
# APPLIES_TO Case — LAYER_1, no caveat) and the VERIFIED Lead prohibition
# (formula ISBLANK(Reason__c) derives a violating value — LAYER_2, no caveat).
def _rel():
    return rel_intent(
        edge_type="APPLIES_TO",
        source={"entity_type": "ValidationRule", "sf_api_name": "Case.RequireReason"},
        target={"entity_type": "Object", "sf_api_name": "Case"})


def _lead_prohibition():
    return intent(claim_kind="prohibition-claim", polarity="negative", sf_api_name="Lead")


def _case_prohibition():   # Case VR has NO formula -> caveated LAYER_1
    return intent(claim_kind="prohibition-claim", polarity="negative", sf_api_name="Case")


def _account_prohibition():   # bare Object, no VR -> never grounds
    return intent(claim_kind="prohibition-claim", polarity="negative", sf_api_name="Account")


# ---------------------------------------------------------------------------
# Two grounded intents -> two claims, one outcome
# ---------------------------------------------------------------------------

def test_two_intents_two_claims_one_outcome(seeded):
    r = _run_one_requirement(seeded, multi_propose([_rel(), _lead_prohibition()]))
    o = r.outcome
    assert o.outcome_kind == OutcomeKind.DRAFT
    assert len(o.claims_written) == 2
    assert len(o.recipes_written) == 2
    assert o.equivalent_existing is None
    # conservative aggregate: config bundle is LAYER_1 -> outcome LAYER_1;
    # neither bundle caveats -> no caveat
    assert o.admissibility_layer == AdmissibilityLayer.LAYER_1
    assert o.caveat_required is False and o.caveat_kind is None
    # both bundles ride the result for the persister
    assert len(r.emissions) == 2
    assert r.emission is r.emissions[0]

    rows = _query()
    assert len(rows["claims"]) == 2
    assert {c["claim_kind"] for c in rows["claims"]} == {
        "metadata-relationship-claim", "prohibition-claim"}
    assert len(rows["recipes"]) == 2
    assert len(rows["outcomes"]) == 1
    # the generated_from link landed per claim
    assert len(rows["links"]) == 2
    assert {l["link_kind"] for l in rows["links"]} == {"generated_from"}


def test_multi_intent_path_ids_reindexed(seeded):
    r = _run_one_requirement(seeded, multi_propose([_rel(), _lead_prohibition()]))
    ai = r.outcome.attempted_interpretation
    path_ids = {p["path_id"] for p in ai.candidate_paths}
    assert path_ids == {"c0", "c1"}
    # multi-intent drafts record the grounded set, not one canonical path
    assert ai.selected_path_id is None
    extra = ai.model_dump()
    assert extra.get("selected_path_ids") == ["c0", "c1"]


def test_caveat_aggregates_across_bundles(seeded):
    # Case prohibition is caveated (VR formula absent); the verified Lead
    # negative is LAYER_2/no-caveat. Any caveated bundle caveats the outcome.
    r = _run_one_requirement(seeded, multi_propose([_lead_prohibition(), _case_prohibition()]))
    o = r.outcome
    assert len(o.claims_written) == 2
    assert o.admissibility_layer == AdmissibilityLayer.LAYER_1
    assert o.caveat_required is True
    assert o.caveat_kind is not None


# ---------------------------------------------------------------------------
# Partial grounding -> draft; zero grounded -> refusal
# ---------------------------------------------------------------------------

def test_partial_grounding_is_a_draft_with_dismissal(seeded):
    r = _run_one_requirement(seeded, multi_propose([_account_prohibition(), _rel()]))
    o = r.outcome
    assert o.outcome_kind == OutcomeKind.DRAFT
    assert len(o.claims_written) == 1
    ai = o.attempted_interpretation
    # the failed intent stays recorded under its own slot (intent 0 -> c0)
    dismissed = {pid for ids in ai.dismissed_alternatives_by_reason.values() for pid in ids}
    assert "c0" in dismissed
    # the grounded intent's path id reflects its slot
    assert ai.model_dump().get("selected_path_id") == "c1"


def test_zero_grounded_refuses(seeded):
    r = _run_one_requirement(seeded, multi_propose(
        [_account_prohibition(), _account_prohibition()]))
    o = r.outcome
    assert o.outcome_kind == OutcomeKind.REFUSAL
    assert o.claims_written is None
    rows = _query()
    assert len(rows["claims"]) == 0
    assert len(rows["outcomes"]) == 1


# ---------------------------------------------------------------------------
# Per-bundle dedup on re-run
# ---------------------------------------------------------------------------

def test_rerun_dedups_both_claims(seeded):
    first = _run_one_requirement(seeded, multi_propose([_rel(), _lead_prohibition()]))
    assert len(first.outcome.claims_written) == 2
    second = _run_one_requirement(seeded, multi_propose([_rel(), _lead_prohibition()]))
    o2 = second.outcome
    # both bundles matched existing claims: refs recorded, nothing new minted
    assert len(o2.claims_written) == 2
    assert o2.equivalent_existing is not None and len(o2.equivalent_existing) == 2
    assert o2.recipes_written is None
    rows = _query()
    assert len(rows["claims"]) == 2     # still just the originals
    assert len(rows["recipes"]) == 2
    assert len(rows["outcomes"]) == 2   # both outcomes on the ledger
