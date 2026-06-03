"""Unit tests for the S8 two-level grounding-validity composition (D-141).

Pure: constructs an :class:`Artifact` (a claim body + recipes) and three stub
ports (subjects / vrs / picklists), and asserts the composed verdict — the
non-collapse + un-masking properties especially. No DB, no org.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import uuid4

from primeqa.evolution import Artifact, grounding_validity
from primeqa.test_representation import (
    IdentityBearingRef,
    LiteralValue,
    ValueClaimBody,
)
from primeqa.test_representation.models.primitives import RejectionExpectation
from primeqa.test_representation.models.recipes.data_recipe import (
    CreateStep,
    DataRecipeBody,
)
from primeqa.test_representation.models.references import LogicalRef


# --- stub ports ------------------------------------------------------------

class _Subjects:
    def __init__(self, *gone: tuple[str, str]):
        self._gone = set(gone)

    def resolves(self, entity_type: str, external_id: str) -> bool:
        return (entity_type, external_id) not in self._gone


@dataclass(frozen=True)
class _Vr:
    is_active: bool
    formula_text: Optional[str]


class _Vrs:
    def __init__(self, *vrs: _Vr):
        self._vrs = vrs

    def vrs_for_object(self, _ext: str) -> tuple:
        return self._vrs


class _Picklists:
    def __init__(self, sets: Optional[dict] = None):
        self._sets = sets or {}

    def active_values(self, object_external_id: str, field_api: str):
        return self._sets.get((object_external_id, field_api))


# --- fixtures --------------------------------------------------------------

def _claim(external_id: str = "Account.Industry") -> ValueClaimBody:
    return ValueClaimBody(
        subject=IdentityBearingRef(
            entity_type="Field", entity_id=uuid4(),
            version_seq=1, external_id=external_id),
        expected_value=LiteralValue(value="Tech"))


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


def _positive_recipe() -> DataRecipeBody:
    return DataRecipeBody(
        api_choice="rest", identity_context="system",
        execution_mechanism="direct_api",
        steps=[CreateStep(
            step_id="s1",
            target_object=LogicalRef(entity_type="Object", external_id="Account"),
            field_values={"Amount": 5})],   # no expect_rejection -> not a negative
    )


_FIRES = _Vrs(_Vr(True, "Amount < 100"))      # payload Amount=99 -> intact
_DRIFTS = _Vrs(_Vr(True, "Amount < 50"))      # payload Amount=99 -> drifted


# --- the composition -------------------------------------------------------

def test_all_intact_parts_addressable():
    art = Artifact(claim=_claim(), recipes=(_negative_recipe({"Amount": 99}),))
    out = grounding_validity(
        art, subjects=_Subjects(), vrs=_FIRES, picklists=_Picklists())
    assert out.overall == "intact"
    assert out.claim_grounding.verdict == "intact"
    assert len(out.recipe_verdicts) == 1
    assert out.recipe_verdicts[0].rolled_up == "intact"


def test_claim_broken_recipes_intact_is_overall_broken_not_collapsed():
    # the subject is gone -> claim broken -> overall broken, but the recipe parts
    # stay individually intact (Fork C: composed, never collapsed).
    art = Artifact(claim=_claim(), recipes=(_negative_recipe({"Amount": 99}),))
    out = grounding_validity(
        art, subjects=_Subjects(("Field", "Account.Industry")),
        vrs=_FIRES, picklists=_Picklists())
    assert out.overall == "broken"
    assert out.claim_grounding.verdict == "broken"
    assert out.recipe_verdicts[0].rolled_up == "intact"          # NOT collapsed
    assert out.recipe_verdicts[0].recipe_grounding.verdict == "intact"


def test_field_value_broken_unmasks_recipe_grounding_intact():
    # the payload still fires the VR (recipe-grounding intact) but its picklist
    # value is removed (field-value broken) -> the recipe rolls up broken.
    art = Artifact(
        claim=_claim(),
        recipes=(_negative_recipe({"Amount": 99, "Industry": "Mining"}),))
    out = grounding_validity(
        art, subjects=_Subjects(), vrs=_FIRES,
        picklists=_Picklists({("Account", "Industry"): frozenset({"Technology"})}))
    rv = out.recipe_verdicts[0]
    assert rv.recipe_grounding.verdict == "intact"               # VR still fires
    assert rv.field_value.verdict == "broken"                    # value gone
    assert rv.rolled_up == "broken"                              # un-masked
    assert out.overall == "broken"


def test_claim_intact_one_recipe_drifts_is_overall_drifted():
    art = Artifact(claim=_claim(), recipes=(_negative_recipe({"Amount": 99}),))
    out = grounding_validity(
        art, subjects=_Subjects(), vrs=_DRIFTS, picklists=_Picklists())
    assert out.recipe_verdicts[0].rolled_up == "drifted"
    assert out.overall == "drifted"


def test_multi_recipe_per_recipe_addressable():
    # two recipes -> two RecipeVerdicts, each carrying its source recipe by
    # identity so a caller can correlate a verdict back to its S2 row.
    first_r = _negative_recipe({"Amount": 99})
    second_r = _negative_recipe({"Amount": 99})
    art = Artifact(claim=_claim(), recipes=(first_r, second_r))
    out = grounding_validity(
        art, subjects=_Subjects(), vrs=_FIRES, picklists=_Picklists())
    assert len(out.recipe_verdicts) == 2
    assert out.recipe_verdicts[0].recipe is first_r
    assert out.recipe_verdicts[1].recipe is second_r


def test_no_negative_recipe_overall_is_claim_verdict():
    # an artifact with no recipes -> empty recipe_verdicts, overall = claim.
    art = Artifact(claim=_claim(), recipes=())
    out = grounding_validity(
        art, subjects=_Subjects(), vrs=_FIRES, picklists=_Picklists())
    assert out.recipe_verdicts == ()
    assert out.overall == "intact" == out.claim_grounding.verdict


def test_non_negative_recipe_is_skipped_not_faked():
    # a positive (non-negative) recipe is not covered by the recipe legs -> it
    # produces no RecipeVerdict (skipped, not fabricated).
    art = Artifact(
        claim=_claim(),
        recipes=(_negative_recipe({"Amount": 99}), _positive_recipe()))
    out = grounding_validity(
        art, subjects=_Subjects(), vrs=_FIRES, picklists=_Picklists())
    assert len(out.recipe_verdicts) == 1                         # only the negative
    assert out.overall == "intact"
