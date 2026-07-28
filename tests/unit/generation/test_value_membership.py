"""D-412 value-membership validator — every verdict branch pinned.

The three-verdict contract (D-399.1) is the thing under test: a capture gap
must yield CANNOT_VALIDATE, never INVALID — a false refusal is silent, a
wrong-red is loud, and the validator must never trade the loud failure for
the silent one.
"""
from __future__ import annotations

import pytest

from primeqa.generation.value_membership import (
    FieldCaptureIndex,
    ValueMembershipError,
    Verdict,
    _FieldCapture,
    extract_field_literals,
)


def _index(**fields):
    return FieldCaptureIndex({k: v for k, v in fields.items()})


def _fc(cap, values=(), ft="picklist"):
    return _FieldCapture(field_type=ft, capture=cap, values=tuple(values))


LOAN = {"Opportunity.Loan_Type__c": _fc("inline", [
    ("Home", "Home", True), ("Personal", "Personal", True),
    ("Business", "Business", True),
])}


# ---------------------------------------------------------------- VALID --

def test_valid_on_api_name_match():
    idx = _index(**LOAN)
    [c] = idx.check_literal("Opportunity.Loan_Type__c", "Home")
    assert c.verdict == Verdict.VALID and c.detail == "api_name"


def test_valid_on_label_only_match_and_reports_it():
    """api 'BestCase' / label 'Best Case': a literal quoting the LABEL is a
    member — but the check must say it matched on the label, because the
    transport payload may still need the api-name spelling."""
    idx = _index(**{"Opportunity.ForecastCategory": _fc("inline_standard", [
        ("BestCase", "Best Case", True),
    ])})
    [c] = idx.check_literal("Opportunity.ForecastCategory", "Best Case")
    assert c.verdict == Verdict.VALID and c.detail == "label"


# -------------------------------------------------------------- INVALID --

def test_invalid_absent_is_the_hallucination_verdict():
    idx = _index(**LOAN)
    [c] = idx.check_literal("Opportunity.Loan_Type__c", "Home Loan")
    assert c.verdict == Verdict.INVALID and c.detail == "absent"


def test_invalid_inactive_is_org_drift_not_hallucination():
    """Present-but-inactive is a DIFFERENT verdict detail from absent: the
    value existed and the org retired it (drift — org owner's problem), vs a
    value that never existed (hallucination — generation's problem). The two
    must never collapse."""
    idx = _index(**{"Case.Status": _fc("inline_standard", [
        ("New", "New", True), ("Retired", "Retired", False),
    ])})
    [c] = idx.check_literal("Case.Status", "Retired")
    assert c.verdict == Verdict.INVALID and c.detail == "inactive"


def test_no_values_refuses_every_literal():
    """no_values asserts a known-EMPTY set (org-verified honest absence,
    D-408) — a member of the empty set does not exist, so refusal is correct
    and it is NOT a capture gap."""
    idx = _index(**{"Location.LocationType": _fc("no_values")})
    [c] = idx.check_literal("Location.LocationType", "Warehouse")
    assert c.verdict == Verdict.INVALID and c.detail == "absent"


# ------------------------------------------------------ CANNOT_VALIDATE --

def test_truncated_capture_never_refuses():
    """inline_truncated stores a disclosed SUBSET (200-cap): 'not in the
    stored 200' cannot distinguish 'org lacks it' from 'we truncated it
    away'. Refusing here would be a silent false refusal — the exact
    inversion D-399.1 forbids."""
    idx = _index(**{"User.TimeZoneSidKey": _fc("inline_truncated", [
        ("Pacific/Apia", "(GMT+13:00) Apia", True),
    ])})
    # A value that IS NOT in the stored subset — still no refusal.
    [c] = idx.check_literal("User.TimeZoneSidKey", "Mars/Olympus")
    assert c.verdict == Verdict.CANNOT_VALIDATE
    assert c.detail == "inline_truncated"


def test_null_capture_is_pre_migration_draw_no_conclusion():
    idx = _index(**{"Account.Industry": _fc(None, [
        ("Banking", "Banking", True),
    ])})
    # Even a literal PRESENT in stored values: NULL capture means the stored
    # rows themselves are unattested — no conclusion in either direction.
    [c] = idx.check_literal("Account.Industry", "Banking")
    assert c.verdict == Verdict.CANNOT_VALIDATE and c.detail == "null_capture"


