"""Unit tests for operational-padding construction (D-115 side B, k16) — stub S1,
no PG.

``resolve_operational_padding`` reads an object's required-on-create fields from
S1 and fills the writable, non-lookup, non-semantic ones with type-valid filler;
fields it cannot synthesize (lookups, unknown types, picklists with no readable
value set) land in ``unfillable``. The semantic field-under-test is never padded
(k16)."""
from __future__ import annotations

from primeqa.execution_engine.world import resolve_operational_padding


# ---------------------------------------------------------------------------
# Stub S1 — models one object's fields (+ optional picklist value sets)
# ---------------------------------------------------------------------------

class _Ent:
    def __init__(self, eid, etype, api):
        self.id, self.entity_type, self.sf_api_name = eid, etype, api
        self.attributes = {}


class _Rel:
    def __init__(self, entity):
        self.entity = entity


class _StubS1:
    """``fields``: list of dicts (api + field_details columns). ``picklists``:
    {pvs_id: [ {value_api_name, is_active, is_default, sort_order} ]}."""

    def __init__(self, object_api, fields, *, version=5, picklists=None,
                 vrs=None):
        self._object_api, self._version = object_api, version
        self._obj_id = "obj-" + object_api
        self._field_ents, self._detail, self._pv_ents = [], {}, {}
        # ``vrs``: [{api, formula, active}] — the object's ValidationRules,
        # served on the APPLIES_TO edge (the picklist gate-check read).
        self._vr_ents = []
        for j, v in enumerate(vrs or ()):
            ent = _Ent(f"vr-{j}", "ValidationRule", v.get("api", f"VR{j}"))
            ent.attributes = {"formula_text": v.get("formula", ""),
                              "is_active": v.get("active", True)}
            self._vr_ents.append(ent)
        for f in fields:
            fid = "fld-" + f["api"]
            self._field_ents.append(_Ent(fid, "Field", f["api"]))
            self._detail[fid] = {
                "field_type": f.get("field_type", "string"),
                "is_nillable": f.get("is_nillable", True),
                "is_calculated": f.get("is_calculated", False),
                "is_createable": f.get("is_createable", True),
                "references_object_entity_id": f.get("references_object_entity_id"),
                "picklist_value_set_entity_id": f.get("picklist_value_set_entity_id"),
                "length": f.get("length"),
            }
        # D-204.2: picklist values are exposed via get_picklist_values (the
        # D-119 detail-FK primitive) — the real store has NO containment edge.
        self._picklists = {
            pvs_id: sorted(
                ({"value_api_name": v["value_api_name"],
                  "is_active": v.get("is_active", True),
                  "is_default": v.get("is_default", False),
                  "sort_order": v.get("sort_order", j)}
                 for j, v in enumerate(values)),
                key=lambda d: d["sort_order"])
            for pvs_id, values in (picklists or {}).items()
        }

    def current_version_seq(self):
        return self._version

    def get_entities(self, entity_type, at_seq, filters=None):
        if (entity_type == "Object" and filters
                and filters.get("sf_api_name") == self._object_api):
            return [_Ent(self._obj_id, "Object", self._object_api)]
        return []

    def get_related(self, entity_id, edge_types, direction, at_seq):
        if entity_id == self._obj_id:
            if edge_types and "APPLIES_TO" in edge_types:
                return [_Rel(e) for e in self._vr_ents]
            return [_Rel(e) for e in self._field_ents]
        return []

    def get_picklist_values(self, pvs_id, at_seq):
        return list(self._picklists.get(pvs_id, []))

    def get_entity_details(self, entity_id, at_seq):
        return self._detail.get(entity_id)


def _pad(s1, object_api="Account", semantic=("Status__c",)):
    return resolve_operational_padding(
        object_api, set(semantic), s1=s1, at_seq=s1.current_version_seq())


# ---------------------------------------------------------------------------
# Filling
# ---------------------------------------------------------------------------

