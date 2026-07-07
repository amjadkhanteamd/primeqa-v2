"""Unit: the D-206 plain-English claim presentation (pure, deterministic)."""
from __future__ import annotations

from primeqa.intelligence.claim_presentation import (
    claim_depth,
    claim_title,
    condition_phrase,
    expected_binding,
    fmt_number,
    org_rejection_message,
    verdict_plain,
)


def test_prohibition_title():
    body = {"target": {"external_id": "Opportunity"}, "operation": "modify_field"}
    assert claim_title("prohibition-claim", body) == \
        "Rejects editing fields on Opportunity"


# --- the distinguishing "when …" conditions clause (D-293 identity, visible) --

_COND_LABELS = {"Opportunity.StageName": "Stage Name",
                "Opportunity.Credit_Score__c": "Credit Score",
                "Opportunity.KYC_Complete__c": "KYC Complete",
                "Opportunity.Loan_Type__c": "Loan Type"}


def _conds(*conds):
    return {"conditions": list(conds)}


def _c(api, predicate, value=None):
    return {"subject": {"external_id": api}, "predicate": predicate,
            "value": value}


def test_prohibition_title_with_conditions_is_distinguishing():
    body = {"target": {"external_id": "Opportunity"}, "operation": "modify_field"}
    conds = _conds(
        _c("Opportunity.StageName", "equals", "Credit Assessment"),
        _c("Opportunity.Credit_Score__c", "is_null"))
    assert claim_title("prohibition-claim", body, _COND_LABELS,
                       semantic_conditions=conds) == \
        ("Rejects editing fields on Opportunity when Stage Name is "
         "Credit Assessment and Credit Score is blank")


def test_acceptance_title_with_conditions():
    body = {"target": {"external_id": "Opportunity"}, "operation": "create"}
    conds = _conds(_c("Opportunity.KYC_Complete__c", "equals", False))
    assert claim_title("acceptance-claim", body, _COND_LABELS,
                       semantic_conditions=conds) == \
        "Accepts creating Opportunity when KYC Complete is False"


def test_title_without_conditions_is_byte_identical():
    body = {"target": {"external_id": "Opportunity"}, "operation": "modify_record"}
    base = claim_title("prohibition-claim", body)
    assert base == "Rejects editing records on Opportunity"
    assert claim_title("prohibition-claim", body, semantic_conditions=None) == base
    assert claim_title("prohibition-claim", body,
                       semantic_conditions={"conditions": []}) == base
    assert claim_title("prohibition-claim", body,
                       semantic_conditions="junk") == base   # never raises


def test_title_conditions_capped_with_more_marker():
    body = {"target": {"external_id": "Opportunity"}, "operation": "modify_record"}
    conds = _conds(
        _c("Opportunity.Loan_Type__c", "equals", "Home Loan"),
        _c("Opportunity.Credit_Score__c", "is_null"),
        _c("Opportunity.KYC_Complete__c", "equals", False))
    title = claim_title("prohibition-claim", body, _COND_LABELS,
                        semantic_conditions=conds)
    assert title == ("Rejects editing records on Opportunity when Loan Type is "
                     "Home Loan and Credit Score is blank (+1 more)")


def test_conditions_do_not_touch_other_kinds():
    body = {"subject": {"external_id": "Opportunity.Amount"},
            "expected_value": {"kind": "literal", "value": 5000}}
    conds = _conds(_c("Opportunity.Loan_Type__c", "equals", "Home Loan"))
    assert claim_title("value-claim", body, semantic_conditions=conds) == \
        claim_title("value-claim", body)


