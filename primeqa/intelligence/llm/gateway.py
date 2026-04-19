"""LLMGateway \u2014 single entry point for every Anthropic call.

Responsibilities (coherent, narrow):
  - Look up the prompt module for this task
  - Ask it for the complexity bucket
  - Ask the router for the model chain
  - Build the spec (prompt module takes care of caching blocks)
  - Invoke the provider (backoff + usage extraction handled there)
  - If the prompt says to escalate AND the chain has a fallback AND
    we haven't already escalated, retry ONCE with the fallback model
  - Record one row in llm_usage_log (always, success or fail)
  - Return LLMResponse with parsed content

Anything NOT in this list (prompt text, model pricing, how to split a
prompt into cache blocks, etc.) is someone else's concern.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from primeqa.intelligence.llm import pricing, usage
from primeqa.intelligence.llm.provider import (
    invoke as provider_invoke,
    ProviderError,
    ProviderResponse,
)
from primeqa.intelligence.llm.prompts import get_prompt
from primeqa.intelligence.llm.router import select_chain, TenantPolicy

log = logging.getLogger(__name__)


# ---- Public types ---------------------------------------------------------

@dataclass
class LLMResponse:
    """What call sites actually use."""
    parsed_content: Any
    raw_text: str
    model: str
    prompt_version: str
    task: str
    cost_usd: float
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cache_write_tokens: int
    latency_ms: int
    escalated: bool
    status: str
    request_id: Optional[str] = None
    complexity: Optional[str] = None
    # Row id of the llm_usage_log entry this call produced. Callers that
    # only know their generation_batch_id / run_id AFTER the LLM call
    # use this to back-link via usage.attach_batch(...) so the cost
    # dashboard attributes the spend correctly.
    usage_log_id: Optional[int] = None


class LLMError(Exception):
    """Raised when the gateway can't satisfy the caller.

    Status codes map 1:1 to ProviderError.status so the UI can pick a
    friendly flash message ("quota_exceeded" \u2192 "Top up credits", etc.)
    """

    def __init__(self, status: str, message: str,
                 request_id: Optional[str] = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.request_id = request_id


# ---- Main entry point -----------------------------------------------------

def llm_call(
    *,
    task: str,
    tenant_id: int,
    api_key: str,
    context: Dict[str, Any],
    user_id: Optional[int] = None,
    complexity: Optional[str] = None,
    tenant_policy: Optional[TenantPolicy] = None,
    recent_misses: Optional[List[Dict[str, Any]]] = None,
    # Cross-reference fields (forwarded to llm_usage_log):
    run_id: Optional[int] = None,
    requirement_id: Optional[int] = None,
    test_case_id: Optional[int] = None,
    generation_batch_id: Optional[int] = None,
    model_override: Optional[str] = None,
) -> LLMResponse:
    """Execute a registered LLM task end-to-end.

    Returns a parsed, usage-tracked LLMResponse. Raises LLMError on
    terminal provider failures (auth, content, quota exhaustion after
    retries). Never silently swallows errors.
    """
    prompt = get_prompt(task)
    prompt_version = getattr(prompt, "VERSION", task)

    # ---- Per-tenant rate limits (migration 032) ---------------------------
    # Check BEFORE anything expensive. A blocked call writes an empty row
    # to llm_usage_log so the dashboard attributes it correctly, then
    # raises LLMError("rate_limited") so the UI can show a friendly
    # "upgrade" flash rather than "500 error".
    from primeqa.intelligence.llm import limits as _limits

    # Tenant policy: caller can pass one explicitly; otherwise load from
    # tenant_agent_settings (single DB roundtrip, cached within the call).
    if tenant_policy is None:
        tenant_limits, tenant_policy = _limits.load_tenant_config(tenant_id)
    else:
        tenant_limits, _ = _limits.load_tenant_config(tenant_id)

    rc = _limits.check(tenant_id, tenant_limits)
    if not rc.allowed:
        usage.record(
            tenant_id=tenant_id, user_id=user_id, task=task,
            model="(blocked)", prompt_version=prompt_version,
            input_tokens=0, output_tokens=0,
            cost_usd=0.0, latency_ms=0,
            status="rate_limited",
            complexity=(complexity or "default"), escalated=False,
            run_id=run_id, requirement_id=requirement_id,
            test_case_id=test_case_id,
            generation_batch_id=generation_batch_id,
            context={"reason": rc.reason},
        )
        raise LLMError("rate_limited", rc.message or "Tenant rate limit hit")

    # Complexity: caller may override; otherwise the prompt module
    # detects from context. Falls through to "default" for tasks that
    # don't route by complexity.
    if complexity is None and hasattr(prompt, "detect_complexity"):
        complexity = prompt.detect_complexity(context) or "default"
    elif complexity is None:
        complexity = "default"

    # Model chain selection. If caller forced a model, use only that.
    if model_override:
        chain = [model_override]
    else:
        chain = select_chain(task, complexity=complexity,
                             tenant_policy=tenant_policy)

    if not chain:
        raise LLMError("content_error",
                       f"no model available for task={task} complexity={complexity}")

    # Auto-load feedback signals for tasks that benefit. Caller override
    # still wins (pass recent_misses=... to inject specific ones).
    #
    # Phase 7: `recent_misses` was a raw list of signal dicts that the
    # prompt had to format itself. We now aggregate signals into a
    # pre-rendered "Common mistakes to avoid" rules block (see
    # feedback_rules.build_rules_block) and pass that string through.
    # Callers passing a list still work via the prompt's back-compat path.
    if recent_misses is None and task == "test_plan_generation":
        from primeqa.intelligence.llm import feedback_rules
        recent_misses = feedback_rules.build_rules_block(tenant_id) or None

    # Build the prompt spec once — escalation reuses the same spec
    # (same prompt, different model) to keep comparisons clean.
    spec = prompt.build(context, tenant_id=tenant_id,
                        recent_misses=recent_misses)

    escalated = False
    last_error: Optional[LLMError] = None

    # Phase 5: Redact obvious PII from outbound prompts before the
    # provider sees them. Safe + fast \u2014 regex-only, preserves structure.
    from primeqa.intelligence.llm import redact
    safe_messages = redact.redact_messages(spec.messages)
    safe_system = (
        redact.redact_messages(
            [{"role": "system", "content": spec.system}]
        )[0]["content"] if spec.system else None
    )

    # Tool use plumbing \u2014 if the prompt declares tools, build the
    # tool_choice parameter to force Anthropic to call it.
    tools_param = spec.tools
    tool_choice_param = None
    if spec.tools and spec.force_tool_name:
        tool_choice_param = {"type": "tool", "name": spec.force_tool_name}

    from primeqa.intelligence.llm.providers import get_provider_for_model

    for attempt, model in enumerate(chain):
        try:
            # Route to the provider that supports this model id. Today
            # every chain is Anthropic, but the architecture accepts
            # cross-vendor fallback chains (Phase 5.5 when OpenAI ships).
            provider = get_provider_for_model(model)
            provider_resp = provider.invoke(
                api_key=api_key,
                model=model,
                messages=safe_messages,
                system=safe_system,
                max_tokens=spec.max_tokens,
                tools=tools_param,
                tool_choice=tool_choice_param,
            )
        except ProviderError as pe:
            _log_usage_error(
                tenant_id=tenant_id, user_id=user_id, task=task,
                model=model, prompt_version=prompt_version,
                complexity=complexity, escalated=(attempt > 0),
                status=pe.status, latency_ms=pe.latency_ms,
                request_id=pe.request_id,
                run_id=run_id, requirement_id=requirement_id,
                test_case_id=test_case_id,
                generation_batch_id=generation_batch_id,
                context=spec.context_for_log,
            )
            # For non-retryable terminal statuses, bubble up now.
            if pe.status in ("auth_error", "content_error", "quota_exceeded"):
                raise LLMError(pe.status, pe.message, pe.request_id) from pe
            # For transient errors the provider already exhausted its
            # backoff budget; try the next model if we have one.
            last_error = LLMError(pe.status, pe.message, pe.request_id)
            continue

        # Parse the response content
        parsed = spec.parse(provider_resp) if spec.parse else provider_resp.raw_text

        # Decide whether to escalate to the next model in the chain.
        has_more = attempt + 1 < len(chain)
        should_retry = (
            has_more
            and not escalated
            and getattr(prompt, "SUPPORTS_ESCALATION", False)
            and prompt.should_escalate(parsed, provider_resp)
        )

        cost = pricing.compute_cost_usd(
            model=provider_resp.model,
            input_tokens=provider_resp.input_tokens,
            output_tokens=provider_resp.output_tokens,
            cached_input_tokens=provider_resp.cached_input_tokens,
            cache_write_tokens=provider_resp.cache_write_tokens,
        )

        usage_log_id = usage.record(
            tenant_id=tenant_id, user_id=user_id, task=task,
            model=provider_resp.model, prompt_version=prompt_version,
            input_tokens=provider_resp.input_tokens,
            output_tokens=provider_resp.output_tokens,
            cached_input_tokens=provider_resp.cached_input_tokens,
            cache_write_tokens=provider_resp.cache_write_tokens,
            cost_usd=cost, latency_ms=provider_resp.latency_ms,
            status="ok",
            complexity=complexity, escalated=(attempt > 0),
            request_id=provider_resp.request_id,
            run_id=run_id, requirement_id=requirement_id,
            test_case_id=test_case_id,
            generation_batch_id=generation_batch_id,
            context=spec.context_for_log,
        )

        if should_retry:
            escalated = True
            continue

        return LLMResponse(
            parsed_content=parsed,
            raw_text=provider_resp.raw_text,
            model=provider_resp.model,
            prompt_version=prompt_version,
            task=task,
            cost_usd=cost,
            input_tokens=provider_resp.input_tokens,
            output_tokens=provider_resp.output_tokens,
            cached_input_tokens=provider_resp.cached_input_tokens,
            cache_write_tokens=provider_resp.cache_write_tokens,
            latency_ms=provider_resp.latency_ms,
            escalated=(attempt > 0),
            status="ok",
            request_id=provider_resp.request_id,
            complexity=complexity,
            usage_log_id=usage_log_id,
        )

    # Fell off the end of the chain on transient errors.
    if last_error:
        raise last_error
    raise LLMError("provider_error", "model chain exhausted without a valid response")


def _log_usage_error(**kwargs):
    """Wrapper so every transient-error path writes a usage row without
    duplicating the field list."""
    kwargs.setdefault("status", "provider_error")
    usage.record(**kwargs)
