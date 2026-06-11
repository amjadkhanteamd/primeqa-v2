"""D-217 redaction round-trip unit tests.

Two layers, no DB / no network:
1. Pure redact module — indexed tokens are value-deterministic (the property
   that makes the separate system/messages redaction calls and the S3
   runtime's multi-turn full-history re-redaction coherent), round-trip
   restores originals, mangled/unknown tokens degrade gracefully.
2. Gateway wiring (test_gateway_shared.py style) — the provider sees ONLY
   tokens (transit privacy), the caller sees ONLY real values.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from primeqa.intelligence.llm import gateway, redact
from primeqa.intelligence.llm.router import TenantPolicy

EMAIL = "pqa.d205@example.com"
PHONE = "415-555-1212"


def _token_for(value: str) -> str:
    """Derive the indexed token via the public API (no private imports)."""
    mapping: dict = {}
    redact.redact_text_indexed(value, mapping)
    assert len(mapping) == 1
    return next(iter(mapping))


# ---------------------------------------------------------------------------
# Pure redact module
# ---------------------------------------------------------------------------

def test_indexed_token_deterministic_and_mapped():
    m1, m2 = {}, {}
    out1 = redact.redact_text_indexed(f"send to {EMAIL} now", m1)
    out2 = redact.redact_text_indexed(f"reply: {EMAIL}", m2)
    tok = _token_for(EMAIL)
    assert tok in out1 and tok in out2          # same value -> same token
    assert m1 == {tok: EMAIL} == m2
    assert EMAIL not in out1


def test_same_value_dedups_to_one_token():
    mapping: dict = {}
    out = redact.redact_text_indexed(f"{EMAIL} and again {EMAIL}", mapping)
    tok = _token_for(EMAIL)
    assert out.count(tok) == 2
    assert mapping == {tok: EMAIL}


def test_multiple_kinds_distinct_tokens():
    mapping: dict = {}
    out = redact.redact_text_indexed(f"mail {EMAIL} call {PHONE}", mapping)
    assert len(mapping) == 2
    assert EMAIL not in out and PHONE not in out
    kinds = sorted(t.split(":")[0].lstrip("<") for t in mapping)
    assert kinds == ["email", "phone"]


def test_system_and_messages_separate_calls_share_map():
    # The gateway redacts system in a SEPARATE call from messages; the
    # value-hash makes both calls issue the identical token.
    mapping: dict = {}
    msgs = redact.redact_messages_indexed(
        [{"role": "user", "content": f"user mentions {EMAIL}"}], mapping)
    sys_msgs = redact.redact_messages_indexed(
        [{"role": "system", "content": f"system mentions {EMAIL}"}], mapping)
    tok = _token_for(EMAIL)
    assert tok in msgs[0]["content"] and tok in sys_msgs[0]["content"]
    assert mapping == {tok: EMAIL}


def test_roundtrip_text_restores_original():
    mapping: dict = {}
    original = f"Create Contact with Email \"{EMAIL}\" and phone {PHONE}."
    redacted = redact.redact_text_indexed(original, mapping)
    assert EMAIL not in redacted and PHONE not in redacted
    assert redact.rehydrate_text(redacted, mapping) == original


def test_rehydrate_value_nested_structures():
    mapping: dict = {}
    tok = redact.redact_text_indexed(EMAIL, mapping)
    obj = {"field_values": {"Email": tok, "n": 3, "flag": True,
                            "nested": [tok, None, {"again": tok}]},
           tok: "token-as-key"}
    out = redact.rehydrate_value(obj, mapping)
    assert out["field_values"]["Email"] == EMAIL
    assert out["field_values"]["nested"][0] == EMAIL
    assert out["field_values"]["nested"][2]["again"] == EMAIL
    assert out["field_values"]["n"] == 3 and out["field_values"]["flag"] is True
    assert out[EMAIL] == "token-as-key"          # keys rehydrate too
    # Empty mapping is identity (the no-PII fast path).
    assert redact.rehydrate_value(obj, {}) is obj


def test_rehydrate_unknown_or_mangled_token_left_alone():
    mapping = {_token_for(EMAIL): EMAIL}
    mangled = "<email:ffffffff> and <EMAIL:00000000>"
    assert redact.rehydrate_text(mangled, mapping) == mangled


def test_second_pass_redaction_is_stable():
    mapping: dict = {}
    once = redact.redact_text_indexed(f"reach {EMAIL}", mapping)
    again = redact.redact_text_indexed(once, dict(mapping))
    assert again == once                          # tokens never re-redact


def test_legacy_flat_redaction_unchanged():
    out = redact.redact_text(f"mail {EMAIL}, bearer abc.def")
    assert "<email>" in out and "bearer <token>" in out
    # Flat legacy tokens are NOT rehydration targets (no hash suffix).
    assert redact.rehydrate_text(out, {_token_for(EMAIL): EMAIL}) == out


def test_rehydrate_content_blocks_sdk_and_dict_shapes():
    mapping: dict = {}
    tok = redact.redact_text_indexed(EMAIL, mapping)
    sdk_tool = SimpleNamespace(type="tool_use", id="tu1", name="propose",
                               input={"Email": tok})
    sdk_text = SimpleNamespace(type="text", text=f"using {tok}")
    other = SimpleNamespace(type="thinking", thinking="...")
    out = redact.rehydrate_content_blocks([sdk_tool, sdk_text, other], mapping)
    assert out[0] == {"type": "tool_use", "id": "tu1", "name": "propose",
                      "input": {"Email": EMAIL}}
    assert out[1] == {"type": "text", "text": f"using {EMAIL}"}
    assert out[2] is other                        # unknown types pass through
    # dict-shaped blocks work the same
    out2 = redact.rehydrate_content_blocks(
        [{"type": "tool_use", "id": "x", "name": "n", "input": {"v": tok}}],
        mapping)
    assert out2[0]["input"]["v"] == EMAIL
    # Empty mapping is identity.
    blocks = [sdk_tool]
    assert redact.rehydrate_content_blocks(blocks, {}) is blocks


# ---------------------------------------------------------------------------
# Gateway wiring (style of test_gateway_shared)
# ---------------------------------------------------------------------------

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
    messages: list = field(default_factory=list)
    system: Any = None
    max_tokens: int = 100
    tools: Any = None
    force_tool_name: Any = None
    context_for_log: dict = field(default_factory=dict)

    def parse(self, resp):
        return {"value": resp.tool_input, "text": resp.raw_text}


def _install(monkeypatch, *, spec, invoke_results, seen):
    class _Prompt:
        VERSION = "fake@v1"
        SUPPORTS_ESCALATION = False
        def build(self, context, tenant_id, recent_misses):
            return spec
        def detect_complexity(self, context):
            return "low"
        def should_escalate(self, parsed, resp):
            return False

    seq = list(invoke_results)

    def fake_invoke(**kw):
        seen.append(kw)
        return seq.pop(0)

    monkeypatch.setattr(gateway, "_enforce_rate_limit",
                        lambda **kw: kw.get("tenant_policy") or TenantPolicy())
    monkeypatch.setattr(gateway, "_invoke_and_record", fake_invoke)
    monkeypatch.setattr(gateway, "select_chain", lambda *a, **k: ["claude-test"])
    monkeypatch.setattr(gateway, "get_prompt", lambda task: _Prompt())


def _ok(resp):
    return gateway._InvokeResult(response=resp, cost_usd=0.01,
                                 usage_log_id=1, error=None)


def test_llm_call_provider_sees_tokens_caller_sees_values(monkeypatch):
    tok = _token_for(EMAIL)
    spec = _FakeSpec(messages=[
        {"role": "user", "content": f"Persist the exact value \"{EMAIL}\"."}])
    # The model echoes the token (it never saw the real value).
    model_resp = _FakeResp(raw_text=f"using {tok}",
                           tool_input={"field_values": {"Email": tok}})
    seen: list = []
    _install(monkeypatch, spec=spec, invoke_results=[_ok(model_resp)], seen=seen)

    resp = gateway.llm_call(task="fake_task", tenant_id=1, api_key="k", context={})

    outbound = seen[0]["messages"][0]["content"]
    assert EMAIL not in outbound and tok in outbound   # transit: tokens only
    assert resp.parsed_content["value"]["field_values"]["Email"] == EMAIL
    assert resp.parsed_content["text"] == f"using {EMAIL}"
    assert resp.raw_text == f"using {EMAIL}"


def test_tool_turn_provider_sees_tokens_caller_sees_values(monkeypatch):
    tok = _token_for(EMAIL)
    sdk_block = SimpleNamespace(type="tool_use", id="tu1", name="propose",
                                input={"expected_value": tok})
    seen: list = []
    _install(monkeypatch, spec=_FakeSpec(),
             invoke_results=[_ok(_FakeResp(content=[sdk_block],
                                           stop_reason="tool_use"))],
             seen=seen)

    res = gateway.tool_turn(
        task="generation_turn", tenant_id=1, api_key="k",
        messages=[{"role": "user", "content": f"value is {EMAIL}"}],
        tools=[{"name": "propose"}], max_tokens=50,
        system=f"requirement mentions {EMAIL}",
    )

    outbound_msg = seen[0]["messages"][0]["content"]
    outbound_sys = seen[0]["system"]
    assert EMAIL not in outbound_msg and tok in outbound_msg
    assert EMAIL not in outbound_sys and tok in outbound_sys
    assert res.content_blocks[0]["input"]["expected_value"] == EMAIL
