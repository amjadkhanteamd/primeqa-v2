"""Substrate-3 model routing (D-091 / D-106.2; Sonnet-5 flip 2026-07-02).

The production runner picks ONE model per batch and binds it as the gateway's
``model_override`` (D-106.2 fork: the ``model_override`` mechanism, not the
gateway's ``_CHAINS`` escalation table — substrate-3 owns its own orchestration,
D-095.2). A pure function so it is trivially unit-testable and carries no I/O.

Precedence:
  1. an explicit ``operational_context.llm_model_identifier`` wins — a caller
     that pinned a model meant it (request-level provenance, D-106.5);
  2. else tenant ``always_use_opus`` — the premium tier keeps Opus everywhere
     (honored exactly as ``router.select_chain`` honors it);
  3. else **Sonnet 5** — the 2026-07-02 default flip (AK). Generation moved off
     the old D-091 archetype table (``configuration`` / ``ui`` → Sonnet, every
     reasoning archetype → Opus / "default-to-capability") to Sonnet-5 for ALL
     archetypes, ahead of the formal Sonnet-vs-Opus A/B. This is safe to flip
     first and measure after: the LLM only PROPOSES intents; the substrate
     verifies each against S1 and refuses/drops unverifiable ones, so a weaker
     model can only MISS tests (visible as refusals + coverage_map holes), never
     author wrong ones. To restore per-archetype Opus after the A/B, reinstate
     the ``_SONNET_ARCHETYPES = {"configuration", "ui"}`` table (git history) and
     branch on ``semantic_context.archetype_hint`` here again.

Reuses ``router.SONNET_5`` / ``router.OPUS`` / ``router.TenantPolicy`` so the
canonical model ids live in exactly one place.
"""
from __future__ import annotations

from typing import Optional

from primeqa.generation.protocol import GenerationRequest
from primeqa.intelligence.llm.router import OPUS, SONNET_5, TenantPolicy


def route_model(
    request: GenerationRequest,
    tenant_policy: Optional[TenantPolicy] = None,
) -> str:
    """Resolve the single model id for this batch. Pure: no I/O, no mutation of
    ``request``. See the module docstring for the precedence rationale (explicit
    pin > tenant always_use_opus > Sonnet 5 default)."""
    explicit = request.operational_context.llm_model_identifier
    if explicit:
        return explicit

    policy = tenant_policy or TenantPolicy()
    if policy.always_use_opus:
        return OPUS

    return SONNET_5
