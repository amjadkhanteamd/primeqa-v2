"""Per-tenant model control (migrations 060/061) — pure unit tests.

Covers the router primitives (resolve_tenant_model, the selectable-set
expression, the select_chain precedence branch + the summary exclusion),
the SELECTABLE_MODELS ⊆ MODEL_PRICING consistency invariant, the pricing
overlay precedence, the catalog refresh diff classification, and the
consumer's error taxonomy. Everything DB-touching is pinned via
monkeypatched seams — these tests run without a database.
"""
from __future__ import annotations

import pytest

from primeqa.intelligence.llm import router as R
from primeqa.intelligence.llm.catalog import classify_refresh
from primeqa.intelligence.llm.router import (
    HAIKU, OPUS, SELECTABLE_MODELS, SONNET, SONNET_5,
    ModelConfigError, TenantPolicy, _combine_selectable, resolve_tenant_model,
    select_chain, summary_model,
)


@pytest.fixture()
def _code_selectable(monkeypatch):
    """Pin the selectable set to the code frozenset (no catalog read)."""
    monkeypatch.setattr(R, "selectable_model_ids",
                        lambda *a, **k: SELECTABLE_MODELS)


# ---------------------------------------------------------------------------
# resolve_tenant_model — the fail-loud primitive
# ---------------------------------------------------------------------------

def test_resolve_known_returns_id():
    assert resolve_tenant_model(OPUS, selectable=SELECTABLE_MODELS) == OPUS


def test_resolve_unknown_raises_named():
    with pytest.raises(ModelConfigError) as ei:
        resolve_tenant_model("claude-nope-x", tenant_id=7,
                             selectable=SELECTABLE_MODELS)
    msg = str(ei.value)
    assert "tenant 7" in msg and "claude-nope-x" in msg and OPUS in msg


def test_resolve_retired_code_model_raises():
    # A catalog-retired CODE model is subtracted from the selectable set —
    # the exact retirement-drill path.
    shrunk = frozenset(SELECTABLE_MODELS - {SONNET_5})
    with pytest.raises(ModelConfigError):
        resolve_tenant_model(SONNET_5, selectable=shrunk)


# ---------------------------------------------------------------------------
# selectable set expression: (code ∪ active) − retired; DB failure → code set
# ---------------------------------------------------------------------------

def test_combine_selectable_union_and_subtract():
    rows = [("claude-new-x", "active"),      # runtime-enabled → added
            (SONNET_5, "retired"),           # code model retired → subtracted
            ("claude-old-y", "retired")]     # non-code retired → stays out
    got = _combine_selectable(rows)
    assert "claude-new-x" in got
    assert SONNET_5 not in got
    assert "claude-old-y" not in got
    assert OPUS in got                        # untouched code models remain


def test_selectable_model_ids_falls_back_to_code_set_on_db_failure(monkeypatch):
    import sqlalchemy.orm as _orm

    def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(_orm, "Session", _boom)
    assert R.selectable_model_ids(refresh=True) == SELECTABLE_MODELS


# ---------------------------------------------------------------------------
# Consistency invariant: every CODE-selectable model has a pricing entry
# ---------------------------------------------------------------------------

def test_selectable_models_subset_of_model_pricing():
    from primeqa.intelligence.llm.pricing import MODEL_PRICING
    missing = SELECTABLE_MODELS - set(MODEL_PRICING)
    assert not missing, (
        f"SELECTABLE_MODELS entries without a MODEL_PRICING row: {missing} — "
        f"add the pricing entry (cost tracking would silently fall back)")


# ---------------------------------------------------------------------------
# select_chain — precedence 3 branch + the summary exclusion
# ---------------------------------------------------------------------------

def test_select_chain_honors_override(_code_selectable):
    assert select_chain("failure_analysis",
                        tenant_policy=TenantPolicy(model_override=HAIKU)) == [HAIKU]


def test_select_chain_override_beats_always_use_opus(_code_selectable):
    assert select_chain(
        "failure_analysis",
        tenant_policy=TenantPolicy(model_override=SONNET, always_use_opus=True),
    ) == [SONNET]


def test_select_chain_summary_tasks_ignore_override(_code_selectable):
    # Architectural exclusion: the enrichment gate folds the model id into the
    # summary hash and cannot see tenant policy — summaries stay env-routed.
    for task in ("entity_summary_flow", "entity_summary_validation_rule"):
        assert select_chain(
            task, tenant_policy=TenantPolicy(model_override=OPUS),
        ) == [summary_model()]


def test_select_chain_unknown_override_fails_loud(_code_selectable):
    with pytest.raises(ModelConfigError):
        select_chain("failure_analysis",
                     tenant_policy=TenantPolicy(model_override="claude-nope-x"))


