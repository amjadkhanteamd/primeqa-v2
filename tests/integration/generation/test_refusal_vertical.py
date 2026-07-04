"""Refusal-vertical integration tests (D-096) — real S1 grounding, local PG.

Scripted LLM (fake tool_turn) + real ``SemanticOrgModel`` over seeded S1 data.
Drives each refusal kind end to end and asserts refusal_kind/cause, phase-
tagged dismissals, deterministic prose-free explanation_hash, FK-ordered
per-requirement persistence with partial-failure isolation, and the
operational/semantic (llm_calls vs attempted_interpretation) separation.
"""
from __future__ import annotations

import pytest

from primeqa.generation.enums import AdmissibilityLayer, OutcomeKind, RefusalKind
from primeqa.generation.governance_core import GovernanceCore, phase_for_reason

from .conftest import (
    FakeToolTurn, TEST_TENANT_ID, intent, make_request, propose_turn,
    query_outcome_rows, rel_intent,
)


def _run(seeded, intents, *, persister=None):
    """Run the refusal vertical end to end. `intents` is one per requirement."""
    from primeqa.semantic.connection import get_tenant_connection
    from primeqa.semantic.query import SemanticOrgModel
    from primeqa.generation.runtime import GenerationRuntime

    req = make_request(s1_version_seq=seeded["v1"])
    req.semantic_context.requirement_refs = [
        {"key": f"R{i}", "text": "the requirement"} for i in range(len(intents))
    ]
    turns = [propose_turn(it) for it in intents]
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        gov = GovernanceCore(SemanticOrgModel(conn))
        result = GenerationRuntime().run(
            request=req, seam=gov, tool_turn_fn=FakeToolTurn(turns), persister=persister,
        )
    return req, result


# ---------------------------------------------------------------------------
# The four refusal kinds (+ cause distinction)
# ---------------------------------------------------------------------------

def test_no_admissible_negative_no_org_constraint(seeded):
    _, res = _run(seeded, [intent(claim_kind="prohibition-claim", polarity="negative",
                                  sf_api_name="Account")])  # bare Object, no VR
    o = res.results[0].outcome
    assert o.outcome_kind == OutcomeKind.REFUSAL
    assert o.refusal_kind == RefusalKind.NO_ADMISSIBLE_NEGATIVE_SCENARIO_FOUND
    assert o.refusals[0].payload["cause"] == "no_org_constraint"
    ai = o.attempted_interpretation
    assert "no_constraint_supports_negative" in ai.dismissed_alternatives_by_reason
    assert phase_for_reason("no_constraint_supports_negative") == "grounding"
    assert len(o.explanation_hash) == 64


def test_no_admissible_negative_ontology_gap(seeded):
    _, res = _run(seeded, [intent(claim_kind="automation-effect-claim", polarity="negative",
                                  sf_api_name="Account")])  # no Flow -> Apex (Tier-2) gap
    o = res.results[0].outcome
    assert o.refusal_kind == RefusalKind.NO_ADMISSIBLE_NEGATIVE_SCENARIO_FOUND
    payload = o.refusals[0].payload
    assert payload["cause"] == "ontology_gap"
    assert payload["what_would_unblock"]   # non-empty (substrate capability)


def test_ungrounded_claim(seeded):
    _, res = _run(seeded, [intent(claim_kind="value-claim", polarity="positive",
                                  sf_api_name="Account")])  # no Field -> ungrounded
    o = res.results[0].outcome
    assert o.refusal_kind == RefusalKind.UNGROUNDED_CLAIM
    assert "insufficient_grounding" in o.attempted_interpretation.dismissed_alternatives_by_reason


def test_ambiguous_reference(seeded):
    _, res = _run(seeded, [intent(claim_kind="prohibition-claim", polarity="negative",
                                  sf_api_name="Contact")])  # two Objects named Contact
    o = res.results[0].outcome
    assert o.refusal_kind == RefusalKind.AMBIGUOUS_REFERENCE
    assert len(o.refusals[0].payload["matched"]) == 2


def test_underspecified_requirement(seeded):
    _, res = _run(seeded, [intent(claim_kind=None, polarity="negative", sf_api_name="Account")])
    o = res.results[0].outcome
    assert o.refusal_kind == RefusalKind.UNDERSPECIFIED_REQUIREMENT


