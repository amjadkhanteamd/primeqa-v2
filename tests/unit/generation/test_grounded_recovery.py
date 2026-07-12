"""B0 Grounded Recovery — unit corpus (no PG, no LLM).

Covers: the pure ranking engine (determinism, thresholds, boundedness, the
FB-V1 job-76 shapes), the AdmissibilityEngine producer over a fake S1
(Resolved / Not Found / Candidate Set contract, Field-via-owner scoping, the
oversized-pool fail-safe), the Layer-A feedback wiring (candidates + offers,
byte-stable-when-no-candidates), and telemetry provenance (model-authored vs
substrate-authored reasons on payloads and the coverage map)."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from primeqa.generation import recovery
from primeqa.generation import governance_core as gc
from primeqa.generation.enums import RefusalKind


# ---------------------------------------------------------------------------
# Pure engine — ranking, thresholds, determinism
# ---------------------------------------------------------------------------

FB_OBJECT_POOL = [
    ("PLS_FB_Order__c", "PLS FB Order"),
    ("PLS_FB_Order_Line__c", "PLS FB Order Line"),
    ("PLS_FB_Fulfilment_Task__c", "PLS FB Fulfilment Task"),
    ("PLS_FB_Ledger_Entry__c", "PLS FB Ledger Entry"),
    ("PLS_FB_Audit_Log__c", "PLS FB Audit Log"),
    ("Task", "Task"),
    ("Opportunity", "Opportunity"),
]


def test_job76_object_guess_recovers():
    """The observed FB-V1 failure: the model guessed 'Order__c' for the object
    labelled 'PLS FB Order' — the real object must rank first."""
    cands = recovery.rank_candidates("Order__c", FB_OBJECT_POOL)
    assert cands, "expected candidates for the near-miss guess"
    assert cands[0].sf_api_name == "PLS_FB_Order__c"
    assert all(c.score >= recovery.DEFAULT_MIN_SCORE for c in cands)


def test_context_text_ranks_the_requirements_own_object_first():
    """The final-gate run-3 failure: for the generic guess 'Order__c', pure
    similarity ranks the standard Order / WorkOrder / a packaged Service Order
    above PLS_FB_Order__c (which fell off the limit) and the model anchored on
    a plausible-wrong object. With the requirement text as the grounded
    relatedness signal, the requirement's own object ranks first."""
    pool = FB_OBJECT_POOL + [
        ("WorkOrder", "Work Order"),
        ("CHANNEL_ORDERS__Service_Order__c", "Service Order"),
        ("Order", "Order"),
    ]
    req_text = ("As a Sales Operations Manager, I want PLS FB Order records "
                "to be maintained automatically as they move through the "
                "order lifecycle...")
    without_ctx = recovery.rank_candidates("Order__c", pool)
    assert without_ctx[0].sf_api_name == "Order"   # honest but unhelpful
    with_ctx = recovery.rank_candidates("Order__c", pool,
                                        context_text=req_text)
    assert with_ctx[0].sf_api_name == "PLS_FB_Order__c"
    # determinism with context too
    assert with_ctx == recovery.rank_candidates(
        "Order__c", list(reversed(pool)), context_text=req_text)


# ---------------------------------------------------------------------------
# B0.1 — exact-label affinity (order-only; the five pinned tests)
# ---------------------------------------------------------------------------

B01_POOL = FB_OBJECT_POOL + [
    ("WorkOrder", "Work Order"),
    ("CHANNEL_ORDERS__Service_Order__c", "Service Order"),
    ("Order", "Order"),
]
B01_REQ = ("As a Sales Operations Manager, I want PLS FB Order records to be "
           "maintained automatically as they move through the order "
           "lifecycle... shows the up-to-date total value and count of its "
           "line items as lines are added or amended. External references "
           "may be typed in any casing.")


def test_b01_exact_label_promotion():
    """The live miss trajectory (runs 44178a2b / c9c6fc99): guess 'Order__c'
    against a requirement whose text contains the label 'PLS FB Order'
    verbatim — the requirement's own object must rank FIRST, above the
    incidental 'line items' token match and the generically-similar
    standard objects."""
    cands = recovery.rank_candidates("Order__c", B01_POOL,
                                     context_text=B01_REQ)
    assert cands[0].sf_api_name == "PLS_FB_Order__c"