def test_required_text_is_filled():
    s1 = _StubS1("Account", [
        {"api": "Status__c"},                                    # semantic — skipped
        {"api": "Name", "field_type": "string", "is_nillable": False}])
    res = _pad(s1)
    assert res.unfillable == ()
    assert res.filler == {"Name": "PQA"}


def test_semantic_field_never_padded_even_if_required():
    # Status__c is required (nillable False) but is the value under test → never filled.
    s1 = _StubS1("Account", [
        {"api": "Status__c", "field_type": "picklist", "is_nillable": False}])
    res = _pad(s1, semantic=("Status__c",))
    assert res.filler == {} and res.unfillable == ()


def test_optional_field_skipped():
    s1 = _StubS1("Account", [{"api": "Note__c", "field_type": "string", "is_nillable": True}])
    assert _pad(s1).filler == {}


def test_calculated_required_field_skipped():
    s1 = _StubS1("Account", [
        {"api": "Roll__c", "field_type": "double", "is_nillable": False, "is_calculated": True}])
    assert _pad(s1).filler == {}


def test_types_filled_by_kind():
    s1 = _StubS1("T__c", [
        {"api": "N__c", "field_type": "double", "is_nillable": False},
        {"api": "B__c", "field_type": "boolean", "is_nillable": False},
        {"api": "D__c", "field_type": "date", "is_nillable": False},
        {"api": "E__c", "field_type": "email", "is_nillable": False}])
    f = _pad(s1, object_api="T__c", semantic=()).filler
    assert f["N__c"] == 1 and f["B__c"] is False
    assert f["E__c"] == "pqa@example.com"
    assert f["D__c"]                          # an ISO date string


# ---------------------------------------------------------------------------
# Required references (F6.2 — collected for construct_world, no longer fenced)
# + genuinely-unfillable types (still a hard stop)
# ---------------------------------------------------------------------------

def test_required_lookup_goes_to_required_refs():
    # F6.2: a required reference is no longer fenced into unfillable — it is
    # collected as a required_ref (field_api, referenced_object_entity_id) so
    # construct_world can build the parent and thread its id.
    s1 = _StubS1("Account", [
        {"api": "Owner__c", "field_type": "reference", "is_nillable": False,
         "references_object_entity_id": "obj-User"}])
    res = _pad(s1)
    assert res.filler == {} and res.unfillable == ()
    assert res.required_refs == (("Owner__c", "obj-User"),)


def test_optional_lookup_is_skipped_not_a_required_ref():
    # A nillable reference is filtered before the reference check (Salesforce
    # does not force it on create) — neither padded, required, nor unfillable.
    s1 = _StubS1("Account", [
        {"api": "Opt__c", "field_type": "reference", "is_nillable": True,
         "references_object_entity_id": "obj-User"}])
    res = _pad(s1)
    assert res.filler == {} and res.unfillable == () and res.required_refs == ()


def test_unknown_type_is_unfillable():
    s1 = _StubS1("Account", [{"api": "Loc__c", "field_type": "location", "is_nillable": False}])
    res = _pad(s1)
    assert res.unfillable == ("Loc__c",) and res.required_refs == ()


def test_non_createable_required_fields_are_skipped():
    # is_nillable=False but is_createable=False — Salesforce-managed audit/system
    # fields (CreatedDate scalar, CreatedById reference). Setting EITHER gets the
    # whole create rejected, so both are skipped: not padded, not a required_ref,
    # not unfillable. Only the createable scalar is padded.
    s1 = _StubS1("Account", [
        {"api": "CreatedDate", "field_type": "datetime", "is_nillable": False,
         "is_createable": False},
        {"api": "CreatedById", "field_type": "reference", "is_nillable": False,
         "is_createable": False, "references_object_entity_id": "obj-User"},
        {"api": "Name", "field_type": "string", "is_nillable": False,
         "is_createable": True}])
    res = _pad(s1)
    assert res.filler == {"Name": "PQA"}
    assert res.unfillable == () and res.required_refs == ()


