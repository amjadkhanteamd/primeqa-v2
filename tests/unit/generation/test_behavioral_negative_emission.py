"""Unit tests for the S3 behavioral-negative emission (D-110.3, S3-thin) — pure,
offline, constructed GroundedNegatives.

A **verified** negative (a VR formula the D-107 parser derives) emits the
**behavioral** data-recipe — a create carrying the parser's violating payload +
`expect_rejection`. **D-293 (decision-2): there is no caveated negative anymore.**
A prohibition with no derivable behavioural reject recipe (non-numeric VR;
delete/share/transfer) is an INCOMPLETE behaviour instance — governance refuses it
before stash, and emission's guard RAISES :class:`BehaviourIncomplete` rather than
degrading to the inspection re-verify (the pre-D-293 fallback). The claim's
`identity_hash` is **stable** across the behavioural recipe shapes (the violating
value lives in the recipe; the business state lives in `semantic_conditions` —
D-293 refines Option-C, it does not abandon recipe-stability).
"""
from __future__ import annotations

import re
from uuid import uuid4

import pytest

from primeqa.generation.emission import (
    BehaviourIncomplete,
    GroundedNegative,
    _Endpoint,
    author_emission,
)
from primeqa.test_representation.identity_hash import compute_identity_hash
from primeqa.test_representation.models.recipes.data_recipe import (
    CreateStep,
    DataRecipeBody,
)
from primeqa.test_representation.models.recipes.metadata_recipe import (
    MetadataRecipeBody,
)
from primeqa.test_representation.models.triggers.data_mutation import (
    DataMutationTriggerBody,
)
from primeqa.test_representation.models.triggers.inspection import (
    InspectionTriggerBody,
)

_VR_CODE = "FIELD_CUSTOM_VALIDATION_EXCEPTION"


def _grounded(*, formulas, external_id="Lead", field_metadata=None):
    return GroundedNegative(
        archetype="data_behavior", claim_kind="prohibition-claim",
        operation_hint="modify_record", version_seq=7,
        subject=_Endpoint(entity_id=uuid4(), entity_type="Object", external_id=external_id),
        requirement_excerpt="Users must not save a Lead without a reason.",
        vr_formulas=formulas, field_metadata=field_metadata or {})


# A derivable formula → VerifiedNegative; a non-derivable one → caveated.
_DERIVABLE = ("ISBLANK(Reason__c)",)          # -> {Reason__c: None}
_DERIVABLE_CMP = ("Amount__c = 0",)            # -> {Amount__c: 0}
_NOT_DERIVABLE = ("ISCHANGED(Status__c)",)     # org-state -> NotDerivable -> caveated
_NONE = ()                                     # no formula -> caveated


# ---------------------------------------------------------------------------
# Verified → behavioral
# ---------------------------------------------------------------------------

def test_verified_negative_emits_behavioral_recipe():
    bundle = author_emission(_grounded(formulas=_DERIVABLE))

    assert bundle.trigger_kind == "data-mutation-trigger"
    assert bundle.recipe_kind == "data-recipe"
    assert isinstance(bundle.causal_initiation, DataMutationTriggerBody)
    assert bundle.causal_initiation.operation == "create"
    assert isinstance(bundle.observation_realization, DataRecipeBody)

    steps = bundle.observation_realization.steps
    assert len(steps) == 1 and isinstance(steps[0], CreateStep)
    create = steps[0]
    # the parser's violating payload IS the create's field_values.
    assert create.field_values == {"Reason__c": None}
    assert create.target_object.external_id == "Lead"


def test_comparison_formula_with_modify_hint_emits_update_rejected():
    # D-203: a comparison derives BOTH directions, so a modify_* prohibition
    # upgrades to the 2-step update-rejected shape — setup create in the
    # non-violating state, update into violation. Field names come out
    # object-QUALIFIED (the positive vertical's convention; Correction B).
    bundle = author_emission(_grounded(formulas=_DERIVABLE_CMP))
    assert bundle.recipe_kind == "data-recipe"
    assert bundle.causal_initiation.operation == "update"
    setup, mutation = bundle.observation_realization.steps
    assert isinstance(setup, CreateStep) and setup.expect_rejection is None
    assert setup.step_id == "create-setup"
    assert setup.field_values == {"Lead.Amount__c": 1}       # satisfy(False): <> 0
    assert mutation.kind == "update"
    assert mutation.field_changes == {"Lead.Amount__c": 0}   # satisfy(True): = 0
    assert mutation.expect_rejection.error_code == _VR_CODE


