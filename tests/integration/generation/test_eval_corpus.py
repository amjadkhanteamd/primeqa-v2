"""Deterministic eval core (D-102) — the golden corpus runs as the suite.

CI-gating integration: the spectrum-representative corpus must pass exactly
(governed properties, never phrasing — D-090(f)), and each case's two replay
invariants must hold (D-090(d)): ``explanation_hash`` stable across runs;
emitted-claim identity stable (a same-version re-run dedups). The live-LLM layer
is intentionally absent (D-102.2 — lands with D-089).
"""
from __future__ import annotations

from primeqa.generation.eval import EvalHarness, aggregate, load_corpus

from .conftest import TEST_TENANT_ID


def test_eval_corpus_full_spectrum(seeded):
    cases = load_corpus()
    harness = EvalHarness(TEST_TENANT_ID)
    results = harness.run_corpus(cases)
    report = aggregate(results)
    print("\n" + report.render())

    failed = [r for r in results if not r.passed]
    assert not failed, "eval corpus failures:\n" + "\n".join(
        f"  {r.case_id}: {'; '.join(r.mismatches)}" for r in failed)

    # spectrum coverage — the full governed-outcome range (D-102.3)
    assert report.by_category.get("draft", (0, 0))[1] >= 1            # verified config draft
    assert report.by_category.get("caveated_draft", (0, 0))[1] >= 1   # caveated negative
    assert report.by_category.get("verified_negative", (0, 0))[1] >= 1  # D-107 Layer-2 verified negative
    assert report.by_category.get("refusal", (0, 0))[1] >= 6          # refusal kinds/causes
    assert report.by_category.get("dedup", (0, 0))[1] >= 1            # was_noop
    assert report.total >= 10
    # both no-admissible-negative causes + the caveated draft are present
    assert "no-admissible-negative-scenario-found" in report.by_refusal_kind
    assert "ungrounded-claim" in report.by_refusal_kind
    assert "ambiguous-reference" in report.by_refusal_kind
    assert report.passed == report.total


def test_eval_replay_two_invariant_stable(seeded):
    cases = load_corpus()
    harness = EvalHarness(TEST_TENANT_ID)
    for c in cases:
        st = harness.replay_stable(c)
        # transparency continuity (D-090(b)): same input -> same explanation_hash
        assert st.explanation_stable, f"{c.id}: explanation_hash drift"
        # semantic continuity (D-090(b)): drafts re-emit to the same identity
        if c.category in ("draft", "caveated_draft", "verified_negative", "dedup"):
            assert st.identity_stable, (
                f"{c.id}: identity_hash drift — same-version re-run did not dedup")
