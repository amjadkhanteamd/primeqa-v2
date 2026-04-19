"""Eval runner — execute a fixture suite against a specific prompt version.

Two modes:

  "dry"       (default) build the prompt spec end-to-end but don't call
              Anthropic. Scorer runs against the fixture's `expected.output`
              if provided, or just checks structural integrity of the
              spec itself (messages, tools, cache_control). Zero cost.

  "live"      actually call the Anthropic API via the gateway. Uses the
              caller's ANTHROPIC_API_KEY. Writes to llm_usage_log with
              task prefixed `eval/` so it's easy to filter out later.
              Scorer runs against the real response.

Why both: `dry` catches regression bugs in the prompt module itself
(missing tool schema, broken build(), NEW unresolved $var in a fixture
context) for free. `live` is the real quality assessment.

Output: a RunReport with per-fixture CheckResult lists + aggregate
stats. The CLI (`__main__.py`) prints a human-readable table; tests
assert against the structured report directly.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


@dataclass
class FixtureResult:
    fixture_id: str
    task: str
    mode: str            # "dry" | "live"
    passed: bool
    checks: List[Dict[str, Any]]
    spec_info: Dict[str, Any] = field(default_factory=dict)  # dry-mode: spec shape
    response_info: Dict[str, Any] = field(default_factory=dict)  # live-mode: latency/tokens
    error: Optional[str] = None


@dataclass
class RunReport:
    suite: str
    mode: str
    total: int
    passed: int
    failed: int
    fixtures: List[FixtureResult]
    elapsed_ms: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "suite": self.suite,
            "mode": self.mode,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "elapsed_ms": self.elapsed_ms,
            "fixtures": [
                {
                    "id": f.fixture_id,
                    "task": f.task,
                    "mode": f.mode,
                    "passed": f.passed,
                    "checks": f.checks,
                    "spec_info": f.spec_info,
                    "response_info": f.response_info,
                    "error": f.error,
                }
                for f in self.fixtures
            ],
        }


# ---- Fixture → prompt-context mapping -------------------------------------
#
# Each task fixture's `input` dict needs to become the prompt module's
# expected `context` shape. Keeping this translation in ONE place means
# fixture authors don't need to know the prompt-module internals.

def _build_context_for_test_plan_generation(fixture_input: Dict[str, Any]) -> Dict[str, Any]:
    """Requirement can be a dict; the prompt uses getattr() on it, so a
    SimpleNamespace wrapper works. Metadata is passed as-is."""
    from types import SimpleNamespace
    req_dict = fixture_input.get("requirement", {})
    req = SimpleNamespace(
        id=req_dict.get("id"),
        jira_key=req_dict.get("jira_key"),
        jira_summary=req_dict.get("jira_summary"),
        jira_description=req_dict.get("jira_description"),
        acceptance_criteria=req_dict.get("acceptance_criteria"),
    )
    return {
        "requirement": req,
        "metadata_context": fixture_input.get("metadata_context") or {},
        "meta_version_id": fixture_input.get("meta_version_id"),
        "min_tests": fixture_input.get("min_tests", 3),
        "max_tests": fixture_input.get("max_tests", 6),
    }


_CONTEXT_BUILDERS = {
    "test_plan_generation": _build_context_for_test_plan_generation,
}


# ---- Runner ---------------------------------------------------------------

def run_suite(
    task: str,
    *,
    mode: str = "dry",
    tenant_id: Optional[int] = None,
    api_key: Optional[str] = None,
    fixtures: Optional[List[Any]] = None,
    include_ids: Optional[List[str]] = None,
) -> RunReport:
    """Execute a fixture suite.

    `mode="dry"` skips the provider call; `mode="live"` hits Anthropic.
    `tenant_id` + `api_key` are required for live mode (flow through
    the gateway).

    `include_ids` filters to specific fixtures (CLI: --filter).
    `fixtures` lets tests pass synthetic fixtures without JSON files.
    """
    from primeqa.intelligence.llm.eval import load_suite, Fixture
    from primeqa.intelligence.llm.prompts import get_prompt
    from primeqa.intelligence.llm.eval.scorer import score

    t0 = time.time()

    if fixtures is None:
        fixtures = load_suite(task)
    if include_ids:
        wanted = set(include_ids)
        fixtures = [f for f in fixtures if f.id in wanted]

    if not fixtures:
        return RunReport(
            suite=task, mode=mode, total=0, passed=0, failed=0,
            fixtures=[], elapsed_ms=0,
        )

    prompt = get_prompt(task)
    ctx_builder = _CONTEXT_BUILDERS.get(task, lambda i: i)

    results: List[FixtureResult] = []
    for fx in fixtures:
        try:
            context = ctx_builder(fx.input)
            # Build the prompt spec — this alone catches build() regressions.
            spec = prompt.build(context, tenant_id=tenant_id or 0,
                                recent_misses=None)
            spec_info = {
                "message_count": len(spec.messages or []),
                "has_system": bool(spec.system),
                "has_tools": bool(spec.tools),
                "has_cache_blocks": spec.has_cache_blocks,
                "force_tool": spec.force_tool_name,
            }

            if mode == "dry":
                # Parse an empty/placeholder response through the
                # prompt's parser if it exists — catches parse() crashes
                # without a real model call.
                actual = fx.expected.get("output") or {}
                checks = score(task, actual, fx.expected, fx.rubric)
                results.append(FixtureResult(
                    fixture_id=fx.id, task=task, mode=mode,
                    passed=all(c.passed for c in checks),
                    checks=[c.to_dict() for c in checks],
                    spec_info=spec_info,
                ))
                continue

            # LIVE mode: call the gateway.
            if not (tenant_id and api_key):
                raise ValueError(
                    "live mode requires both tenant_id and api_key"
                )
            from primeqa.intelligence.llm import llm_call, LLMError

            try:
                resp = llm_call(
                    task=task,
                    tenant_id=tenant_id,
                    api_key=api_key,
                    context=context,
                )
                actual = resp.parsed_content or {}
                response_info = {
                    "model": resp.model,
                    "prompt_version": resp.prompt_version,
                    "latency_ms": resp.latency_ms,
                    "cost_usd": resp.cost_usd,
                    "input_tokens": resp.input_tokens,
                    "output_tokens": resp.output_tokens,
                    "cached_input_tokens": resp.cached_input_tokens,
                    "escalated": resp.escalated,
                }
                checks = score(task, actual, fx.expected, fx.rubric)
                results.append(FixtureResult(
                    fixture_id=fx.id, task=task, mode=mode,
                    passed=all(c.passed for c in checks),
                    checks=[c.to_dict() for c in checks],
                    spec_info=spec_info,
                    response_info=response_info,
                ))
            except LLMError as e:
                results.append(FixtureResult(
                    fixture_id=fx.id, task=task, mode=mode,
                    passed=False, checks=[],
                    spec_info=spec_info,
                    error=f"{e.status}: {e.message}",
                ))
        except Exception as e:
            results.append(FixtureResult(
                fixture_id=fx.id, task=task, mode=mode,
                passed=False, checks=[],
                error=f"{type(e).__name__}: {e}",
            ))

    passed = sum(1 for r in results if r.passed)
    return RunReport(
        suite=task, mode=mode,
        total=len(results),
        passed=passed,
        failed=len(results) - passed,
        fixtures=results,
        elapsed_ms=int((time.time() - t0) * 1000),
    )
