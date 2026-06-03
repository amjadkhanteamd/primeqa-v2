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

import json
from uuid import uuid4

import pytest
from sqlalchemy import text

from primeqa.generation.enums import AdmissibilityLayer, CaveatKind, OutcomeKind, RefusalKind
from primeqa.generation.governance_core import GovernanceCore
from primeqa.generation.persistence import LedgerPersister
from primeqa.generation.semantic_completeness import caveat_kind, requires_caveat

from .conftest import (
    FakeTurn, FakeToolTurn, TEST_TENANT_ID, intent, make_request, propose_turn, rel_intent,
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
            "recipes_written, caveat_required, caveat_kind FROM generation_outcomes"
        )).mappings().all()
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

    # config is Layer-1-complete -> no caveat (in-memory + persisted, D-101.3 regression)
    assert o.caveat_required is False and o.caveat_kind is None

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
    # the outcome row points at the emitted claim + recipe, no caveat columns set
    out = rows["outcomes"][0]
    assert out["claims_written"] is not None and out["recipes_written"] is not None
    assert out["caveat_required"] is False and out["caveat_kind"] is None


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


# ---------------------------------------------------------------------------
# Caveated negative (D-101) — the third outcome type; first caveat firing
# ---------------------------------------------------------------------------

# A grounded prohibition negative: "Case" HAS a ValidationRule (APPLIES_TO),
# so the rejection is admissible at Layer-1-plausible.
def _grounded_negative():
    return intent(claim_kind="prohibition-claim", polarity="negative",
                  sf_api_name="Case")


def test_caveated_negative_emitted_end_to_end(seeded):
    _, res = _emit_run(seeded, [_grounded_negative()], persister=LedgerPersister(TEST_TENANT_ID))
    o = res.results[0].outcome
    assert o.outcome_kind == OutcomeKind.DRAFT
    # marker LAYER_1 (constraint exists/active — formula NOT parsed)
    assert o.admissibility_layer == AdmissibilityLayer.LAYER_1
    # the caveat fires for the first time: Layer-1-plausible -> required + typed
    assert requires_caveat("prohibition-claim") is True
    assert o.caveat_required is True
    assert o.caveat_kind == CaveatKind.DEEPER_VERIFICATION_LAYER_UNPARSED
    assert o.claims_written and o.recipes_written

    rows = _query()
    assert len(rows["claims"]) == 1 and len(rows["recipes"]) == 1
    # the claim is the data_behavior prohibition negative
    claim = rows["claims"][0]
    assert claim["archetype"] == "data_behavior"
    assert claim["claim_kind"] == "prohibition-claim"
    # recipe is the reused inspection shape (parser-gated behavioral test deferred)
    recipe = rows["recipes"][0]
    assert recipe["trigger_kind"] == "inspection-trigger"
    assert recipe["recipe_kind"] == "metadata-recipe"
    # the ledger row is SELF-DESCRIBING (D-101.3): caveat posture stored, not derived
    out = rows["outcomes"][0]
    assert out["caveat_required"] is True
    assert out["caveat_kind"] == "deeper_verification_layer_unparsed"


def test_negative_no_constraint_refuses(seeded):
    # "Account" is a bare Object (no ValidationRule) -> the rejection cannot be
    # grounded -> no_constraint_supports_negative refusal (no caveated emit).
    _, res = _emit_run(seeded, [intent(claim_kind="prohibition-claim",
                                       polarity="negative", sf_api_name="Account")],
                       persister=LedgerPersister(TEST_TENANT_ID))
    o = res.results[0].outcome
    assert o.outcome_kind == OutcomeKind.REFUSAL
    assert o.refusal_kind == RefusalKind.NO_ADMISSIBLE_NEGATIVE_SCENARIO_FOUND
    assert o.refusals[0].payload["cause"] == "no_org_constraint"
    rows = _query()
    # nothing emitted; the refusal row carries no caveat (self-describing)
    assert len(rows["claims"]) == 0 and len(rows["recipes"]) == 0
    assert len(rows["outcomes"]) == 1
    assert rows["outcomes"][0]["caveat_required"] is False
    assert rows["outcomes"][0]["caveat_kind"] is None


