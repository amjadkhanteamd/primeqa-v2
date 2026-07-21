"""D-376 shadow resolution — governance wiring (read-only posture).

Mirrors ``test_control_coverage_wiring.py``: the stash accumulates verdicts on
the state; finalize AND route_refusal attach
``attempted_interpretation["shadow_resolution"]`` when verdicts exist and
attach NOTHING when they don't; the explanation_hash is identical either way;
and a raising shadow path never changes a ``_resolve_one`` outcome.

DB-free: ``GovernanceCore.__init__`` performs no reads (test-pinned)."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from primeqa.generation import shadow_resolution as sr
from primeqa.generation.emission import GroundedExistence, _Endpoint
from primeqa.generation.enums import AdmissibilityLayer
from primeqa.generation.governance import ConversationContext, NextAction, PresentedCandidate
from primeqa.generation.governance_core import GovernanceCore
from primeqa.generation.protocol import (
    BudgetSpec, GovernanceContext, OperationalContext, SemanticContext)


def _ctx():
    return ConversationContext(
        request_id=uuid4(),
        requirement_ref={"key": "req-t", "text": "t"},
        requirement_text="high priority PLS FB Order records escalate",
        semantic_context=SemanticContext(
            requirement_refs=[{"key": "req-t", "text": "t"}],
            s1_version_seq=1, s1_version_name="v1"),
        governance_context=GovernanceContext(),
        operational_context=OperationalContext(budgets=BudgetSpec()))


def _grounding():
    return GroundedExistence(
        archetype="configuration", claim_kind="existence-claim", version_seq=1,
        subject=_Endpoint(entity_id=uuid4(), entity_type="Field",
                          external_id="PLS_FB_Order__c.PLS_FB_Priority__c"),
        requirement_excerpt="x")


def _state(shadow_verdicts=None):
    return SimpleNamespace(
        groundings=[_grounding()],
        presented_candidates=[PresentedCandidate("c0", AdmissibilityLayer.LAYER_1)],
        attempted_interpretation={
            "candidate_paths": [{"path_id": "c0"}],
            "dismissed_alternatives_by_reason": {},
            "selected_path_id": None,
        },
        shadow_verdicts=shadow_verdicts)


def _verdict(**over):
    base = {
        "shadow_version": 1, "term": "Order__c", "claim_kind": "value-claim",
        "ac_ref": "AC1",
        "actual": {"outcome": "resolved", "sf_api_name": "Order"},
        "shadow": {"grade": "bound_unique", "winner": "PLS_FB_Order__c",
                   "structural_coverage": [2, 2], "runner_up": None,
                   "field_mentions": ["Priority__c"], "model_binds": 0,
                   "winner_binds": 1},
        "agreement": "conflict", "would_veto": True,
        "connected_org_id": None, "s1_version_seq": 1,
    }
    base.update(over)
    return base


# -- the finalize attach ------------------------------------------------------

def test_finalize_attaches_map_when_verdicts_stashed():
    gov = GovernanceCore(None)
    ov = gov.finalize_outcome(outcome_input={}, ctx=_ctx(),
                              state=_state(shadow_verdicts=[_verdict()]))
    ai = ov.outcome.attempted_interpretation.model_dump()
    m = ai["shadow_resolution"]
    assert m["version"] == sr.SHADOW_VERSION
    assert m["counts"] == {"conflict": 1, "would_veto": 1, "total": 1}
    assert m["verdicts"][0]["shadow"]["winner"] == "PLS_FB_Order__c"


def test_finalize_without_verdicts_attaches_nothing():
    gov = GovernanceCore(None)
    ov = gov.finalize_outcome(outcome_input={}, ctx=_ctx(), state=_state())
    ai = ov.outcome.attempted_interpretation.model_dump()
    assert "shadow_resolution" not in ai


def test_finalize_hash_identical_with_and_without_map():
    gov = GovernanceCore(None)
    with_map = gov.finalize_outcome(outcome_input={}, ctx=_ctx(),
                                    state=_state(shadow_verdicts=[_verdict()]))
    without = gov.finalize_outcome(outcome_input={}, ctx=_ctx(), state=_state())
    assert with_map.outcome.explanation_hash == without.outcome.explanation_hash


# -- the route_refusal attach -------------------------------------------------

def test_route_refusal_attaches_and_does_not_mutate_state():
    from primeqa.generation.enums import RefusalKind
    from primeqa.generation.governance import RefusalDirective
    gov = GovernanceCore(None)
    state = _state(shadow_verdicts=[_verdict()])
    out = gov.route_refusal(
        directive=RefusalDirective(RefusalKind.UNGROUNDED_CLAIM, {"d": 1}),
        ctx=_ctx(), state=state)
    ai = out.attempted_interpretation.model_dump()
    assert ai["shadow_resolution"]["counts"]["total"] == 1
    # the state's own dict was copied, never mutated
    assert "shadow_resolution" not in state.attempted_interpretation


def test_route_refusal_hash_identical_with_and_without_map():
    from primeqa.generation.enums import RefusalKind
    from primeqa.generation.governance import RefusalDirective
    gov = GovernanceCore(None)
    d = RefusalDirective(RefusalKind.UNGROUNDED_CLAIM, {"d": 1})
    with_map = gov.route_refusal(directive=d, ctx=_ctx(),
                                 state=_state(shadow_verdicts=[_verdict()]))
    without = gov.route_refusal(directive=d, ctx=_ctx(), state=_state())
    assert with_map.explanation_hash == without.explanation_hash


# -- the real _resolve_one path: observation + no-interference ----------------

def _resolve_one_args():
    return ({"intent_descriptor": {
                 "archetype_hint": "data_behavior",
                 "claim_kind_hint": "prohibition-claim",
                 "polarity_hint": "negative",
                 "target_subject_hint": {"entity_type": "Object",
                                         "sf_api_name": "PLS_BM_Deal__c"}},
             "requirement_excerpt": "Deal values must be commercially valid."},
            _ctx())


def test_resolve_one_observes_via_the_hook(monkeypatch):
    """The hook sits right after resolve_subject: a resolved subject produces
    a stashed verdict even when the intent later refuses."""
    gov = GovernanceCore(None)
    subject = SimpleNamespace(id=uuid4(), entity_type="Object",
                              sf_api_name="PLS_BM_Deal__c")
    monkeypatch.setattr(gov._admit, "resolve_subject",
                        lambda et, api, at: [subject])
    monkeypatch.setattr(gov._admit, "scoped_neighborhood", lambda subj, at: [])
    observed = {}

    def fake_observe(s1, tables, desc, excerpt, ctx, matches, state):
        observed["matches"] = list(matches)
        sr._stash_shadow_verdict(state, _verdict())

    monkeypatch.setattr(sr, "observe_subject_resolution", fake_observe)
    intent_input, ctx = _resolve_one_args()
    state = SimpleNamespace(attempted_interpretation={"candidate_paths": []},
                            groundings=[])
    res = gov._resolve_one(intent_input, ctx, state)
    assert res.next_action == NextAction.REFUSE          # intent refused...
    assert observed["matches"] == [subject]              # ...hook saw matches
    assert len(state.shadow_verdicts) == 1               # ...verdict stashed


def test_resolve_one_is_unaffected_by_a_raising_shadow(monkeypatch):
    """No-interference: the shadow path raising must not change the outcome."""
    gov_a = GovernanceCore(None)
    gov_b = GovernanceCore(None)
    subject = SimpleNamespace(id=uuid4(), entity_type="Object",
                              sf_api_name="PLS_BM_Deal__c")
    for gov in (gov_a, gov_b):
        monkeypatch.setattr(gov._admit, "resolve_subject",
                            lambda et, api, at: [subject])
        monkeypatch.setattr(gov._admit, "scoped_neighborhood",
                            lambda subj, at: [])

    def boom(*a, **k):
        raise RuntimeError("shadow exploded")

    monkeypatch.setattr(sr, "observe_subject_resolution", boom)
    intent_input, ctx = _resolve_one_args()
    state_a = SimpleNamespace(attempted_interpretation={"candidate_paths": []},
                              groundings=[])
    res_a = gov_a._resolve_one(intent_input, ctx, state_a)

    monkeypatch.setattr(sr, "observe_subject_resolution",
                        lambda *a, **k: None)
    state_b = SimpleNamespace(attempted_interpretation={"candidate_paths": []},
                              groundings=[])
    res_b = gov_b._resolve_one(intent_input, ctx, state_b)

    assert res_a.next_action == res_b.next_action
    assert res_a.refusal.refusal_kind == res_b.refusal.refusal_kind
    assert getattr(state_a, "shadow_verdicts", None) is None


def test_hydration_failure_is_cached_not_retried(monkeypatch):
    """A failing S1 disables shadow for that seq after ONE attempt."""
    calls = {"n": 0}

    class FailingSource:
        def __init__(self, model):
            pass

        def symbol_table(self, at_seq):
            calls["n"] += 1
            raise RuntimeError("no S1")

    monkeypatch.setattr(sr, "S1KnowledgeSource", FailingSource)
    tables: dict = {}
    assert sr._table_for(object(), tables, 5) is None
    assert sr._table_for(object(), tables, 5) is None
    assert calls["n"] == 1


def test_governance_init_performs_no_reads():
    gov = GovernanceCore(None)                 # None model must be fine
    assert gov._shadow_tables == {}