def test_unknown_future_mark_cannot_validate():
    idx = _index(**{"X.Y": _fc("from_some_future_source", [("A", "A", True)])})
    [c] = idx.check_literal("X.Y", "A")
    assert c.verdict == Verdict.CANNOT_VALIDATE
    assert c.detail == "from_some_future_source"


# ------------------------------------------------------------- scoping --

def test_unknown_field_is_out_of_jurisdiction():
    """Field grounding is the grounding validator's job; membership says
    nothing about fields it does not know."""
    assert _index(**LOAN).check_literal("Case.Priority", "High") == []


def test_none_and_bool_literals_are_skipped():
    idx = _index(**LOAN)
    assert idx.check_literal("Opportunity.Loan_Type__c", None) == []
    assert idx.check_literal("Opportunity.Loan_Type__c", True) == []


def test_multipicklist_splits_on_semicolon_per_part_verdicts():
    idx = _index(**{"Contact.BuyerAttributes": _fc(
        "inline_standard",
        [("Economic", "Economic", True), ("Technical", "Technical", True)],
        ft="multipicklist")})
    checks = idx.check_literal("Contact.BuyerAttributes",
                               "Economic; Imaginary")
    assert [(c.value, c.verdict) for c in checks] == [
        ("Economic", Verdict.VALID), ("Imaginary", Verdict.INVALID)]


# ---------------------------------------------------------- extraction --

def test_extracts_structured_shapes_not_prose():
    """field_values / field_changes maps, literal wrappers and subject/value
    condition nodes are extracted; prose sentences NEVER are — a value that
    exists only inside triggering_action.description must not surface."""
    claim = {
        "triggering_action": {
            "description":
                "creating a Opportunity with Loan_Type__c='PROSE ONLY'"},
        "to_state": {"field_values": {"Opportunity.StageName": "Approved"}},
        "conds": [{"subject": {"entity_type": "Field",
                               "external_id": "Case.Priority"},
                   "value": "High", "predicate": "equals"}],
    }
    recipe = {"steps": [
        {"kind": "create",
         "field_values": {"PLS_FB_Order__c.PLS_FB_Status__c":
                          {"kind": "literal", "value": "Draft"}}},
        {"kind": "update",
         "field_changes": {"Opportunity.Amount": 500000}},
    ]}
    pairs = extract_field_literals(claim, recipe)
    assert ("Opportunity.StageName", "Approved") in pairs
    assert ("Case.Priority", "High") in pairs
    assert ("PLS_FB_Order__c.PLS_FB_Status__c", "Draft") in pairs
    assert ("Opportunity.Amount", 500000) in pairs
    assert not any("PROSE" in str(v) for _f, v in pairs)


def test_extraction_dedups_on_field_and_value():
    body = {"a": {"X.F": "V"}, "b": {"X.F": "V"}}
    assert extract_field_literals(body) == [("X.F", "V")]


# ------------------------------------------------------------ rollups --

def test_claim_rollup_invalid_dominates_then_cannot_validate():
    idx = _index(**LOAN,
                 **{"User.TimeZoneSidKey": _fc("inline_truncated")})
    r = idx.validate({"x": {"Opportunity.Loan_Type__c": "Home Loan",
                            "User.TimeZoneSidKey": "Mars/Olympus"}})
    assert r.verdict == Verdict.INVALID
    r2 = idx.validate({"x": {"Opportunity.Loan_Type__c": "Home",
                             "User.TimeZoneSidKey": "Mars/Olympus"}})
    assert r2.verdict == Verdict.CANNOT_VALIDATE
    r3 = idx.validate({"x": {"Opportunity.Loan_Type__c": "Home"}})
    assert r3.verdict == Verdict.VALID


def test_claim_with_no_enumerated_literals_is_vacuously_valid():
    assert _index(**LOAN).validate({"x": {"Opportunity.Amount": 1}}).verdict \
        == Verdict.VALID


# ----------------------------------------------------------- fail-loud --

class _BrokenConn:
    def execute(self, *_a, **_k):
        raise RuntimeError("column picklist_capture does not exist")


class _EmptyConn:
    class _R:
        def mappings(self):
            return self
        def all(self):
            return []
    def execute(self, *_a, **_k):
        return self._R()


def test_load_fails_loud_when_capture_unreadable():
    with pytest.raises(ValueMembershipError, match="cannot read capture"):
        FieldCaptureIndex.load(_BrokenConn(), "00000000-0000-0000-0000-0")


