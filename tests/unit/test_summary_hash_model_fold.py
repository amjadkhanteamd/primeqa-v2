"""Phase-1 close-out #5 — summary_input_hash folds the summary MODEL.

Red-proofs:
  * Same model+prompt+context → same hash (carry forward, no re-summarize).
  * A different model → a different hash (a model swap re-summarizes), mirroring
    embed_input_hash's model_tag fold.
  * Transition: post-backfill, an unchanged row at the DEFAULT model produces a
    hash matching what the next sync's gate computes → no re-summarization; a
    SUMMARY_MODEL swap after the backfill correctly mismatches.
"""
import pytest

from primeqa.intelligence.llm.router import HAIKU, SONNET, SUMMARY_MODEL_ENV
from primeqa.sync.enrichment_gate import summary_input_hash, summary_model_tag

pytestmark = pytest.mark.unit

# A fixed Flow build_summary_context row (7 columns: sf_api_name, semantic_text,
# attributes, flow_type, trigger_type, is_active, trigger_obj).
_FLOW_ROW = ("MyFlow", "a flow", {"Metadata": {"description": "d"}},
             "AutoLaunchedFlow", "RecordAfterSave", True, "Account")
_PROMPT = "entity_summary_flow@v1"


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Exec:
    """Minimal stand-in for a SQLAlchemy connection: .execute(...).fetchone()."""
    def __init__(self, row):
        self._row = row

    def execute(self, *args, **kwargs):
        return _Result(self._row)


def _hash(model, prompt=_PROMPT, row=_FLOW_ROW):
    return summary_input_hash(_Exec(row), "Flow", "eid", prompt, model)


class TestModelFold:
    def test_same_inputs_same_hash(self):
        assert _hash(HAIKU) == _hash(HAIKU)            # idempotent → carry forward

    def test_model_swap_changes_hash(self):
        assert _hash(HAIKU) != _hash(SONNET)           # swap → re-summarize

    def test_prompt_bump_still_changes_hash(self):
        assert _hash(HAIKU, "v1") != _hash(HAIKU, "v2")

    def test_context_change_still_changes_hash(self):
        other = ("MyFlow", "DIFFERENT semantic", _FLOW_ROW[2],
                 "AutoLaunchedFlow", "RecordAfterSave", True, "Account")
        assert _hash(HAIKU, row=_FLOW_ROW) != _hash(HAIKU, row=other)

    def test_none_context_returns_none(self):
        assert summary_input_hash(_Exec(None), "Flow", "eid", _PROMPT, HAIKU) is None


class TestBackfillTransition:
    def test_post_backfill_unchanged_at_default_matches(self, monkeypatch):
        # Backfill stores the hash recomputed at the current (default) model.
        monkeypatch.delenv(SUMMARY_MODEL_ENV, raising=False)
        backfilled = _hash(summary_model_tag())        # == _hash(HAIKU)
        # The next sync's gate computes the hash at the still-default model.
        next_sync = _hash(summary_model_tag())
        assert backfilled == next_sync                 # no needless re-summarize

    def test_model_swap_after_backfill_mismatches(self, monkeypatch):
        monkeypatch.delenv(SUMMARY_MODEL_ENV, raising=False)
        backfilled = _hash(summary_model_tag())        # HAIKU
        monkeypatch.setenv(SUMMARY_MODEL_ENV, SONNET)
        next_sync = _hash(summary_model_tag())         # SONNET
        assert backfilled != next_sync                 # re-summarize on swap
