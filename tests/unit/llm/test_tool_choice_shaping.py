"""D-504: forced tool choice is a per-model capability, shaped at the gateway's
ONE shared internal (_invoke_and_record). No DB, no network.

The rule table (docs/design/LLD_TOOL_CHOICE_CAPABILITY.md section 3.3):

  tool_choice in                 capability   out
  None / auto / none             any          unchanged, shape None
  {"type":"tool"} / {"type":"any"} True       unchanged, shape "forced"
  same                           False        auto + disable_parallel_tool_use +
                                              a TRAILING role:system instruction
                                              naming the tool, shape "auto+instruction"
  same                           None         ModelConfigError PRE-SPEND
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from primeqa.intelligence.llm import gateway
from primeqa.intelligence.llm.router import ModelConfigError

FORCED = {"type": "tool", "name": "propose_semantic_intent"}
MSGS = [
    {"role": "user", "content": "opening"},
    {"role": "assistant", "content": [{"type": "tool_use", "id": "t1",
                                       "name": "propose_semantic_intent", "input": {}}]},
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                  "content": "ok"}]},
]


@pytest.fixture
def capability(monkeypatch):
    """Pin the resolver to a fixed table (no catalog read)."""
    table = {}
    monkeypatch.setattr(gateway, "forced_tool_choice_supported",
                        lambda model, **kw: table.get(model))
    return table


# ---- the pure shaping function ----------------------------------------------

@pytest.mark.parametrize("tc", [None, {"type": "auto"}, {"type": "none"}])
def test_non_forcing_choice_is_untouched_whatever_the_model(capability, tc):
    capability["m"] = False
    out_tc, out_msgs, shape = gateway._shape_tool_choice("m", tc, MSGS)
    assert out_tc is tc and out_msgs is MSGS and shape is None


def test_supported_model_keeps_the_forced_choice(capability):
    capability["m"] = True
    out_tc, out_msgs, shape = gateway._shape_tool_choice("m", FORCED, MSGS)
    assert out_tc is FORCED and out_msgs is MSGS and shape == "forced"


def test_rejecting_model_gets_auto_one_call_and_a_trailing_instruction(capability):
    capability["m"] = False
    out_tc, out_msgs, shape = gateway._shape_tool_choice("m", FORCED, MSGS)
    assert shape == "auto+instruction"
    assert out_tc == {"type": "auto", "disable_parallel_tool_use": True}
    # The instruction is the LAST message, a system turn, and names the tool.
    assert out_msgs[:-1] == MSGS
    tail = out_msgs[-1]
    assert tail["role"] == "system"
    assert "propose_semantic_intent" in tail["content"]
    assert tail["content"] == gateway.FORCED_TOOL_INSTRUCTION.format(
        name="propose_semantic_intent")


def test_any_choice_on_rejecting_model_names_exactly_one_tool(capability):
    capability["m"] = False
    out_tc, out_msgs, shape = gateway._shape_tool_choice("m", {"type": "any"}, MSGS)
    assert shape == "auto+instruction"
    assert out_tc["type"] == "auto" and out_tc["disable_parallel_tool_use"] is True
    assert out_msgs[-1] == {"role": "system", "content": gateway.ANY_TOOL_INSTRUCTION}


def test_shaping_never_mutates_the_callers_objects(capability):
    capability["m"] = False
    before = json.dumps(MSGS, sort_keys=True)
    tc = dict(FORCED)
    gateway._shape_tool_choice("m", tc, MSGS)
    assert json.dumps(MSGS, sort_keys=True) == before
    assert tc == FORCED


def test_unknown_capability_is_refused_as_model_config_error(capability):
    # "m" absent from the table -> None -> refused with the act that fixes it.
    with pytest.raises(ModelConfigError) as ei:
        gateway._shape_tool_choice("m", FORCED, MSGS)
    assert "UNKNOWN" in str(ei.value) and "Refresh models" in str(ei.value)


def test_instruction_needs_a_trailing_user_turn(capability):
    # The API's placement rule; unreachable by today's callers (they always end
    # on a user turn) — pinned so a future caller cannot send a 400 blindly.
    capability["m"] = False
    bad = MSGS[:2]   # ends on the assistant turn
    with pytest.raises(gateway.LLMError) as ei:
        gateway._shape_tool_choice("m", FORCED, bad)
    assert ei.value.status == "content_error"
    with pytest.raises(gateway.LLMError):
        gateway._shape_tool_choice("m", FORCED, [])


# ---- the shaping sits in _invoke_and_record, before the provider ---------------

@dataclass
class _FakeResp:
    content: list = field(default_factory=list)
    raw_text: str = ""
    model: str = "m"
    input_tokens: int = 1
    output_tokens: int = 1
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    latency_ms: int = 1
    request_id: str = "req"
    stop_reason: str = "tool_use"
    tool_input: Any = None
    tool_name: Any = None


class _FakeProvider:
    def __init__(self):
        self.calls = []

    def invoke(self, **kw):
        self.calls.append(kw)
        return _FakeResp()


@pytest.fixture
def provider(monkeypatch):
    from primeqa.intelligence.llm import providers
    fake = _FakeProvider()
    monkeypatch.setattr(providers, "get_provider_for_model", lambda m: fake)
    monkeypatch.setattr(gateway.pricing, "compute_cost_usd", lambda **kw: 0.0)
    recorded = []

    def fake_record(**kw):
        recorded.append(kw)
        return 7

    monkeypatch.setattr(gateway.usage, "record", fake_record)
    monkeypatch.setattr(gateway, "_log_usage_error", lambda **kw: 8)
    fake.recorded = recorded
    return fake


def _invoke(model, tool_choice, context=None):
    return gateway._invoke_and_record(
        api_key="k", model=model, messages=MSGS, system=None, max_tokens=10,
        tools=[{"name": "propose_semantic_intent"}], tool_choice=tool_choice,
        task="generation", tenant_id=1, user_id=None, prompt_version="v",
        complexity="default", escalated=False, run_id=None, requirement_id=None,
        test_case_id=None, generation_batch_id=None, context_for_log=context,
    )


def test_invoke_sends_the_shaped_request_and_records_the_shape(capability, provider):
    capability["m"] = False
    ctx = {"s3_request_id": "r1"}
    res = _invoke("m", FORCED, ctx)
    assert res.error is None
    sent = provider.calls[0]
    assert sent["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert sent["messages"][-1]["role"] == "system"
    assert provider.recorded[0]["context"] == {"s3_request_id": "r1",
                                               "tool_choice": "auto+instruction"}
    assert ctx == {"s3_request_id": "r1"}        # the caller's dict untouched


def test_invoke_on_supported_model_sends_the_forced_choice(capability, provider):
    capability["m"] = True
    _invoke("m", FORCED, {})
    assert provider.calls[0]["tool_choice"] == FORCED
    assert provider.calls[0]["messages"] == MSGS
    assert provider.recorded[0]["context"] == {"tool_choice": "forced"}


def test_invoke_without_tools_records_no_shape(capability, provider):
    _invoke("m", None, {"k": 1})
    assert provider.calls[0]["tool_choice"] is None
    assert provider.recorded[0]["context"] == {"k": 1}


def test_invoke_refuses_unknown_capability_before_any_spend(capability, provider):
    with pytest.raises(ModelConfigError):
        _invoke("m", FORCED, {})
    assert provider.calls == []            # no provider call
    assert provider.recorded == []         # no usage row


# ---- both entry points pass through the shaping ----------------------------------

def test_tool_turn_delivers_the_shaped_request_to_the_provider(capability, provider, monkeypatch):
    from primeqa.intelligence.llm.router import TenantPolicy
    capability["m"] = False
    monkeypatch.setattr(gateway, "_enforce_rate_limit",
                        lambda **kw: kw.get("tenant_policy") or TenantPolicy())
    gateway.tool_turn(
        task="generation", tenant_id=1, api_key="k", messages=MSGS,
        tools=[{"name": "propose_semantic_intent", "input_schema": {}}],
        max_tokens=10, system="GRAMMAR", tool_choice=FORCED, model_override="m",
    )
    sent = provider.calls[0]
    assert sent["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert sent["messages"][-1]["role"] == "system"
    # The moving cache breakpoint sits on the last COMPLETED turn, before the
    # appended instruction — the cached prefix is untouched.
    assert sent["messages"][-2]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in sent["messages"][-1]


def test_llm_call_delivers_the_shaped_request_to_the_provider(capability, provider, monkeypatch):
    from primeqa.intelligence.llm.router import TenantPolicy

    class _Spec:
        messages = [{"role": "user", "content": "fix it"}]
        system = None
        max_tokens = 10
        tools = [{"name": "submit_recipe_fix", "input_schema": {}}]
        force_tool_name = "submit_recipe_fix"
        context_for_log = {"run_id": 5}
        has_cache_blocks = False

        def parse(self, resp):
            return {"ok": True}

    class _Prompt:
        VERSION = "p@v1"
        SUPPORTS_ESCALATION = False

        def build(self, context, tenant_id, recent_misses):
            return _Spec()

        def detect_complexity(self, context):
            return "default"

    capability["m"] = False
    monkeypatch.setattr(gateway, "_enforce_rate_limit",
                        lambda **kw: kw.get("tenant_policy") or TenantPolicy())
    monkeypatch.setattr(gateway, "get_prompt", lambda task: _Prompt())
    gateway.llm_call(task="repair_proposal", tenant_id=1, api_key="k", context={},
                     model_override="m")
    sent = provider.calls[0]
    assert sent["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert sent["messages"][-1] == {"role": "system",
                                    "content": gateway.FORCED_TOOL_INSTRUCTION.format(
                                        name="submit_recipe_fix")}
    assert provider.recorded[0]["context"] == {"run_id": 5, "tool_choice": "auto+instruction"}
