"""D-504: the forced-tool-choice capability as a recorded fact — the router's
resolver and code table, the catalog's free probe classification, and the
boot gate's WARN (not raise) on an unknown override. No DB, no network."""
from __future__ import annotations

import logging

import pytest

from primeqa.intelligence.llm import catalog as C
from primeqa.intelligence.llm import router as R
from primeqa.intelligence.llm.router import (
    FORCED_TOOL_CHOICE_CODE, SELECTABLE_MODELS, SONNET_5, OPUS,
    forced_tool_choice_supported,
)


# ---- the resolver ---------------------------------------------------------------

def test_every_code_selectable_model_carries_the_fact():
    # The invariant the picker relies on: a built-in model is never UNKNOWN.
    assert set(FORCED_TOOL_CHOICE_CODE) == set(SELECTABLE_MODELS)
    assert all(v is True for v in FORCED_TOOL_CHOICE_CODE.values())


def test_catalog_fact_wins_over_the_code_table():
    # A re-probe can correct a built-in id; a catalog model answers from its row.
    assert forced_tool_choice_supported(SONNET_5, catalog={SONNET_5: False}) is False
    assert forced_tool_choice_supported("claude-sonnet-5-5",
                                        catalog={"claude-sonnet-5-5": False}) is False
    assert forced_tool_choice_supported("claude-fable-5",
                                        catalog={"claude-fable-5": True}) is True


def test_code_table_answers_when_the_catalog_is_silent():
    assert forced_tool_choice_supported(OPUS, catalog={}) is True


def test_unknown_model_is_none():
    assert forced_tool_choice_supported("claude-sonnet-5-5", catalog={}) is None
    assert forced_tool_choice_supported("claude-mystery", catalog={"x": True}) is None


def test_resolver_reads_the_cached_catalog_facts(monkeypatch):
    monkeypatch.setattr(R, "catalog_forced_tool_choice_facts",
                        lambda **kw: {"claude-opus-5-5": False})
    assert forced_tool_choice_supported("claude-opus-5-5") is False
    assert forced_tool_choice_supported(OPUS) is True
    assert forced_tool_choice_supported("claude-nobody") is None


def test_facts_are_empty_when_the_catalog_cannot_be_read(monkeypatch):
    import sqlalchemy.orm as _orm

    def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(_orm, "Session", _boom)
    monkeypatch.setattr(R, "_selectable_cache", None)
    assert R.selectable_model_ids(refresh=True) == SELECTABLE_MODELS
    assert R.catalog_forced_tool_choice_facts() == {}
    # The code table still answers; everything else is unknown (refused pre-spend).
    assert forced_tool_choice_supported(OPUS) is True
    assert forced_tool_choice_supported("claude-sonnet-5-5") is None


# ---- the probe's classification -------------------------------------------------

class _Err(Exception):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code


def test_probe_success_is_true():
    assert C.classify_probe_outcome(None) is True


def test_probe_exact_rejection_is_false():
    # The SDK's rendering (a Python repr of the body — observed 2026-10-08) ...
    sdk = _Err(400, "Error code: 400 - {'type': 'error', 'error': {'type': "
                    "'invalid_request_error', 'message': 'tool_choice: type \"tool\" "
                    "and \"any\" are not supported for this model.'}}")
    assert C.classify_probe_outcome(sdk) is False
    # ... and a JSON rendering with the inner quotes escaped.
    js = _Err(400, 'Error code: 400 - {"message": "tool_choice: type \\"tool\\" and '
                   '\\"any\\" are not supported for this model."}')
    assert C.classify_probe_outcome(js) is False


@pytest.mark.parametrize("exc", [
    _Err(400, "messages: roles must alternate"),       # another 400
    _Err(404, "model: not found"),                     # unknown model id
    _Err(401, "invalid x-api-key"),                    # auth
    RuntimeError("connection reset"),                  # transport
])
def test_probe_other_failures_leave_the_fact_unknown(exc):
    with pytest.raises(C.CatalogProbeError):
        C.classify_probe_outcome(exc)


def test_probe_sends_a_forced_count_tokens_call():
    class _Messages:
        def __init__(self):
            self.calls = []

        def count_tokens(self, **kw):
            self.calls.append(kw)
            raise _Err(400, C.FORCED_TOOL_CHOICE_REJECTION + " for this model.")

    class _Client:
        def __init__(self):
            self.messages = _Messages()

    client = _Client()
    assert C.probe_forced_tool_choice("k", "claude-sonnet-5-5", client=client) is False
    sent = client.messages.calls[0]
    assert sent["model"] == "claude-sonnet-5-5"
    assert sent["tool_choice"] == {"type": "tool", "name": "probe_tool"}
    assert sent["tools"][0]["name"] == "probe_tool"


def test_probe_without_a_key_is_refused_not_guessed():
    with pytest.raises(C.CatalogProbeError):
        C.probe_forced_tool_choice("", "claude-sonnet-5-5")


# ---- the boot gate: WARN, never raise, on an unknown-capability override ----------

def test_boot_gate_warns_on_unknown_capability_override(monkeypatch, caplog):
    class _Sess:
        def __init__(self, *a, **k):
            pass

        def execute(self, *a, **k):
            class _R:
                def fetchall(self_inner):
                    return [(1, "claude-sonnet-5-5"), (2, OPUS)]
            return _R()

        def close(self):
            pass

    import sqlalchemy.orm as _orm
    monkeypatch.setattr(_orm, "Session", _Sess)
    monkeypatch.setattr(R, "selectable_model_ids",
                        lambda *a, **k: SELECTABLE_MODELS | {"claude-sonnet-5-5"})
    monkeypatch.setattr(R, "catalog_forced_tool_choice_facts", lambda **kw: {})
    with caplog.at_level(logging.WARNING, logger="primeqa.intelligence.llm.router"):
        R.validate_tenant_model_overrides()      # does NOT raise
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "UNKNOWN" in text and "tenant 1" in text and "claude-sonnet-5-5" in text
    assert "tenant 2" not in text              # a known model is not named


def test_boot_gate_is_silent_when_every_override_is_known(monkeypatch, caplog):
    class _Sess:
        def __init__(self, *a, **k):
            pass

        def execute(self, *a, **k):
            class _R:
                def fetchall(self_inner):
                    return [(1, "claude-sonnet-5-5")]
            return _R()

        def close(self):
            pass

    import sqlalchemy.orm as _orm
    monkeypatch.setattr(_orm, "Session", _Sess)
    monkeypatch.setattr(R, "selectable_model_ids",
                        lambda *a, **k: SELECTABLE_MODELS | {"claude-sonnet-5-5"})
    monkeypatch.setattr(R, "catalog_forced_tool_choice_facts",
                        lambda **kw: {"claude-sonnet-5-5": False})
    with caplog.at_level(logging.WARNING, logger="primeqa.intelligence.llm.router"):
        R.validate_tenant_model_overrides()
    assert "UNKNOWN" not in " ".join(r.getMessage() for r in caplog.records)