def test_behavioral_recipe_carries_expect_rejection_projection():
    bundle = author_emission(_grounded(formulas=_DERIVABLE))
    expect = bundle.observation_realization.steps[0].expect_rejection
    assert expect is not None
    # the projection (D-110.1): the generic VR code, no error_field.
    assert expect.error_code == _VR_CODE
    assert not hasattr(expect, "error_field")
    # the claim still carries the identity-bearing RejectionSignal (same code).
    assert bundle.asserted_truth.expected_rejection.error_code == _VR_CODE


def test_verified_marker_and_caveat_dropped():
    bundle = author_emission(_grounded(formulas=_DERIVABLE))
    # LAYER_2 + caveat dropped (D-107 invariant — unchanged by D-110.3).
    assert bundle.admissibility_layer.value == "layer_2"
    assert bundle.caveat_required is False


# ---------------------------------------------------------------------------
# D-293 decision-2: non-derivable → REFUSE (emission guard raises; no degrade)
# ---------------------------------------------------------------------------

def test_non_derivable_formula_raises_behaviour_incomplete():
    # An org-state VR (ISCHANGED) derives no violating input -> incomplete
    # behaviour instance. Governance refuses before stash; emission's guard makes
    # the invariant explicit by raising rather than degrading to the inspection.
    with pytest.raises(BehaviourIncomplete):
        author_emission(_grounded(formulas=_NOT_DERIVABLE))


def test_no_formula_raises_behaviour_incomplete():
    with pytest.raises(BehaviourIncomplete):
        author_emission(_grounded(formulas=_NONE))


# ---------------------------------------------------------------------------
# ⚑ The claim identity is STABLE across recipe shapes (Option-C, refined by
#   D-293: the violating VALUE rewrites the recipe but never the claim identity)
# ---------------------------------------------------------------------------

def test_claim_identity_hash_stable_across_recipe_value_rewrite():
    # Same subject, same (empty) business state; the ONLY difference is the
    # violating VALUE the recipe sends — a comparison VR drives the 2-step
    # update-rejected shape, ISBLANK drives the create-rejected shape. The
    # operational recipe differs; the CLAIM body (identity) must be byte-identical
    # (D-110.3's preserved Option-C property — value is operational, not identity).
    eid = uuid4()
    g_update = GroundedNegative(
        archetype="data_behavior", claim_kind="prohibition-claim",
        operation_hint="modify_record", version_seq=7,
        subject=_Endpoint(entity_id=eid, entity_type="Object", external_id="Lead"),
        requirement_excerpt="x", vr_formulas=_DERIVABLE_CMP)
    g_create = GroundedNegative(
        archetype="data_behavior", claim_kind="prohibition-claim",
        operation_hint="modify_record", version_seq=7,
        subject=_Endpoint(entity_id=eid, entity_type="Object", external_id="Lead"),
        requirement_excerpt="x", vr_formulas=_DERIVABLE)

    b_u = author_emission(g_update)
    b_c = author_emission(g_create)

    # the recipes differ (update-rejected vs create-rejected)...
    assert b_u.causal_initiation.operation == "update"
    assert b_c.causal_initiation.operation == "create"
    # ...but the CLAIM bodies are byte-identical -> identity_hash stable.
    assert b_u.asserted_truth.model_dump() == b_c.asserted_truth.model_dump()
    h_u = compute_identity_hash(
        b_u.archetype, b_u.claim_kind, b_u.asserted_truth, b_u.semantic_conditions)
    h_c = compute_identity_hash(
        b_c.archetype, b_c.claim_kind, b_c.asserted_truth, b_c.semantic_conditions)
    assert h_u == h_c


# ---------------------------------------------------------------------------
# The emitted behavioral recipe is well-formed (S2-acceptable shape)
# ---------------------------------------------------------------------------