def test_condition_phrase_per_predicate():
    assert condition_phrase(_c("Opportunity.KYC_Complete__c", "not_equals", True),
                            _COND_LABELS)[0] == "KYC Complete is not True"
    assert condition_phrase(_c("Opportunity.Loan_Type__c", "in_set",
                               ["Home Loan", "Auto"]), _COND_LABELS)[0] == \
        "Loan Type is one of Home Loan, Auto"
    assert condition_phrase(_c("Opportunity.StageName", "matches_pattern", ">10"),
                            _COND_LABELS)[0] == \
        "Stage Name matches the required format"
    assert condition_phrase(_c("Opportunity.Credit_Score__c", "is_not_null"),
                            _COND_LABELS)[0] == "Credit Score is set"
    assert condition_phrase("junk", _COND_LABELS) is None


def test_value_title_quotes_strings():
    body = {"subject": {"external_id": "Contact.Email"},
            "expected_value": {"value": "pqa@example.com"}}
    assert claim_title("value-claim", body) == \
        'Contact.Email saves as "pqa@example.com"'


def test_relationship_title():
    body = {"source": {"external_id": "Opportunity.Amount"},
            "target": {"external_id": "Opportunity"},
            "edge_type": "APPLIES_TO"}
    assert claim_title("metadata-relationship-claim", body) == \
        "Opportunity.Amount applies to Opportunity"


def test_title_falls_back_on_missing_body():
    assert claim_title("prohibition-claim", None) == \
        "Rejects the operation on the object"
    assert claim_title("platform-event-claim", {}) == "platform event claim"


def test_title_never_raises_on_garbage():
    assert claim_title("value-claim", {"subject": 42, "expected_value": object()})


# ---------------------------------------------------------------------------
# Descriptive titles for the live data-behavior kinds that used to fall back
# to the bare humanized kind ("automation effect claim", "state transition
# claim"), plus the capability-claim field-name regression.
# ---------------------------------------------------------------------------

def test_state_transition_title_reads_to_state():
    body = {
        "subject": {"external_id": "Case"},
        "to_state": {"field_values": {"Status": {"kind": "literal", "value": "Escalated"}}},
        "triggering_event": {"trigger_kind": "data-mutation-trigger", "description": "x"},
    }
    assert claim_title("state-transition-claim", body) == \
        'Case: Status becomes "Escalated"'


def test_state_transition_title_falls_back_when_no_field_values():
    body = {"subject": {"external_id": "Opportunity"}, "to_state": {"field_values": {}}}
    assert claim_title("state-transition-claim", body) == \
        "Opportunity reaches the expected state"


def test_automation_effect_title_field_change():
    # D-267: the title states the EFFECT in business terms; the triggering Flow
    # API name is traceability (technical details), never the spine headline.
    body = {
        "automation": {"external_id": "SLA_Escalation_Flow"},
        "expected_effect": {
            "kind": "field_change",
            "changes": {"field_values": {"SLA_Deadline__c": {"kind": "literal", "value": 24}}},
        },
    }
    assert claim_title("automation-effect-claim", body) == \
        "Automatically sets SLA_Deadline__c to 24"


def test_automation_effect_title_blocked_and_side_effect():
    # blocked_operation reads off the effect kind alone (the block IS the
    # effect); a side effect has no stateable effect in the body, so the
    # humanized binding name disambiguates (same rationale as the
    # empty-effect fallback — an anonymous title made every such test
    # indistinguishable in the plan list).
    blocked = {"automation": {"external_id": "Amount_Guard"},
               "expected_effect": {"kind": "blocked_operation", "reason": "too big"}}
    assert claim_title("automation-effect-claim", blocked) == \
        "An automation blocks the change"
    side = {"automation": {"external_id": "Case_Email_Alert"},
            "expected_effect": {"kind": "side_effect"}}
    assert claim_title("automation-effect-claim", side) == \
        "The Case Email Alert automation fires a side effect"
    # No binding → the anonymous fallback survives.
    assert claim_title("automation-effect-claim",
                       {"expected_effect": {"kind": "side_effect"}}) == \
        "An automation fires a side effect"


