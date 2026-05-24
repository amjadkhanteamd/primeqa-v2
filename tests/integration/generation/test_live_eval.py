"""Live eval gate (D-089 slice 2 / D-104) — the real prompt gate.

The real LLM (pinned Sonnet via the gateway) proposes from each live-eligible
probe's requirement_text; results are adjudicated against the per-probe semantic
envelope. The gate = no invariant violations (ontology collapse auto-fails);
drift is printed for human adjudication, never auto-failed (D-104.4 / D-090(c)).

Periodic, NEVER PR-gating (D-104): marked ``live`` and skipped unless
ANTHROPIC_API_KEY is set (mirrors the ``sandbox`` marker). Also needs the
v2-platform tenant / llm_usage_log infra the gateway writes to.
"""
from __future__ import annotations

import os

import pytest

from primeqa.generation.eval import (
    EvalHarness, api_key_from_env, load_corpus, render_live, run_live_corpus,
)

from .conftest import TEST_TENANT_ID

pytestmark = pytest.mark.live


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"),
                    reason="live eval needs ANTHROPIC_API_KEY (periodic, not PR-gating)")
def test_live_corpus_no_invariant_violations(seeded):
    cases = [c for c in load_corpus() if c.live_eligible]
    assert cases, "no live-eligible probes found"
    harness = EvalHarness(TEST_TENANT_ID)
    results = run_live_corpus(
        harness, cases, tenant_id=TEST_TENANT_ID, api_key=api_key_from_env())

    print("\n" + render_live(results))

    # Gate (D-104.4): no invariant violations (ontology collapse). Drift rows
    # are reported above for human adjudication, never auto-failed.
    violations = [r for r in results if not r.invariant_ok]
    assert not violations, "ontology-coherence invariant violations:\n" + "\n".join(
        f"  {r.case_id} [{r.model}/{r.prompt_version}]: {'; '.join(r.violated)}"
        for r in violations)