def test_behavioral_recipe_round_trips_through_registry():
    # Proves the emitted recipe is a valid DataRecipeBody that decodes back
    # (what S2's write_recipe / read path do).
    from primeqa.test_representation.models.registry import get_body_model
    bundle = author_emission(_grounded(formulas=_DERIVABLE))
    dumped = bundle.observation_realization.model_dump(mode="json")
    cls = get_body_model(dumped["kind"], dumped["body_schema_version"])
    assert cls is DataRecipeBody
    restored = cls.model_validate(dumped)
    assert restored.steps[0].expect_rejection.error_code == _VR_CODE


# ---------------------------------------------------------------------------
# D-203 — graded operation dispatch
# ---------------------------------------------------------------------------

def _grounded_op(operation_hint, formulas):
    return GroundedNegative(
        archetype="data_behavior", claim_kind="prohibition-claim",
        operation_hint=operation_hint, version_seq=7,
        subject=_Endpoint(entity_id=uuid4(), entity_type="Object",
                          external_id="Lead"),
        requirement_excerpt="x", vr_formulas=formulas)


def test_isblank_with_modify_hint_falls_back_to_create_rejected():
    # Graded fallback: ISBLANK derives only the violating direction (no certain
    # non-blank setup), so modify_record drops back to TODAY'S create-rejected —
    # no regression for the existing corpus.
    bundle = author_emission(_grounded_op("modify_record", _DERIVABLE))
    assert bundle.recipe_kind == "data-recipe"
    assert bundle.causal_initiation.operation == "create"
    assert len(bundle.observation_realization.steps) == 1
    assert bundle.observation_realization.steps[0].field_values == {"Reason__c": None}


def test_create_duplicate_hint_keeps_create_rejected():
    bundle = author_emission(_grounded_op("create_duplicate", _DERIVABLE_CMP))
    assert bundle.causal_initiation.operation == "create"
    steps = bundle.observation_realization.steps
    assert len(steps) == 1 and steps[0].field_values == {"Amount__c": 0}


def test_delete_hint_refuses_even_when_formula_derivable():
    # The semantic-blur fix (D-203) + D-293: VRs never fire on delete, so a
    # delete prohibition derives no behavioural reject recipe REGARDLESS of the
    # formula. Pre-D-293 it degraded to the caveated inspection; now it is an
    # incomplete behaviour instance -> refuse (emission guard raises).
    with pytest.raises(BehaviourIncomplete):
        author_emission(_grounded_op("delete", _DERIVABLE_CMP))


def test_update_shape_is_layer_2_no_caveat():
    bundle = author_emission(_grounded_op("modify_record", _DERIVABLE_CMP))
    assert bundle.admissibility_layer.value == "layer_2"
    assert bundle.caveat_required is False


def test_claim_identity_stable_across_both_behavioural_shapes():
    # The Option-C invariant extended to D-203, refined by D-293: update-rejected
    # vs create-rejected — the two BEHAVIOURAL shapes (the caveated-inspection
    # third shape no longer exists; it refuses). The CLAIM body never varies.
    eid = uuid4()

    def _g(formulas):
        return GroundedNegative(
            archetype="data_behavior", claim_kind="prohibition-claim",
            operation_hint="modify_record", version_seq=7,
            subject=_Endpoint(entity_id=eid, entity_type="Object",
                              external_id="Lead"),
            requirement_excerpt="x", vr_formulas=formulas)

    b_update = author_emission(_g(_DERIVABLE_CMP))     # 2-step update shape
    b_create = author_emission(_g(_DERIVABLE))         # create-rejected fallback

    assert b_update.causal_initiation.operation == "update"
    assert b_create.causal_initiation.operation == "create"
    assert (b_update.asserted_truth.model_dump()
            == b_create.asserted_truth.model_dump())
    hashes = {
        compute_identity_hash(b.archetype, b.claim_kind,
                              b.asserted_truth, b.semantic_conditions)
        for b in (b_update, b_create)
    }
    assert len(hashes) == 1


