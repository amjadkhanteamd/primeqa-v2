"""Unit: D-236 — the repair_proposal LLM prompt module + its registry/router
wiring. tool_use schema, parse (tool_input pre-parsed + text fallback),
escalation on low confidence, and the drift guard (task registered AND routed)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from primeqa.intelligence.llm.prompts import get_prompt, repair_proposal
from primeqa.intelligence.llm.router import select_chain

pytestmark = pytest.mark.unit


def _ctx():
    return {"verdict": "value_not_persisted", "cause_kind": "field_not_createable",
            "sobject": "Opportunity", "current_field_values": {"Amount": "5000",
            "CreatedById": "005xx"}, "error_code": "INVALID_FIELD_FOR_INSERT_UPDATE",
            "error_message": "Unable to create/update fields: CreatedById",
            "error_fields": ["CreatedById"], "run_id": "r1", "claim_test_id": "c1"}


def test_build_uses_tool_use_with_strict_schema():
    spec = repair_proposal.build(_ctx(), tenant_id=1)
    assert spec.force_tool_name == "submit_recipe_fix"
    assert spec.tools and spec.tools[0]["name"] == "submit_recipe_fix"
    props = spec.tools[0]["input_schema"]["properties"]
    assert set(props) == {"confidence", "rationale", "field_changes"}
    # the failing field + the error code reach the prompt
    text = spec.messages[0]["content"]
    assert "CreatedById" in text and "field_not_createable" in text
    assert spec.context_for_log["verdict"] == "value_not_persisted"


def test_parse_prefers_tool_input():
    spec = repair_proposal.build(_ctx(), tenant_id=1)
    fix = {"confidence": 0.9, "rationale": "drop read-only field",
           "field_changes": {"CreatedById": repair_proposal.REMOVE_SENTINEL}}
    resp = SimpleNamespace(tool_input=fix, raw_text=None)
    assert spec.parse(resp) == fix


def test_parse_text_fallback_and_error():
    spec = repair_proposal.build(_ctx(), tenant_id=1)
    ok = SimpleNamespace(tool_input=None, raw_text='{"confidence": 0.8, "rationale": "x", "field_changes": {}}')
    assert spec.parse(ok)["confidence"] == 0.8
    bad = SimpleNamespace(tool_input=None, raw_text="not json")
    assert spec.parse(bad).get("_parse_error") is True


def test_should_escalate_on_low_confidence_or_parse_error():
    assert repair_proposal.should_escalate({"confidence": 0.5}, None) is True
    assert repair_proposal.should_escalate({"_parse_error": True}, None) is True
    assert repair_proposal.should_escalate({"confidence": 0.92}, None) is False
    assert repair_proposal.should_escalate({"confidence": "bad"}, None) is True


def test_registered_and_routed_drift_guard():
    # the task must be in BOTH the prompt registry AND the router chain
    assert get_prompt("repair_proposal") is repair_proposal
    chain = select_chain("repair_proposal")
    assert chain and len(chain) == 2          # [Sonnet, Opus] — one escalation hop
