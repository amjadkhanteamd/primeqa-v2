"""Anthropic provider adapter \u2014 thin wrapper over the existing
provider.invoke() to fit the Provider interface."""

from __future__ import annotations

from primeqa.intelligence.llm.provider import invoke as _invoke, ProviderResponse


class AnthropicProvider:
    """Default provider. Delegates to primeqa.intelligence.llm.provider."""

    VENDOR = "anthropic"

    def supports_model(self, model_id: str) -> bool:
        return isinstance(model_id, str) and model_id.startswith("claude-")

    def invoke(self, *, api_key, model, messages, max_tokens,
               system=None, tools=None, tool_choice=None,
               timeout=90, metadata=None) -> ProviderResponse:
        return _invoke(
            api_key=api_key, model=model, messages=messages,
            max_tokens=max_tokens, system=system, tools=tools,
            tool_choice=tool_choice, timeout=timeout, metadata=metadata,
        )
