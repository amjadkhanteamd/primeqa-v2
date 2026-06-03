"""Unit tests for the S8 field-value-validity leg (D-140).

Membership verdicts over a stub :class:`PicklistReader` (duck-typed: a dict from
``(object, field)`` to the active value frozenset, or absent = not a constrained
picklist), plus the recipe adapter that extracts the stored payload + target
object from a real ``DataRecipeBody``.
"""
from __future__ import annotations

import pytest

from primeqa.evolution import (
    FieldValueGroundingResult,
    field_value_grounding_validity,
    field_value_grounding_validity_for_recipe,
)
from primeqa.test_representation.models.primitives import RejectionExpectation
from primeqa.test_representation.models.recipes.data_recipe import (
    CreateStep,
    DataRecipeBody,
)
from primeqa.test_representation.models.references import LogicalRef


class _Reader:
    """Stub PicklistReader. ``sets`` maps ``(object, field)`` to the active value
    frozenset; a key absent from the map is not a constrained picklist (None)."""

    def __init__(self, sets: dict):
        self._sets = sets

    def active_values(self, object_external_id: str, field_api: str):
        return self._sets.get((object_external_id, field_api))


# --- the verdicts ----------------------------------------------------------

def test_active_value_is_intact():
    reader = _Reader({("Account", "Industry"): frozenset({"Technology", "Finance"})})
    r = field_value_grounding_validity({"Industry": "Technology"}, "Account", s1=reader)
    assert r == FieldValueGroundingResult("intact")


def test_removed_or_inactive_value_is_broken():
    # the reader returns only ACTIVE values, so a removed/deactivated value is
    # simply absent from the set -> broken/picklist_value_removed.
    reader = _Reader({("Account", "Industry"): frozenset({"Technology", "Finance"})})
    r = field_value_grounding_validity({"Industry": "Mining"}, "Account", s1=reader)
    assert r.verdict == "broken"
    assert r.reason == "picklist_value_removed"
    assert r.invalid == (("Industry", "Mining"),)


def test_non_picklist_field_skipped_is_intact():
    # the field is not a constrained picklist (reader returns None) -> not this
    # leg's concern -> skipped.
    r = field_value_grounding_validity({"Amount": 99}, "Account", s1=_Reader({}))
    assert r.verdict == "intact"


def test_null_value_skipped_is_intact():
    reader = _Reader({("Account", "Industry"): frozenset({"Technology"})})
    r = field_value_grounding_validity({"Industry": None}, "Account", s1=reader)
    assert r.verdict == "intact"


def test_only_invalid_value_collected():
    # two picklist fields, one valid one not -> broken, invalid names only the bad one.
    reader = _Reader({
        ("Account", "Industry"): frozenset({"Technology"}),
        ("Account", "Rating"): frozenset({"Hot", "Warm", "Cold"}),
    })
    r = field_value_grounding_validity(
        {"Industry": "Technology", "Rating": "Lukewarm"}, "Account", s1=reader)
    assert r.verdict == "broken"
    assert r.invalid == (("Rating", "Lukewarm"),)


def test_reason_and_invalid_only_on_broken():
    reader = _Reader({("Account", "Industry"): frozenset({"Technology"})})
    r = field_value_grounding_validity({"Industry": "Technology"}, "Account", s1=reader)
    assert r.reason is None and r.invalid == ()


# --- the recipe adapter ----------------------------------------------------

def _negative_recipe(field_values: dict) -> DataRecipeBody:
    return DataRecipeBody(
        api_choice="rest", identity_context="system",
        execution_mechanism="direct_api",
        steps=[CreateStep(
            step_id="s1",
            target_object=LogicalRef(entity_type="Object", external_id="Account"),
            field_values=field_values,
            expect_rejection=RejectionExpectation(
                error_code="FIELD_CUSTOM_VALIDATION_EXCEPTION"))],
    )


def test_adapter_extracts_payload_and_object():
    recipe = _negative_recipe({"Industry": "Mining"})
    reader = _Reader({("Account", "Industry"): frozenset({"Technology"})})
    r = field_value_grounding_validity_for_recipe(recipe, s1=reader)
    assert r.verdict == "broken" and r.invalid == (("Industry", "Mining"),)


def test_adapter_intact_when_value_present():
    recipe = _negative_recipe({"Industry": "Technology"})
    reader = _Reader({("Account", "Industry"): frozenset({"Technology"})})
    assert field_value_grounding_validity_for_recipe(recipe, s1=reader).verdict == "intact"


def test_adapter_raises_without_negative_step():
    recipe = DataRecipeBody(
        api_choice="rest", identity_context="system",
        execution_mechanism="direct_api",
        steps=[CreateStep(
            step_id="s1",
            target_object=LogicalRef(entity_type="Object", external_id="Account"),
            field_values={"Industry": "Technology"})],
    )
    with pytest.raises(ValueError, match="behavioral-negative"):
        field_value_grounding_validity_for_recipe(recipe, s1=_Reader({}))