def test_select_chain_no_override_unchanged(_code_selectable):
    # NULL override → today's chains, byte-identical.
    assert select_chain("failure_analysis", tenant_policy=TenantPolicy()) \
        == [SONNET, OPUS]


# ---------------------------------------------------------------------------
# TenantPolicy shape — loader contract (raw string, tenant carried)
# ---------------------------------------------------------------------------

def test_tenant_policy_defaults():
    tp = TenantPolicy()
    assert tp.model_override is None and tp.tenant_id is None


def test_tenant_policy_carries_raw_override():
    # The loader stores the RAW string (fail-open loading; validation is the
    # resolution points' job) — even a bad id must be representable.
    tp = TenantPolicy(model_override="claude-nope-x", tenant_id=3)
    assert tp.model_override == "claude-nope-x" and tp.tenant_id == 3


# ---------------------------------------------------------------------------
# Pricing overlay precedence: catalog → MODEL_PRICING → fallback
# ---------------------------------------------------------------------------

def test_resolve_rates_prefers_catalog(monkeypatch):
    from primeqa.intelligence.llm import pricing as P
    override = P.ModelPrice(input=9.99, output=19.99)
    monkeypatch.setattr(P, "_catalog_rates", lambda m: override)
    assert P.resolve_rates(SONNET_5) is override


def test_resolve_rates_falls_back_to_code_table(monkeypatch):
    from primeqa.intelligence.llm import pricing as P
    monkeypatch.setattr(P, "_catalog_rates", lambda m: None)
    assert P.resolve_rates(SONNET_5) is P.MODEL_PRICING[SONNET_5]
    assert P.resolve_rates("claude-nope-x") is None


def test_compute_cost_unknown_model_keeps_sonnet4_fallback(monkeypatch):
    from primeqa.intelligence.llm import pricing as P
    monkeypatch.setattr(P, "_catalog_rates", lambda m: None)
    # Behavior pin: an unknown id still bills at the honest Sonnet-4 estimate.
    assert P.compute_cost_usd("claude-nope-x", 1_000_000, 0) == \
        P.MODEL_PRICING["claude-sonnet-4-20250514"].input


def test_catalog_priced_model_bills_at_entered_rates(monkeypatch):
    from primeqa.intelligence.llm import pricing as P
    monkeypatch.setattr(P, "_catalog_rates",
                        lambda m: P.ModelPrice(input=2.0, output=4.0)
                        if m == "claude-new-x" else None)
    assert P.compute_cost_usd("claude-new-x", 1_000_000, 500_000) == 2.0 + 2.0


# ---------------------------------------------------------------------------
# Catalog refresh — pure diff classification (expired / new / reappeared)
# ---------------------------------------------------------------------------

def _upstream(*ids):
    return [{"id": i, "display_name": i.title()} for i in ids]


def test_classify_expired_selectable_gone_upstream():
    r = classify_refresh(_upstream(OPUS, SONNET, HAIKU),   # SONNET_5 missing
                         code_set=SELECTABLE_MODELS, active=set(), retired=set())
    assert r.expired == [SONNET_5]
    assert r.new == [] and r.reappeared == []


def test_classify_new_upstream_model():
    r = classify_refresh(_upstream(*SELECTABLE_MODELS, "claude-new-x"),
                         code_set=SELECTABLE_MODELS, active=set(), retired=set())
    assert r.expired == [] and r.reappeared == []
    assert [m["id"] for m in r.new] == ["claude-new-x"]


def test_classify_reappeared_retired_model():
    r = classify_refresh(_upstream(*SELECTABLE_MODELS, "claude-old-y"),
                         code_set=SELECTABLE_MODELS, active=set(),
                         retired={"claude-old-y"})
    # Reappeared is flagged, NOT offered as new (never silently resurrected).
    assert r.reappeared == ["claude-old-y"]
    assert r.new == [] and r.expired == []


def test_classify_runtime_enabled_gone_upstream_expires():
    r = classify_refresh(_upstream(*SELECTABLE_MODELS),
                         code_set=SELECTABLE_MODELS,
                         active={"claude-new-x"}, retired=set())
    assert r.expired == ["claude-new-x"]


def test_classify_counts_upstream():
    r = classify_refresh(_upstream(*SELECTABLE_MODELS),
                         code_set=SELECTABLE_MODELS, active=set(), retired=set())
    assert r.seen == len(SELECTABLE_MODELS)


# ---------------------------------------------------------------------------
# Consumer error taxonomy — ModelConfigError is distinguishable
# ---------------------------------------------------------------------------

def test_consumer_classifies_model_config_error():
    from primeqa.generation.consumer import _classify_error
    assert _classify_error(ModelConfigError("x")) == "model_config_error"
    assert _classify_error(ValueError("x")) == "generation_error"