def test_b01_no_label_in_text_keeps_pre_b01_ordering():
    """When NO admitted candidate's label appears verbatim in the context,
    every phrase bonus is 0 and the ordering must be byte-identical to the
    pre-B0.1 blend (order-only guarantee)."""
    pool = [("Alpha_Record__c", "Alpha Record"),
            ("Beta_Record__c", "Beta Record"),
            ("Record__c", "Record")]
    # "record" appears in the prose as a common noun, but the single-token
    # guard means NO candidate earns a phrase bonus here — ordering must be
    # byte-identical to the pre-B0.1 blend (main's actual output, pinned).
    ctx = "the record of the beta stream is kept current"
    got = [(c.sf_api_name, c.score) for c in
           recovery.rank_candidates("Record__c", pool, context_text=ctx)]
    assert got == [("Beta_Record__c", 0.8205), ("Record__c", 0.75),
                   ("Alpha_Record__c", 0.5595)]
    # and all phrase bonuses were genuinely zero (single-token guard)
    assert all(recovery._phrase_tokens(lbl, ctx) == 0 for _, lbl in pool)


def test_b01_competing_lexical_matches_longer_label_wins():
    """Both 'Order' and 'PLS FB Order' occur verbatim in the text — the more
    specific (longer) label outranks the generic one."""
    cands = recovery.rank_candidates("Order__c", B01_POOL,
                                     context_text=B01_REQ, limit=10)
    apis = [c.sf_api_name for c in cands]
    assert apis.index("PLS_FB_Order__c") < apis.index("Order")
    assert recovery._phrase_tokens("PLS FB Order", B01_REQ) == 3
    # single-token guard: the generic 'Order' label earns NO phrase bonus
    assert recovery._phrase_tokens("Order", B01_REQ) == 0


def test_b01_field_recovery_unchanged_and_word_bounded():
    """Field offers carry no verbatim-label hits here, so their ordering is
    untouched; and the phrase test is word-bounded — 'External Ref' must NOT
    match inside 'External references'."""
    pool = [("PLS_FB_Order__c.PLS_FB_External_Ref__c", "External Ref"),
            ("PLS_FB_Order__c.PLS_FB_Priority__c", "Priority"),
            ("PLS_FB_Order__c.PLS_FB_Amount__c", "Amount")]
    with_ctx = recovery.rank_candidates(
        "Order__c.External_Reference__c", pool, context_text=B01_REQ)
    without = recovery.rank_candidates(
        "Order__c.External_Reference__c", pool)
    assert [c.sf_api_name for c in with_ctx][0] == \
        [c.sf_api_name for c in without][0] == \
        "PLS_FB_Order__c.PLS_FB_External_Ref__c"
    assert recovery._phrase_tokens("External Ref", B01_REQ) == 0
    assert recovery._phrase_tokens("PLS FB Order", "the PLS FB Order record") == 3
    assert recovery._phrase_tokens("Order", "an order raised") == 0  # 1-token guard


def test_b01_deterministic_under_permutation():
    a = recovery.rank_candidates("Order__c", B01_POOL, context_text=B01_REQ)
    b = recovery.rank_candidates("Order__c", list(reversed(B01_POOL)),
                                 context_text=B01_REQ)
    assert a == b


def test_unrelated_pool_yields_nothing():
    """A garbage reference must NOT produce a directory listing."""
    assert recovery.rank_candidates("Zebra_Quantum__c", FB_OBJECT_POOL) == ()


def test_unrelated_entities_never_exposed():
    """Related candidates only: the Ledger/Audit objects never ride along on a
    priority-field-flavoured guess."""
    got = {c.sf_api_name for c in recovery.rank_candidates("Order__c", FB_OBJECT_POOL)}
    assert "PLS_FB_Ledger_Entry__c" not in got
    assert "PLS_FB_Audit_Log__c" not in got
    assert "Task" not in got


def test_field_guess_recovers_qualified():
    """The job-76 field shape: 'External_Reference__c' vs the stored
    'PLS_FB_Order__c.PLS_FB_External_Ref__c' (qualified pool)."""
    pool = [
        ("PLS_FB_Order__c.PLS_FB_External_Ref__c", "External Ref"),
        ("PLS_FB_Order__c.PLS_FB_Priority__c", "Priority"),
        ("PLS_FB_Order__c.PLS_FB_Customer_Email__c", "Customer Email"),
        ("PLS_FB_Order__c.PLS_FB_Amount__c", "Amount"),
    ]
    cands = recovery.rank_candidates("Order__c.External_Reference__c", pool)
    assert cands and cands[0].sf_api_name == "PLS_FB_Order__c.PLS_FB_External_Ref__c"