def test_load_fails_loud_on_zero_picklist_fields():
    """An empty index would validate everything vacuously — a silent
    wrong-green of the validator's own. Refuse instead."""
    with pytest.raises(ValueMembershipError, match="ZERO picklist fields"):
        FieldCaptureIndex.load(_EmptyConn(), "00000000-0000-0000-0000-0")


# ===========================================================================
# D-413 — the finalize-time gate (wiring)
# ===========================================================================

from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from primeqa.generation.emission import GroundedExistence, _Endpoint
from primeqa.generation.enums import AdmissibilityLayer, OutcomeKind, RefusalKind
from primeqa.generation.governance import ConversationContext, PresentedCandidate
from primeqa.generation.governance_core import (
    GovernanceCore,
    _merge_vm_payloads,
    _vm_bundle_bodies,
    _vm_declination_payload,
)
from primeqa.generation.protocol import (
    BudgetSpec, GovernanceContext, OperationalContext, SemanticContext)


class _FakeS1:
    """The minimal S1 surface FieldCaptureIndex.from_s1 reads, with a
    Loan_Type__c whose capture is AUTHORITATIVE (inline, 3 values)."""

    def __init__(self, capture="inline", include_capture_key=True):
        self._capture = capture
        self._include = include_capture_key

    def get_entities(self, entity_type, at_seq, filters=None):
        if (filters or {}).get("sf_api_name") == "Opportunity.Loan_Type__c":
            return [SimpleNamespace(id=uuid4())]
        return []

    def get_entity_details(self, entity_id, at_seq):
        d = {"field_type": "picklist",
             "picklist_value_set_entity_id": "pvs-1"}
        if self._include:
            d["picklist_capture"] = self._capture
        return d

    def get_picklist_values(self, pvs_id, at_seq):
        return [
            {"value_api_name": "Home", "value_label": "Home", "is_active": True},
            {"value_api_name": "Personal", "value_label": "Personal",
             "is_active": True},
            {"value_api_name": "Business", "value_label": "Business",
             "is_active": True},
        ]


def _wire_ctx():
    return ConversationContext(
        request_id=uuid4(),
        requirement_ref={"key": "req-w", "text": "w"},
        requirement_text="w",
        semantic_context=SemanticContext(
            requirement_refs=[{"key": "req-w", "text": "w"}],
            s1_version_seq=1, s1_version_name="v1"),
        governance_context=GovernanceContext(),
        operational_context=OperationalContext(budgets=BudgetSpec()))


def _wire_state():
    return SimpleNamespace(
        groundings=[GroundedExistence(
            archetype="configuration", claim_kind="existence-claim",
            version_seq=1,
            subject=_Endpoint(entity_id=uuid4(), entity_type="Field",
                              external_id="Opportunity.Loan_Type__c"),
            requirement_excerpt="x")],
        presented_candidates=[
            PresentedCandidate("c0", AdmissibilityLayer.LAYER_1)],
        attempted_interpretation={
            "candidate_paths": [{"path_id": "c0"}],
            "dismissed_alternatives_by_reason": {},
            "selected_path_id": None,
        },
        control_facts=None)


def _bundle(value, kind="state-transition-claim"):
    """A fake authored bundle staging Loan_Type__c=<value>. Plain-dict bodies
    (fine at len==1 — the D-339 dedup only hashes multi-bundle drafts)."""
    return SimpleNamespace(
        archetype="behavioural", claim_kind=kind,
        asserted_truth={"to_state": {"field_values":
                                     {"Opportunity.Loan_Type__c": value}}},
        semantic_conditions={"kind": "semantic-conditions"},
        causal_initiation={"kind": "data-mutation-trigger"},
        observation_realization={"steps": [
            {"kind": "create",
             "field_values": {"Opportunity.Loan_Type__c": value}}]},
        secondary_recipes=(), boundary_recipes=(),
        admissibility_layer=AdmissibilityLayer.LAYER_1,
        caveat_required=False, caveat_kind=None)


def _finalize_with(bundle, s1):
    gov = GovernanceCore(s1)
    with patch("primeqa.generation.governance_core.author_emission",
               return_value=bundle):
        return gov.finalize_outcome(outcome_input={}, ctx=_wire_ctx(),
                                    state=_wire_state())


