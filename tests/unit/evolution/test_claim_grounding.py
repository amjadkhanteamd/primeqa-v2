"""Unit tests for the S8 claim-grounding leg (D-139).

Two-valued resolution verdicts over a stub :class:`SubjectResolver` (duck-typed),
plus the claim adapter that walks a real ``asserted_truth`` body for every
identity-bearing subject and resolves each. Claim bodies are constructed the same
way ``test_coverage.py`` does (the D-058 §5.4 walk this leg mirrors).
"""
from __future__ import annotations

from uuid import uuid4

from primeqa.evolution import (
    ClaimGroundingResult,
    claim_grounding_validity,
    claim_grounding_validity_for_claim,
)
from primeqa.test_representation import (
    IdentityBearingRef,
    LiteralValue,
    ProhibitionClaimBody,
    RejectionSignal,
    SemanticConditionsBody,
    ValueClaimBody,
)


def _ib(entity_type: str, external_id: str) -> IdentityBearingRef:
    return IdentityBearingRef(
        entity_type=entity_type, entity_id=uuid4(),
        version_seq=1, external_id=external_id)


class _Resolver:
    """Stub SubjectResolver — resolves everything except the ``gone`` set of
    ``(entity_type, external_id)`` pairs."""

    def __init__(self, *gone: tuple[str, str]):
        self._gone = set(gone)

    def resolves(self, entity_type: str, external_id: str) -> bool:
        return (entity_type, external_id) not in self._gone


# --- the primitive ---------------------------------------------------------

def test_subject_resolves_is_intact():
    r = claim_grounding_validity("Field", "Account.Industry", s1=_Resolver())
    assert r == ClaimGroundingResult("intact")


def test_subject_gone_is_broken():
    r = claim_grounding_validity(
        "Field", "Account.Industry", s1=_Resolver(("Field", "Account.Industry")))
    assert r.verdict == "broken"
    assert r.reason == "subject_not_resolved"
    assert r.unresolved == (("Field", "Account.Industry"),)


def test_reason_and_unresolved_only_on_broken():
    r = claim_grounding_validity("Object", "Account", s1=_Resolver())
    assert r.reason is None and r.unresolved == ()


# --- the claim adapter -----------------------------------------------------

def test_adapter_value_claim_subject_resolves_is_intact():
    body = ValueClaimBody(
        subject=_ib("Field", "Account.Industry"),
        expected_value=LiteralValue(value="Tech"))
    assert claim_grounding_validity_for_claim(body, s1=_Resolver()).verdict == "intact"


def test_adapter_prohibition_target_resolves_is_intact():
    body = ProhibitionClaimBody(
        target=_ib("Object", "Opportunity"),
        operation="delete", prohibition_mechanism="validation_rule",
        expected_rejection=RejectionSignal(error_code="X"))
    assert claim_grounding_validity_for_claim(body, s1=_Resolver()).verdict == "intact"


def test_adapter_value_claim_subject_gone_is_broken():
    body = ValueClaimBody(
        subject=_ib("Field", "Account.Industry"),
        expected_value=LiteralValue(value="Tech"))
    r = claim_grounding_validity_for_claim(
        body, s1=_Resolver(("Field", "Account.Industry")))
    assert r.verdict == "broken"
    assert r.unresolved == (("Field", "Account.Industry"),)


def test_adapter_two_refs_one_gone_is_broken():
    # prohibition target (an Object) resolves; the error_field (a Field, a SECOND
    # identity-bearing ref nested in the rejection signal) does not -> broken.
    body = ProhibitionClaimBody(
        target=_ib("Object", "Opportunity"),
        operation="modify_field", prohibition_mechanism="validation_rule",
        expected_rejection=RejectionSignal(
            error_code="X",
            error_field=_ib("Field", "Opportunity.StageName")))
    r = claim_grounding_validity_for_claim(
        body, s1=_Resolver(("Field", "Opportunity.StageName")))
    assert r.verdict == "broken"
    assert r.unresolved == (("Field", "Opportunity.StageName"),)  # only the gone one


def test_adapter_two_refs_both_resolve_is_intact():
    body = ProhibitionClaimBody(
        target=_ib("Object", "Opportunity"),
        operation="modify_field", prohibition_mechanism="validation_rule",
        expected_rejection=RejectionSignal(
            error_code="X",
            error_field=_ib("Field", "Opportunity.StageName")))
    assert claim_grounding_validity_for_claim(body, s1=_Resolver()).verdict == "intact"


def test_adapter_no_identity_refs_is_vacuously_intact():
    # a body carrying no IdentityBearingRef -> nothing to be ungrounded -> intact.
    assert claim_grounding_validity_for_claim(
        SemanticConditionsBody(), s1=_Resolver()).verdict == "intact"


def test_adapter_dedups_repeated_subject():
    # the same (entity_type, external_id) in two slots collapses to one entry in
    # ``unresolved`` (the walk dedups first-seen).
    same = _ib("Object", "Opportunity")
    body = ProhibitionClaimBody(
        target=same,
        operation="modify_field", prohibition_mechanism="validation_rule",
        expected_rejection=RejectionSignal(
            error_code="X",
            error_field=IdentityBearingRef(
                entity_type="Object", entity_id=uuid4(),
                version_seq=1, external_id="Opportunity")))
    r = claim_grounding_validity_for_claim(
        body, s1=_Resolver(("Object", "Opportunity")))
    assert r.verdict == "broken"
    assert r.unresolved == (("Object", "Opportunity"),)  # deduped, not twice
