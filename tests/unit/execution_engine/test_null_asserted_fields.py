"""Unit tests for the D-338 asserted-blank resolution at the run chokepoint.

``_null_asserted_fields_of`` reads the claim's ``semantic_conditions`` through
the S2 coordinator (pinned to the recipe's ``claim_version_seq``; latest when
unpinned) and extracts the ``is_null``-conditioned field external_ids — the
business state the claim asserts as BLANK, which D-305 stages by ABSENCE so
the recipe's ``field_values`` cannot carry it."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from primeqa.execution_engine import run as run_module
from primeqa.execution_engine.run import _null_asserted_fields_of
from primeqa.test_representation.models.conditions import (
    Condition,
    ConditionV2,
    SemanticConditionsBody,
    SemanticConditionsBodyV2,
)
from primeqa.test_representation.models.references import IdentityBearingRef


def _ref(external_id):
    return IdentityBearingRef(entity_type="Field", entity_id=uuid4(),
                              version_seq=3, external_id=external_id)


class _FakeCoord:
    def __init__(self, claim):
        self._claim = claim
        self.version_calls, self.latest_calls = [], []

    def get_claim_version(self, session, test_id, version_seq):
        self.version_calls.append((test_id, version_seq))
        return self._claim

    def get_latest_claim(self, session, test_id):
        self.latest_calls.append(test_id)
        return self._claim


def _patch(monkeypatch, claim):
    coord = _FakeCoord(claim)
    monkeypatch.setattr(run_module, "SemanticTransactionCoordinator",
                        lambda: coord)
    return coord


def _recipe(version_seq=4):
    return SimpleNamespace(claim_test_id=uuid4(), claim_version_seq=version_seq)


def test_extracts_only_is_null_subjects_v1(monkeypatch):
    body = SemanticConditionsBody(conditions=[
        Condition(subject=_ref("Opportunity.StageName"), predicate="equals",
                  value="Credit Assessment"),
        Condition(subject=_ref("Opportunity.Credit_Score__c"),
                  predicate="is_null"),
        Condition(subject=_ref("Opportunity.KYC_Complete__c"),
                  predicate="is_not_null"),
    ])
    _patch(monkeypatch, SimpleNamespace(semantic_conditions=body))
    assert _null_asserted_fields_of(object(), _recipe()) == frozenset(
        {"Opportunity.Credit_Score__c"})


def test_extracts_is_null_from_v2_body_too(monkeypatch):
    body = SemanticConditionsBodyV2(conditions=[
        ConditionV2(subject=_ref("Opportunity.Loan_Amount__c"),
                    predicate="exceeds",
                    compared_to=_ref("Opportunity.Property_Value__c")),
        ConditionV2(subject=_ref("Opportunity.Insurance_Doc__c"),
                    predicate="is_null"),
    ])
    _patch(monkeypatch, SimpleNamespace(semantic_conditions=body))
    assert _null_asserted_fields_of(object(), _recipe()) == frozenset(
        {"Opportunity.Insurance_Doc__c"})


def test_pinned_version_read_when_recipe_pins_the_claim(monkeypatch):
    coord = _patch(monkeypatch, SimpleNamespace(
        semantic_conditions=SemanticConditionsBody(conditions=[])))
    recipe = _recipe(version_seq=4)
    assert _null_asserted_fields_of(object(), recipe) == frozenset()
    assert coord.version_calls == [(recipe.claim_test_id, 4)]
    assert coord.latest_calls == []


def test_latest_read_when_recipe_is_unpinned(monkeypatch):
    coord = _patch(monkeypatch, SimpleNamespace(
        semantic_conditions=SemanticConditionsBody(conditions=[])))
    recipe = _recipe(version_seq=None)
    assert _null_asserted_fields_of(object(), recipe) == frozenset()
    assert coord.latest_calls == [recipe.claim_test_id]
    assert coord.version_calls == []


def test_missing_claim_is_the_empty_set(monkeypatch):
    # Hand-built recipes with no claim row — the pre-D-338 behavior.
    _patch(monkeypatch, None)
    assert _null_asserted_fields_of(object(), _recipe()) == frozenset()


def test_conditions_free_body_is_the_empty_set(monkeypatch):
    # A claim whose conditions body carries no ``conditions`` attribute
    # (condition-free kinds) resolves to no asserted-blank fields.
    _patch(monkeypatch, SimpleNamespace(semantic_conditions=object()))
    assert _null_asserted_fields_of(object(), _recipe()) == frozenset()


def test_injected_coordinator_is_used():
    # The run paths thread their own coordinator; the helper must not build
    # a fresh one when given it.
    body = SemanticConditionsBody(conditions=[
        Condition(subject=_ref("Account.Blank__c"), predicate="is_null")])
    coord = _FakeCoord(SimpleNamespace(semantic_conditions=body))
    recipe = _recipe(version_seq=2)
    out = _null_asserted_fields_of(object(), recipe, coordinator=coord)
    assert out == frozenset({"Account.Blank__c"})
    assert coord.version_calls == [(recipe.claim_test_id, 2)]
