"""Phase-1 close-out #5 — configurable, validated summary model.

Red-proofs:
  * Unset → Haiku (no behaviour change); a known value is returned; an UNKNOWN
    value RAISES (no silent fallback) — boot gate both directions.
  * The router routes the summary tasks by SUMMARY_MODEL (overriding tenant
    always_use_opus), and leaves every other task/chain exactly as-is.
  * Single source of truth: the model summary_model_tag() folds into the hash ==
    the model select_chain() routes the summary task to (== summary_model()).
"""
import pytest

from primeqa.intelligence.llm.router import (
    HAIKU, OPUS, SONNET,
    SUMMARY_MODEL_ENV,
    SummaryModelConfigError,
    TenantPolicy,
    select_chain,
    summary_model,
    validate_summary_model,
)
from primeqa.sync.enrichment_gate import summary_model_tag

pytestmark = pytest.mark.unit

SUMMARY_TASKS = ["entity_summary_flow", "entity_summary_validation_rule"]


class TestSummaryModelResolution:
    def test_unset_defaults_to_haiku(self, monkeypatch):
        monkeypatch.delenv(SUMMARY_MODEL_ENV, raising=False)
        assert summary_model() == HAIKU

    def test_blank_defaults_to_haiku(self, monkeypatch):
        monkeypatch.setenv(SUMMARY_MODEL_ENV, "   ")
        assert summary_model() == HAIKU

    @pytest.mark.parametrize("model", [HAIKU, SONNET, OPUS])
    def test_known_value_returned(self, monkeypatch, model):
        monkeypatch.setenv(SUMMARY_MODEL_ENV, model)
        assert summary_model() == model

    @pytest.mark.parametrize("bad", [
        "gpt-4", "haiku", "claude-haiku-4-5", "claude-opus-4-8", "nonsense",
    ])
    def test_unknown_raises_no_silent_fallback(self, monkeypatch, bad):
        # Incl. the BARE aliases — only the router's exact known ids are allowed.
        monkeypatch.setenv(SUMMARY_MODEL_ENV, bad)
        with pytest.raises(SummaryModelConfigError):
            summary_model()


class TestBootValidator:
    def test_unset_ok(self, monkeypatch):
        monkeypatch.delenv(SUMMARY_MODEL_ENV, raising=False)
        validate_summary_model()                 # no raise

    def test_valid_set_ok(self, monkeypatch):
        monkeypatch.setenv(SUMMARY_MODEL_ENV, SONNET)
        validate_summary_model()                 # no raise

    def test_invalid_raises(self, monkeypatch):
        monkeypatch.setenv(SUMMARY_MODEL_ENV, "definitely-not-a-model")
        with pytest.raises(SummaryModelConfigError):
            validate_summary_model()


class TestRouterRoutesSummaryByConfig:
    @pytest.mark.parametrize("task", SUMMARY_TASKS)
    def test_default_is_haiku(self, monkeypatch, task):
        monkeypatch.delenv(SUMMARY_MODEL_ENV, raising=False)
        assert select_chain(task, "low") == [HAIKU]
        assert select_chain(task, "default") == [HAIKU]

    @pytest.mark.parametrize("task", SUMMARY_TASKS)
    def test_configured_model_routed(self, monkeypatch, task):
        monkeypatch.setenv(SUMMARY_MODEL_ENV, SONNET)
        assert select_chain(task) == [SONNET]

    @pytest.mark.parametrize("task", SUMMARY_TASKS)
    def test_overrides_always_use_opus(self, monkeypatch, task):
        # SUMMARY_MODEL controls summaries even for a premium always_use_opus
        # tenant — the deliberate override that prevents gate/worker drift.
        monkeypatch.setenv(SUMMARY_MODEL_ENV, SONNET)
        assert select_chain(task, "default", TenantPolicy(always_use_opus=True)) == [SONNET]

    def test_non_summary_tasks_unchanged(self, monkeypatch):
        # SUMMARY_MODEL set, but no other task/chain is touched.
        monkeypatch.setenv(SUMMARY_MODEL_ENV, SONNET)
        assert select_chain("test_plan_generation", "high") == [OPUS]
        assert select_chain("classification", "default") == [HAIKU, OPUS]
        # always_use_opus still works for non-summary tasks
        assert select_chain("classification", "default",
                            TenantPolicy(always_use_opus=True)) == [OPUS]


class TestSingleSourceOfTruth:
    @pytest.mark.parametrize("model", [HAIKU, SONNET, OPUS])
    @pytest.mark.parametrize("task", SUMMARY_TASKS)
    def test_gate_model_equals_worker_model(self, monkeypatch, model, task):
        # The model the gate folds into the hash == the model the worker calls.
        monkeypatch.setenv(SUMMARY_MODEL_ENV, model)
        assert summary_model_tag() == select_chain(task)[0] == model

    def test_default_single_source(self, monkeypatch):
        monkeypatch.delenv(SUMMARY_MODEL_ENV, raising=False)
        assert summary_model_tag() == select_chain("entity_summary_flow")[0] == HAIKU