# ---------------------------------------------------------------------------
# Positive control: real grounding -> PROCEED_TO_EMIT (emission stubbed)
# ---------------------------------------------------------------------------

def test_grounded_candidate_proceeds_to_emit(seeded):
    from uuid import uuid4
    from primeqa.semantic.connection import get_tenant_connection
    from primeqa.semantic.query import SemanticOrgModel
    from primeqa.generation.governance import ConversationContext, NextAction
    from primeqa.generation.protocol import (
        GovernanceContext, OperationalContext, SemanticContext,
    )
    from primeqa.generation.runtime import RequirementState

    with get_tenant_connection(TEST_TENANT_ID) as conn:
        gov = GovernanceCore(SemanticOrgModel(conn))
        ctx = ConversationContext(
            request_id=uuid4(), requirement_ref={}, requirement_text="x",
            semantic_context=SemanticContext(s1_version_seq=seeded["v1"]),
            governance_context=GovernanceContext(), operational_context=OperationalContext(),
            shared_context={},
        )
        state = RequirementState(request_id=ctx.request_id, requirement_ref={})
        res = gov.resolve_intent(
            # D-293: use "Lead" — its VR carries a derivable ISBLANK formula, so
            # the prohibition is a COMPLETE behaviour instance and PROCEEDS. (Case,
            # the old positive control, has a formula-less VR and now refuses
            # behaviour-incomplete — see test_refusal_vertical below.)
            intent_input=intent(claim_kind="prohibition-claim", polarity="negative",
                                sf_api_name="Lead"),
            ctx=ctx, state=state,
        )
    assert res.next_action == NextAction.PROCEED_TO_EMIT
    assert res.grounded_candidates[0].admissibility_layer == AdmissibilityLayer.LAYER_1


# ---------------------------------------------------------------------------
# Emission-deferred (D-105) — a grounded-but-unbuilt kind refuses, not crashes
# ---------------------------------------------------------------------------

def test_grounded_value_claim_refuses_emission_deferred(seeded):
    # "Invoice.Amount" exists (BELONGS_TO Invoice) -> a value-claim naming it GROUNDS
    # (verify-at-grounding, D-115.3). But the intent carries NO expected_value, so S3
    # won't fabricate one (D-115 §2): the value-claim defers EMISSION_DEFERRED at the
    # stash (grounded-then-deferred), NOT PROCEED_TO_EMIT. A value-claim naming a field
    # that doesn't exist instead dismisses insufficient_grounding *before* the gate
    # (test_value_claim_unknown_field) — distinct paths.
    from primeqa.generation.persistence import LedgerPersister
    _, res = _run(seeded, [intent(claim_kind="value-claim", polarity="positive",
                                  sf_api_name="Invoice", field_name="Invoice.Amount")],
                  persister=LedgerPersister(TEST_TENANT_ID))
    o = res.results[0].outcome
    assert o.outcome_kind == OutcomeKind.REFUSAL
    assert o.refusal_kind == RefusalKind.EMISSION_DEFERRED
    # the candidate GROUNDED (admissibly_grounded), then deferred — NOT dismissed.
    paths = o.attempted_interpretation.candidate_paths
    assert paths and paths[0]["admissibility_status"] == "admissibly_grounded"
    assert not o.attempted_interpretation.dismissed_alternatives_by_reason
    # the operational refusal names the unbuilt capability.
    payload = o.refusals[0].payload
    assert payload["archetype"] == "data_behavior" and payload["claim_kind"] == "value-claim"
    # persisted end to end -> the new PG enum value round-trips (proves the
    # 20260522_0040 ADD VALUE migration applied + models_db enum accepts it).
    rows = query_outcome_rows()
    assert len(rows["outcomes"]) == 1
    assert rows["outcomes"][0]["refusal_kind"] == "emission-deferred"


