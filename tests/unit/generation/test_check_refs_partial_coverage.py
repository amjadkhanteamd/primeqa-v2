"""D-311 — Layer-A ref-existence mirrors the semantic seam's per-intent posture.

`GovernanceCore.check_refs_exist` over a multi-intent propose must reject the
whole call for correction ONLY when EVERY intent's refs miss. When at least one
intent's refs resolve it returns ``ok=True`` and lets ``resolve_intent`` isolate
the misses (recording each as a D-302 partial_refusal). This preserves the
base-prompt partial-coverage contract: a 4-intent batch where 3 refs resolve and
1 does not must NOT collapse into structural-validation-failure and lose all 4
(the L7g journey regression, D-310).

These are DB-free: a narrow fake S1 answers ``get_entities`` (non-empty for the
valid api names, empty otherwise) so the REAL check_refs_exist aggregation +
``_check_refs_one`` default (data_behavior) path run unmocked.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from primeqa.generation.governance_core import GovernanceCore


# ---------------------------------------------------------------------------
# Narrow, faithful S1 fake — only the get_entities lookup resolve_subject uses.
# ---------------------------------------------------------------------------

VALID_APIS = {"Home_Loan__c", "Loan_Stage__c", "Risk_Rating__c"}
MISSING_API = "Approval_Absence__c"
AT = 5910


class _FakeS1:
    """Answers the single call resolve_subject makes: get_entities(entity_type,
    at_seq=, filters={'sf_api_name': ...}) -> non-empty iff the api is known."""

    def get_entities(self, entity_type, at_seq, filters):
        api = (filters or {}).get("sf_api_name")
        return [SimpleNamespace(entity_type=entity_type, sf_api_name=api)] if api in VALID_APIS else []


def _ctx(at=AT):
    return SimpleNamespace(semantic_context=SimpleNamespace(s1_version_seq=at))


def _intent(api):
    return {
        "requirement_excerpt": f"a {api} exists",
        "archetype_hint": "data_behavior",
        "polarity_hint": "positive",
        "claim_kind_hint": "existence-claim",
        "target_subject_hint": {"entity_type": "Object", "sf_api_name": api},
    }


def _multi(*apis):
    return {"intent_descriptors": [_intent(a) for a in apis]}


@pytest.fixture
def gov():
    return GovernanceCore(_FakeS1())


# ---------------------------------------------------------------------------
# Multi-intent posture
# ---------------------------------------------------------------------------

def test_all_valid_ok(gov):
    rc = gov.check_refs_exist(intent_input=_multi(*VALID_APIS), ctx=_ctx())
    assert rc.ok


def test_partial_miss_proceeds(gov):
    """THE regression: 3 valid + 1 missing -> ok=True (proceed to resolve_intent),
    NOT a whole-call rejection. Pre-D-311 this returned ok=False and, after 3
    corrections, lost all 4 intents to structural-validation-failure."""
    rc = gov.check_refs_exist(
        intent_input=_multi("Home_Loan__c", "Loan_Stage__c", "Risk_Rating__c", MISSING_API),
        ctx=_ctx())
    assert rc.ok, "at least one intent grounds -> Layer A must proceed (partial coverage)"


def test_only_one_valid_still_proceeds(gov):
    """Boundary: exactly one of four resolves -> still proceed. resolve_intent
    grounds the one and records the other three as partial_refusals."""
    rc = gov.check_refs_exist(
        intent_input=_multi("Home_Loan__c", MISSING_API, "X__c", "Y__c"), ctx=_ctx())
    assert rc.ok


def test_all_miss_rejects_for_correction(gov):
    """The all-miss case keeps its correction hop; feedback names each intent."""
    rc = gov.check_refs_exist(intent_input=_multi(MISSING_API, "Nope__c"), ctx=_ctx())
    assert not rc.ok
    assert "intent[0]" in rc.feedback and "intent[1]" in rc.feedback
    assert rc.missing_refs


# ---------------------------------------------------------------------------
# Single-intent (legacy) path is unchanged
# ---------------------------------------------------------------------------

def test_single_valid_ok(gov):
    rc = gov.check_refs_exist(intent_input=_multi("Home_Loan__c"), ctx=_ctx())
    assert rc.ok


def test_single_missing_rejects(gov):
    rc = gov.check_refs_exist(intent_input=_multi(MISSING_API), ctx=_ctx())
    assert not rc.ok
    assert rc.missing_refs


def test_no_version_pinned_rejects(gov):
    rc = gov.check_refs_exist(intent_input=_multi("Home_Loan__c"), ctx=_ctx(at=None))
    assert not rc.ok
