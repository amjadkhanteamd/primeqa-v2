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
