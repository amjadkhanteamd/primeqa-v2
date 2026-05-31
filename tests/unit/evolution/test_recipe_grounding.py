"""Unit tests for the S8 recipe-grounding leg (D-113).

Object-level, evaluate-based verdicts over a stub VR-reader (duck-typed — a
local ``_Vr`` with ``is_active`` + ``formula_text`` stands in for S6's VrMeta,
which S8 does not import). Plus the recipe adapter that extracts the stored
payload + target object from a real ``DataRecipeBody``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest

from primeqa.evolution import (
    RecipeGroundingResult,
    recipe_grounding_validity,
    recipe_grounding_validity_for_recipe,
)
from primeqa.test_representation.models.primitives import RejectionExpectation
from primeqa.test_representation.models.recipes.data_recipe import (
    CreateStep,
    DataRecipeBody,
)
from primeqa.test_representation.models.references import LogicalRef


@dataclass(frozen=True)
class _Vr:
    """Stub VR metadata — duck-types S8's VrReader return shape."""
    is_active: bool
    formula_text: Optional[str]


class _Reader:
    def __init__(self, *vrs: _Vr):
        self._vrs = vrs

    def vrs_for_object(self, _subject_external_id: str) -> tuple:
        return self._vrs


# --- the verdicts ----------------------------------------------------------

def test_intact_basic():
    r = recipe_grounding_validity(
        {"Amount": 99}, "Obj", s1=_Reader(_Vr(True, "Amount < 100")))
    assert r == RecipeGroundingResult("intact")


def test_loosened_but_still_violating_is_intact():
    # current formula loosened (`< 200`); the stored payload (Amount=99) still
    # fires it -> intact. The proxy (re-derive + compare) would call this drifted.
    r = recipe_grounding_validity(
        {"Amount": 99}, "Obj", s1=_Reader(_Vr(True, "Amount < 200")))
    assert r.verdict == "intact"


def test_tightened_formula_is_drifted():
    # active, evaluable VR exists but the payload no longer trips it -> drifted.
    r = recipe_grounding_validity(
        {"Amount": 99}, "Obj", s1=_Reader(_Vr(True, "Amount < 50")))
    assert r == RecipeGroundingResult("drifted")


def test_deactivated_but_another_active_catches_is_intact():
    # the payload would fire the *deactivated* VR, but a different ACTIVE VR also
    # catches it -> still rejected -> intact (object-level behavioral verdict).
    reader = _Reader(
        _Vr(False, "ISBLANK(Reason__c)"),     # would fire, but inactive
        _Vr(True, "Amount < 100"),            # active, also fires
    )
    r = recipe_grounding_validity(
        {"Reason__c": None, "Amount": 99}, "Obj", s1=reader)
    assert r.verdict == "intact"


def test_deactivated_grounding_with_only_untriggered_active_is_drifted():
    # the VR the payload would fire is deactivated; the one active VR is not
    # triggered -> the prohibition is no longer enforced -> drifted (re-groundable).
    reader = _Reader(
        _Vr(False, "ISBLANK(Reason__c)"),     # would fire, but inactive
        _Vr(True, "Amount < 50"),             # active, NOT fired by Amount=99
    )
    r = recipe_grounding_validity(
        {"Reason__c": None, "Amount": 99}, "Obj", s1=reader)
    assert r == RecipeGroundingResult("drifted")


def test_no_active_vr_is_broken_no_active_vr():
    # all VRs inactive -> no foundation to ground against.
    r = recipe_grounding_validity(
        {"X": None}, "Obj", s1=_Reader(_Vr(False, "ISBLANK(X)")))
    assert r == RecipeGroundingResult("broken", reason="no_active_vr")


def test_empty_object_is_broken_no_active_vr():
    r = recipe_grounding_validity({"X": None}, "Obj", s1=_Reader())
    assert r == RecipeGroundingResult("broken", reason="no_active_vr")


def test_structural_non_evaluable_is_broken_formula_non_evaluable():
    # active VR present, but its current formula left the single-object subset
    # (org-state) -> can't confirm violation -> broken/formula_non_evaluable.
    r = recipe_grounding_validity(
        {}, "Obj", s1=_Reader(_Vr(True, "ISCHANGED(Status)")))
    assert r == RecipeGroundingResult("broken", reason="formula_non_evaluable")


def test_reason_is_set_only_on_broken():
    intact = recipe_grounding_validity(
        {"Amount": 99}, "Obj", s1=_Reader(_Vr(True, "Amount < 100")))
    assert intact.reason is None


# --- the recipe adapter ----------------------------------------------------

def _negative_recipe(field_values: dict) -> DataRecipeBody:
    return DataRecipeBody(
        api_choice="rest",
        identity_context="system",
        execution_mechanism="direct_api",
        steps=[
            CreateStep(
                step_id="s1",
                target_object=LogicalRef(entity_type="Object", external_id="Account"),
                field_values=field_values,
                expect_rejection=RejectionExpectation(
                    error_code="FIELD_CUSTOM_VALIDATION_EXCEPTION"),
            )
        ],
    )


def test_adapter_extracts_payload_and_object():
    recipe = _negative_recipe({"Amount": 99})
    r = recipe_grounding_validity_for_recipe(
        recipe, s1=_Reader(_Vr(True, "Amount < 100")))
    assert r.verdict == "intact"


def test_adapter_raises_without_negative_step():
    # an ordinary create (no expect_rejection) is not a behavioral negative.
    recipe = DataRecipeBody(
        api_choice="rest", identity_context="system",
        execution_mechanism="direct_api",
        steps=[CreateStep(
            step_id="s1",
            target_object=LogicalRef(entity_type="Object", external_id="Account"),
            field_values={"Amount": 99})],
    )
    with pytest.raises(ValueError, match="behavioral-negative"):
        recipe_grounding_validity_for_recipe(recipe, s1=_Reader())