def test_missing_object_yields_empty_padding():
    s1 = _StubS1("Account", [{"api": "Name", "is_nillable": False}])
    res = resolve_operational_padding("Ghost__c", {"X"}, s1=s1, at_seq=5)
    assert res.filler == {} and res.unfillable == ()


# ---------------------------------------------------------------------------
# Simple-picklist filler
# ---------------------------------------------------------------------------

def test_required_picklist_uses_default_active_value():
    s1 = _StubS1("Account", [
        {"api": "Stage__c", "field_type": "picklist", "is_nillable": False,
         "picklist_value_set_entity_id": "pvs-1"}],
        picklists={"pvs-1": [
            {"value_api_name": "Open", "is_active": True, "sort_order": 1},
            {"value_api_name": "Won", "is_active": True, "is_default": True, "sort_order": 2}]})
    assert _pad(s1).filler == {"Stage__c": "Won"}


def test_required_picklist_first_active_when_no_default():
    s1 = _StubS1("Account", [
        {"api": "Stage__c", "field_type": "picklist", "is_nillable": False,
         "picklist_value_set_entity_id": "pvs-1"}],
        picklists={"pvs-1": [
            {"value_api_name": "B", "is_active": True, "sort_order": 2},
            {"value_api_name": "A", "is_active": True, "sort_order": 1}]})
    assert _pad(s1).filler == {"Stage__c": "A"}            # lowest sort_order


def test_required_picklist_without_value_set_is_unfillable():
    s1 = _StubS1("Account", [
        {"api": "Stage__c", "field_type": "picklist", "is_nillable": False}])
    res = _pad(s1)
    assert res.filler == {} and res.unfillable == ("Stage__c",)


# ---------------------------------------------------------------------------
# VR-gated picklist padding (the AmbiguousRejection fix): a value an ACTIVE VR
# names as a quoted literal is skipped — the first unmentioned candidate wins;
# all-mentioned / unreadable VRs fall back to today's exact pick.
# ---------------------------------------------------------------------------

_STAGE_FIELD = [{"api": "Stage__c", "field_type": "picklist",
                 "is_nillable": False, "picklist_value_set_entity_id": "pvs-1"}]
_STAGES = {"pvs-1": [
    {"value_api_name": "Credit Assessment", "is_active": True, "sort_order": 1},
    {"value_api_name": "Needs Analysis", "is_active": True, "sort_order": 2},
    {"value_api_name": "Closed Won", "is_active": True, "sort_order": 3}]}


def test_picklist_skips_vr_gated_first_value():
    # The live env-59 shape: the first active stage is entry-gated by an active
    # VR (double-quoted literal) → the next unmentioned stage wins.
    s1 = _StubS1("Account", _STAGE_FIELD, picklists=_STAGES, vrs=[
        {"api": "Gate", "active": True,
         "formula": 'AND(ISPICKVAL(StageName, "Credit Assessment"), NOT(KYC__c))'}])
    assert _pad(s1).filler == {"Stage__c": "Needs Analysis"}


def test_picklist_skips_vr_gated_default_too():
    stages = {"pvs-1": [
        {"value_api_name": "Gated", "is_active": True, "is_default": True,
         "sort_order": 1},
        {"value_api_name": "Free", "is_active": True, "sort_order": 2}]}
    s1 = _StubS1("Account", _STAGE_FIELD, picklists=stages, vrs=[
        {"api": "Gate", "formula": "ISPICKVAL(Stage__c, 'Gated')"}])
    assert _pad(s1).filler == {"Stage__c": "Free"}


def test_picklist_all_values_gated_falls_back_to_todays_pick():
    s1 = _StubS1("Account", _STAGE_FIELD, picklists=_STAGES, vrs=[
        {"api": "G1", "formula": '"Credit Assessment" "Needs Analysis"'},
        {"api": "G2", "formula": "'Closed Won'"}])
    # never unfillable, never worse — today's first-active pick survives
    assert _pad(s1).filler == {"Stage__c": "Credit Assessment"}


