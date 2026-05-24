"""Substrate-3 model routing (D-091 / D-106.2).

The production runner picks ONE model per batch and binds it as the gateway's
``model_override`` (D-106.2 fork: the ``model_override`` mechanism, not the
gateway's ``_CHAINS`` escalation table — substrate-3 owns its own orchestration,
D-095.2). A pure function so it is trivially unit-testable and carries no I/O.

Precedence (D-106.2):
  1. an explicit ``operational_context.llm_model_identifier`` wins — a caller
     that pinned a model meant it (request-level provenance, D-106.5);
  2. else tenant ``always_use_opus`` — the premium tier gets capability
     everywhere (honored exactly as ``router.select_chain`` honors it);
  3. else the D-091 archetype table on ``semantic_context.archetype_hint`` —
     ``configuration`` / ``ui`` route to Sonnet (the cost win), everything that
     genuinely needs reasoning depth routes to Opus;
  4. else Opus — default-to-capability (D-106.2). Opus-default is the safe
     posture: the Sonnet cost win is contingent on callers setting the hint
     (a pilot-integration note), so a missing / unknown hint never silently
     downgrades quality.

Reuses ``router.SONNET`` / ``router.OPUS`` / ``router.TenantPolicy`` so the
canonical model ids live in exactly one place.
"""
from __future__ import annotations

from typing import Optional

from primeqa.generation.protocol import GenerationRequest
from primeqa.intelligence.llm.router import OPUS, SONNET, TenantPolicy

# D-091 archetype routing table (D-106.2). Only these route to Sonnet; every
# other archetype — ``data_behavior`` / ``permission`` / ``integration`` /
# ``mixed`` — plus a missing / unknown hint fall through to Opus
# (default-to-capability). Encoding the Sonnet set (not the Opus set) makes the
# default-to-capability posture structural: a new archetype is Opus until
# someone deliberately adds it here.
_SONNET_ARCHETYPES = frozenset({"configuration", "ui"})


def route_model(
    request: GenerationRequest,
    tenant_policy: Optional[TenantPolicy] = None,
) -> str:
    """Resolve the single model id for this batch (D-091 / D-106.2). Pure: no
    I/O, no mutation of ``request``. See the module docstring for the precedence
    rationale."""
    explicit = request.operational_context.llm_model_identifier
    if explicit:
        return explicit

    policy = tenant_policy or TenantPolicy()
    if policy.always_use_opus:
        return OPUS

    archetype = request.semantic_context.archetype_hint
    if archetype in _SONNET_ARCHETYPES:
        return SONNET
    return OPUS