def test_caveated_negative_identity_dedup(seeded):
    persister = LedgerPersister(TEST_TENANT_ID)
    _, r1 = _emit_run(seeded, [_grounded_negative()], persister=persister)
    _, r2 = _emit_run(seeded, [_grounded_negative()], persister=persister)
    o1, o2 = r1.results[0].outcome, r2.results[0].outcome
    assert o1.equivalent_existing is None
    assert o2.equivalent_existing == [o1.claims_written[0].test_id]
    # both caveated, even the dedup re-emit (posture is per-emission)
    assert o1.caveat_required is True and o2.caveat_required is True
    assert len(_query()["claims"]) == 1   # one test, deduped


# ---------------------------------------------------------------------------
# Verified negative (D-107) — the fourth outcome posture; first LAYER_2 firing
# ---------------------------------------------------------------------------

# "Lead" HAS a VR whose formula (ISBLANK(Reason__c)) parses AND yields a certain
# violating value -> the negative is Layer-2-VERIFIED, no caveat. Same grounding
# shape as the caveated "Case" negative; the ONLY difference is a derivable
# formula on the VR (Option C: the derived payload is the gate, not persisted).
def _verified_negative():
    return intent(claim_kind="prohibition-claim", polarity="negative",
                  sf_api_name="Lead")


def test_verified_negative_emitted_end_to_end(seeded):
    _, res = _emit_run(seeded, [_verified_negative()], persister=LedgerPersister(TEST_TENANT_ID))
    o = res.results[0].outcome
    assert o.outcome_kind == OutcomeKind.DRAFT
    # marker LAYER_2 (formula parsed + violating value derived with certainty)
    assert o.admissibility_layer == AdmissibilityLayer.LAYER_2
    # verified -> the caveat is DROPPED (the D-107 verified-vs-caveated line)
    assert requires_caveat("prohibition-claim", verified=True) is False
    assert o.caveat_required is False
    assert o.caveat_kind is None
    assert o.claims_written and o.recipes_written

    rows = _query()
    assert len(rows["claims"]) == 1 and len(rows["recipes"]) == 1
    # still the data_behavior prohibition negative — the claim body is identical
    # to a caveated one (Option C: only the marker + caveat posture differ; the
    # violating payload lives in the recipe, never the claim).
    claim = rows["claims"][0]
    assert claim["archetype"] == "data_behavior"
    assert claim["claim_kind"] == "prohibition-claim"
    # D-110.3: a VERIFIED negative now emits the BEHAVIORAL recipe (the violating
    # create + expect_rejection), replacing the inspection re-verify — the parser
    # derived the violation, so we construct + observe it.
    recipe = rows["recipes"][0]
    assert recipe["trigger_kind"] == "data-mutation-trigger"
    assert recipe["recipe_kind"] == "data-recipe"
    # the ledger row is SELF-DESCRIBING (D-101.3): verified posture is stored
    out = rows["outcomes"][0]
    assert out["admissibility_layer"] == "layer_2"
    assert out["caveat_required"] is False
    assert out["caveat_kind"] is None


# ---------------------------------------------------------------------------
# Positive value-claim (D-115) — the fifth outcome shape; first positive emit
# ---------------------------------------------------------------------------

# "Invoice.Amount" BELONGS_TO "Invoice" -> a value-claim naming it + carrying a
# value GROUNDS (verify-at-grounding, D-115.3) and emits the positive recipe.
def _grounded_positive():
    return intent(claim_kind="value-claim", polarity="positive",
                  sf_api_name="Invoice", field_name="Invoice.Amount",
                  expected_value="100")


