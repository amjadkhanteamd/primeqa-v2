"""Base types for the prompt registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol


@dataclass
class PromptSpec:
    """Everything the Gateway needs to invoke the provider for one task.

    The Gateway doesn't know or care what the prompt says; it ships the
    spec to the provider, captures usage, and hands .parsed back to the
    caller by running `parse` over the raw response.
    """
    # Messages are passed to provider.invoke() verbatim. For prompt caching
    # the last static block gets cache_control: {"type": "ephemeral"}.
    messages: List[Dict[str, Any]]

    # Optional system prompt as content blocks (can also carry cache_control).
    system: Optional[List[Dict[str, Any]]] = None

    # Callable that extracts structured output from the ProviderResponse.
    # Default: return raw text stripped.
    parse: Optional[Callable[[Any], Any]] = None

    # Upper bound on output tokens. Lower is cheaper; higher risks truncation.
    max_tokens: int = 1024

    # Task-specific metadata stored in llm_usage_log.context
    context_for_log: Dict[str, Any] = field(default_factory=dict)

    # Whether this spec has cache blocks \u2014 routes the "cached tokens
    # saved" metric correctly on the usage log.
    has_cache_blocks: bool = False

    # Phase 5: structured output via Anthropic tool use. When set, the
    # provider passes tools + tool_choice to the API and the response
    # carries a parsed JSON object inside the tool_use content block
    # \u2014 eliminating the JSON-parse failure mode entirely.
    #
    # tools: list of Anthropic tool definitions (name, description,
    #        input_schema).
    # force_tool_name: name of the tool to force; maps to
    #        tool_choice={"type":"tool","name":<force_tool_name>}
    tools: Optional[List[Dict[str, Any]]] = None
    force_tool_name: Optional[str] = None


class PromptModule(Protocol):
    """Duck-typed interface each prompt file must satisfy."""

    VERSION: str
    MAX_TOKENS: int
    SUPPORTS_CACHE: bool
    SUPPORTS_ESCALATION: bool

    def build(self, context: Dict[str, Any], *, tenant_id: int,
              recent_misses: Optional[list] = None) -> PromptSpec: ...

    def detect_complexity(self, context: Dict[str, Any]) -> Optional[str]:
        """Return "low" | "medium" | "high" or None (no bucket)."""
        ...

    def should_escalate(self, parsed: Any, raw_response: Any) -> bool:
        """Given a parsed response + raw, decide whether to retry with
        the fallback model. Return False to accept the response as-is."""
        ...
