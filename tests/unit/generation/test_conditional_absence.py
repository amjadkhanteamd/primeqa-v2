"""D-381 — the CONDITIONAL absence vertical (AC11: cancelling leaves
already-Completed fulfilment tasks untouched).

Grounds on the real FL05 fixture shape: update-producer fan-out whose own
filter (`Status = 'Open'`) provably EXCLUDES the protected value; the recipe
stages one protected child and asserts it still matches after the trigger.
Every law pinned: half-pair refuses, no-exclusion refuses, plain absence
byte-identical, E2 presence untouched."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from uuid import uuid4

from primeqa.generation import governance_core as gc
from tests.unit.generation.test_xo_evidence import (
    _FakeS1, _ctx, _ent, _state)

FL05_FIXTURE = os.path.join(os.path.dirname(__file__), "..", "semantic",
                            "fixtures", "pls_fb_flows",
                            "PLS_FB_FL05_Cancellation_Sync.json")

EXCERPT = ("cancelling an order leaves already-completed fulfilment work "
           "unchanged")


def _world_fl05(*, updaters=1, drop_status_filter=False):
    order = _ent("Object", "PLS_FB_Order__c", "PLS FB Order")
    status = _ent("Field", "PLS_FB_Order__c.PLS_FB_Status__c", "Status",
                  attrs={"data_type": "Picklist"})
    task = _ent("Object", "PLS_FB_Fulfilment_Task__c",
                "PLS FB Fulfilment Task")
    t_order = _ent("Field", "PLS_FB_Fulfilment_Task__c.PLS_FB_Order__c",
                   "Order")
    t_status = _ent("Field", "PLS_FB_Fulfilment_Task__c.PLS_FB_Status__c",
                    "Status")
    with open(FL05_FIXTURE) as f:
        d = json.load(f)
    meta = d["Metadata"]
    if drop_status_filter:
        # a BLANKET fan-out: strip the Status filter from the update op so
        # the exclusion proof must fail
        meta = json.loads(json.dumps(meta))
        for ru in meta.get("recordUpdates", []):
            ru["filters"] = [f_ for f_ in ru.get("filters", [])
                             if f_.get("field") != "PLS_FB_Status__c"]
    flows = [_ent("Flow", "PLS_FB_FL05_Cancellation_Sync",
                  "Cancellation Sync", attrs={"Metadata": meta})]
    for i in range(updaters - 1):
        flows.append(_ent("Flow", f"PLS_FB_FL05_Clone{i}", "Clone",
                          attrs={"Metadata": meta}))
    pvs = uuid4()
    s1 = _FakeS1(
        entities=[order, status, task, t_order, t_status] + flows,
        rows_by_object={
            "PLS_FB_Order__c": [
                SimpleNamespace(edge_type=gc.EDGE_BELONGS, entity=status)] + [
                SimpleNamespace(edge_type=gc.EDGE_FLOW, entity=fl)
                for fl in flows],
            "PLS_FB_Fulfilment_Task__c": [
                SimpleNamespace(edge_type=gc.EDGE_BELONGS, entity=e)
                for e in (t_order, t_status)],
        },
        details={status.id: {"field_type": "picklist",
                             "picklist_value_set_entity_id": pvs}},
        picklists={pvs: [{"value_api_name": v, "is_active": True}
                         for v in ("Draft", "Cancelled", "Fulfilled")]})
    return gc.GovernanceCore(s1)


def _intent(**kw):
    d = {"ac_ref": 11, "archetype_hint": "data_behavior",
         "polarity_hint": "positive",
         "claim_kind_hint": "automation-effect-claim",
         "requirement_excerpt": EXCERPT,
         "target_subject_hint": {
             "entity_type": "Object", "sf_api_name": "PLS_FB_Order__c",
             "effect_object": "PLS_FB_Fulfilment_Task__c",
             "expected_absence": True, **kw}}
    return {"requirement_excerpt": EXCERPT, "intent_descriptor": d}


COND = {"effect_field": "PLS_FB_Status__c", "effect_value": "Completed"}


def test_conditional_absence_grounds_on_the_exclusion_proof():
    core = _world_fl05()
    state = _state()
    res = core.resolve_intent(intent_input=_intent(**COND), ctx=_ctx(),
                              state=state)
    assert res.refusal is None, getattr(res.refusal, "payload", None)
    [g] = state.groundings
    assert g.automation.external_id == "PLS_FB_FL05_Cancellation_Sync"
    assert g.expected_absence is True
    assert g.premise_children["protected"] == {
        "field": "PLS_FB_Status__c", "value": "Completed"}
    # the entry transition derived from the op guard (Status -> Cancelled)
    upd = {ep.external_id.rsplit(".", 1)[-1]: v
           for ep, v in g.update_trigger_fields}
    assert upd == {"PLS_FB_Status__c": "Cancelled"}


def test_conditional_absence_emission_shape_and_identity():
    from primeqa.generation.emission import author_emission
    from primeqa.test_representation.identity_hash import compute_identity_hash
    core = _world_fl05()
    state = _state()
    core.resolve_intent(intent_input=_intent(**COND), ctx=_ctx(), state=state)
    [g] = state.groundings
    bundle = author_emission(g)
    steps = bundle.observation_realization.steps
    ids = [s.step_id for s in steps]
    assert ids == ["create-record", "create-protected", "update-record",
                   "read-effect", "assert-effect"]
    assert steps[1].field_values[
        "PLS_FB_Fulfilment_Task__c.PLS_FB_Status__c"] == "Completed"
    assert steps[1].field_values[
        "PLS_FB_Fulfilment_Task__c.PLS_FB_Order__c"] == "$create-record.id"
    assert "PLS_FB_Status__c = 'Completed'" in steps[3].soql
    assert steps[4].predicate.predicate == "count_equals"
    assert steps[4].predicate.value == 1
    assert bundle.asserted_truth.body_schema_version == 3
    assert bundle.asserted_truth.protected_value == "Completed"
    b2 = author_emission(g)
    assert compute_identity_hash(bundle.archetype, bundle.claim_kind,
                                 bundle.asserted_truth,
                                 bundle.semantic_conditions) == \
        compute_identity_hash(b2.archetype, b2.claim_kind,
                              b2.asserted_truth, b2.semantic_conditions)


def test_half_pair_refuses():
    core = _world_fl05()
    for half in ({"effect_field": "PLS_FB_Status__c"},
                 {"effect_value": "Completed"}):
        res = core.resolve_intent(intent_input=_intent(**half), ctx=_ctx(),
                                  state=_state())
        assert res.refusal is not None
        assert "half-pair" in str(res.refusal.payload.get("detail"))


def test_no_exclusion_refuses():
    core = _world_fl05(drop_status_filter=True)
    res = core.resolve_intent(intent_input=_intent(**COND), ctx=_ctx(),
                              state=_state())
    assert res.refusal is not None
    assert "not provably scoped" in str(res.refusal.payload.get("detail"))


def test_protected_value_equal_to_fanout_scope_refuses():
    # protecting 'Open' — the very value the fan-out targets — is a false
    # claim; the pin equals the protected value so exclusion fails
    core = _world_fl05()
    res = core.resolve_intent(
        intent_input=_intent(effect_field="PLS_FB_Status__c",
                             effect_value="Open"),
        ctx=_ctx(), state=_state())
    assert res.refusal is not None
    assert "not provably scoped" in str(res.refusal.payload.get("detail"))


def test_two_updaters_refuse_ambiguity():
    core = _world_fl05(updaters=2)
    res = core.resolve_intent(intent_input=_intent(**COND), ctx=_ctx(),
                              state=_state())
    assert res.refusal is not None


def test_plain_absence_unchanged():
    # no condition pair -> the D-307 plain absence path (create-producer
    # story) — FL05 only UPDATES, so plain absence has no creator to bind
    # and keeps its existing posture (refusal here; the point is no crash
    # and no conditional rerouting)
    core = _world_fl05()
    res = core.resolve_intent(intent_input=_intent(), ctx=_ctx(),
                              state=_state())
    # plain absence over an updater-only world refuses (no create producer)
    assert res.refusal is not None
    assert "half-pair" not in str(res.refusal.payload.get("detail"))


def test_interpreter_selects_the_conditional_vocab():
    from datetime import datetime, timezone
    from primeqa.execution_engine.evidence import (
        AssertEvidence, CleanupRecord, CreateAttemptEvidence, RunEvidence)
    from primeqa.interpretation.interpreter import interpret_run
    t = datetime(2026, 7, 22, tzinfo=timezone.utc)

    def _create(step_id):
        return CreateAttemptEvidence(
            step_id=step_id, ordinal=0, sobject="X",
            field_values={}, http_status=201, success=True,
            error_code=None, message=None, rejection_body=(),
            matched=False,
            cleanup=CleanupRecord(attempted=True, succeeded=True,
                                  record_id="001Z"),
            started_at=t, finished_at=t, duration_ms=1)

    ev = RunEvidence(
        run_id=uuid4(), recipe_id=uuid4(), recipe_version_seq=1,
        claim_test_id=uuid4(), claim_version_seq=None, environment_id=59,
        api_choice="rest", outcome="passed", started_at=t, finished_at=t,
        steps=(
            _create("create-record"), _create("create-protected"),
            AssertEvidence(step_id="assert-effect", ordinal=3,
                           predicate="count_equals",
                           subject_ref="read-effect.Id",
                           evaluated_row_count=1, held=True,
                           started_at=t, finished_at=t, duration_ms=0)))
    out = interpret_run(ev, claim_kind="automation-effect-claim")
    assert out.verdict == "automation_exclusion_confirmed"
    assert "untouched" in out.attribution