def test_update_recipe_round_trips_through_registry():
    from primeqa.test_representation.models.registry import get_body_model
    bundle = author_emission(_grounded_op("modify_record", _DERIVABLE_CMP))
    dumped = bundle.observation_realization.model_dump(mode="json")
    restored = get_body_model(
        dumped["kind"], dumped["body_schema_version"]).model_validate(dumped)
    assert restored.steps[1].kind == "update"
    assert restored.steps[1].expect_rejection.error_code == _VR_CODE


# ---------------------------------------------------------------------------
# D-294 — a cross-field prohibition (Loan__c > Property__c) with numeric field
# metadata AUTHORS a behavioural reject-test end to end (was behaviour-incomplete
# refuse pre-D-294). Proves the rail -> derive -> author path, not just derive().
# ---------------------------------------------------------------------------

def test_cross_field_with_metadata_authors_update_rejected():
    meta = {"Loan__c": {"field_type": "currency", "is_calculated": False},
            "Property__c": {"field_type": "double", "is_calculated": False}}
    bundle = author_emission(_grounded(
        formulas=("Loan__c > Property__c",), external_id="Deal", field_metadata=meta))
    # verified -> LAYER_2, no caveat, a behavioural data-recipe (not the refuse guard)
    assert bundle.admissibility_layer.value == "layer_2"
    assert bundle.caveat_required is False
    assert bundle.recipe_kind == "data-recipe"
    setup, mutation = bundle.observation_realization.steps
    # the violating update carries the ordered cross-field pair (object-qualified)
    assert mutation.field_changes == {"Deal.Loan__c": 1, "Deal.Property__c": 0}
    assert setup.field_values == {"Deal.Loan__c": 0, "Deal.Property__c": 1}  # non-violating
    assert mutation.expect_rejection.error_code == _VR_CODE


def test_cross_field_without_metadata_still_raises_behaviour_incomplete():
    # the D-293 floor holds when the rail is empty (the certainty bar).
    with pytest.raises(BehaviourIncomplete):
        author_emission(_grounded(formulas=("Loan__c > Property__c",), external_id="Deal"))


# ---------------------------------------------------------------------------
# D-228 — multi-recipe authoring: a verified negative ALSO carries the
# caveated inspection re-verify as a fallback secondary (priority -10)
# ---------------------------------------------------------------------------

def test_verified_create_rejected_carries_inspection_secondary():
    bundle = author_emission(_grounded(formulas=_DERIVABLE))
    assert bundle.recipe_kind == "data-recipe"          # primary is behavioral
    assert len(bundle.secondary_recipes) == 1
    sec = bundle.secondary_recipes[0]
    assert sec.trigger_kind == "inspection-trigger"
    assert sec.recipe_kind == "metadata-recipe"
    assert sec.priority == -10
    assert isinstance(sec.causal_initiation, InspectionTriggerBody)
    assert isinstance(sec.observation_realization, MetadataRecipeBody)
    # the fallback assumes metadata-API auth only — that's what makes it
    # selectable on an env where the behavioral primary cannot run.
    assert [a.auth_kind for a in sec.execution_environment.auth_assumptions] \
        == ["metadata_api_user"]


def test_verified_update_rejected_carries_inspection_secondary():
    bundle = author_emission(_grounded_op("modify_record", _DERIVABLE_CMP))
    assert bundle.causal_initiation.operation == "update"
    assert len(bundle.secondary_recipes) == 1
    assert bundle.secondary_recipes[0].recipe_kind == "metadata-recipe"


# (D-293 removed test_caveated_negative_has_no_secondaries — a caveated negative
# no longer exists; the non-derivable case refuses, covered by the raises tests.)


def test_secondary_round_trips_through_registry():
    from primeqa.test_representation.models.registry import get_body_model
    bundle = author_emission(_grounded(formulas=_DERIVABLE))
    sec = bundle.secondary_recipes[0]
    dumped = sec.observation_realization.model_dump(mode="json")
    restored = get_body_model(
        dumped["kind"], dumped["body_schema_version"]).model_validate(dumped)
    assert restored.steps[0].fields_to_capture == ["APPLIES_TO"]