def test_typo_recovers_via_trigrams():
    """Generalisation beyond FB-V1 shapes: a typo'd ApprovalProcess name."""
    pool = [("PLS_FB_Large_Order_Approval", "Large Order Approval"),
            ("SomeOther_Process", "Some Other Process")]
    cands = recovery.rank_candidates("PLS_FB_Large_Order_Aproval", pool)
    assert cands and cands[0].sf_api_name == "PLS_FB_Large_Order_Approval"


def test_ranking_is_deterministic_and_bounded():
    pool = [(f"PLS_FB_Order_{i}__c", None) for i in range(10)] + [
        ("PLS_FB_Order__c", "PLS FB Order")]
    a = recovery.rank_candidates("Order__c", pool)
    b = recovery.rank_candidates("Order__c", list(reversed(pool)))
    assert a == b, "ranking must not depend on pool order"
    assert len(a) <= recovery.DEFAULT_LIMIT


def test_equal_scores_break_ties_lexicographically():
    pool = [("Order_B__c", None), ("Order_A__c", None)]
    cands = recovery.rank_candidates("Order__c", pool)
    assert [c.sf_api_name for c in cands] == sorted(c.sf_api_name for c in cands) \
        or len({c.score for c in cands}) == len(cands)


def test_format_candidates_offers_choice_not_conclusion():
    cands = recovery.rank_candidates("Order__c", FB_OBJECT_POOL)
    text = recovery.format_candidates(cands)
    assert "PLS_FB_Order__c" in text
    assert "re-propose" in text and "refuse honestly" in text
    assert recovery.format_candidates(()) == ""


def test_offer_payload_carries_substrate_provenance():
    cands = recovery.rank_candidates("Order__c", FB_OBJECT_POOL)
    p = recovery.offer_payload("Object", "Order__c", cands)
    assert p["source"] == "substrate"
    assert p["proposed"] == "Order__c"
    assert p["candidates"][0]["sf_api_name"] == "PLS_FB_Order__c"


# ---------------------------------------------------------------------------
# Fake S1 + AdmissibilityEngine producer (the recovery contract)
# ---------------------------------------------------------------------------

def _ent(entity_type, api, label=None, attrs=None):
    return SimpleNamespace(id=uuid4(), entity_type=entity_type,
                           sf_api_name=api, display_name=label,
                           attributes=attrs or {})


class FakeS1:
    """Minimal SemanticOrgModel stand-in: exact-name resolution + typed
    population reads + a BELONGS_TO/TRIGGERS_ON neighborhood for one object."""

    def __init__(self, entities, fields_by_object=None, flows_by_object=None):
        self._entities = entities
        self._fields_by_object = fields_by_object or {}
        self._flows_by_object = flows_by_object or {}

    def get_entities(self, entity_type, at_seq, filters=None):
        out = [e for e in self._entities if e.entity_type == entity_type]
        if filters and "sf_api_name" in filters:
            out = [e for e in out if e.sf_api_name == filters["sf_api_name"]]
        return out

    def get_related(self, subject_id, edge_types, direction, at_seq):
        rows = []
        for obj_api, fields in self._fields_by_object.items():
            owner = next((e for e in self._entities
                          if e.sf_api_name == obj_api), None)
            if owner is not None and owner.id == subject_id:
                rows = [SimpleNamespace(edge_type=gc.EDGE_BELONGS, entity=f)
                        for f in fields]
        for obj_api, flows in self._flows_by_object.items():
            owner = next((e for e in self._entities
                          if e.sf_api_name == obj_api), None)
            if owner is not None and owner.id == subject_id:
                rows += [SimpleNamespace(edge_type=gc.EDGE_FLOW, entity=f)
                         for f in flows]
        return rows