def test_automation_effect_title_falls_back_to_the_binding_when_no_effect():
    # When the body states no concrete effect, the automation name is the only
    # distinguishing fact left — an anonymous "An automation fires" title made
    # every such test indistinguishable in the plan list. (The D-267 rule
    # stands where an effect exists: the effect leads, never the flow name.)
    body = {"automation": {"external_id": "Some_Flow"}}
    assert claim_title("automation-effect-claim", body) == \
        "The Some Flow automation fires"
    assert claim_title("automation-effect-claim", {}) == "An automation fires"


def test_automation_effect_title_approval_process_empty_effect():
    # The D-308 approval claims carry an empty field_change (the effect IS the
    # submission) — run 4b8cbe84's claim titled "An automation updates the
    # record". The primitive + binding give the honest, distinguishing title.
    body = {
        "automation": {"external_id": "HL_High_Value_Loan"},
        "automation_primitive": "approval_process",
        "expected_effect": {"kind": "field_change",
                            "changes": {"field_values": {}}},
    }
    assert claim_title("automation-effect-claim", body) == \
        "Automatically submits for approval (HL High Value Loan)"


def test_automation_effect_title_side_effect_approval():
    # The honest S3 emission (SideEffect — the effect IS the submission);
    # same specific title as the legacy empty-field_change fallback, so old
    # and new approval bodies read identically in every list.
    body = {
        "automation": {"external_id": "HL_High_Value_Loan"},
        "automation_primitive": "approval_process",
        "expected_effect": {"kind": "side_effect",
                            "description": "the record is submitted for "
                                           "approval — a ProcessInstance "
                                           "approval request is created"},
    }
    assert claim_title("automation-effect-claim", body) == \
        "Automatically submits for approval (HL High Value Loan)"


def test_automation_effect_title_side_effect_named_binding():
    body = {
        "automation": {"external_id": "Case_Email_Alert"},
        "automation_primitive": "flow",
        "expected_effect": {"kind": "side_effect", "description": "x"},
    }
    assert claim_title("automation-effect-claim", body) == \
        "The Case Email Alert automation fires a side effect"


def test_automation_effect_title_named_binding_empty_effect():
    body = {
        "automation": {"external_id": "HL_Auto_Risk_Rating"},
        "automation_primitive": "flow",
        "expected_effect": {"kind": "field_change",
                            "changes": {"field_values": {}}},
    }
    assert claim_title("automation-effect-claim", body) == \
        "The HL Auto Risk Rating automation makes its expected change"
    # No binding at all keeps the legacy generic fallback.
    nobind = {"expected_effect": {"kind": "field_change",
                                  "changes": {"field_values": {}}}}
    assert claim_title("automation-effect-claim", nobind) == \
        "An automation updates the record"


def test_capability_title_reads_granting_subject_not_grantee():
    # Regression: the body key is ``granting_subject`` (CapabilityClaimBody),
    # not ``grantee`` — the old code read the wrong key and fell back.
    body = {"granting_subject": {"external_id": "Sales User"},
            "target": {"external_id": "Opportunity"},
            "granted_capability": "edit"}
    assert claim_title("capability-claim", body) == "Sales User has edit on Opportunity"


def test_null_expected_value_renders_blank():
    body = {
        "subject": {"external_id": "Lead"},
        "to_state": {"field_values": {"OwnerId": {"kind": "null"}}},
    }
    assert claim_title("state-transition-claim", body) == "Lead: OwnerId becomes blank"


def test_new_kinds_still_fall_back_on_empty_body():
    assert claim_title("state-transition-claim", {}) == \
        "the record reaches the expected state"
    assert claim_title("automation-effect-claim", {}) == "An automation fires"


# ---------------------------------------------------------------------------
# Business labels in the spine (D-267) — claim_title resolves api names to org
# display names via the optional label map; without it the api name renders
# verbatim (the pre-label behavior, asserted above).
# ---------------------------------------------------------------------------