def test_picklist_inactive_vr_is_ignored():
    s1 = _StubS1("Account", _STAGE_FIELD, picklists=_STAGES, vrs=[
        {"api": "Gate", "active": False,
         "formula": 'ISPICKVAL(StageName, "Credit Assessment")'}])
    assert _pad(s1).filler == {"Stage__c": "Credit Assessment"}


def test_picklist_vr_mentioning_other_values_only_is_no_gate():
    s1 = _StubS1("Account", _STAGE_FIELD, picklists=_STAGES, vrs=[
        {"api": "Gate", "formula": "ISPICKVAL(Stage__c, 'Closed Lost')"}])
    assert _pad(s1).filler == {"Stage__c": "Credit Assessment"}


def test_picklist_gating_is_deterministic():
    s1 = _StubS1("Account", _STAGE_FIELD, picklists=_STAGES, vrs=[
        {"api": "Gate", "formula": 'ISPICKVAL(StageName, "Credit Assessment")'}])
    picks = {_pad(s1).filler["Stage__c"] for _ in range(5)}
    assert picks == {"Needs Analysis"}


# ---------------------------------------------------------------------------
# req-302 robustness R1: the VR-satisfaction pass (semantic_values-gated)
# ---------------------------------------------------------------------------
# The live shape: 3 state-transition claims staged StageName='Credit
# Assessment' and padding chose KYC_Complete__c=False + omitted the nillable
# Credit_Score__c — the org's VR ("KYC must be complete and Credit Score must
# be populated before moving to Credit Assessment") rejected every create
# (AmbiguousRejection / setup_rejection).

_KYC_VR = ('AND(ISPICKVAL(StageName, "Credit Assessment"), '
           'OR(NOT(KYC_Complete__c), ISBLANK(Credit_Score__c)))')


def _kyc_stub(vrs=None):
    return _StubS1("Opportunity", [
        {"api": "StageName", "field_type": "picklist"},
        {"api": "Name", "field_type": "string", "is_nillable": False},
        {"api": "KYC_Complete__c", "field_type": "boolean",
         "is_nillable": False},
        {"api": "Credit_Score__c", "field_type": "double",
         "is_nillable": True},
    ], vrs=vrs if vrs is not None else [
        {"api": "HL_Stage_Gate", "formula": _KYC_VR, "active": True}])


def _pad_vals(s1, semantic=("StageName",), values=None):
    return resolve_operational_padding(
        "Opportunity", set(semantic), s1=s1,
        at_seq=s1.current_version_seq(), semantic_values=values)


def test_vr_satisfaction_fills_the_req302_shape():
    # Armed by the staged stage → the boolean flips True AND the nillable
    # VR-required field gets the type filler.
    res = _pad_vals(_kyc_stub(), values={"StageName": "Credit Assessment"})
    assert res.filler["KYC_Complete__c"] is True
    assert res.filler["Credit_Score__c"] == 1


def test_vr_satisfaction_unarmed_state_is_untouched():
    res = _pad_vals(_kyc_stub(), values={"StageName": "Needs Analysis"})
    assert res.filler["KYC_Complete__c"] is False
    assert "Credit_Score__c" not in res.filler


def test_vr_satisfaction_disabled_without_values_is_byte_identical():
    res = _pad_vals(_kyc_stub(), values=None)
    assert res.filler == {"Name": "PQA", "KYC_Complete__c": False}


def test_vr_satisfaction_never_touches_a_staged_field_k16():
    # The deficiency field is SEMANTIC (recipe-staged) → padding must not
    # override it, even though the VR will fire — the honest rejection is
    # the claim's own business.
    res = _pad_vals(_kyc_stub(), semantic=("StageName", "KYC_Complete__c"),
                    values={"StageName": "Credit Assessment",
                            "KYC_Complete__c": False})
    assert "KYC_Complete__c" not in res.filler
    assert "Credit_Score__c" not in res.filler   # OR needs ALL arms falsified


