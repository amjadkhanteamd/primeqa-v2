"""C3 — band-interval witness synthesis (pure math, no PG, no LLM).

``interval_witness`` derives a value strictly interior to a first-match
band; ``guard_witness_values`` turns one grounded arm's guard + negation
context into the create-state witnesses that make THAT arm fire. Outside
the grammar both refuse (None / named detail) — never a guess."""
from __future__ import annotations

import pytest

from primeqa.generation.witnesses import (
    guard_witness_values, interval_witness)


# ── interval_witness ─────────────────────────────────────────────────

GTE = "GreaterThanOrEqualTo"
GT = "GreaterThan"
LTE = "LessThanOrEqualTo"
LT = "LessThan"


def test_top_band_is_threshold_plus_unit():
    assert interval_witness([(GTE, 250000.0, False)], 0) == 250001
    assert interval_witness([(GTE, 250000.0, False)], 2) == 250000.01


def test_interior_band_is_midpoint_by_scale():
    # Gold: >=50000 with ¬(>=250000) → [50000, 250000) → 150000
    assert interval_witness(
        [(GTE, 50000.0, False), (GTE, 250000.0, True)], 2) == 150000


def test_default_band_is_below_the_lowest():
    # Bronze: ¬(>=250000) ∧ ¬(>=50000) ∧ ¬(>=10000) → (-inf, 10000)
    conds = [(GTE, 250000.0, True), (GTE, 50000.0, True),
             (GTE, 10000.0, True)]
    assert interval_witness(conds, 0) == 9999
    assert interval_witness(conds, 2) == 9999.99


def test_witness_satisfies_every_original_constraint():
    conds = [(GT, 10.0, False), (LT, 10.6, False)]
    w = interval_witness(conds, 1)
    assert w is not None and 10.0 < w < 10.6


def test_degenerate_one_unit_band_falls_back_to_the_bound():
    # [10, 10] after adjustment: >=10 ∧ <=10 → only member is 10 itself
    assert interval_witness([(GTE, 10, False), (LTE, 10, False)], 0) == 10


def test_empty_band_returns_none():
    assert interval_witness([(GTE, 100, False), (LT, 100, False)], 0) is None
    assert interval_witness([(GT, 5, False), (LT, 5, False)], 0) is None


def test_out_of_grammar_operator_returns_none():
    assert interval_witness([("EqualTo", 5, False)], 0) is None
    assert interval_witness([(GTE, "not-a-number", False)], 0) is None
    assert interval_witness([], 0) is None


def test_integral_witness_is_an_int_fractional_a_float():
    assert interval_witness([(GTE, 10, False)], 0) == 11
    assert isinstance(interval_witness([(GTE, 10, False)], 0), int)
    assert isinstance(interval_witness([(GTE, 10, False)], 2), float)


# ── guard_witness_values ─────────────────────────────────────────────

AMT = "PLS_FB_Amount__c"
TIER = "PLS_FB_Tier__c"


def _scale2(_bare):
    return 2


def test_gold_arm_witness():
    status, wit = guard_witness_values(
        guard=((AMT, GTE, 50000.0),),
        negated_guards=((AMT, GTE, 250000.0),),
        exclude_field=TIER, scale_of=_scale2)
    assert status == "ok" and wit == {AMT: 150000}


def test_default_arm_witness_comes_from_negations_alone():
    status, wit = guard_witness_values(
        guard=(),
        negated_guards=((AMT, GTE, 250000.0), (AMT, GTE, 50000.0),
                        (AMT, GTE, 10000.0)),
        exclude_field=TIER, scale_of=_scale2)
    assert status == "ok" and wit == {AMT: 9999.99}


def test_isnull_and_effect_field_conditions_stage_nothing():
    # FL01's shape: the only guard is IsNull on the effect field itself —
    # omission (k16) satisfies it, so the witness set is empty, not a refusal
    status, wit = guard_witness_values(
        guard=(("PLS_FB_Priority__c", "IsNull", True),),
        negated_guards=(),
        exclude_field="PLS_FB_Priority__c", scale_of=_scale2)
    assert (status, wit) == ("ok", {})
    # IsNull on ANOTHER field is likewise omission-satisfied
    status, wit = guard_witness_values(
        guard=(("Other__c", "IsNull", True),), negated_guards=(),
        exclude_field=TIER, scale_of=_scale2)
    assert (status, wit) == ("ok", {})


def test_single_positive_equality_stages_the_literal():
    status, wit = guard_witness_values(
        guard=(("Status__c", "EqualTo", "Open"),), negated_guards=(),
        exclude_field=TIER, scale_of=_scale2)
    assert (status, wit) == ("ok", {"Status__c": "Open"})


def test_empty_band_refuses_with_named_detail():
    status, detail = guard_witness_values(
        guard=((AMT, GTE, 100.0), (AMT, LT, 100.0)), negated_guards=(),
        exclude_field=TIER, scale_of=_scale2)
    assert status == "refuse" and "empty" in detail


def test_unknown_scale_refuses():
    status, detail = guard_witness_values(
        guard=((AMT, GTE, 100.0),), negated_guards=(),
        exclude_field=TIER, scale_of=lambda _b: None)
    assert status == "refuse" and "scale" in detail


@pytest.mark.parametrize("conds", [
    ((AMT, "NotEqualTo", 5),),                       # NotEqualTo
    (("Status__c", "EqualTo", "Open"),
     ("Status__c", "EqualTo", "Closed")),            # two equalities
    ((AMT, GTE, 5), (AMT, "EqualTo", 7)),            # mixed numeric+equality
])
def test_out_of_grammar_guard_shapes_refuse(conds):
    status, detail = guard_witness_values(
        guard=conds, negated_guards=(),
        exclude_field=TIER, scale_of=_scale2)
    assert status == "refuse" and "witness grammar" in detail


def test_negated_equality_refuses():
    status, detail = guard_witness_values(
        guard=(), negated_guards=(("Status__c", "EqualTo", "Open"),),
        exclude_field=TIER, scale_of=_scale2)
    assert status == "refuse"


def test_multi_field_guards_derive_independently():
    status, wit = guard_witness_values(
        guard=((AMT, GTE, 10.0), ("Qty__c", "EqualTo", 3)),
        negated_guards=(),
        exclude_field=TIER, scale_of=lambda b: 0)
    assert status == "ok" and wit == {AMT: 11, "Qty__c": 3}