def test_positive_value_claim_emitted_end_to_end(seeded):
    _, res = _emit_run(seeded, [_grounded_positive()], persister=LedgerPersister(TEST_TENANT_ID))
    o = res.results[0].outcome
    assert o.outcome_kind == OutcomeKind.DRAFT
    # value-claim is a Layer-1 positive (directly-set state; no Layer-2 marker)
    assert o.admissibility_layer == AdmissibilityLayer.LAYER_1
    assert requires_caveat("value-claim") is False
    assert o.caveat_required is False and o.caveat_kind is None
    assert o.claims_written and o.recipes_written

    rows = _query()
    assert len(rows["claims"]) == 1 and len(rows["recipes"]) == 1
    claim = rows["claims"][0]
    assert claim["archetype"] == "data_behavior"
    assert claim["claim_kind"] == "value-claim"
    # the positive create-and-verify recipe side A authors: a data-mutation trigger
    # + a data-recipe (create -> read -> assert), end to end from a real intent.
    recipe = rows["recipes"][0]
    assert recipe["trigger_kind"] == "data-mutation-trigger"
    assert recipe["recipe_kind"] == "data-recipe"


# ---------------------------------------------------------------------------
# Configuration breadth (D-122): existence-claim grounds + emits end to end
# ---------------------------------------------------------------------------

def _existence_intent(entity_type: str, sf_api_name: str):
    """A config existence-claim intent (D-122) — the flat target_subject_hint
    convention {entity_type, sf_api_name}."""
    return {"requirement_excerpt": f"{sf_api_name} exists in the org",
            "intent_descriptor": {
                "archetype_hint": "configuration", "polarity_hint": "positive",
                "claim_kind_hint": "existence-claim",
                "target_subject_hint": {"entity_type": entity_type, "sf_api_name": sf_api_name}}}


def test_existence_claim_grounds_and_emits(seeded):
    # "Account" exists in the seeded S1 -> existence-claim grounds (get_entities),
    # emits the inspection recipe, persists whole (Layer-1-complete, no caveat).
    _, res = _emit_run(seeded, [_existence_intent("Object", "Account")],
                       persister=LedgerPersister(TEST_TENANT_ID))
    o = res.results[0].outcome
    assert o.outcome_kind == OutcomeKind.DRAFT
    assert o.admissibility_layer == AdmissibilityLayer.LAYER_1
    assert o.caveat_required is False and o.caveat_kind is None
    assert o.claims_written and o.recipes_written

    rows = _query()
    assert len(rows["claims"]) == 1 and len(rows["recipes"]) == 1
    claim = rows["claims"][0]
    assert claim["archetype"] == "configuration"
    assert claim["claim_kind"] == "existence-claim"
    assert rows["recipes"][0]["recipe_kind"] == "metadata-recipe"
    assert rows["recipes"][0]["trigger_kind"] == "inspection-trigger"


# ---------------------------------------------------------------------------
# Permission breadth (D-123): capability-claim grounds on the grant edge's BIT
# ---------------------------------------------------------------------------

def _seed_grant(v1: int, profile_api: str, target_api: str, target_type: str,
                edge_type: str, props: dict) -> None:
    """Idempotently seed a Profile + a GRANTS_*_ACCESS edge (carrying capability
    properties) to an already-seeded target. The conftest ``_edge`` helper forces
    empty properties, but capability grounding reads ``can_read``/``can_edit`` off
    the edge, so we insert directly. Guarded SELECT-then-INSERT keeps it safe under
    the session-scoped (committed) S1 seed."""
    from primeqa.semantic.connection import get_tenant_connection
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        prof = conn.execute(text(
            "SELECT id FROM entities WHERE entity_type='Profile' AND sf_api_name=:api "
            "AND valid_from_seq=:vf"), {"api": profile_api, "vf": v1}).scalar()
        if prof is None:
            prof = conn.execute(text(
                "INSERT INTO entities (entity_type, sf_api_name, display_name, attributes, "
                "valid_from_seq, valid_to_seq, last_synced_at) "
                "VALUES ('Profile',:api,:api,'{}'::jsonb,:vf,NULL,NOW()) RETURNING id"),
                {"api": profile_api, "vf": v1}).scalar()
        tgt = conn.execute(text(
            "SELECT id FROM entities WHERE entity_type=:et AND sf_api_name=:api "
            "AND valid_from_seq=:vf"), {"et": target_type, "api": target_api, "vf": v1}).scalar()
        present = conn.execute(text(
            "SELECT 1 FROM edges WHERE source_entity_id=:s AND target_entity_id=:t "
            "AND edge_type=:et AND valid_from_seq=:vf"),
            {"s": str(prof), "t": str(tgt), "et": edge_type, "vf": v1}).scalar()
        if not present:
            conn.execute(text(
                "INSERT INTO edges (source_entity_id, target_entity_id, edge_type, "
                "edge_category, properties, valid_from_seq, valid_to_seq) "
                "VALUES (CAST(:s AS uuid),CAST(:t AS uuid),:et,'PERMISSION',"
                "CAST(:p AS jsonb),:vf,NULL)"),
                {"s": str(prof), "t": str(tgt), "et": edge_type,
                 "p": json.dumps(props), "vf": v1})


