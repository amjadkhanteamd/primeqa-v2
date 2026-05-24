"""Pure unit tests for D-091 model routing (route_model, D-106.2): the archetype
table, explicit-pin precedence, default-to-capability, and tenant
``always_use_opus`` — plus purity (no mutation of the request)."""
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
from primeqa.intelligence.llm.router import OPUS, SONNET, TenantPolicy


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
# Archetype table (D-091): config/ui -> Sonnet; everything else -> Opus
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("archetype", ["configuration", "ui"])
def test_config_and_ui_route_to_sonnet(archetype):
    assert route_model(_req(archetype_hint=archetype)) == SONNET


@pytest.mark.parametrize("archetype", ["data_behavior", "permission", "integration"])
def test_reasoning_archetypes_route_to_opus(archetype):
    assert route_model(_req(archetype_hint=archetype)) == OPUS


@pytest.mark.parametrize("archetype", [None, "mixed", "totally_unknown"])
def test_missing_or_unknown_archetype_defaults_to_opus(archetype):
    # Default-to-capability (D-106.2): a missing / unknown hint never silently
    # downgrades to Sonnet.
    assert route_model(_req(archetype_hint=archetype)) == OPUS


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

def test_always_use_opus_overrides_sonnet_archetype():
    # config would route Sonnet; the premium tier gets Opus everywhere.
    assert route_model(_req(archetype_hint="configuration"),
                       TenantPolicy(always_use_opus=True)) == OPUS


def test_default_policy_does_not_force_opus():
    # The default policy leaves the archetype table in charge.
    assert route_model(_req(archetype_hint="ui"), TenantPolicy()) == SONNET


# ---------------------------------------------------------------------------
# Purity — route_model never mutates the request (the writeback is run.py's job)
# ---------------------------------------------------------------------------

def test_route_model_does_not_mutate_request():
    req = _req(archetype_hint="configuration")
    assert req.operational_context.llm_model_identifier is None
    route_model(req)
    assert req.operational_context.llm_model_identifier is None