def test_existence_title_resolves_object_label():
    body = {"subject": {"external_id": "Case_SLA__c"}}
    labels = {"Case_SLA__c": "Case SLA"}
    assert claim_title("existence-claim", body, labels) == \
        "Case SLA exists in the org"
    # no map → the api name (unchanged fallback)
    assert claim_title("existence-claim", body) == "Case_SLA__c exists in the org"
    # map missing the key → the api name (graceful)
    assert claim_title("existence-claim", body, {"Other__c": "Other"}) == \
        "Case_SLA__c exists in the org"


def test_property_title_business_templates():
    labels = {"Case_SLA__c.SLA_Code__c": "SLA Code",
              "Case_SLA__c.Target_Hours__c": "SLA Target Hours"}
    length = {"subject": {"external_id": "Case_SLA__c.SLA_Code__c"},
              "property_name": "length", "expected_value": {"value": 8}}
    assert claim_title("property-claim", length, labels) == "SLA Code is 8 characters"
    precision = {"subject": {"external_id": "Case_SLA__c.Target_Hours__c"},
                 "property_name": "precision", "expected_value": {"value": 4}}
    assert claim_title("property-claim", precision, labels) == \
        "SLA Target Hours holds up to 4 digits"
    scale1 = {"subject": {"external_id": "Case_SLA__c.Target_Hours__c"},
              "property_name": "scale", "expected_value": {"value": 1}}
    assert claim_title("property-claim", scale1, labels) == \
        "SLA Target Hours has 1 decimal place"
    scale2 = {"subject": {"external_id": "Case_SLA__c.Target_Hours__c"},
              "property_name": "scale", "expected_value": {"value": 2}}
    assert claim_title("property-claim", scale2, labels) == \
        "SLA Target Hours has 2 decimal places"
    # the boolean field flags + field_type read as business sentences — no raw
    # schema column name on the spine
    is_unique = {"subject": {"external_id": "Case_SLA__c.SLA_Code__c"},
                 "property_name": "is_unique", "expected_value": {"value": True}}
    assert claim_title("property-claim", is_unique, labels) == "SLA Code must be unique"
    nillable_false = {"subject": {"external_id": "Case_SLA__c.SLA_Code__c"},
                      "property_name": "is_nillable", "expected_value": {"value": False}}
    assert claim_title("property-claim", nillable_false, labels) == "SLA Code is required"
    field_type = {"subject": {"external_id": "Case_SLA__c.SLA_Code__c"},
                  "property_name": "field_type", "expected_value": {"value": "Currency"}}
    assert claim_title("property-claim", field_type, labels) == \
        "SLA Code is a Currency field"
    # an unmapped property humanizes the column name — never raw snake_case
    other = {"subject": {"external_id": "Case_SLA__c.SLA_Code__c"},
             "property_name": "is_filterable", "expected_value": {"value": True}}
    assert claim_title("property-claim", other, labels) == \
        "SLA Code: is filterable is True"


def test_automation_effect_title_object_qualified_field_label():
    # the live SQ-205 shape: object-qualified field key → object + field labels,
    # no Flow name.
    body = {
        "automation": {"external_id": "SQ205_Create_Case_SLA"},
        "expected_effect": {
            "kind": "field_change",
            "changes": {"field_values": {
                "Case_SLA__c.Status__c": {"kind": "literal", "value": "Active"}}},
        },
    }
    labels = {"Case_SLA__c": "Case SLA", "Case_SLA__c.Status__c": "Status"}
    assert claim_title("automation-effect-claim", body, labels) == \
        'Automatically sets Case SLA Status to "Active"'


def test_humanize_attribution_relabels_stored_subject():
    from primeqa.intelligence.claim_presentation import humanize_attribution
    attribution = ("The asserted metadata for Object Case_SLA__c is present "
                   "(the inspection read returned it).")
    steps = [{"kind": "read", "subject_entity_type": "Object",
              "subject_external_id": "Case_SLA__c", "row_count": 1}]
    labels = {"Case_SLA__c": "Case SLA"}
    out = humanize_attribution(attribution, steps, labels)
    assert "the Case SLA object is present" in out
    assert "Case_SLA__c" not in out and "Object Case_SLA__c" not in out
    # no labels → unchanged; no read step → unchanged; falsy → passthrough
    assert humanize_attribution(attribution, steps, None) == attribution
    assert humanize_attribution(attribution, [], labels) == attribution
    assert humanize_attribution("", steps, labels) == ""


