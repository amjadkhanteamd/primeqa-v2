"""D-223 — the pre-enqueue executability gate.

Two layers:

1. ``check_recipe_executability`` — dry-runs S4's own pure machinery (bridge
   projection + per-read translator) over recipes authored by S3's REAL
   emission paths, and the ``EXPECTED_EXECUTABILITY`` map binds every
   ``EMITTABLE`` pair to a decided verdict (the D-105.1 lockstep discipline,
   pointed at executability instead of authorability). Adding an emittable
   kind without deciding its executability trips the set-equality assert;
   D-224's translators flip capability/layout to ``True`` VISIBLY here.

2. ``gate_enqueue`` — the ≥1-eligible-all-fail refusal semantics, with the
   coordinator read monkeypatched (no PG).
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from primeqa.execution_engine.errors import (
    ExecutionEngineError,
    PlanTranslationError,
    UnexecutableClaimError,
)
from primeqa.execution_engine.executability import (
    check_recipe_executability,
    gate_enqueue,
)
from primeqa.generation.emission import (
    EMITTABLE,
    GroundedAutomationEffect,
    GroundedCapability,
    GroundedEmission,
    GroundedExistence,
    GroundedLayout,
    GroundedNegative,
    GroundedPositive,
    GroundedProperty,
    GroundedStateTransition,
    _Endpoint,
    author_emission,
)
from primeqa.test_representation.coordinator import RecipeRead


def _ep(entity_type: str, external_id: str) -> _Endpoint:
    return _Endpoint(entity_id=uuid4(), entity_type=entity_type,
                     external_id=external_id)


# One grounded shape per EMITTABLE pair — mirrors the authorability lockstep
# map in tests/unit/generation/test_governance_unit.py (which guards
# EMITTABLE-completeness); THIS map guards decided executability. The
# property shape deliberately uses ``length`` (the executable representative;
# the is_required refusal gets its own test below).
_SHAPES = {
    ("configuration", "metadata-relationship-claim"): lambda: GroundedEmission(
        archetype="configuration", claim_kind="metadata-relationship-claim",
        edge_type="APPLIES_TO", version_seq=1,
        source=_ep("ValidationRule", "Case.RequireReason"),
        target=_ep("Object", "Case"), requirement_excerpt="x"),
    ("configuration", "existence-claim"): lambda: GroundedExistence(
        archetype="configuration", claim_kind="existence-claim", version_seq=1,
        subject=_ep("Field", "Account.Industry"), requirement_excerpt="x"),
    ("configuration", "property-claim"): lambda: GroundedProperty(
        archetype="configuration", claim_kind="property-claim", version_seq=1,
        subject=_ep("Field", "Account.Industry"), property_name="length",
        expected_value=255, requirement_excerpt="x"),
    ("permission", "capability-claim"): lambda: GroundedCapability(
        archetype="permission", claim_kind="capability-claim", version_seq=1,
        granting_subject=_ep("Profile", "Admin"),
        target=_ep("Field", "Account.AnnualRevenue"),
        granted_capability="edit", grant_type="field", requirement_excerpt="x"),
    ("ui", "layout-claim"): lambda: GroundedLayout(
        archetype="ui", claim_kind="layout-claim", version_seq=1,
        layout=_ep("Layout", "Account-Account Layout"),
        field=_ep("Field", "Account.AnnualRevenue"), requirement_excerpt="x"),
    ("data_behavior", "prohibition-claim"): lambda: GroundedNegative(
        archetype="data_behavior", claim_kind="prohibition-claim",
        operation_hint=None, version_seq=1,
        subject=_ep("Object", "Case"), requirement_excerpt="x"),
    ("data_behavior", "value-claim"): lambda: GroundedPositive(
        archetype="data_behavior", claim_kind="value-claim", version_seq=1,
        target_object=_ep("Object", "Invoice"),
        field=_ep("Field", "Invoice.Amount"),
        value="100", requirement_excerpt="x"),
    ("data_behavior", "state-transition-claim"): lambda: GroundedStateTransition(
        archetype="data_behavior", claim_kind="state-transition-claim",
        version_seq=1, subject=_ep("Object", "Case"),
        field=_ep("Field", "Case.Status"), to_value="In Escalation",
        requirement_excerpt="x"),
    ("data_behavior", "automation-effect-claim"): lambda: GroundedAutomationEffect(
        archetype="data_behavior", claim_kind="automation-effect-claim",
        version_seq=1, subject=_ep("Object", "Case"),
        automation=_ep("Flow", "Escalate_Case"), requirement_excerpt="x",
        effect_field=_ep("Field", "Case.Status"),
        effect_value="In Escalation"),
}

# The decided verdict per pair — the D-223 contract. capability + layout stay
# False until D-224 lands their translators.
EXPECTED_EXECUTABILITY = {
    ("configuration", "metadata-relationship-claim"): True,
    ("configuration", "existence-claim"): True,
    ("configuration", "property-claim"): True,
    ("permission", "capability-claim"): False,
    ("ui", "layout-claim"): False,
    ("data_behavior", "prohibition-claim"): True,
    ("data_behavior", "value-claim"): True,
    ("data_behavior", "state-transition-claim"): True,
    ("data_behavior", "automation-effect-claim"): True,
}


def _recipe_read(bundle, *, status: str = "approved") -> RecipeRead:
    """Wrap an authored EmissionBundle as the RecipeRead the coordinator
    would hydrate — same bodies, synthetic ids/timestamps."""
    now = datetime.now(timezone.utc)
    return RecipeRead(
        recipe_id=uuid4(), version_seq=1, valid_from=now, valid_to=None,
        claim_test_id=uuid4(), claim_version_seq=1,
        trigger_kind=bundle.trigger_kind, recipe_kind=bundle.recipe_kind,
        causal_initiation=bundle.causal_initiation,
        observation_realization=bundle.observation_realization,
        execution_environment=bundle.execution_environment,
        priority=100, status=status, created_at=now, updated_at=now,
    )


def _is_executable(recipe: RecipeRead) -> bool:
    try:
        check_recipe_executability(recipe)
        return True
    except ExecutionEngineError:
        return False


# ---------------------------------------------------------------------------
# The drift guard: EMITTABLE ↔ decided executability lockstep
# ---------------------------------------------------------------------------

def test_expected_map_covers_emittable_exactly():
    assert set(EXPECTED_EXECUTABILITY) == set(EMITTABLE)
    assert set(_SHAPES) == set(EMITTABLE)


def test_every_emittable_pair_has_the_decided_executability():
    observed = {}
    for pair, make in _SHAPES.items():
        bundle = author_emission(make())
        observed[pair] = _is_executable(_recipe_read(bundle))
    assert observed == EXPECTED_EXECUTABILITY


# ---------------------------------------------------------------------------
# check_recipe_executability specifics
# ---------------------------------------------------------------------------

def test_capability_recipe_fails_on_the_grants_capture():
    bundle = author_emission(_SHAPES[("permission", "capability-claim")]())
    with pytest.raises(ExecutionEngineError) as ei:
        check_recipe_executability(_recipe_read(bundle))
    assert "GRANTS_FIELD_ACCESS" in str(ei.value)


def test_is_required_property_recipe_is_gated():
    # D-128 refused a faithful Tooling column for is_required — the gate must
    # reproduce that refusal at the enqueue door, not after a dead job.
    g = GroundedProperty(
        archetype="configuration", claim_kind="property-claim", version_seq=1,
        subject=_ep("Field", "Account.Industry"), property_name="is_required",
        expected_value=True, requirement_excerpt="x")
    assert _is_executable(_recipe_read(author_emission(g))) is False


def test_unknown_recipe_kind_is_gated():
    bundle = author_emission(_SHAPES[("data_behavior", "value-claim")]())
    recipe = _recipe_read(bundle)
    object.__setattr__(recipe, "recipe_kind", "ui-recipe")
    with pytest.raises(PlanTranslationError):
        check_recipe_executability(recipe)


# ---------------------------------------------------------------------------
# gate_enqueue semantics (coordinator read monkeypatched — no PG)
# ---------------------------------------------------------------------------

def _patch_recipes(monkeypatch, recipes):
    from primeqa.test_representation.coordinator import (
        SemanticTransactionCoordinator,
    )
    monkeypatch.setattr(SemanticTransactionCoordinator, "list_active_recipes",
                        lambda self, session, tid: recipes)


def _executable_recipe(status="approved"):
    bundle = author_emission(_SHAPES[("configuration", "existence-claim")]())
    return _recipe_read(bundle, status=status)


def _unexecutable_recipe(status="approved"):
    bundle = author_emission(_SHAPES[("ui", "layout-claim")]())
    return _recipe_read(bundle, status=status)


def test_gate_passes_when_one_eligible_recipe_is_executable(monkeypatch):
    _patch_recipes(monkeypatch, [_unexecutable_recipe(), _executable_recipe()])
    gate_enqueue(None, uuid4())          # no raise


def test_gate_refuses_when_all_eligible_recipes_fail(monkeypatch):
    _patch_recipes(monkeypatch, [_unexecutable_recipe(), _unexecutable_recipe()])
    tid = uuid4()
    with pytest.raises(UnexecutableClaimError) as ei:
        gate_enqueue(None, tid)
    assert ei.value.test_id == tid
    assert len(ei.value.reasons) == 2
    assert "INCLUDES_FIELD" in str(ei.value)


def test_gate_ignores_status_ineligible_recipes(monkeypatch):
    # A draft/deprecated unexecutable recipe never triggers the gate — status
    # outcomes stay runtime-owned (the no-eligible-recipe result).
    _patch_recipes(monkeypatch, [
        _unexecutable_recipe(status="generated_unapproved"),
        _unexecutable_recipe(status="deprecated"),
    ])
    gate_enqueue(None, uuid4())          # no raise


def test_gate_passes_on_zero_recipes(monkeypatch):
    _patch_recipes(monkeypatch, [])
    gate_enqueue(None, uuid4())          # no raise
