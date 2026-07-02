"""Pure unit tests for model routing (route_model): the 2026-07-02 Sonnet-5
default flip (ALL archetypes → Sonnet 5), explicit-pin precedence, tenant
``always_use_opus`` (still Opus) — plus purity (no mutation of the request)."""
from __future__ import annotations

from uuid import uuid4

import pytest

from primeqa.generation.protocol import (
    GenerationRequest,
    GovernanceContext,
    OperationalContext,
    SemanticContext,
)
from primeqa.generation.routing import route_model
from primeqa.intelligence.llm.router import OPUS, SONNET_5, TenantPolicy


def _req(*, archetype_hint=None, model=None) -> GenerationRequest:
    return GenerationRequest(
        request_id=uuid4(),
        semantic_context=SemanticContext(
            requirement_refs=[{"key": "R0", "text": "x"}],
            s1_version_seq=1, archetype_hint=archetype_hint),
        governance_context=GovernanceContext(),
        operational_context=OperationalContext(llm_model_identifier=model),
    )


# ---------------------------------------------------------------------------
# Sonnet-5 default flip (2026-07-02): EVERY archetype routes to Sonnet 5.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("archetype", [
    "configuration", "ui", "data_behavior", "permission", "integration",
    "mixed", None, "totally_unknown",
])
def test_all_archetypes_route_to_sonnet_5(archetype):
    # The archetype table was collapsed: generation defaults to Sonnet 5 for all
    # archetypes (and for a missing / unknown hint) ahead of the A/B.
    assert route_model(_req(archetype_hint=archetype)) == SONNET_5


# ---------------------------------------------------------------------------
# Explicit pin wins (D-106.2 precedence #1)
# ---------------------------------------------------------------------------

def test_explicit_model_wins_over_archetype():
    # config would route Sonnet, but an explicit pin is the most deliberate signal.
    assert route_model(_req(archetype_hint="configuration", model="claude-pinned-x")) \
        == "claude-pinned-x"


def test_explicit_model_wins_over_always_use_opus():
    assert route_model(_req(archetype_hint="data_behavior", model="claude-pinned-x"),
                       TenantPolicy(always_use_opus=True)) == "claude-pinned-x"


# ---------------------------------------------------------------------------
# Tenant always_use_opus (D-106.2 precedence #2)
# ---------------------------------------------------------------------------

def test_always_use_opus_overrides_sonnet_default():
    # The premium tier keeps Opus everywhere, despite the Sonnet-5 default.
    assert route_model(_req(archetype_hint="data_behavior"),
                       TenantPolicy(always_use_opus=True)) == OPUS


def test_default_policy_routes_sonnet_5():
    # The default policy leaves the Sonnet-5 default in charge.
    assert route_model(_req(archetype_hint="ui"), TenantPolicy()) == SONNET_5


# ---------------------------------------------------------------------------
# Purity — route_model never mutates the request (the writeback is run.py's job)
# ---------------------------------------------------------------------------

def test_route_model_does_not_mutate_request():
    req = _req(archetype_hint="configuration")
    assert req.operational_context.llm_model_identifier is None
    route_model(req)
    assert req.operational_context.llm_model_identifier is None
