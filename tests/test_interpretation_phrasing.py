"""Unit: the interpretation-phrasing enricher + prompt module (D-117).

The enricher's `llm_call` is stubbed with a fake `LLMResponse` — tests never burn
credits. Covers the happy path (capped dict), best-effort (None on bad / raising
response), and the prompt module's build/parse. No DB.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

from primeqa.intelligence.interpretation_phrasing import InterpretationPhrasingEnricher
from primeqa.intelligence.llm import LLMError
from primeqa.intelligence.llm.prompts import interpretation_phrasing as prompt
from primeqa.interpretation.model import Cause, Interpretation


def _interp(*, cause=None) -> Interpretation:
    return Interpretation(
        run_id=uuid4(), recipe_id=uuid4(), claim_test_id=uuid4(),
        outcome="failed", verdict="prohibition_not_enforced",
        attribution="The violating create SUCCEEDED — the prohibition did not enforce.",
        cause=cause)


def _fake_resp(parsed):
    r = MagicMock()
    r.parsed_content = parsed
    r.model = "claude-haiku"
    r.prompt_version = "interpretation_phrasing@v1"
    return r


_GOOD = {
    "headline": "The rule did not block the bad save.",
    "explanation": "A create that should have been rejected went through, so the prohibition is not enforced.",
}

_PATCH = "primeqa.intelligence.interpretation_phrasing.llm_call"


def _enricher():
    return InterpretationPhrasingEnricher(tenant_id=1, api_key="k")


# --- the enricher ---

def test_phrase_happy_path_shape():
    with patch(_PATCH, return_value=_fake_resp(_GOOD)):
        out = _enricher().phrase(
            _interp(cause=Cause(cause_kind="enforcement_gap", vr_name="VR_A")))
    assert out["headline"] == _GOOD["headline"]
    assert out["explanation"] == _GOOD["explanation"]
    assert out["model"] == "claude-haiku"
    assert out["prompt_version"] == "interpretation_phrasing@v1"
    assert "generated_at" in out


def test_phrase_caps_long_text():
    long = {"headline": "h" * 500, "explanation": "e" * 5000}
    with patch(_PATCH, return_value=_fake_resp(long)):
        out = _enricher().phrase(_interp())
    assert len(out["headline"]) == 200 and len(out["explanation"]) == 2000


def test_phrase_none_on_non_dict():
    with patch(_PATCH, return_value=_fake_resp("not a dict")):
        assert _enricher().phrase(_interp()) is None


def test_phrase_none_on_missing_or_empty_keys():
    with patch(_PATCH, return_value=_fake_resp({"headline": "x"})):     # no explanation
        assert _enricher().phrase(_interp()) is None
    with patch(_PATCH, return_value=_fake_resp({"headline": "", "explanation": " "})):
        assert _enricher().phrase(_interp()) is None


def test_phrase_none_on_llm_error_never_raises():
    with patch(_PATCH, side_effect=LLMError("rate_limited", "boom")):
        assert _enricher().phrase(_interp()) is None


# --- the prompt module ---

def test_prompt_build_carries_only_the_facts():
    spec = prompt.build(
        {"outcome": "failed", "verdict": "prohibition_not_enforced",
         "attribution": "A.", "cause": {"cause_kind": "enforcement_gap", "vr_name": "VR_A"}},
        tenant_id=1)
    assert spec.max_tokens == prompt.MAX_TOKENS
    user = spec.messages[0]["content"]
    assert "prohibition_not_enforced" in user and "enforcement_gap" in user
    assert "INVENT NOTHING" in spec.system[0]["text"]


def test_prompt_parse_extracts_json():
    r = MagicMock(); r.raw_text = '```json\n{"headline":"h","explanation":"e"}\n```'
    assert prompt._parse(r) == {"headline": "h", "explanation": "e"}
    r2 = MagicMock(); r2.raw_text = "no json here"
    assert prompt._parse(r2) is None
