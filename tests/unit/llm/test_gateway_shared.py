"""Gateway refactor unit tests (D-095.2): llm_call and tool_turn share the
same cross-cutting internals (_enforce_rate_limit + _invoke_and_record), and
llm_call's escalation behavior is preserved.

No DB / no network — the shared helpers are monkeypatched to spies, which both
proves they are SHARED (both entry points call them) and isolates the loop /
assembly logic from the real rate-limit + provider calls (those run in the
integration suite when a DB is available).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from primeqa.intelligence.llm import gateway
from primeqa.intelligence.llm.router import TenantPolicy


@dataclass
class _FakeResp:
    content: list = field(default_factory=list)
    raw_text: str = "hi"
    model: str = "claude-test"
    input_tokens: int = 10
    output_tokens: int = 5
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    latency_ms: int = 11
    request_id: str = "req_1"
    stop_reason: str = "end_turn"
    tool_input: Any = None
    tool_name: Any = None


@dataclass
class _FakeSpec:
    messages: list = field(default_factory=lambda: [{"role": "user", "content": "hi"}])
    system: Any = None
    max_tokens: int = 100
    tools: Any = None
    force_tool_name: Any = None
    context_for_log: dict = field(default_factory=dict)

    def parse(self, resp):
        return {"parsed": True, "model": resp.model}


class _FakePrompt:
    VERSION = "fake@v1"
    SUPPORTS_ESCALATION = False

    def build(self, context, tenant_id, recent_misses):
        return _FakeSpec()

    def detect_complexity(self, context):
        return "low"

    def should_escalate(self, parsed, resp):
        return False


def _install_common(monkeypatch, *, chain, prompt, invoke_results):
    """Spy the shared internals + the prompt/router lookups. Returns the call
    log so tests can assert sharing."""
    calls = {"enforce": 0, "invoke": 0}

    def fake_enforce(**kw):
        calls["enforce"] += 1
        return kw.get("tenant_policy") or TenantPolicy()

    seq = list(invoke_results)

    def fake_invoke(**kw):
        calls["invoke"] += 1
        return seq.pop(0)

    monkeypatch.setattr(gateway, "_enforce_rate_limit", fake_enforce)
    monkeypatch.setattr(gateway, "_invoke_and_record", fake_invoke)
    monkeypatch.setattr(gateway, "select_chain", lambda *a, **k: list(chain))
    monkeypatch.setattr(gateway, "get_prompt", lambda task: prompt)
    return calls


def _ok(resp=None):
    return gateway._InvokeResult(response=resp or _FakeResp(), cost_usd=0.01,
                                 usage_log_id=1, error=None)


# ---------------------------------------------------------------------------
# tool_turn
# ---------------------------------------------------------------------------

def test_tool_turn_returns_blocks_and_shares_internals(monkeypatch):
    blocks = [{"type": "tool_use", "id": "tu1", "name": "propose_semantic_intent", "input": {}}]
    calls = _install_common(
        monkeypatch, chain=["claude-test"], prompt=_FakePrompt(),
        invoke_results=[_ok(_FakeResp(content=blocks, stop_reason="tool_use"))],
    )
    res = gateway.tool_turn(
        task="generation_turn", tenant_id=1, api_key="k",
        messages=[{"role": "user", "content": "go"}],
        tools=[{"name": "propose_semantic_intent"}], max_tokens=200,
        tool_choice={"type": "tool", "name": "propose_semantic_intent"},
    )
    assert isinstance(res, gateway.ToolTurnResult)
    assert res.content_blocks == blocks
    assert res.stop_reason == "tool_use"
    assert res.usage_log_id == 1
    # tool_turn goes through BOTH shared internals exactly once (no loop).
    assert calls["enforce"] == 1
    assert calls["invoke"] == 1


def test_tool_turn_marks_stable_prefix_cacheable(monkeypatch):
    """The S3 cost lever: tool_turn must attach an ephemeral cache breakpoint to
    the stable prefix (system grammar + tool schemas) so Anthropic caches it.
    Without this, cache_read/cache_write are 0 on every call (the 0% hit-rate
    bug). Capture what actually reaches _invoke_and_record."""
    captured = {}

    def fake_enforce(**kw):
        return kw.get("tenant_policy") or TenantPolicy()

    def fake_invoke(**kw):
        captured.update(kw)
        return _ok(_FakeResp(content=[], stop_reason="tool_use"))

    monkeypatch.setattr(gateway, "_enforce_rate_limit", fake_enforce)
    monkeypatch.setattr(gateway, "_invoke_and_record", fake_invoke)
    monkeypatch.setattr(gateway, "select_chain", lambda *a, **k: ["claude-test"])

    gateway.tool_turn(
        task="generation_turn", tenant_id=1, api_key="k",
        messages=[{"role": "user", "content": "go"}],
        tools=[{"name": "propose_semantic_intent", "input_schema": {}}],
        max_tokens=200, system="FROZEN GRAMMAR",
        tool_choice={"type": "tool", "name": "propose_semantic_intent"},
    )

    # system: plain string -> a text block carrying the breakpoint
    sys_blocks = captured["system"]
    assert isinstance(sys_blocks, list)
    assert sys_blocks[-1]["cache_control"] == {"type": "ephemeral"}
    assert sys_blocks[-1]["text"] == "FROZEN GRAMMAR"
    # tools: last schema carries the breakpoint (and the original TOOLS list is
    # not mutated — a fresh dict was substituted)
    assert captured["tools"][-1]["cache_control"] == {"type": "ephemeral"}


def test_tool_turn_raises_on_provider_error(monkeypatch):
    from primeqa.intelligence.llm.provider import ProviderError
    err = gateway._InvokeResult(response=None, cost_usd=0.0, usage_log_id=2,
                                error=ProviderError("overloaded", "busy"))
    _install_common(monkeypatch, chain=["claude-test"], prompt=_FakePrompt(),
                    invoke_results=[err])
    with pytest.raises(gateway.LLMError):
        gateway.tool_turn(task="generation_turn", tenant_id=1, api_key="k",
                          messages=[{"role": "user", "content": "x"}],
                          tools=[], max_tokens=50)


# ---------------------------------------------------------------------------
# llm_call still routes through the shared internals (chokepoint preserved)
# ---------------------------------------------------------------------------

def test_llm_call_shares_internals(monkeypatch):
    calls = _install_common(monkeypatch, chain=["claude-test"], prompt=_FakePrompt(),
                            invoke_results=[_ok()])
    resp = gateway.llm_call(task="fake_task", tenant_id=1, api_key="k", context={})
    assert resp.status == "ok"
    assert resp.parsed_content == {"parsed": True, "model": "claude-test"}
    assert resp.cost_usd == 0.01
    assert calls["enforce"] == 1   # same gate as tool_turn
    assert calls["invoke"] == 1


def test_llm_call_escalation_preserved(monkeypatch):
    class _EscPrompt(_FakePrompt):
        SUPPORTS_ESCALATION = True
        def should_escalate(self, parsed, resp):
            return resp.model == "m1"   # escalate off the primary once

    calls = _install_common(
        monkeypatch, chain=["m1", "m2"], prompt=_EscPrompt(),
        invoke_results=[_ok(_FakeResp(model="m1")), _ok(_FakeResp(model="m2"))],
    )
    resp = gateway.llm_call(task="fake_task", tenant_id=1, api_key="k", context={})
    # Escalated to the fallback: _invoke_and_record called twice; final is m2.
    assert calls["invoke"] == 2
    assert resp.escalated is True
    assert resp.parsed_content["model"] == "m2"
    # Both billable attempts back-linked.
    assert resp.usage_log_ids == [1, 1]


def test_llm_call_terminal_error_raises(monkeypatch):
    from primeqa.intelligence.llm.provider import ProviderError
    err = gateway._InvokeResult(response=None, cost_usd=0.0, usage_log_id=9,
                                error=ProviderError("auth_error", "bad key"))
    _install_common(monkeypatch, chain=["m1"], prompt=_FakePrompt(),
                    invoke_results=[err])
    with pytest.raises(gateway.LLMError) as ei:
        gateway.llm_call(task="fake_task", tenant_id=1, api_key="k", context={})
    assert ei.value.status == "auth_error"
