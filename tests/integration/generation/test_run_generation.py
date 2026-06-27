"""run_generation orchestration + routing->binding wiring tests (D-106.1).

Real S1 grounding + real persistence, scripted LLM (no live gateway).

The **orchestration** tests assert what the runner observably does with an
injected ``tool_turn_fn`` seam — ``BatchResult`` shape, outcome kind, ledger
persistence, that the seam is driven. Routing has no observable effect through
an injected seam, so the **wiring** test instead lets ``run_generation`` build
its own closure (no seam) and patches ``run.build_tool_turn_fn`` with a spy that
captures the ``model`` it is handed — proving the routed model
(``route_model``) actually reaches the gateway as ``model_override`` in CI, the
thing the seam tests structurally can't see. No API key, no gateway call in any
of these."""
from __future__ import annotations

from uuid import uuid4

import pytest

from primeqa.generation.enums import OutcomeKind
from primeqa.generation.routing import route_model
from primeqa.generation.run import run_generation
from primeqa.intelligence.llm.router import OPUS, SONNET, TenantPolicy

from .conftest import (
    FakeTurn, FakeToolTurn, TEST_ENV_ID, TEST_TENANT_ID, intent, make_request,
    propose_turn, query_outcome_rows, rel_intent,
)


def _emit_draft_turn() -> FakeTurn:
    return FakeTurn([{"type": "tool_use", "id": f"tu_{uuid4().hex[:6]}",
                      "name": "emit_outcome",
                      "input": {"outcome_kind": "draft",
                                "payload": {"admissibility_layer": "layer_1"}}}])


def _grounded_rel():
    # config metadata-relationship: VR "Case.RequireReason" APPLIES_TO "Case".
    return rel_intent(
        edge_type="APPLIES_TO",
        source={"entity_type": "ValidationRule", "sf_api_name": "Case.RequireReason"},
        target={"entity_type": "Object", "sf_api_name": "Case"})


# ---------------------------------------------------------------------------
# Orchestration (injected seam): BatchResult / outcome / persistence / seam
# ---------------------------------------------------------------------------

def test_run_generation_config_batch_persists_draft(seeded):
    req = make_request(s1_version_seq=seeded["v1"])
    req.semantic_context.archetype_hint = "configuration"
    seam = FakeToolTurn([propose_turn(_grounded_rel()), _emit_draft_turn()])

    result = run_generation(req, tenant_id=TEST_TENANT_ID, api_key="unused", environment_id=TEST_ENV_ID,
                            tool_turn_fn=seam)

    # BatchResult: exactly one outcome for the one requirement (no-silent-drops).
    assert len(result.results) == 1
    assert result.results[0].outcome.outcome_kind == OutcomeKind.DRAFT
    # the injected seam was actually driven (propose + emit) — no live gateway.
    assert seam.calls == 2
    # persister wiring (D-106.1): the draft is durable in the ledger.
    rows = query_outcome_rows()
    assert len(rows["outcomes"]) == 1
    assert rows["outcomes"][0]["outcome_kind"] == "draft"


def test_run_generation_data_behavior_batch_persists_refusal(seeded):
    # a data_behavior batch: the prohibition negative on bare Account refuses; the
    # runner completes the batch end to end and persists the refusal.
    req = make_request(s1_version_seq=seeded["v1"])
    req.semantic_context.archetype_hint = "data_behavior"
    seam = FakeToolTurn([propose_turn(intent(claim_kind="prohibition-claim",
                                             polarity="negative", sf_api_name="Account"))])

    result = run_generation(req, tenant_id=TEST_TENANT_ID, api_key="unused", environment_id=TEST_ENV_ID,
                            tool_turn_fn=seam)

    assert result.results[0].outcome.outcome_kind == OutcomeKind.REFUSAL
    assert len(query_outcome_rows()["outcomes"]) == 1


# ---------------------------------------------------------------------------
# Routing -> binding wiring (no seam): the routed model reaches the gateway
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("archetype, policy, expected_model", [
    ("configuration", None, SONNET),
    ("data_behavior", None, OPUS),
    ("configuration", TenantPolicy(always_use_opus=True), OPUS),
])
def test_run_generation_binds_routed_model_to_gateway(
    seeded, monkeypatch, archetype, policy, expected_model,
):
    # No injected seam -> run_generation builds its own closure via
    # run.build_tool_turn_fn. Patch that with a spy that captures the `model` it
    # is handed (what becomes the gateway's model_override) and returns a scripted
    # turn fn so the batch still completes offline.
    req = make_request(s1_version_seq=seeded["v1"])
    req.semantic_context.archetype_hint = archetype
    captured = {}

    def _spy(*, tenant_id, api_key, model, task, max_tokens=2048):
        captured["model"] = model
        return FakeToolTurn([propose_turn(_grounded_rel()), _emit_draft_turn()])

    monkeypatch.setattr("primeqa.generation.run.build_tool_turn_fn", _spy)

    run_generation(req, tenant_id=TEST_TENANT_ID, api_key="unused", environment_id=TEST_ENV_ID, tenant_policy=policy)

    # the model handed to the gateway binding IS route_model's output ...
    assert captured["model"] == route_model(req, policy)
    # ... and matches the D-106.2 expectation for this (archetype, policy).
    assert captured["model"] == expected_model