def test_gate_declines_nonmember_via_override_when_sole_bundle():
    """The 31eaa21e shape: the only authored bundle stages 'Home Loan' → the
    whole draft becomes an UNGROUNDED_CLAIM refusal whose payload is the
    defer_class-discriminated declination — visible, never a silent drop."""
    ov = _finalize_with(_bundle("Home Loan"), _FakeS1())
    assert ov.override is not None and ov.outcome is None
    assert ov.override.refusal_kind == RefusalKind.UNGROUNDED_CLAIM
    p = ov.override.payload
    assert p["defer_class"] == "value-membership"
    [v] = p["violations"]
    assert v["field"] == "Opportunity.Loan_Type__c"
    assert v["asserted_value"] == "Home Loan"
    assert v["reason"] == "absent"
    assert v["org_value_set"] == ["Business", "Home", "Personal"]
    assert "Home Loan" in p["detail"] and "org accepts" in p["detail"]


def test_gate_passes_member_value_untouched():
    ov = _finalize_with(_bundle("Home"), _FakeS1())
    assert ov.override is None
    assert ov.outcome is not None and ov.outcome.outcome_kind == OutcomeKind.DRAFT
    ai = ov.outcome.attempted_interpretation.model_dump()
    assert "partial_refusals" not in ai


def test_gate_cannot_validate_passes_through_null_capture():
    """A fixture/pre-migration S1 (no capture mark) must NEVER decline —
    CANNOT_VALIDATE is a pass-through, not a refusal (D-399.1)."""
    ov = _finalize_with(_bundle("Home Loan"),
                        _FakeS1(include_capture_key=False))
    assert ov.override is None and ov.outcome is not None


def test_gate_cannot_validate_passes_through_truncated_capture():
    ov = _finalize_with(_bundle("Home Loan"), _FakeS1(capture="inline_truncated"))
    assert ov.override is None and ov.outcome is not None


def test_gate_mixed_batch_keeps_valid_and_declines_invalid():
    """Gate-level mixed case: the invalid bundle becomes a D-302-shaped
    partial_refusals entry; the valid one survives with its aligned
    presented_candidate."""
    gov = GovernanceCore(_FakeS1())
    state = _wire_state()
    state.presented_candidates = [
        PresentedCandidate("c0", AdmissibilityLayer.LAYER_1),
        PresentedCandidate("c1", AdmissibilityLayer.LAYER_1)]
    kept, decls = gov._value_membership_gate(
        [_bundle("Home"), _bundle("Home Loan")], _wire_ctx(), state)
    assert len(kept) == 1 and kept[0].asserted_truth["to_state"][
        "field_values"]["Opportunity.Loan_Type__c"] == "Home"
    [d] = decls
    assert d["path_id"] == "c1"
    assert d["refusal_kind"] == "ungrounded-claim"
    assert d["payload"]["defer_class"] == "value-membership"
    assert [c.path_id for c in state.presented_candidates] == ["c0"]


def test_gate_fails_loud_when_validator_errors():
    """Never emit unvalidated: an erroring S1 read propagates out of
    finalize — generation errors rather than passing the claim through."""
    class _ExplodingS1(_FakeS1):
        def get_entity_details(self, entity_id, at_seq):
            raise RuntimeError("connection lost")
    with pytest.raises(RuntimeError, match="connection lost"):
        _finalize_with(_bundle("Home Loan"), _ExplodingS1())


def test_vm_bundle_bodies_covers_secondary_and_boundary_recipes():
    b = _bundle("Home")
    b.secondary_recipes = (SimpleNamespace(
        causal_initiation={"kind": "sec-ci"},
        observation_realization={"steps": [
            {"field_values": {"Opportunity.Loan_Type__c": "SEC"}}]}),)
    b.boundary_recipes = (SimpleNamespace(
        causal_initiation=None,
        observation_realization={"steps": [
            {"field_values": {"Opportunity.Loan_Type__c": "BND"}}]}),)
    blob = str(_vm_bundle_bodies(b))
    assert "SEC" in blob and "BND" in blob


def test_merge_vm_payloads_dedups_violations():
    p1 = _vm_declination_payload(
        [], None) if False else {
        "defer_class": "value-membership", "detail": "d",
        "detail_source": "substrate", "detail_layer": "value-membership",
        "violations": [{"field": "F", "asserted_value": "X",
                        "reason": "absent", "org_value_set": [],
                        "capture": "inline"}]}
    merged = _merge_vm_payloads([p1, {"violations": p1["violations"]}])
    assert len(merged["violations"]) == 1
