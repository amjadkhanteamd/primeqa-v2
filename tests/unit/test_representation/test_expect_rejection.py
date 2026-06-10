"""Tests for the S2 expect-rejection model (D-110.1).

Covers the operational `RejectionExpectation` primitive, the
`CreateStep.expect_rejection` flag, the `DataRecipeBody` at-most-one
invariant, the registry round-trip, and — the key proof — that a recipe
carrying `RejectionExpectation` passes the operational-layer
`_verify_no_identity_bearing_refs` walk (no identity-bearing leak; the
whole reason `RejectionExpectation` is a projection of `RejectionSignal`
rather than a reuse of it).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from primeqa.test_representation.coordinator import (
    _verify_no_identity_bearing_refs,
)
from primeqa.test_representation.models.primitives import (
    RejectionExpectation,
)
from primeqa.test_representation.models.recipes.data_recipe import (
    CreateStep,
    DataRecipeBody,
)
from primeqa.test_representation.models.registry import get_body_model

_VR_CODE = "FIELD_CUSTOM_VALIDATION_EXCEPTION"


def _logical(entity_type="Object", ext_id="Lead") -> dict:
    return {"ref_kind": "logical", "entity_type": entity_type, "external_id": ext_id}


def _create(step_id="create-1", *, expect_rejection=None) -> dict:
    d = {"kind": "create", "step_id": step_id,
         "target_object": _logical(), "field_values": {"Name": "x"}}
    if expect_rejection is not None:
        d["expect_rejection"] = expect_rejection
    return d


def _body(steps) -> dict:
    return {"api_choice": "rest", "identity_context": "system",
            "execution_mechanism": "direct_api", "steps": steps}


# ---------------------------------------------------------------------------
# RejectionExpectation — the ≥1-required validator + scalar-only shape
# ---------------------------------------------------------------------------

def test_error_code_alone_is_valid():
    r = RejectionExpectation(error_code=_VR_CODE)
    assert r.error_code == _VR_CODE and r.error_message_pattern is None


def test_message_pattern_alone_is_valid():
    r = RejectionExpectation(error_message_pattern="must provide a reason")
    assert r.error_message_pattern == "must provide a reason"


def test_both_signals_valid():
    r = RejectionExpectation(error_code=_VR_CODE, error_message_pattern="reason")
    assert r.error_code == _VR_CODE


def test_empty_expectation_rejected():
    with pytest.raises(ValidationError, match="at least one"):
        RejectionExpectation()


def test_rejection_expectation_has_no_identity_bearing_field():
    # The whole point: scalars only, no error_field (IdentityBearingRef) like
    # RejectionSignal has. Guards against a future regression re-adding it.
    fields = set(RejectionExpectation.model_fields)
    assert fields == {"error_code", "error_message_pattern"}


# ---------------------------------------------------------------------------
# CreateStep.expect_rejection
# ---------------------------------------------------------------------------

def test_create_step_defaults_to_no_rejection():
    step = CreateStep.model_validate(_create())
    assert step.expect_rejection is None


def test_create_step_carries_rejection_expectation():
    step = CreateStep.model_validate(_create(expect_rejection={"error_code": _VR_CODE}))
    assert isinstance(step.expect_rejection, RejectionExpectation)
    assert step.expect_rejection.error_code == _VR_CODE


# ---------------------------------------------------------------------------
# DataRecipeBody — the at-most-one invariant
# ---------------------------------------------------------------------------

def test_zero_expect_rejection_is_valid():
    # Ordinary (positive / future) recipe — no behavioral negative.
    body = DataRecipeBody.model_validate(_body([_create(), _create("create-2")]))
    assert body.kind == "data-recipe"


def test_one_expect_rejection_is_valid():
    # The behavioral-negative recipe.
    body = DataRecipeBody.model_validate(
        _body([_create(expect_rejection={"error_code": _VR_CODE})]))
    assert sum(1 for s in body.steps if s.expect_rejection is not None) == 1


def test_two_expect_rejections_rejected():
    with pytest.raises(ValidationError, match="at most one"):
        DataRecipeBody.model_validate(_body([
            _create("c1", expect_rejection={"error_code": _VR_CODE}),
            _create("c2", expect_rejection={"error_code": "DUPLICATE_VALUE"}),
        ]))


# ---------------------------------------------------------------------------
# The key proof — operational-layer compliance
# ---------------------------------------------------------------------------

def test_negative_recipe_passes_no_identity_bearing_refs():
    # RejectionExpectation is scalar-only, so a recipe carrying it has NO
    # IdentityBearingRef — it passes the Coordinator's operational-layer walk.
    # (Reusing RejectionSignal, whose error_field is an IdentityBearingRef,
    # would fail this whenever the field was set. This is why D-110.1 projects
    # rather than reuses.)
    body = DataRecipeBody.model_validate(
        _body([_create(expect_rejection={
            "error_code": _VR_CODE,
            "error_message_pattern": "reason required"})]))
    # raises OntologyViolationError on any IdentityBearingRef; returns None clean.
    assert _verify_no_identity_bearing_refs(body, "observation_realization") is None


# ---------------------------------------------------------------------------
# Registry round-trip
# ---------------------------------------------------------------------------

def test_round_trip_decode_preserves_expect_rejection():
    body = DataRecipeBody.model_validate(
        _body([_create(expect_rejection={"error_code": _VR_CODE})]))
    dumped = body.model_dump(mode="json")
    cls = get_body_model(dumped["kind"], dumped["body_schema_version"])
    assert cls is DataRecipeBody
    restored = cls.model_validate(dumped)
    assert restored.steps[0].expect_rejection.error_code == _VR_CODE
    # still v1 — additive, no version bump.
    assert restored.body_schema_version == 1


# ---------------------------------------------------------------------------
# D-203 — expect_rejection on Update / Delete steps (the 2-step negative)
# ---------------------------------------------------------------------------

def _update(step_id="update-violating", *, expect_rejection=None) -> dict:
    d = {"kind": "update", "step_id": step_id, "target": _logical(),
         "field_changes": {"Lead.Status": "Closed"}}
    if expect_rejection is not None:
        d["expect_rejection"] = expect_rejection
    return d


def _delete(step_id="delete-violating", *, expect_rejection=None) -> dict:
    d = {"kind": "delete", "step_id": step_id, "target": _logical()}
    if expect_rejection is not None:
        d["expect_rejection"] = expect_rejection
    return d


def test_update_and_delete_steps_default_to_no_rejection():
    body = DataRecipeBody.model_validate(_body([_create(), _update(), _delete()]))
    assert all(s.expect_rejection is None for s in body.steps)


def test_two_step_update_rejected_negative_is_valid():
    # The D-203 shape: setup create (None) + flagged update.
    body = DataRecipeBody.model_validate(_body([
        _create("create-setup"),
        _update(expect_rejection={"error_code": _VR_CODE}),
    ]))
    assert body.steps[0].expect_rejection is None
    assert body.steps[1].expect_rejection.error_code == _VR_CODE


def test_two_step_delete_rejected_negative_is_valid():
    body = DataRecipeBody.model_validate(_body([
        _create("create-setup"),
        _delete(expect_rejection={"error_code": _VR_CODE}),
    ]))
    assert body.steps[1].expect_rejection.error_code == _VR_CODE


def test_at_most_one_counts_mixed_step_kinds():
    # A flagged create AND a flagged update in one recipe violate D-110.1.
    with pytest.raises(ValidationError, match="at most one"):
        DataRecipeBody.model_validate(_body([
            _create(expect_rejection={"error_code": _VR_CODE}),
            _update(expect_rejection={"error_code": "DUPLICATE_VALUE"}),
        ]))


def test_two_step_negative_passes_no_identity_bearing_refs():
    body = DataRecipeBody.model_validate(_body([
        _create("create-setup"),
        _update(expect_rejection={"error_code": _VR_CODE,
                                  "error_message_pattern": "cannot exceed"}),
    ]))
    assert _verify_no_identity_bearing_refs(body, "observation_realization") is None


def test_round_trip_preserves_mutation_expect_rejection():
    body = DataRecipeBody.model_validate(_body([
        _create("create-setup"),
        _delete(expect_rejection={"error_code": _VR_CODE}),
    ]))
    dumped = body.model_dump(mode="json")
    restored = get_body_model(
        dumped["kind"], dumped["body_schema_version"]).model_validate(dumped)
    assert restored.steps[1].expect_rejection.error_code == _VR_CODE
    assert restored.body_schema_version == 1   # additive — still v1


def test_identity_hash_cannot_see_recipes():
    # The Option-C invariant, proven at the signature level: identity hashing
    # takes ONLY the claim's identity-bearing bodies — a recipe gaining steps
    # or flags (D-203) structurally cannot churn claim identity.
    import inspect
    from primeqa.test_representation.identity_hash import compute_identity_hash
    params = set(inspect.signature(compute_identity_hash).parameters)
    assert params == {"archetype", "claim_kind",
                      "asserted_truth", "semantic_conditions"}