def test_vr_satisfaction_staged_arm_already_false_skips_not_bails():
    # R1.1 (the req-302 Task.Subject re-mint wrong-red): the staged
    # Credit_Score=600 already falsifies ISBLANK(Credit_Score__c), so the OR
    # must SKIP that arm (no demand needed) and still pad KYC_Complete=True —
    # the old code bailed the whole OR at the k16 leaf, KYC stayed unpadded,
    # and the armed VR rejected the create (AmbiguousRejection).
    res = _pad_vals(_kyc_stub(), semantic=("StageName", "Credit_Score__c"),
                    values={"StageName": "Credit Assessment",
                            "Credit_Score__c": 600})
    assert res.filler["KYC_Complete__c"] is True
    assert "Credit_Score__c" not in res.filler   # staged — never padded


def test_vr_satisfaction_all_arms_already_false_demands_nothing():
    # Both OR arms are falsified by the staged state itself — the VR cannot
    # fire; no demand is needed (and none may touch the staged fields).
    res = _pad_vals(_kyc_stub(),
                    semantic=("StageName", "KYC_Complete__c",
                              "Credit_Score__c"),
                    values={"StageName": "Credit Assessment",
                            "KYC_Complete__c": True,
                            "Credit_Score__c": 600})
    assert "KYC_Complete__c" not in res.filler
    assert "Credit_Score__c" not in res.filler


def test_vr_satisfaction_comparison_pin_arms_too():
    vr = ('AND(StageName = "Credit Assessment", NOT(KYC_Complete__c))')
    res = _pad_vals(_kyc_stub(vrs=[{"api": "G", "formula": vr}]),
                    values={"StageName": "Credit Assessment"})
    assert res.filler["KYC_Complete__c"] is True


def test_vr_satisfaction_bare_boolean_conjunct_falsifies_to_false():
    vr = 'AND(ISPICKVAL(StageName, "Closed"), Escalated__c)'
    s1 = _StubS1("Opportunity", [
        {"api": "StageName", "field_type": "picklist"},
        {"api": "Escalated__c", "field_type": "boolean", "is_nillable": False},
    ], vrs=[{"api": "G", "formula": vr}])
    res = _pad_vals(s1, values={"StageName": "Closed"})
    assert res.filler["Escalated__c"] is False


def test_vr_satisfaction_skips_unparseable_and_or_rooted():
    for formula in ("REGEX(Phone, '[0-9]+')",
                    "OR(NOT(KYC_Complete__c), ISBLANK(Credit_Score__c))"):
        res = _pad_vals(_kyc_stub(vrs=[{"api": "G", "formula": formula}]),
                        values={"StageName": "Credit Assessment"})
        assert res.filler["KYC_Complete__c"] is False
        assert "Credit_Score__c" not in res.filler


def test_vr_satisfaction_conflicting_demands_drop_the_field():
    vr_a = 'AND(ISPICKVAL(StageName, "Credit Assessment"), KYC_Complete__c)'
    vr_b = ('AND(ISPICKVAL(StageName, "Credit Assessment"), '
            'NOT(KYC_Complete__c))')
    res = _pad_vals(_kyc_stub(vrs=[{"api": "A", "formula": vr_a},
                                   {"api": "B", "formula": vr_b}]),
                    values={"StageName": "Credit Assessment"})
    # Contradictory rules — no guess; the blind default stays.
    assert res.filler["KYC_Complete__c"] is False


def test_vr_satisfaction_inactive_vr_never_arms():
    res = _pad_vals(_kyc_stub(vrs=[
        {"api": "G", "formula": _KYC_VR, "active": False}]),
        values={"StageName": "Credit Assessment"})
    assert res.filler["KYC_Complete__c"] is False


# ---------------------------------------------------------------------------
# D-338: the claim's is_null-conditioned fields are k16 (asserted-blank state)
# ---------------------------------------------------------------------------
# An acceptance/prohibition state is partly defined by ABSENCE — D-305 stages
# is_null clauses by leaving them out of field_values — so the run path
# threads them in as semantic keys with staged value None. A VR armed by the
# staged equals values may demand one via ISBLANK; the demand must be REFUSED:
# a filled value would green a create whose state the claim excludes.