def test_behaviour_incomplete_prohibition_refuses(seeded):
    # D-293 decision-2: "Case" grounds a prohibition (VR Case.RequireReason
    # APPLIES_TO Case) but that VR has NO formula, so no behavioural reject recipe
    # derives -> the substrate REFUSES (behaviour-incomplete) rather than degrading
    # to a caveated metadata inspection. This exercises the PRIMARY refuse path
    # (the governance gate -> BEHAVIOUR_INCOMPLETE), which the unit suite covers
    # only in halves.
    from primeqa.generation.persistence import LedgerPersister
    _, res = _run(seeded, [intent(claim_kind="prohibition-claim", polarity="negative",
                                  sf_api_name="Case")],
                  persister=LedgerPersister(TEST_TENANT_ID))
    o = res.results[0].outcome
    assert o.outcome_kind == OutcomeKind.REFUSAL
    assert o.refusal_kind == RefusalKind.BEHAVIOUR_INCOMPLETE
    assert not o.claims_written and not o.recipes_written
    # persisted end to end -> the new PG enum value round-trips (proves the
    # 20260701_0010 ADD VALUE migration applied + models_db enum accepts it).
    rows = query_outcome_rows()
    assert len(rows["outcomes"]) == 1
    assert rows["outcomes"][0]["refusal_kind"] == "behaviour-incomplete"


def test_value_claim_unknown_field(seeded):
    # A value-claim naming a field that does NOT exist on Invoice -> grounding can't
    # verify it (verify-at-grounding, D-115.3) -> insufficient_grounding ->
    # UNGROUNDED_CLAIM, *before* the emittability gate. The named field gates grounding.
    _, res = _run(seeded, [intent(claim_kind="value-claim", polarity="positive",
                                  sf_api_name="Invoice", field_name="Invoice.Nope",
                                  expected_value="x")])
    o = res.results[0].outcome
    assert o.refusal_kind == RefusalKind.UNGROUNDED_CLAIM
    assert "insufficient_grounding" in o.attempted_interpretation.dismissed_alternatives_by_reason


def test_complete_value_claim_proceeds_and_stashes(seeded):
    # A value-claim naming an existing field + carrying a value -> grounds + stashes a
    # GroundedPositive (field/value/object) -> PROCEED_TO_EMIT (D-115.3). Direct
    # resolve_intent, mirroring test_grounded_candidate_proceeds_to_emit.
    from uuid import uuid4
    from primeqa.semantic.connection import get_tenant_connection
    from primeqa.semantic.query import SemanticOrgModel
    from primeqa.generation.governance import ConversationContext, NextAction
    from primeqa.generation.protocol import (
        GovernanceContext, OperationalContext, SemanticContext,
    )
    from primeqa.generation.runtime import RequirementState

    with get_tenant_connection(TEST_TENANT_ID) as conn:
        gov = GovernanceCore(SemanticOrgModel(conn))
        ctx = ConversationContext(
            request_id=uuid4(), requirement_ref={}, requirement_text="x",
            semantic_context=SemanticContext(s1_version_seq=seeded["v1"]),
            governance_context=GovernanceContext(), operational_context=OperationalContext(),
            shared_context={},
        )
        state = RequirementState(request_id=ctx.request_id, requirement_ref={})
        res = gov.resolve_intent(
            intent_input=intent(claim_kind="value-claim", polarity="positive",
                                sf_api_name="Invoice", field_name="Invoice.Amount",
                                expected_value="100"),
            ctx=ctx, state=state,
        )
    assert res.next_action == NextAction.PROCEED_TO_EMIT
    # D-207: groundings is the ordered stash list (one entry per grounded intent)
    assert len(state.groundings) == 1
    gp = state.groundings[0]
    assert gp.target_object.external_id == "Invoice"
    assert gp.field.external_id == "Invoice.Amount"
    assert gp.value == "100"


# ---------------------------------------------------------------------------
# Persistence (FK order, per-requirement transaction, partial-failure)
# ---------------------------------------------------------------------------

def test_persistence_fk_order_and_success_telemetry(seeded):
    from primeqa.generation.persistence import LedgerPersister
    req, res = _run(seeded, [intent(claim_kind="prohibition-claim", polarity="negative",
                                    sf_api_name="Account")],
                    persister=LedgerPersister(TEST_TENANT_ID))
    rows = query_outcome_rows()
    assert len(rows["requests"]) == 1
    assert len(rows["outcomes"]) == 1
    out = rows["outcomes"][0]
    assert str(out["request_id"]) == str(req.request_id)             # outcome FK -> request
    assert out["outcome_kind"] == "refusal"
    assert len(out["explanation_hash"]) == 64
    # llm_calls FK -> the outcome; the propose tool call recorded SUCCESS
    assert rows["llm_calls"]
    assert all(str(c["generation_outcome_id"]) == str(out["outcome_id"]) for c in rows["llm_calls"])
    assert any(c["operational_outcome"] == "success" for c in rows["llm_calls"])


