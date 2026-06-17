"""Unit: the D-206 plain-English claim presentation (pure, deterministic)."""
from __future__ import annotations

from primeqa.intelligence.claim_presentation import (
    claim_depth,
    claim_title,
    verdict_plain,
)


def test_prohibition_title():
    body = {"target": {"external_id": "Opportunity"}, "operation": "modify_field"}
    assert claim_title("prohibition-claim", body) == \
        "Rejects editing fields on Opportunity"


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
    body = {
        "automation": {"external_id": "SLA_Escalation_Flow"},
        "expected_effect": {
            "kind": "field_change",
            "changes": {"field_values": {"SLA_Deadline__c": {"kind": "literal", "value": 24}}},
        },
    }
    assert claim_title("automation-effect-claim", body) == \
        "SLA_Escalation_Flow sets SLA_Deadline__c to 24"


def test_automation_effect_title_blocked_and_side_effect():
    blocked = {"automation": {"external_id": "Amount_Guard"},
               "expected_effect": {"kind": "blocked_operation", "reason": "too big"}}
    assert claim_title("automation-effect-claim", blocked) == \
        "Amount_Guard blocks the change"
    side = {"automation": {"external_id": "Case_Email_Alert"},
            "expected_effect": {"kind": "side_effect"}}
    assert claim_title("automation-effect-claim", side) == \
        "Case_Email_Alert fires a side effect"


def test_automation_effect_title_falls_back_when_no_effect():
    body = {"automation": {"external_id": "Some_Flow"}}
    assert claim_title("automation-effect-claim", body) == "Some_Flow fires"


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
    assert claim_title("automation-effect-claim", {}) == "the automation fires"


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
