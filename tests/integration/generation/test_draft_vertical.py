"""Draft-vertical emission tests (D-098/D-099) — the C debut's emission half.

Real S1 grounding + the real S2 Coordinator + the unified persistence
transaction, over seeded local PG. Drives the config metadata-relationship
claim from ``propose`` -> grounded -> ``emit_outcome`` -> atomic
claim+recipe+ledger persistence. Asserts:

  - the marker is applied and (config Layer-1-complete) NO caveat;
  - claim + recipe + ledger all persist end to end, the recipe carrying the
    D-099 ``inspection-trigger`` (which also proves the enum migration applied
    and ``write_recipe`` accepts the sixth kind);
  - the transaction is atomic — a mid-emission failure rolls back claim,
    recipe, AND outcome together (D-097.4 / D-099);
  - identity dedup — a same-relationship re-emit is a ``was_noop`` that mints
    no duplicate test.

The reconciled Session-based persister keeping the *refusal* vertical green is
covered by the existing ``test_refusal_vertical.py`` persistence tests, which
now run through this same persister.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

from primeqa.generation.enums import AdmissibilityLayer, OutcomeKind
from primeqa.generation.governance_core import GovernanceCore
from primeqa.generation.persistence import LedgerPersister
from primeqa.generation.semantic_completeness import requires_caveat

from .conftest import (
    FakeTurn, FakeToolTurn, TEST_TENANT_ID, make_request, propose_turn, rel_intent,
)


# the seeded grounded relationship: VR "Case.RequireReason" APPLIES_TO "Case"
def _grounded_rel():
    return rel_intent(
        edge_type="APPLIES_TO",
        source={"entity_type": "ValidationRule", "sf_api_name": "Case.RequireReason"},
        target={"entity_type": "Object", "sf_api_name": "Case"})


def _emit_draft_turn() -> FakeTurn:
    """The LLM's emit_outcome call — transcribes the substrate-authored
    admissibility_layer (Layer A requires its presence); the substrate authors
    the actual bodies."""
    return FakeTurn([{"type": "tool_use", "id": f"tu_{uuid4().hex[:6]}",
                      "name": "emit_outcome",
                      "input": {"outcome_kind": "draft",
                                "payload": {"admissibility_layer": "layer_1"}}}])


def _emit_run(seeded, intents, *, persister=None):
    """Drive each requirement propose -> emit through the runtime (scripted
    [propose, emit] per requirement)."""
    from primeqa.semantic.connection import get_tenant_connection
    from primeqa.semantic.query import SemanticOrgModel
    from primeqa.generation.runtime import GenerationRuntime

    req = make_request(s1_version_seq=seeded["v1"])
    req.semantic_context.requirement_refs = [
        {"key": f"R{i}", "text": "the requirement"} for i in range(len(intents))
    ]
    turns = []
    for it in intents:
        turns.append(propose_turn(it))
        turns.append(_emit_draft_turn())
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        gov = GovernanceCore(SemanticOrgModel(conn))
        result = GenerationRuntime().run(
            request=req, seam=gov, tool_turn_fn=FakeToolTurn(turns), persister=persister)
    return req, result


def _query():
    """Read the persisted S2 + ledger state (fresh connection, after run)."""
    from primeqa.semantic.connection import get_tenant_connection
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        claims = conn.execute(text(
            "SELECT test_id, identity_hash, archetype, claim_kind FROM test_claims"
        )).mappings().all()
        recipes = conn.execute(text(
            "SELECT recipe_id, trigger_kind, recipe_kind FROM test_recipes"
        )).mappings().all()
        outcomes = conn.execute(text(
            "SELECT outcome_id, outcome_kind, admissibility_layer, claims_written, "
            "recipes_written FROM generation_outcomes")).mappings().all()
    return {"claims": claims, "recipes": recipes, "outcomes": outcomes}


# ---------------------------------------------------------------------------
# End-to-end: a verified draft (marker, no caveat) is persisted whole
# ---------------------------------------------------------------------------

def test_draft_emitted_end_to_end(seeded):
    _, res = _emit_run(seeded, [_grounded_rel()], persister=LedgerPersister(TEST_TENANT_ID))
    o = res.results[0].outcome
    assert o.outcome_kind == OutcomeKind.DRAFT
    # marker present; caveat absent (config metadata-relationship is Layer-1-complete)
    assert o.admissibility_layer == AdmissibilityLayer.LAYER_1
    assert requires_caveat("metadata-relationship-claim") is False
    # refs assigned post-write (D-099); a fresh emit, not a dedup
    assert o.claims_written and o.recipes_written
    assert o.equivalent_existing is None

    rows = _query()
    assert len(rows["claims"]) == 1
    assert len(rows["recipes"]) == 1
    assert len(rows["outcomes"]) == 1
    # the claim is the config metadata-relationship claim, identity persisted
    claim = rows["claims"][0]
    assert claim["archetype"] == "configuration"
    assert claim["claim_kind"] == "metadata-relationship-claim"
    assert len(claim["identity_hash"]) == 64
    # the recipe carries the D-099 inspection-trigger (proves the enum migration
    # applied + write_recipe accepts the sixth kind) over a metadata recipe
    recipe = rows["recipes"][0]
    assert recipe["trigger_kind"] == "inspection-trigger"
    assert recipe["recipe_kind"] == "metadata-recipe"
    # the outcome row points at the emitted claim + recipe
    assert rows["outcomes"][0]["claims_written"] is not None
    assert rows["outcomes"][0]["recipes_written"] is not None


# ---------------------------------------------------------------------------
# Atomicity: a mid-emission failure rolls back claim + recipe + outcome
# ---------------------------------------------------------------------------

def test_emission_atomic_rollback(seeded):
    class FailAtLedger(LedgerPersister):
        """Fails AFTER the S2 claim + recipe are written (flushed) but before
        the ledger outcome row — exercising the unified-transaction rollback."""

        def _insert_outcome(self, session, outcome):
            raise RuntimeError("simulated ledger failure after S2 writes")

    with pytest.raises(RuntimeError):
        _emit_run(seeded, [_grounded_rel()], persister=FailAtLedger(TEST_TENANT_ID))

    rows = _query()
    # all three roll back together — the claim/recipe flushed within the same
    # transaction are undone alongside the never-written outcome.
    assert len(rows["claims"]) == 0
    assert len(rows["recipes"]) == 0
    assert len(rows["outcomes"]) == 0


# ---------------------------------------------------------------------------
# Identity dedup: re-emitting the same relationship is a was_noop
# ---------------------------------------------------------------------------

def test_emission_identity_dedup_was_noop(seeded):
    persister = LedgerPersister(TEST_TENANT_ID)
    _, r1 = _emit_run(seeded, [_grounded_rel()], persister=persister)
    _, r2 = _emit_run(seeded, [_grounded_rel()], persister=persister)

    o1, o2 = r1.results[0].outcome, r2.results[0].outcome
    assert o1.equivalent_existing is None            # first emit: fresh test
    assert o2.equivalent_existing is not None        # re-emit: same-hash no-op
    # the same identity resolved to the SAME test_id (no duplicate minted)
    assert o2.equivalent_existing[0] == o1.claims_written[0].test_id

    rows = _query()
    assert len(rows["claims"]) == 1                  # one test, deduped
    # both runs recorded an outcome in the ledger
    assert len(rows["outcomes"]) == 2
