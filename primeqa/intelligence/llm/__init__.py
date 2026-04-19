"""LLM gateway — single chokepoint for every Anthropic API call in PrimeQA.

All features go through `llm_call(task=..., context=..., tenant_id=...)`
so that policy (model selection, caching, backoff, rate limits, usage
tracking, escalation, feedback capture) lives in one place.

Public surface:

    from primeqa.intelligence.llm import llm_call, LLMResponse, LLMError

    resp = llm_call(
        task="test_plan_generation",
        tenant_id=1,
        api_key=env_llm_api_key,
        context={"requirement": req, "metadata_context": ctx, ...},
        complexity="high",         # LOW | MEDIUM | HIGH
        user_id=7,                 # optional, for usage log
        run_id=None, requirement_id=40, test_case_id=None,
        generation_batch_id=None,  # optional cross-refs
    )

    parsed = resp.parsed_content   # dict if structured, str otherwise
    model  = resp.model
    cost   = resp.cost_usd

See `gateway.py` for the orchestration, `router.py` for model selection,
`provider.py` for the Anthropic wrapper, `prompts/` for registered prompts,
and `usage.py` for llm_usage_log writes.
"""

from primeqa.intelligence.llm.gateway import llm_call, LLMResponse, LLMError

__all__ = ["llm_call", "LLMResponse", "LLMError"]