# ---------------------------------------------------------------------------
# D-288 (4f.2-prep) — the EmissionBundle.strategy_kind slot. The slot exists
# and DEFAULTS None: no authoring path stamps it yet (the bva-authoring helper
# is deferred to 4f.2b), so every bundle authored today carries None → the
# persister writes NULL → router/decision read None → single, byte-identical.
# ---------------------------------------------------------------------------

def test_authored_bundle_strategy_kind_defaults_none():
    # both behavioural shapes (create-rejected + update-rejected) — no authoring
    # path sets it. Non-derivable shapes now refuse (D-293), so they author no
    # bundle to inspect.
    for formulas in (_DERIVABLE, _DERIVABLE_CMP):
        bundle = author_emission(_grounded(formulas=formulas))
        assert hasattr(bundle, "strategy_kind")
        assert bundle.strategy_kind is None


# (D-293 removed test_secondary_does_not_perturb_claim_identity — it relied on a
# no-secondary authored prohibition, which no longer exists, every authored
# prohibition now being verified and carrying the inspection secondary. The
# identity-invariance across operational recipe variation is covered by
# test_claim_identity_stable_across_both_behavioural_shapes.)


# ---------------------------------------------------------------------------
# D-296 (lever 4) — the compound cross-field / NOT-ISBLANK prohibition composes
# end to end: selection (governance, test_vr_alignment.py) hands the aligned
# Loan_Exceeds VR here, and the D-296 soft-merge derivation emits a runnable
# 2-step update-rejected recipe. This pins the EMISSION end (gate + golden + hash).
# field_metadata is keyed by the VERBATIM mixed-case field name (the derive side is
# uncased — a lowercase key would silently disengage the reconciliation).
# ---------------------------------------------------------------------------

_EXCEEDS = ("AND(NOT(ISBLANK(Loan_Amount__c)), NOT(ISBLANK(Property_Value__c)), "
            "Loan_Amount__c > Property_Value__c)",)
_XF_META = {
    "Loan_Amount__c": {"field_type": "currency", "is_calculated": False},
    "Property_Value__c": {"field_type": "currency", "is_calculated": False},
}


def test_d296_ac2_prohibition_recipe_derivable():
    # The D-293 completeness gate now passes for AC2's aligned VR (was False
    # pre-D-296 — the compound AND(NOT-ISBLANK, NOT-ISBLANK, a>b) refused).
    from primeqa.generation.emission import prohibition_recipe_derivable
    assert prohibition_recipe_derivable("modify_record", _EXCEEDS, _XF_META) is True


def test_d296_compound_cross_field_emits_update_rejected_recipe():
    bundle = author_emission(_grounded(
        formulas=_EXCEEDS, external_id="Opportunity", field_metadata=_XF_META))
    assert bundle.recipe_kind == "data-recipe"
    assert bundle.causal_initiation.operation == "update"
    setup, mutation = bundle.observation_realization.steps
    assert isinstance(setup, CreateStep) and setup.expect_rejection is None
    # non-violating setup: Loan blank -> the AND doesn't fire. Object-qualified key.
    assert setup.field_values == {"Opportunity.Loan_Amount__c": None}
    assert mutation.kind == "update"
    # violating: Loan 1 > Property 0 fires; both non-blank (0 is a non-blank number).
    assert mutation.field_changes == {"Opportunity.Loan_Amount__c": 1,
                                      "Opportunity.Property_Value__c": 0}
    assert mutation.expect_rejection.error_code == _VR_CODE
    # no _SoftFill sentinel leaked into the emitted recipe (raw values only).
    from primeqa.generation.verified_negative import _SoftFill
    for v in list(setup.field_values.values()) + list(mutation.field_changes.values()):
        assert not isinstance(v, _SoftFill)