FB_S1 = FakeS1(
    entities=[_ent("Object", api, label) for api, label in FB_OBJECT_POOL]
    + [_ent("Flow", "PLS_FB_FL01_Default_Priority", "Default Priority"),
       _ent("ApprovalProcess", "PLS_FB_Large_Order_Approval", "Large Order Approval"),
       _ent("PermissionSet", "PLS_FB_Access", "PLS FB Access"),
       _ent("ValidationRule", "PLS_FB_Order__c.PLS_FB_VR01_External_Ref_Format",
            "External Ref Format")],
    fields_by_object={"PLS_FB_Order__c": [
        _ent("Field", "PLS_FB_Order__c.PLS_FB_External_Ref__c", "External Ref"),
        _ent("Field", "PLS_FB_Order__c.PLS_FB_Priority__c", "Priority"),
        _ent("Field", "PLS_FB_Order__c.PLS_FB_Amount__c", "Amount"),
    ]},
)


def test_recover_reference_resolved_short_circuits():
    eng = gc.AdmissibilityEngine(FB_S1)
    r = eng.recover_reference("Object", "PLS_FB_Order__c", 128)
    assert r.status == recovery.RESOLVED and r.candidates == ()


def test_recover_reference_candidates_for_near_miss():
    eng = gc.AdmissibilityEngine(FB_S1)
    r = eng.recover_reference("Object", "Order__c", 128)
    assert r.status == recovery.CANDIDATES
    assert r.candidates[0].sf_api_name == "PLS_FB_Order__c"


def test_recover_reference_not_found_for_garbage():
    eng = gc.AdmissibilityEngine(FB_S1)
    r = eng.recover_reference("Object", "Quantum_Zebra__c", 128)
    assert r.status == recovery.NOT_FOUND and r.candidates == ()


def test_recover_reference_never_supplies_automation_names():
    """Flow / ApprovalProcess are EXCLUDED from recovery: supplying an
    automation name lets the D-299 name-trust binding attach it WITHOUT
    effect verification (the live-observed wrong-attribution class). The
    LLM never names automations (D-318); the substrate must not teach it to."""
    eng = gc.AdmissibilityEngine(FB_S1)
    assert eng.recover_reference(
        "ApprovalProcess", "PLS_FB_Large_Order_Aproval", 128).status \
        == recovery.NOT_FOUND
    assert eng.recover_reference(
        "Flow", "PLS_FB_FL01_Default_Priorty", 128).status == recovery.NOT_FOUND


def test_recovery_boundary_is_an_allowlist():
    """D-362: the boundary is an ALLOWLIST — behavioural entities require
    behavioural verification, so lexical recovery is offered only for the
    documented lexical types; anything else (including lexical-ish types not
    on the list, and any future entity type) defaults to non-recoverable."""
    assert gc.AdmissibilityEngine._RECOVERY_ALLOWED_TYPES == frozenset({
        "Object", "Field", "RecordType", "ValidationRule", "PermissionSet",
        "CustomMetadata"})
    assert gc.AdmissibilityEngine._RECOVERY_BEHAVIOURAL_TYPES == frozenset({
        "Flow", "ApprovalProcess", "ApexClass", "InvocableAction"})
    eng = gc.AdmissibilityEngine(FB_S1)
    # not on the allowlist -> not recoverable, even though pools would exist
    assert eng.recover_reference("Layout", "Order_Layot", 128).status \
        == recovery.NOT_FOUND
    assert eng.recover_reference("ApexClass", "OrderServce", 128).status \
        == recovery.NOT_FOUND


def test_recover_reference_generalises_to_permission_set():
    """Quality-gate shape: a non-Object, non-automation metadata entity type
    (PermissionSet) recovers normally."""
    eng = gc.AdmissibilityEngine(FB_S1)
    r = eng.recover_reference("PermissionSet", "PLS_FB_Acces", 128)
    assert r.status == recovery.CANDIDATES
    assert r.candidates[0].sf_api_name == "PLS_FB_Access"


def test_recover_reference_generalises_to_validation_rule():
    eng = gc.AdmissibilityEngine(FB_S1)
    r = eng.recover_reference(
        "ValidationRule", "PLS_FB_Order__c.VR01_External_Ref_Format", 128)
    assert r.status == recovery.CANDIDATES
    assert (r.candidates[0].sf_api_name
            == "PLS_FB_Order__c.PLS_FB_VR01_External_Ref_Format")


