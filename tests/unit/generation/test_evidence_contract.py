"""D-345 claim-strength ↔ evidence-strength contract classifier."""
from primeqa.generation.evidence_contract import (
    EvidenceTier, required_evidence, provided_evidence, meets_contract,
)


def _reject(pattern):
    return {"kind": "data-recipe", "steps": [
        {"kind": "create", "field_values": {"F__c": 1}},
        {"kind": "update", "field_changes": {"F__c": 2},
         "expect_rejection": {"error_code": "FIELD_CUSTOM_VALIDATION_EXCEPTION",
                              "error_message_pattern": pattern}}]}


def _accept(readback):
    steps = [{"kind": "create", "field_values": {"F__c": 1},
              "expect_acceptance": True}]
    if readback:
        steps += [{"kind": "read", "soql": "SELECT Id ..."},
                  {"kind": "assert", "predicate": {"predicate": "exists"}}]
    return {"kind": "data-recipe", "steps": steps}


def _metadata():
    return {"kind": "metadata-recipe", "steps": [
        {"kind": "read_metadata", "target_entity": {"external_id": "Obj.F__c"}},
        {"kind": "assert", "predicate": {"predicate": "exists"}}]}


# --- required tiers ---------------------------------------------------------

def test_required_evidence_by_kind():
    assert required_evidence("existence-claim") == EvidenceTier.STRUCTURAL
    assert required_evidence("prohibition-claim") == EvidenceTier.ATTRIBUTED
    assert required_evidence("acceptance-claim") == EvidenceTier.ATTRIBUTED
    assert required_evidence("unknown-kind") == EvidenceTier.ATTRIBUTED   # fail-honest


# --- provided tiers ---------------------------------------------------------

def test_prohibition_with_message_is_attributed():
    assert provided_evidence("prohibition-claim",
                             [_reject("Contract Number is required")]) == EvidenceTier.ATTRIBUTED


def test_prohibition_without_message_is_outcome_only():
    assert provided_evidence("prohibition-claim",
                             [_reject(None)]) == EvidenceTier.OUTCOME


def test_acceptance_with_readback_is_attributed():
    assert provided_evidence("acceptance-claim",
                             [_accept(readback=True)]) == EvidenceTier.ATTRIBUTED


def test_acceptance_without_readback_is_outcome():
    assert provided_evidence("acceptance-claim",
                             [_accept(readback=False)]) == EvidenceTier.OUTCOME


def test_metadata_only_is_structural():
    assert provided_evidence("existence-claim", [_metadata()]) == EvidenceTier.STRUCTURAL


def test_empty_is_none():
    assert provided_evidence("prohibition-claim", []) == EvidenceTier.NONE


# --- the contract -----------------------------------------------------------

def test_attributed_prohibition_meets_contract():
    assert meets_contract("prohibition-claim", [_reject("Approval Reason required")])


def test_outcome_only_prohibition_fails_contract():
    # rejected, but not attributed to WHICH rule -> not trustworthy.
    assert not meets_contract("prohibition-claim", [_reject(None)])


def test_existence_structural_meets_its_own_contract():
    assert meets_contract("existence-claim", [_metadata()])


def test_existence_evidence_does_not_meet_behavioural_contract():
    # the T7 wrong-green: STRUCTURAL evidence cannot back a behavioural claim.
    assert not meets_contract("prohibition-claim", [_metadata()])
