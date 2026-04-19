"""OpenAI provider stub.

Not yet implemented. Present so the provider-abstraction architecture
has both primary and fallback slots wired, and a future commit can
plug in the OpenAI SDK without touching the Gateway or callers.

Supports models prefixed with `gpt-` or `o1-`. Raises NotImplementedError
on invoke() until the implementation lands; the Gateway should never
route to this provider today.
"""

from __future__ import annotations

from primeqa.intelligence.llm.provider import ProviderError, ProviderResponse


class OpenAIProvider:
    VENDOR = "openai"

    def supports_model(self, model_id: str) -> bool:
        return isinstance(model_id, str) and (
            model_id.startswith("gpt-") or model_id.startswith("o1-")
        )

    def invoke(self, *, api_key, model, messages, max_tokens,
               system=None, tools=None, tool_choice=None,
               timeout=90, metadata=None) -> ProviderResponse:
        raise NotImplementedError(
            "OpenAI provider not yet implemented \u2014 add the adapter "
            "when a fallback chain is configured."
        )