def test_recover_reference_field_scopes_through_owner():
    eng = gc.AdmissibilityEngine(FB_S1)
    r = eng.recover_reference(
        "Field", "PLS_FB_Order__c.External_Reference__c", 128)
    assert r.status == recovery.CANDIDATES
    assert r.candidates[0].sf_api_name == "PLS_FB_Order__c.PLS_FB_External_Ref__c"


def test_recover_reference_field_unqualified_is_not_found():
    eng = gc.AdmissibilityEngine(FB_S1)
    assert eng.recover_reference("Field", "External_Reference__c", 128).status \
        == recovery.NOT_FOUND


def test_recover_reference_oversized_pool_fails_safe():
    big = FakeS1([_ent("Object", f"Obj_{i}__c") for i in range(2000)])
    eng = gc.AdmissibilityEngine(big)
    assert eng.recover_reference("Object", "Obj__c", 1).status == recovery.NOT_FOUND


def test_recover_reference_never_raises():
    class Exploding:
        def get_entities(self, *a, **k):
            raise RuntimeError("boom")
    eng = gc.AdmissibilityEngine(Exploding())
    assert eng.recover_reference("Object", "X__c", 1).status == recovery.NOT_FOUND


# ---------------------------------------------------------------------------
# Layer-A wiring — candidates in feedback, offers on RefCheck
# ---------------------------------------------------------------------------

def _core(s1=FB_S1):
    return gc.GovernanceCore(s1)


def _ctx(at=128):
    return SimpleNamespace(semantic_context=SimpleNamespace(s1_version_seq=at))


def _propose(et, api, extra=None):
    d = {"intent_descriptor": {
        "archetype_hint": "data_behavior", "claim_kind_hint": "value-claim",
        "requirement_excerpt": "x",
        "target_subject_hint": {"entity_type": et, "sf_api_name": api}}}
    if extra:
        d["intent_descriptor"].update(extra)
    return d


def test_layer_a_miss_offers_candidates_in_feedback():
    rc = _core().check_refs_exist(
        intent_input=_propose("Object", "Order__c"), ctx=_ctx())
    assert not rc.ok
    assert "no Object named 'Order__c' exists at s1_version_seq 128." in rc.feedback
    assert "PLS_FB_Order__c" in rc.feedback
    assert "refuse honestly" in rc.feedback
    assert rc.offers and rc.offers[0]["source"] == "substrate"
    assert rc.offers[0]["candidates"][0]["sf_api_name"] == "PLS_FB_Order__c"


def test_layer_a_miss_without_near_miss_stays_plain():
    rc = _core().check_refs_exist(
        intent_input=_propose("Object", "Quantum_Zebra__c"), ctx=_ctx())
    assert not rc.ok
    assert rc.feedback.endswith(
        "no Object named 'Quantum_Zebra__c' exists at s1_version_seq 128.")
    assert rc.offers == []


def test_layer_a_resolved_ref_unchanged():
    rc = _core().check_refs_exist(
        intent_input=_propose("Object", "PLS_FB_Order__c"), ctx=_ctx())
    assert rc.ok and rc.offers == []


def test_layer_a_multi_intent_all_miss_aggregates_offers():
    """All-miss multi-intent rejection aggregates each intent's offer. The
    second guess ('Orders__c', pluralized) scores 0.21 — below threshold — so
    it correctly yields NO offer (documented v1 boundary: pluralization is a
    trigram-weak case; loosening the threshold to catch it would start
    exposing unrelated entities elsewhere)."""
    intent = {"intent_descriptors": [
        _propose("Object", "Order__c")["intent_descriptor"],
        _propose("Object", "Orders__c")["intent_descriptor"],
    ]}
    rc = _core().check_refs_exist(intent_input=intent, ctx=_ctx())
    assert not rc.ok
    assert len(rc.offers) == 1
    assert rc.offers[0]["proposed"] == "Order__c"
    assert rc.offers[0]["candidates"][0]["sf_api_name"] == "PLS_FB_Order__c"
    assert "Orders__c" in rc.feedback   # the miss itself is still reported


def test_no_admissible_test_skips_recovery_and_marks_model_prose():
    core = _core()
    res = core.resolve_intent(
        intent_input={"intent_descriptor": {
            "ac_ref": 1, "archetype_hint": "data_behavior",
            "claim_kind_hint": "value-claim", "no_admissible_test": True,
            "no_admissible_test_reason": "could not be resolved at the pinned version",
            "requirement_excerpt": "x",
            "target_subject_hint": {"entity_type": "Object",
                                    "sf_api_name": "PLS_FB_Order__c"}}},
        ctx=_ctx(), state=None)
    assert res.refusal is not None
    assert res.refusal.refusal_kind == RefusalKind.NO_RELEVANT_CONTEXT
    assert res.refusal.payload["detail_source"] == "model"
    assert res.refusal.payload["detail_layer"] == "resolution"