# ---------------------------------------------------------------------------
# Results-page helpers: cause_plain / duration_human / time_ago
# ---------------------------------------------------------------------------

def test_cause_plain_known_and_fallback():
    from primeqa.intelligence.claim_presentation import cause_plain
    assert cause_plain("enforcement_gap") == \
        "A forbidden action was allowed — the rule didn't fire"
    # unknown kind → humanized
    assert cause_plain("some_future_cause") == "Some future cause"
    assert cause_plain(None) is None
    assert cause_plain("") is None


def test_duration_human():
    from primeqa.intelligence.claim_presentation import duration_human
    assert duration_human(None) == ""
    assert duration_human(0) == "0 ms"
    assert duration_human(999) == "999 ms"
    assert duration_human(1000) == "1.0s"
    assert duration_human(9090) == "9.1s"
    assert duration_human("nope") == ""


def test_time_ago_relative_with_injected_now():
    from datetime import datetime, timedelta, timezone
    from primeqa.intelligence.claim_presentation import time_ago
    now = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)

    def iso(delta):
        return (now - delta).isoformat()

    assert time_ago(iso(timedelta(seconds=10)), now) == "just now"
    assert time_ago(iso(timedelta(minutes=5)), now) == "5m ago"
    assert time_ago(iso(timedelta(hours=2)), now) == "2h ago"
    assert time_ago(iso(timedelta(days=3)), now) == "3d ago"
    assert time_ago(iso(timedelta(days=30)), now) == "May 19"
    assert time_ago(None, now) == ""
    assert time_ago("not-a-date", now) == ""


def test_time_ago_naive_timestamp_treated_as_utc():
    from datetime import datetime, timezone
    from primeqa.intelligence.claim_presentation import time_ago
    now = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
    # a naive ISO string (no offset) must not raise and is read as UTC
    assert time_ago("2026-06-18T11:00:00", now) == "1h ago"


def test_depth_behavioral_when_any_data_recipe():
    assert claim_depth(["metadata-recipe", "data-recipe"]) == "behavioral"
    assert claim_depth(["data-recipe"]) == "behavioral"


def test_depth_config_check_otherwise():
    assert claim_depth(["metadata-recipe"]) == "configuration-check"
    assert claim_depth([]) == "configuration-check"
    assert claim_depth(None) == "configuration-check"


def test_verdict_plain_covers_full_s6_vocabulary():
    from primeqa.interpretation.model import Interpretation  # noqa: F401
    verdicts = [
        "prohibition_enforced", "prohibition_not_enforced",
        "rejected_unasserted_reason", "value_persisted", "value_not_persisted",
        "asserted_metadata_present", "asserted_metadata_absent",
        "asserted_value_matches", "asserted_value_differs", "not_evaluated",
    ]
    for v in verdicts:
        line = verdict_plain(v)
        assert line and line != "No result recorded", v


def test_verdict_plain_falls_back_to_outcome_then_default():
    assert verdict_plain(None, "passed") == "Passed"
    assert verdict_plain("unknown_verdict", "errored") == \
        "Could not run to completion"
    assert verdict_plain(None, None) == "No result recorded"


# --- D-272 Slice 1: not_evaluated plain line splits by failure_category -------

def test_verdict_plain_not_evaluated_default_is_re_runnable():
    # no failure_category (incl. every pre-D-272 caller) → the re-runnable line.
    line = verdict_plain("not_evaluated", "errored")
    assert "re-run" in line.lower()
    assert "needs attention" not in line.lower()


