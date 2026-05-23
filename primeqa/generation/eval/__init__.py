"""Substrate-3 generation eval suite (D-102 — realizes D-090 v1).

The deterministic core: a corpus-driven harness that runs the generation engine
on golden cases with **scripted tool-turns** (a fixed intent), so the governed
outcome — and both replay hashes — are fully reproducible (D-102.1). It asserts
governed properties only (``outcome_kind`` / ``admissibility_layer`` / caveat /
``refusal_kind`` + cause / claim+recipe shape), never LLM phrasing (D-090(f)),
and doubles as the two-invariant replay net (D-090(d)).

The live-LLM layer (real gateway, tolerant assertions, drift feed) is deferred
to land with D-089's prompt registry (D-102.2).
"""
from primeqa.generation.eval.corpus import GoldenCase, load_corpus
from primeqa.generation.eval.runner import CaseResult, EvalHarness, StabilityResult
from primeqa.generation.eval.scorer import (
    LiveResult, ObservedOutcome, Report, aggregate, observe, render_live, score_live,
)
from primeqa.generation.eval.live import (
    api_key_from_env, build_tool_turn_fn, run_live_corpus,
)

__all__ = [
    "GoldenCase", "load_corpus",
    "EvalHarness", "CaseResult", "StabilityResult",
    "Report", "aggregate",
    # live layer (D-104)
    "ObservedOutcome", "observe", "score_live", "LiveResult", "render_live",
    "api_key_from_env", "build_tool_turn_fn", "run_live_corpus",
]
