"""Prompt registry \u2014 one file per registered task.

Each prompt module exports:

    VERSION: str                     # "test_plan_generation@v1"
    MAX_TOKENS: int                  # sensible upper bound
    SUPPORTS_CACHE: bool              # whether prompt_spec uses cache blocks
    SUPPORTS_ESCALATION: bool         # whether to invoke the fallback model on low-confidence
    DETECT_COMPLEXITY: callable | None # (context) -> "low"|"medium"|"high" or None

    def build(context, *, tenant_id, recent_misses=None) -> PromptSpec:
        ...

PromptSpec carries everything the Gateway needs to invoke the provider:
  - messages          : list[dict] with content blocks + optional cache_control
  - system            : list[dict] | None  (system blocks, also cacheable)
  - parse(resp)       : callable extracting structured output from response
  - max_tokens        : override VERSION-level default when context demands

PromptRegistry.get(task) returns the module. Look up is intentionally
static so adding a prompt = adding a file + registering in _REGISTRY.
"""

from primeqa.intelligence.llm.prompts.base import PromptSpec, PromptModule
from primeqa.intelligence.llm.prompts.registry import get as get_prompt, all_tasks

__all__ = ["PromptSpec", "PromptModule", "get_prompt", "all_tasks"]