def test_router_substrate_details_are_tagged():
    """Both provenance dimensions on every router payload: origin
    (substrate|model) and layer (resolution|grounding|admissibility|execution)."""
    r = gc.RefusalRouter()
    nrc = r.no_relevant_context("x").payload
    assert nrc["detail_source"] == "substrate"
    assert nrc["detail_layer"] == "resolution"
    assert r.no_relevant_context("x", layer="grounding").payload[
        "detail_layer"] == "grounding"
    und = r.underspecified().payload
    assert und["detail_source"] == "substrate"
    assert und["detail_layer"] == "resolution"
    emd = r.emission_deferred("a", "k").payload
    assert emd["detail_source"] == "substrate"
    assert emd["detail_layer"] == "grounding"
    bhi = r.behaviour_incomplete("x").payload
    assert bhi["detail_source"] == "substrate"
    assert bhi["detail_layer"] == "grounding"


def test_from_dismissed_carries_recovery_offer():
    r = gc.RefusalRouter()
    cand = gc._Candidate(
        path_id="c0", archetype="data_behavior", claim_kind="value-claim",
        subject_refs=[{"entity_type": "Object", "sf_api_name": "PLS_FB_Order__c"}],
        requirement_anchor="x", status="dismissed",
        dismissal_reason="insufficient_grounding",
        recovery={"entity_type": "Field", "proposed": "Priority__c",
                  "candidates": [{"sf_api_name": "PLS_FB_Order__c.PLS_FB_Priority__c",
                                  "display_name": "Priority", "score": 0.5}],
                  "source": "substrate"})
    d = r.from_dismissed(cand, is_negative=False)
    assert d.refusal_kind == RefusalKind.UNGROUNDED_CLAIM
    assert d.payload["detail_source"] == "substrate"
    assert d.payload["detail_layer"] == "admissibility"
    assert d.payload["candidates"]["proposed"] == "Priority__c"
    # to_path stays byte-stable — recovery never rides candidate_paths
    assert "recovery" not in cand.to_path()


def test_evaluate_positive_attaches_field_recovery():
    """A value-claim naming a near-miss field gets a recovery offer on its
    dismissed candidate (rides the ungrounded payload -> recovery re-prompt)."""
    eng = gc.AdmissibilityEngine(FB_S1)
    subject = FB_S1.get_entities("Object", 128,
                                 {"sf_api_name": "PLS_FB_Order__c"})[0]
    neighborhood = FB_S1.get_related(subject.id, None, "inbound", 128)
    cand = eng.evaluate(
        archetype="data_behavior", claim_kind="value-claim",
        polarity_hint="positive", subject=subject, neighborhood=neighborhood,
        excerpt="x", field_hint="PLS_FB_Order__c.Priority__c")
    assert cand.status == "dismissed"
    assert cand.dismissal_reason == "insufficient_grounding"
    assert cand.recovery is not None
    assert (cand.recovery["candidates"][0]["sf_api_name"]
            == "PLS_FB_Order__c.PLS_FB_Priority__c")


def test_evaluate_positive_never_offers_automation_candidates():
    """An automation-effect intent whose automation hint fails Layer-1 gets NO
    recovery offer — the flow-binding gap stays honestly refused (B1's job)."""
    eng = gc.AdmissibilityEngine(FB_S1)
    subject = FB_S1.get_entities("Object", 128,
                                 {"sf_api_name": "PLS_FB_Order__c"})[0]
    neighborhood = FB_S1.get_related(subject.id, None, "inbound", 128)
    cand = eng.evaluate(
        archetype="data_behavior", claim_kind="automation-effect-claim",
        polarity_hint="positive", subject=subject, neighborhood=neighborhood,
        excerpt="x", automation_hint="Order_Priority_Defaulting_Automation",
        field_hint="PLS_FB_Order__c.PLS_FB_Priority__c",
        effect_value_hint="Standard")
    assert cand.status == "dismissed"
    assert cand.recovery is None





