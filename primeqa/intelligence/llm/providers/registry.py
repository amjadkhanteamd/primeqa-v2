"""Provider registry \u2014 get_provider_for_model(model_id) returns the
first provider that supports the model, or AnthropicProvider as default.

Today's registration list:
    AnthropicProvider   \u2014 "claude-*"
    OpenAIProvider      \u2014 "gpt-*" | "o1-*" (stub; raises NotImplementedError)

The Gateway model_chain is free to mix vendors as long as each model id
has a supporting provider in the registry.
"""

from __future__ import annotations

from typing import List, Optional

from primeqa.intelligence.llm.providers.anthropic_provider import AnthropicProvider
from primeqa.intelligence.llm.providers.openai_provider import OpenAIProvider


class ProviderRegistry:
    def __init__(self, providers: Optional[List] = None):
        self.providers = providers or [AnthropicProvider(), OpenAIProvider()]

    def get(self, model_id: str):
        for p in self.providers:
            if p.supports_model(model_id):
                return p
        # Default to Anthropic \u2014 avoids KeyError when a model id is
        # unknown (usage log will attribute cost at fallback pricing).
        return self.providers[0]


_default = ProviderRegistry()


def get_provider_for_model(model_id: str):
    return _default.get(model_id)