def test_persistence_partial_failure_isolation(seeded):
    from primeqa.generation.persistence import LedgerPersister

    class FailingPersister(LedgerPersister):
        def __init__(self, tid):
            super().__init__(tid)
            self._n = 0

        def persist_requirement_result(self, request, result):
            self._n += 1
            if self._n == 2:
                raise RuntimeError("simulated persist failure on requirement 2")
            super().persist_requirement_result(request, result)

    two = [intent(claim_kind="prohibition-claim", polarity="negative", sf_api_name="Account")] * 2
    with pytest.raises(RuntimeError):
        _run(seeded, two, persister=FailingPersister(TEST_TENANT_ID))
    # Requirement 1 was committed in its own transaction before requirement 2
    # failed — it must NOT have rolled back.
    rows = query_outcome_rows()
    assert len(rows["outcomes"]) == 1


def test_explanation_hash_deterministic_across_runs(seeded):
    _, r1 = _run(seeded, [intent(claim_kind="prohibition-claim", polarity="negative",
                                 sf_api_name="Account")])
    _, r2 = _run(seeded, [intent(claim_kind="prohibition-claim", polarity="negative",
                                 sf_api_name="Account")])
    assert r1.results[0].outcome.explanation_hash == r2.results[0].outcome.explanation_hash


# ---------------------------------------------------------------------------
# Operational / semantic separation (D-087)
# ---------------------------------------------------------------------------

def test_operational_semantic_separation(seeded):
    _, res = _run(seeded, [intent(claim_kind="prohibition-claim", polarity="negative",
                                  sf_api_name="Account")])
    r = res.results[0]
    # The propose tool call passed Layer A + check_refs_exist -> operational SUCCESS.
    assert [c.operational_outcome.value for c in r.llm_calls] == ["success"]
    # The semantic dismissal lives ONLY in attempted_interpretation, never llm_calls.
    ai = r.outcome.attempted_interpretation
    assert "no_constraint_supports_negative" in ai.dismissed_alternatives_by_reason
    assert all(c.raw_response is None for c in r.llm_calls)   # no operational rejection feedback


# ---------------------------------------------------------------------------
# Configuration metadata-relationship admissibility (D-098.1 — the C debut)
# ---------------------------------------------------------------------------

def test_config_metadata_relationship_present_edge_admits(seeded):
    # Direct resolve_intent (grounded -> PROCEED_TO_EMIT; emission stubbed at ★).
    # Seeded: ValidationRule "Case.RequireReason" APPLIES_TO Object "Case".
    from uuid import uuid4
    from primeqa.semantic.connection import get_tenant_connection
    from primeqa.semantic.query import SemanticOrgModel
    from primeqa.generation.governance import ConversationContext, NextAction
    from primeqa.generation.protocol import (
        GovernanceContext, OperationalContext, SemanticContext,
    )
    from primeqa.generation.runtime import RequirementState

    ri = rel_intent(
        edge_type="APPLIES_TO",
        source={"entity_type": "ValidationRule", "sf_api_name": "Case.RequireReason"},
        target={"entity_type": "Object", "sf_api_name": "Case"})
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        gov = GovernanceCore(SemanticOrgModel(conn))
        ctx = ConversationContext(
            request_id=uuid4(), requirement_ref={}, requirement_text="x",
            semantic_context=SemanticContext(s1_version_seq=seeded["v1"]),
            governance_context=GovernanceContext(), operational_context=OperationalContext(),
            shared_context={})
        assert gov.check_refs_exist(intent_input=ri, ctx=ctx).ok   # both endpoints resolve
        res = gov.resolve_intent(intent_input=ri, ctx=ctx,
                                 state=RequirementState(request_id=ctx.request_id, requirement_ref={}))
    assert res.next_action == NextAction.PROCEED_TO_EMIT
    assert res.grounded_candidates[0].admissibility_layer == AdmissibilityLayer.LAYER_1