def test_verdict_plain_not_evaluated_indeterminate_categories_re_runnable():
    for cat in ("auth", "permission", "transient", "rate_limit", "unknown"):
        line = verdict_plain("not_evaluated", "errored", cat)
        assert "re-run" in line.lower(), cat
        assert "needs attention" not in line.lower(), cat


def test_verdict_plain_not_evaluated_permanent_needs_attention():
    line = verdict_plain("not_evaluated", "errored", "normalization")
    assert "needs attention" in line.lower()
    assert "re-run" not in line.lower()


def test_verdict_plain_not_evaluated_setup_rejection_names_the_setup():
    # a deterministic org rejection of the setup data — says so, and says a
    # re-run repeats it (never the generic permanent line).
    line = verdict_plain("not_evaluated", "errored", "setup_rejection")
    assert "setup data" in line.lower()
    assert "fail the same way" in line.lower()
    assert "could not be built" not in line.lower()


# --- org_rejection_message: the run page's "Salesforce said:" headline --------

def test_org_rejection_message_prefers_failed_step_message():
    steps = [
        {"kind": "read", "success": True},
        {"kind": "create", "success": False,
         "message": "KYC must be complete before moving to Credit Assessment.",
         "rejection_body": [{"message": "other"}]},
    ]
    assert org_rejection_message(steps) == \
        "KYC must be complete before moving to Credit Assessment."


def test_org_rejection_message_falls_back_to_rejection_body():
    steps = [{"kind": "update", "success": False, "message": None,
              "rejection_body": [{"message": "The VR said no.",
                                  "errorCode": "FIELD_CUSTOM_VALIDATION_EXCEPTION",
                                  "fields": []}]}]
    assert org_rejection_message(steps) == "The VR said no."


def test_org_rejection_message_none_when_no_failed_mutation():
    assert org_rejection_message([{"kind": "create", "success": True}]) is None
    assert org_rejection_message([{"kind": "read", "success": False}]) is None
    assert org_rejection_message([]) is None
    assert org_rejection_message(None) is None


def test_org_rejection_message_never_raises_on_malformed_steps():
    assert org_rejection_message([{"kind": "create", "success": False,
                                   "rejection_body": "not-a-list"},
                                  "junk", 42]) is None


# --- expected_binding: the shared expected-value dispatch ---------------------

def test_expected_binding_value_claim():
    body = {"subject": {"external_id": "Opportunity.Amount"},
            "expected_value": {"kind": "literal", "value": 5000}}
    assert expected_binding("value-claim", body) == ("Opportunity.Amount", "5000")


def test_expected_binding_automation_effect_field_change():
    body = {"expected_effect": {"kind": "field_change", "changes": {
        "field_values": {"Opportunity.Loan_to_Value__c": {
            "kind": "literal", "value": "50"}}}}}
    assert expected_binding("automation-effect-claim", body) == \
        ("Opportunity.Loan_to_Value__c", '"50"')


def test_expected_binding_state_transition_and_acceptance_update():
    st = {"to_state": {"field_values": {"Opportunity.StageName": {
        "kind": "literal", "value": "Approved"}}}}
    assert expected_binding("state-transition-claim", st) == \
        ("Opportunity.StageName", '"Approved"')
    acc = {"operation": "update", "update_state": {"field_values": {
        "Opportunity.Amount": {"kind": "literal", "value": 7500}}}}
    assert expected_binding("acceptance-claim", acc) == \
        ("Opportunity.Amount", "7500")


def test_expected_binding_none_for_unbound_kinds():
    assert expected_binding("prohibition-claim", {"operation": "create"}) is None
    assert expected_binding("acceptance-claim", {"operation": "create"}) is None
    assert expected_binding("automation-effect-claim",
                            {"expected_absence": True}) is None
    assert expected_binding("automation-effect-claim", {"expected_effect": {
        "kind": "blocked_operation"}}) is None
    assert expected_binding("existence-claim", {}) is None
    assert expected_binding("value-claim", "junk") is None    # never raises


# --- fmt_number ---------------------------------------------------------------

