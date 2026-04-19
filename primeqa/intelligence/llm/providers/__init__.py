"""Provider abstraction \u2014 one class per LLM vendor.

Today only Anthropic is wired (primeqa.intelligence.llm.provider.invoke()).
This package exists so future phases can plug in OpenAI (outage
fallback), local (EU / on-prem), or a new Anthropic-compatible endpoint
without touching the Gateway.

Minimum viable interface:

    class Provider:
        def invoke(
            self, *, model, messages, max_tokens, system=None,
            tools=None, tool_choice=None, timeout=90, metadata=None,
        ) -> ProviderResponse: ...

        def supports_model(self, model_id: str) -> bool: ...

ProviderRegistry.get(model_id) returns the first provider whose
supports_model() is true. The Gateway's model_chain can safely mix
models from different vendors once the Registry has entries for them.
"""

from primeqa.intelligence.llm.providers.anthropic_provider import AnthropicProvider
from primeqa.intelligence.llm.providers.openai_provider import OpenAIProvider
from primeqa.intelligence.llm.providers.registry import ProviderRegistry, get_provider_for_model

__all__ = [
    "AnthropicProvider",
    "OpenAIProvider",
    "ProviderRegistry",
    "get_provider_for_model",
]