def test_config_metadata_relationship_absent_edge_refuses(seeded):
    # The VR does NOT apply to Account -> the org lacks the assumed relationship
    # -> ungrounded refusal (no silent emit). Both endpoints exist, so this is a
    # semantic (not operational) refusal.
    _, res = _run(seeded, [rel_intent(
        edge_type="APPLIES_TO",
        source={"entity_type": "ValidationRule", "sf_api_name": "Case.RequireReason"},
        target={"entity_type": "Object", "sf_api_name": "Account"})])
    o = res.results[0].outcome
    assert o.outcome_kind == OutcomeKind.REFUSAL
    assert o.refusal_kind == RefusalKind.UNGROUNDED_CLAIM
    assert "insufficient_grounding" in o.attempted_interpretation.dismissed_alternatives_by_reason


def test_config_metadata_relationship_bad_edge_type_refuses(seeded):
    # edge_type not in TIER_1_EDGES -> ungrounded (verbatim edge-type binding).
    _, res = _run(seeded, [rel_intent(
        edge_type="NOT_A_REAL_EDGE",
        source={"entity_type": "ValidationRule", "sf_api_name": "Case.RequireReason"},
        target={"entity_type": "Object", "sf_api_name": "Case"})])
    assert res.results[0].outcome.refusal_kind == RefusalKind.UNGROUNDED_CLAIM


# ---------------------------------------------------------------------------
# D-317 — the behavioral claim kinds name the subject object under `object`
# (matching the rest of target_subject_hint), not {entity_type, sf_api_name}.
# normalize_propose_input injects the entity ref so the subject resolves through
# the REAL check_refs_exist. These drive the object-shaped hint end-to-end (the
# adversarial-review durability gap: no other test exercises object->resolve —
# they all feed pre-resolved {entity_type, sf_api_name}). If the injection is
# removed or the resolver drifts, the first test FAILS LOUDLY instead of silently
# regressing behavioral subjects to "subject None:None did not resolve".
# ---------------------------------------------------------------------------

def _check_refs_object_hint(seeded, obj: str):
    """Drive an `object`-shaped prohibition hint (the shape the model emits) through
    the real check_refs_exist against the seeded S1. Returns the RefCheck."""
    from uuid import uuid4
    from primeqa.semantic.connection import get_tenant_connection
    from primeqa.semantic.query import SemanticOrgModel
    from primeqa.generation.governance import ConversationContext
    from primeqa.generation.protocol import (
        GovernanceContext, OperationalContext, SemanticContext)

    intent_input = {"intent_descriptors": [{
        "requirement_excerpt": "Users must not save it without a reason.",
        "archetype_hint": "data_behavior", "polarity_hint": "negative",
        "claim_kind_hint": "prohibition-claim",
        "target_subject_hint": {              # NO entity_type / sf_api_name — the model's shape
            "object": obj, "operation": "modify_record",
            "rejection_conditions": [{"field": f"{obj}.SomeField__c", "operator": "is_null"}],
        }}]}
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        gov = GovernanceCore(SemanticOrgModel(conn))
        ctx = ConversationContext(
            request_id=uuid4(), requirement_ref={}, requirement_text="x",
            semantic_context=SemanticContext(s1_version_seq=seeded["v1"]),
            governance_context=GovernanceContext(),
            operational_context=OperationalContext(), shared_context={})
        return gov.check_refs_exist(intent_input=intent_input, ctx=ctx)


def test_d317_object_shaped_subject_resolves_via_check_refs(seeded):
    # "Account" is a seeded Object; the object-shaped hint's subject must resolve
    # (the D-317 injection turns {object:"Account"} into {entity_type:"Object",
    # sf_api_name:"Account"} that check_refs_exist reads).
    rc = _check_refs_object_hint(seeded, "Account")
    assert rc.ok, rc.feedback


def test_d317_object_shaped_absent_subject_still_refuses(seeded):
    # The injection must NOT mask a genuinely-absent object: a bare object name
    # with no matching S1 entity still fails ref-existence (no fake-green).
    rc = _check_refs_object_hint(seeded, "NoSuchObject__x")
    assert not rc.ok