def test_fmt_number_shapes():
    assert fmt_number(5000000) == "5,000,000"
    assert fmt_number(50.0) == "50"          # the org's float vs asserted "50"
    assert fmt_number(62.5) == "62.5"
    assert fmt_number("10000000") == "10,000,000"
    assert fmt_number("Needs Analysis") == "Needs Analysis"
    assert fmt_number(True) == "True"        # bool is not a number for display
    assert fmt_number(None) == "None"
    assert fmt_number({"v": 1}) == "{'v': 1}"


def test_verdict_plain_other_verdicts_ignore_failure_category():
    # a non-not_evaluated verdict is unaffected by failure_category.
    assert verdict_plain("value_persisted", "passed", "normalization") == \
        verdict_plain("value_persisted", "passed")


# ---------------------------------------------------------------------------
# Refusal notes (D-206.1)
# ---------------------------------------------------------------------------

def test_refusal_plain_known_kind_with_detail():
    from primeqa.intelligence.claim_presentation import refusal_plain
    refusals = [{"refusal_kind": "emission-deferred",
                 "payload": {"detail": "data_behavior/state-transition-claim is "
                                       "groundable, but emission for this claim_kind "
                                       "is not yet built"}}]
    out = refusal_plain("emission-deferred", refusals)
    assert out.startswith("The engine understood the requirement")
    assert "state-transition-claim" in out          # the substrate detail rides along


def test_refusal_plain_missing_refs_list_detail():
    from primeqa.intelligence.claim_presentation import refusal_plain
    refusals = [{"refusal_kind": "no-relevant-context",
                 "payload": {"missing_refs": ["Object:Case_SLA__c", "Object:Escalation__c"]}}]
    out = refusal_plain("no-relevant-context", refusals)
    assert "do not exist in the synced org model" in out
    assert "Case_SLA__c" in out and "Escalation__c" in out


def test_refusal_plain_unknown_kind_humanized():
    from primeqa.intelligence.claim_presentation import refusal_plain
    assert refusal_plain("future-new-kind", None) == "Future new kind"


def test_refusal_plain_none_when_no_refusal():
    from primeqa.intelligence.claim_presentation import refusal_plain
    assert refusal_plain(None) is None
    assert refusal_plain("") is None


def test_refusal_plain_never_raises_on_garbage_payload():
    from primeqa.intelligence.claim_presentation import refusal_plain
    out = refusal_plain("ungrounded-claim", [{"payload": 42}, "junk", None])
    assert "no rule or configuration" in out


# ---------------------------------------------------------------------------
# Evidence-step lines (D-233)
# ---------------------------------------------------------------------------

def _import_step_plain():
    from primeqa.intelligence.claim_presentation import step_plain
    return step_plain


def test_step_plain_read():
    sp = _import_step_plain()
    assert sp({"kind": "read", "sobject": "Account", "row_count": 3}) == \
        "Read 3 Account rows"
    assert sp({"kind": "read", "sobject": "Case", "row_count": 1}) == \
        "Read 1 Case row"
    assert sp({"kind": "read", "sobject": "Opportunity",
               "error": {"message": "QUERY_TIMEOUT"}}) == \
        "Couldn't read Opportunity — QUERY_TIMEOUT"


def test_step_plain_assert():
    sp = _import_step_plain()
    assert sp({"kind": "assert", "held": True}) == \
        "Checked the records — the assertion held"
    assert sp({"kind": "assert", "held": False}) == \
        "Checked the records — the assertion did NOT hold"


def test_step_plain_create_positive_and_setup():
    sp = _import_step_plain()
    # matched is None → a positive/setup create (no rejection expected)
    assert sp({"kind": "create", "sobject": "Opportunity",
               "success": True, "matched": None}) == "Created a Opportunity"
    assert sp({"kind": "create", "sobject": "Opportunity",
               "success": False, "matched": None,
               "error_code": "REQUIRED_FIELD_MISSING"}) == \
        "Couldn't create the Opportunity (REQUIRED_FIELD_MISSING)"