def _capability_intent(profile_api, target_api, target_type, grant_type, capability):
    """A permission capability-claim intent (D-123) — two endpoints (grantee +
    target) under target_subject_hint, plus the capability + grant_type."""
    return {"requirement_excerpt": f"The {profile_api} profile can {capability} {target_api}",
            "intent_descriptor": {
                "archetype_hint": "permission", "polarity_hint": "positive",
                "claim_kind_hint": "capability-claim",
                "target_subject_hint": {
                    "grantee": {"entity_type": "Profile", "sf_api_name": profile_api},
                    "target": {"entity_type": target_type, "sf_api_name": target_api},
                    "granted_capability": capability, "grant_type": grant_type}}}


def test_capability_claim_grounds_and_emits(seeded):
    # Profile "PQA_Admin" --GRANTS_FIELD_ACCESS{can_edit}--> Invoice.Amount, so a
    # capability-claim "Admin can edit Invoice.Amount" grounds on the grant edge's
    # SET bit, emits the inspection recipe, persists whole (Layer-1, no caveat).
    _seed_grant(seeded["v1"], "PQA_Admin", "Invoice.Amount", "Field",
                "GRANTS_FIELD_ACCESS", {"can_read": True, "can_edit": True})
    _, res = _emit_run(seeded,
                       [_capability_intent("PQA_Admin", "Invoice.Amount", "Field", "field", "edit")],
                       persister=LedgerPersister(TEST_TENANT_ID))
    o = res.results[0].outcome
    assert o.outcome_kind == OutcomeKind.DRAFT
    assert o.admissibility_layer == AdmissibilityLayer.LAYER_1
    assert o.caveat_required is False and o.caveat_kind is None
    assert o.claims_written and o.recipes_written

    rows = _query()
    assert len(rows["claims"]) == 1 and len(rows["recipes"]) == 1
    claim = rows["claims"][0]
    assert claim["archetype"] == "permission"
    assert claim["claim_kind"] == "capability-claim"
    assert rows["recipes"][0]["recipe_kind"] == "metadata-recipe"
    assert rows["recipes"][0]["trigger_kind"] == "inspection-trigger"


def test_capability_claim_refuses_when_bit_unset(seeded):
    # Profile "PQA_ReadOnly" --GRANTS_FIELD_ACCESS{can_read, NOT can_edit}-->
    # Invoice.Amount. A claim that ReadOnly can *edit* must REFUSE: the grant edge
    # exists but the asserted capability BIT is unset. This is the property that
    # makes capability-claim more than a renamed relationship-claim — grounding
    # checks the bit, not mere edge existence. Nothing emits.
    _seed_grant(seeded["v1"], "PQA_ReadOnly", "Invoice.Amount", "Field",
                "GRANTS_FIELD_ACCESS", {"can_read": True, "can_edit": False})
    _, res = _emit_run(seeded,
                       [_capability_intent("PQA_ReadOnly", "Invoice.Amount", "Field", "field", "edit")],
                       persister=LedgerPersister(TEST_TENANT_ID))
    o = res.results[0].outcome
    assert o.outcome_kind == OutcomeKind.REFUSAL
    assert o.refusal_kind == RefusalKind.UNGROUNDED_CLAIM
    rows = _query()
    assert len(rows["claims"]) == 0 and len(rows["recipes"]) == 0
