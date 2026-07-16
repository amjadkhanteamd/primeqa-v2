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


# ---------------------------------------------------------------------------
# Tenant model override (migration 060 — precedence 3, between the explicit
# pin and always_use_opus). Deterministic: pin the selectable set to the code
# frozenset so a catalog row in the test DB can't shift these outcomes.
# ---------------------------------------------------------------------------

@pytest.fixture()
def _code_selectable(monkeypatch):
    from primeqa.intelligence.llm import router as R
    monkeypatch.setattr(R, "selectable_model_ids",
                        lambda *a, **k: R.SELECTABLE_MODELS)


def test_model_override_wins_over_default(_code_selectable):
    assert route_model(_req(), TenantPolicy(model_override=OPUS)) == OPUS


def test_model_override_wins_over_always_use_opus(_code_selectable):
    from primeqa.intelligence.llm.router import HAIKU
    # An exact model id beats the Opus boolean (more specific wins).
    assert route_model(
        _req(), TenantPolicy(model_override=HAIKU, always_use_opus=True),
    ) == HAIKU


def test_explicit_pin_still_beats_model_override(_code_selectable):
    # A caller that pinned a model meant it (D-106.5) — even over the tenant.
    assert route_model(
        _req(model="claude-pinned-x"), TenantPolicy(model_override=OPUS),
    ) == "claude-pinned-x"


def test_unknown_override_fails_loud(_code_selectable):
    from primeqa.intelligence.llm.router import ModelConfigError
    with pytest.raises(ModelConfigError) as ei:
        route_model(_req(), TenantPolicy(model_override="claude-nope-x",
                                         tenant_id=7))
    assert "tenant 7" in str(ei.value)
    assert "claude-nope-x" in str(ei.value)


def test_null_override_falls_through_to_sonnet_5(_code_selectable):
    assert route_model(_req(), TenantPolicy(model_override=None)) == SONNET_5