def test_step_plain_create_negative_paths():
    sp = _import_step_plain()
    # rejection expected + matched → the forbidden create was blocked
    assert sp({"kind": "create", "sobject": "Lead", "success": False,
               "matched": True, "error_code": "FIELD_CUSTOM_VALIDATION_EXCEPTION"}) == \
        "Tried to create a forbidden Lead — Salesforce blocked it " \
        "(FIELD_CUSTOM_VALIDATION_EXCEPTION)"
    # rejection expected but the org ALLOWED it → a real defect
    assert sp({"kind": "create", "sobject": "Lead", "success": True,
               "matched": False}) == \
        "Created a Lead that should have been rejected — a real defect"
    # blocked, but by a different rule than the one under test
    assert sp({"kind": "create", "sobject": "Lead", "success": False,
               "matched": False, "error_code": "INSUFFICIENT_ACCESS"}) == \
        "Creation was blocked, but by a different rule (INSUFFICIENT_ACCESS)"


def test_step_plain_update_and_delete_negative():
    sp = _import_step_plain()
    assert sp({"kind": "update", "sobject": "Opportunity", "success": False,
               "matched": True, "error_code": "FIELD_CUSTOM_VALIDATION_EXCEPTION"}) == \
        "Tried the forbidden edit on Opportunity — Salesforce blocked it " \
        "(FIELD_CUSTOM_VALIDATION_EXCEPTION)"
    assert sp({"kind": "delete", "sobject": "Account", "success": True,
               "matched": False}) == \
        "Deleted the Account when it should have been blocked — a real defect"


def test_step_plain_error_surface_on_mutation():
    sp = _import_step_plain()
    assert sp({"kind": "create", "sobject": "Case",
               "error": {"phase": "create", "error_type": "TransportError",
                         "message": "connection reset"}}) == \
        "Couldn't attempt the Case create — connection reset"


def test_step_plain_falls_back_and_never_raises():
    sp = _import_step_plain()
    assert sp({"kind": "mystery_kind"}) == "mystery kind"
    assert sp({}) == "step"
    assert sp(None) == "Step"
    # garbage values must not raise
    assert sp({"kind": "create", "sobject": object(), "success": "yes",
               "matched": True})


def test_step_plain_dejargons_system_sobject_read():
    # D-267: a metadata/Tooling sobject must not leak into the spine — it reads
    # as a plain phrase. A data sobject keeps the established form.
    sp = _import_step_plain()
    assert sp({"kind": "read", "sobject": "EntityDefinition", "row_count": 1}) == \
        "Read the object definition (1 row)"
    assert sp({"kind": "read", "sobject": "FieldDefinition", "row_count": 3}) == \
        "Read the field definition (3 rows)"
    # a data read is unchanged (Account is already business language)
    assert sp({"kind": "read", "sobject": "Account", "row_count": 3}) == \
        "Read 3 Account rows"
    # a custom sobject resolves through the label map
    assert sp({"kind": "read", "sobject": "Case_SLA__c", "row_count": 2},
              {"Case_SLA__c": "Case SLA"}) == "Read 2 Case SLA rows"


def test_cross_field_exceeds_condition_renders_both_fields():
    # D-330: the v2 cross-field clause — "when Loan Amount exceeds Property Value"
    labels = {"Opportunity.Loan_Amount__c": "Loan Amount",
              "Opportunity.Property_Value__c": "Property Value"}
    body = {"target": {"external_id": "Opportunity"}, "operation": "modify_record"}
    conds = _conds({
        "subject": {"external_id": "Opportunity.Loan_Amount__c"},
        "predicate": "exceeds", "value": None,
        "compared_to": {"external_id": "Opportunity.Property_Value__c"}})
    assert claim_title("prohibition-claim", body, labels,
                       semantic_conditions=conds) == \
        "Rejects editing records on Opportunity when Loan Amount exceeds Property Value"