def test_null_asserted_field_is_never_demanded_by_r1():
    # The claim: "the org accepts Stage='Credit Assessment' with
    # Credit_Score__c BLANK". The KYC VR is armed by the staged stage and
    # demands the blank field via ISBLANK — refused at k16, which kills the
    # whole OR (every live arm must falsify), so R1 makes no demands at all
    # and the org's own rejection stays the honest surface.
    res = _pad_vals(_kyc_stub(), semantic=("StageName", "Credit_Score__c"),
                    values={"StageName": "Credit Assessment",
                            "Credit_Score__c": None})
    assert "Credit_Score__c" not in res.filler
    assert res.filler["KYC_Complete__c"] is False   # blind default — no demand


def test_null_asserted_picklist_arm_reads_already_false():
    # A staged-blank picklist provably falsifies ISPICKVAL(f, 'X') — the OR
    # arm is skipped (R1.1's _already_false) and the OTHER arm's demand
    # still lands; the blank field itself is never filled.
    vr = ('AND(ISPICKVAL(StageName, "Credit Assessment"), '
          'OR(ISPICKVAL(Loan_Type__c, "Home"), NOT(KYC_Complete__c)))')
    s1 = _StubS1("Opportunity", [
        {"api": "StageName", "field_type": "picklist"},
        {"api": "Loan_Type__c", "field_type": "picklist"},
        {"api": "KYC_Complete__c", "field_type": "boolean",
         "is_nillable": False},
    ], vrs=[{"api": "G", "formula": vr}])
    res = _pad_vals(s1, semantic=("StageName", "Loan_Type__c"),
                    values={"StageName": "Credit Assessment",
                            "Loan_Type__c": None})
    assert res.filler["KYC_Complete__c"] is True
    assert "Loan_Type__c" not in res.filler


def test_plan_world_bakes_the_null_asserted_guard_into_detached_plans():
    # The async bracket resolves worlds up front (D-230.2) — the guard must
    # ride the detached WorldPlan, because the execute bracket holds no DB
    # connection and cannot re-derive the claim's asserted-blank set.
    from uuid import uuid4
    from primeqa.execution_engine.data_executor import plan_data_recipe_world
    from primeqa.execution_engine.plan import DataRecipePlan, PlannedCreate
    from primeqa.test_representation.models.references import LogicalRef

    kyc_vr = ('AND(ISPICKVAL(StageName, "Credit Assessment"), '
              'OR(NOT(KYC_Complete__c), ISBLANK(Credit_Score__c)))')

    def _s1_qualified():
        # The real S1 convention: object-qualified field api names.
        return _StubS1("Opportunity", [
            {"api": "Opportunity.StageName", "field_type": "picklist"},
            {"api": "Opportunity.KYC_Complete__c", "field_type": "boolean",
             "is_nillable": False},
            {"api": "Opportunity.Credit_Score__c", "field_type": "double",
             "is_nillable": True},
        ], vrs=[{"api": "HL_Stage_Gate", "formula": kyc_vr, "active": True}])

    def _acceptance_plan():
        target = LogicalRef(entity_type="Object", external_id="Opportunity")
        return DataRecipePlan(
            recipe_id=uuid4(), recipe_version_seq=1, claim_test_id=uuid4(),
            claim_version_seq=None, api_choice="rest",
            steps=(PlannedCreate(
                step_id="create-record", target_object=target,
                field_values={"Opportunity.StageName": "Credit Assessment"},
                expect_rejection=None, expect_acceptance=True),))

    # Unguarded (no is_null conditions): R1 fills the VR's demand — the
    # exact wrong-green shape when the claim asserts the field BLANK.
    unguarded = plan_data_recipe_world(_acceptance_plan(), _s1_qualified())
    assert unguarded["create-record"].scalar_filler[
        "Opportunity.Credit_Score__c"] == 1

    guarded = plan_data_recipe_world(
        _acceptance_plan(), _s1_qualified(),
        null_asserted_fields={"Opportunity.Credit_Score__c"})
    assert ("Opportunity.Credit_Score__c"
            not in guarded["create-record"].scalar_filler)