def test_d296_compound_claim_identity_hash_stable():
    # lever 4 changes which VR grounds AC2 (and the recipe), NEVER the claim identity:
    # same subject + (empty) conditions as a create-rejected claim -> same hash.
    eid = uuid4()
    g_compound = GroundedNegative(
        archetype="data_behavior", claim_kind="prohibition-claim",
        operation_hint="modify_record", version_seq=7,
        subject=_Endpoint(entity_id=eid, entity_type="Object", external_id="Opportunity"),
        requirement_excerpt="x", vr_formulas=_EXCEEDS, field_metadata=_XF_META)
    g_create = GroundedNegative(
        archetype="data_behavior", claim_kind="prohibition-claim",
        operation_hint="modify_record", version_seq=7,
        subject=_Endpoint(entity_id=eid, entity_type="Object", external_id="Opportunity"),
        requirement_excerpt="x", vr_formulas=_DERIVABLE)
    b_x = author_emission(g_compound)
    b_c = author_emission(g_create)
    assert b_x.causal_initiation.operation == "update"
    assert b_x.asserted_truth.model_dump() == b_c.asserted_truth.model_dump()
    h_x = compute_identity_hash(
        b_x.archetype, b_x.claim_kind, b_x.asserted_truth, b_x.semantic_conditions)
    h_c = compute_identity_hash(
        b_c.archetype, b_c.claim_kind, b_c.asserted_truth, b_c.semantic_conditions)
    assert h_x == h_c


# ---------------------------------------------------------------------------
# D-297 (lever 5, S2 activate): the DERIVED VR's synced error message is projected
# into the recipe's error_message_pattern (re.escape), so the S4 grade confirms
# WHICH rule fired — bound to the derived source VR, never an arbitrary aligned one.
# No message / unknown source -> None -> byte-identical (fabrication-safety).
# ---------------------------------------------------------------------------

def _grounded_msg(*, formulas, vr_messages, external_id="Lead", field_metadata=None,
                  operation_hint="modify_record"):
    return GroundedNegative(
        archetype="data_behavior", claim_kind="prohibition-claim",
        operation_hint=operation_hint, version_seq=7,
        subject=_Endpoint(entity_id=uuid4(), entity_type="Object", external_id=external_id),
        requirement_excerpt="x", vr_formulas=formulas,
        field_metadata=field_metadata or {}, vr_messages=vr_messages)


def test_d297_message_projected_when_derived_vr_has_message():
    # A comparison VR derives (update-rejected shape); its message is escaped into
    # the UpdateStep's error_message_pattern, alongside the generic error_code.
    g = _grounded_msg(formulas=("Amount__c = 0",),
                      vr_messages={"Amount__c = 0": "Amount must not be zero."})
    _, mutation = author_emission(g).observation_realization.steps
    assert mutation.expect_rejection.error_code == _VR_CODE
    assert mutation.expect_rejection.error_message_pattern == re.escape("Amount must not be zero.")


def test_d297_no_pattern_when_derived_vr_has_no_message():
    # The map has no entry for the derived VR -> pattern stays None -> byte-identical
    # (never invent a message).
    g = _grounded_msg(formulas=("Amount__c = 0",), vr_messages={})
    _, mutation = author_emission(g).observation_realization.steps
    assert mutation.expect_rejection.error_message_pattern is None


def test_d297_binds_to_the_derived_vr_not_an_arbitrary_one():
    # The load-bearing multi-VR passthrough: the FIRST candidate is org-state
    # (NotDerivable), the SECOND derives. The message MUST bind to the derived
    # (second) VR — never the first's (wrong) message.
    g = _grounded_msg(
        formulas=("ISCHANGED(Status__c)", "Amount__c = 0"),
        vr_messages={"ISCHANGED(Status__c)": "WRONG — underivable VR's message",
                     "Amount__c = 0": "Amount must not be zero."})
    _, mutation = author_emission(g).observation_realization.steps
    assert mutation.expect_rejection.error_message_pattern == re.escape("Amount must not be zero.")


def test_d297_create_rejected_shape_also_carries_message():
    # ISBLANK -> create-rejected (single CreateStep); the message rides that step.
    g = _grounded_msg(formulas=("ISBLANK(Reason__c)",),
                      vr_messages={"ISBLANK(Reason__c)": "Reason is required."})
    step = author_emission(g).observation_realization.steps[0]
    assert step.kind == "create"
    assert step.expect_rejection.error_message_pattern == re.escape("Reason is required.")


def test_d297_default_grounded_no_messages_byte_identical():
    # A GroundedNegative with the default empty vr_messages -> pattern None -> the
    # pre-D-297 recipe (the existing _grounded helper path stays byte-identical).
    _, mutation = author_emission(_grounded(formulas=_DERIVABLE_CMP)).observation_realization.steps
    assert mutation.expect_rejection.error_message_pattern is None


def test_d297_regex_metacharacters_in_message_are_escaped():
    # A message with regex metachars must be re.escape-d (matched literally by _matches).
    msg = "Value must match [A-Z]+ (uppercase)."
    g = _grounded_msg(formulas=("Amount__c = 0",), vr_messages={"Amount__c = 0": msg})
    _, mutation = author_emission(g).observation_realization.steps
    assert mutation.expect_rejection.error_message_pattern == re.escape(msg)
    # and the escaped pattern matches the literal message (teeth of the projection)
    assert re.fullmatch(mutation.expect_rejection.error_message_pattern, msg)


def test_d297_1_html_entity_message_unescaped_before_escape():
    # D-297.1: the Tooling API stores VR messages with HTML entities (e.g. the rupee
    # sign as "&#8377;"); emission HTML-unescapes before re.escape so the pattern is
    # escape(RENDERED), matching the org's rendered runtime message (S4 _matches also
    # unescapes the runtime side). The pattern must NOT carry the raw entity.
    g = _grounded_msg(
        formulas=("Amount__c = 0",),
        vr_messages={"Amount__c = 0": "Loans over &#8377;50,00,000 need approval."})
    _, mutation = author_emission(g).observation_realization.steps
    assert mutation.expect_rejection.error_message_pattern == re.escape(
        "Loans over ₹50,00,000 need approval.")   # ₹ = rendered rupee sign
    assert "&#8377;" not in mutation.expect_rejection.error_message_pattern


# ---------------------------------------------------------------------------
# D-300 (S3): flag-gated bva boundary authoring on the prohibition path.
# ---------------------------------------------------------------------------

_SINGLE_THRESHOLD = ("Amount__c > 10000",)


def test_flag_off_is_byte_identical():
    # The recon gate: flag OFF (default AND explicit) authors EXACTLY the
    # pre-D-300 bundle — no boundary probes, NULL strategy.
    g = _grounded(formulas=_SINGLE_THRESHOLD)
    default = author_emission(g)
    explicit_off = author_emission(g, enable_bva_boundaries=False)
    assert default == explicit_off
    assert default.boundary_recipes == () and default.strategy_kind is None


def test_flag_on_single_threshold_stamps_bva_and_authors_the_pair():
    g = _grounded(formulas=_SINGLE_THRESHOLD)
    off = author_emission(g)
    on = author_emission(g, enable_bva_boundaries=True)
    # stamped + one just-inside accept probe
    assert on.strategy_kind == "bva"
    assert len(on.boundary_recipes) == 1
    accept_create = on.boundary_recipes[0].observation_realization.steps[0]
    assert accept_create.field_values == {"Lead.Amount__c": 10000}
    assert accept_create.expect_rejection is None
    # the PRIMARY (firing) recipe is byte-identical to the flag-OFF primary —
    # the boundary set never perturbs the shipped reject shape
    assert on.observation_realization == off.observation_realization
    assert on.asserted_truth == off.asserted_truth              # identity untouched
    assert on.semantic_conditions == off.semantic_conditions
    # the firing probe IS the primary: the update's violating change == the
    # derive_boundary_set firing member (the D-300 invariant, end-to-end)
    update_step = on.observation_realization.steps[1]
    assert update_step.field_changes == {"Lead.Amount__c": 10001}


def test_flag_on_non_threshold_shape_does_not_stamp():
    # ISBLANK derives a verified create-rejected primary, but the boundary
    # generator refuses function shapes -> NO stamp, NO probes: a bva claim is
    # minted ONLY with a non-empty boundary set (never single-with-extra-steps).
    g = _grounded(formulas=("ISBLANK(Reason__c)",))
    on = author_emission(g, enable_bva_boundaries=True)
    assert on.strategy_kind is None and on.boundary_recipes == ()
    assert on == author_emission(g)                             # fully byte-identical
