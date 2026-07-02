"""Unit tests for the S4 data-recipe behavioral-negative bridge (D-110.2 slice 1)
— pure, no PG.

`build_data_recipe_plan` turns an S2 `RecipeRead` (a data-mutation trigger + a
data-recipe whose single step is a create carrying `expect_rejection`) into a
`DataRecipePlan` / `PlannedCreate`. These tests exercise the decode path
(bodies round-trip through JSONB via the registry, as the Coordinator does on
read), the gates (fail-loud on the wrong shape), and that `expect_rejection` is
projected intact. No DB, no live org.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from primeqa.execution_engine import (
    DataRecipePlan,
    PlannedCreate,
    PlanTranslationError,
    build_data_recipe_plan,
)
from primeqa.execution_engine.plan import (
    PlannedAssertion,
    PlannedDataRead,
    PlannedDelete,
    PlannedUpdate,
)
from primeqa.test_representation.coordinator import RecipeRead
from primeqa.test_representation.models.environment import ExecutionEnvironmentBody
from primeqa.test_representation.models.recipes.data_recipe import DataRecipeBody
from primeqa.test_representation.models.registry import get_body_model
from primeqa.test_representation.models.triggers.data_mutation import (
    DataMutationTriggerBody,
)

_NOW = datetime(2026, 5, 27, tzinfo=timezone.utc)
_VR_CODE = "FIELD_CUSTOM_VALIDATION_EXCEPTION"


# ---------------------------------------------------------------------------
# Builders — the S2 bodies a behavioral-negative data-recipe carries
# ---------------------------------------------------------------------------

def _trigger() -> DataMutationTriggerBody:
    return DataMutationTriggerBody.model_validate({
        "operation": "create",
        "target": {"ref_kind": "logical", "entity_type": "Object", "external_id": "Lead"},
        "identity_context": "system",
        "volume": "single",
    })


def _negative_body(*, expect_rejection=True, extra_steps=None, target_pinned=False) -> DataRecipeBody:
    target = ({"ref_kind": "pinned", "entity_type": "Object", "entity_id": str(uuid4()),
               "version_seq": 1, "external_id": "Lead"} if target_pinned
              else {"ref_kind": "logical", "entity_type": "Object", "external_id": "Lead"})
    create = {"kind": "create", "step_id": "create-violating",
              "target_object": target, "field_values": {"Company": "Acme"}}
    if expect_rejection:
        create["expect_rejection"] = {"error_code": _VR_CODE}
    steps = [create] + list(extra_steps or [])
    return DataRecipeBody.model_validate({
        "api_choice": "rest", "identity_context": "system",
        "execution_mechanism": "direct_api", "steps": steps,
    })


def _positive_body(
    *, field="Status__c", value="Active", object_api="Account",
    omit_read=False, omit_assert=False, read_pinned=False,
) -> DataRecipeBody:
    """The S2 body a positive create-and-verify carries (D-115): a create with no
    expect_rejection → read-back → assert(equals)."""
    target = {"ref_kind": "logical", "entity_type": "Object", "external_id": object_api}
    read_target = ({"ref_kind": "pinned", "entity_type": "Object",
                    "entity_id": str(uuid4()), "version_seq": 1, "external_id": object_api}
                   if read_pinned else target)
    steps = [{"kind": "create", "step_id": "create-record",
              "target_object": target, "field_values": {field: value}}]
    if not omit_read:
        steps.append({"kind": "read", "step_id": "read-created", "target": read_target,
                      "soql": f"SELECT {field} FROM {object_api} WHERE Id = '$create-record.id'",
                      "fields_to_capture": [field]})
    if not omit_assert:
        steps.append({"kind": "assert", "step_id": "assert-value",
                      "predicate": {"subject_ref": f"read-created.{field}",
                                    "predicate": "equals", "value": value}})
    return DataRecipeBody.model_validate({
        "api_choice": "rest", "identity_context": "system",
        "execution_mechanism": "direct_api", "steps": steps,
    })


def _env() -> ExecutionEnvironmentBody:
    return ExecutionEnvironmentBody.model_validate({})


def _roundtrip(body):
    """Dump + re-decode via the registry — what the Coordinator does on read."""
    dumped = body.model_dump(mode="json")
    cls = get_body_model(dumped["kind"], dumped["body_schema_version"])
    return cls.model_validate(dumped)


def _recipe_read(
    *, recipe_id=None, version_seq=3, claim_test_id=None, claim_version_seq=None,
    trigger_kind="data-mutation-trigger", recipe_kind="data-recipe",
    causal_initiation=None, observation_realization=None, execution_environment=None,
    roundtrip=True,
):
    causal = causal_initiation if causal_initiation is not None else _trigger()
    obs = observation_realization if observation_realization is not None else _negative_body()
    env = execution_environment if execution_environment is not None else _env()
    if roundtrip:
        causal, obs, env = _roundtrip(causal), _roundtrip(obs), _roundtrip(env)
    return RecipeRead(
        recipe_id=recipe_id or uuid4(), version_seq=version_seq, valid_from=_NOW,
        valid_to=None, claim_test_id=claim_test_id or uuid4(),
        claim_version_seq=claim_version_seq, trigger_kind=trigger_kind,
        recipe_kind=recipe_kind, causal_initiation=causal,
        observation_realization=obs, execution_environment=env, priority=0,
        status="approved", created_at=_NOW, updated_at=_NOW)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_decodes_negative_recipe_to_plan():
    plan = build_data_recipe_plan(_recipe_read())

    assert isinstance(plan, DataRecipePlan)
    assert plan.api_choice == "rest"
    assert len(plan.steps) == 1
    create = plan.steps[0]
    assert isinstance(create, PlannedCreate)
    assert create.kind == "create"
    assert create.step_id == "create-violating"
    assert create.target_object.external_id == "Lead"
    assert create.field_values == {"Company": "Acme"}


def test_expect_rejection_projected_intact():
    plan = build_data_recipe_plan(_recipe_read())
    assert plan.steps[0].expect_rejection.error_code == _VR_CODE


def test_plan_carries_recipe_and_claim_identity():
    rid, ctid = uuid4(), uuid4()
    plan = build_data_recipe_plan(_recipe_read(
        recipe_id=rid, version_seq=5, claim_test_id=ctid, claim_version_seq=2))
    assert plan.recipe_id == rid
    assert plan.recipe_version_seq == 5
    assert plan.claim_test_id == ctid
    assert plan.claim_version_seq == 2


# ---------------------------------------------------------------------------
# Positive create-and-verify projection (D-115 side B)
# ---------------------------------------------------------------------------

def test_decodes_positive_recipe_to_plan():
    plan = build_data_recipe_plan(_recipe_read(observation_realization=_positive_body()))

    assert isinstance(plan, DataRecipePlan)
    assert len(plan.steps) == 3
    create, read, assertion = plan.steps
    assert isinstance(create, PlannedCreate) and create.expect_rejection is None
    assert create.step_id == "create-record"
    assert create.field_values == {"Status__c": "Active"}     # semantic field only (k16)
    assert isinstance(read, PlannedDataRead)
    assert read.step_id == "read-created"
    assert read.soql.endswith("'$create-record.id'")
    assert read.fields_to_capture == ("Status__c",)
    assert isinstance(assertion, PlannedAssertion)
    assert assertion.predicate.predicate == "equals"
    assert assertion.predicate.value == "Active"
    assert assertion.predicate.subject_ref == "read-created.Status__c"


def test_rejects_positive_missing_assert():
    recipe = _recipe_read(observation_realization=_positive_body(omit_assert=True))
    with pytest.raises(PlanTranslationError, match="ReadStep -> AssertStep"):
        build_data_recipe_plan(recipe)


def test_rejects_positive_missing_read():
    recipe = _recipe_read(observation_realization=_positive_body(omit_read=True))
    with pytest.raises(PlanTranslationError, match="ReadStep -> AssertStep"):
        build_data_recipe_plan(recipe)


def test_rejects_positive_pinned_read_target():
    recipe = _recipe_read(observation_realization=_positive_body(read_pinned=True))
    with pytest.raises(PlanTranslationError, match="LogicalRef"):
        build_data_recipe_plan(recipe)


# ---------------------------------------------------------------------------
# Shape gates — fail-loud (mirroring the inspection bridge)
# ---------------------------------------------------------------------------

def test_rejects_non_data_recipe_kind():
    with pytest.raises(PlanTranslationError, match="recipe_kind"):
        build_data_recipe_plan(_recipe_read(recipe_kind="metadata-recipe"))


def test_rejects_non_data_mutation_trigger():
    with pytest.raises(PlanTranslationError, match="trigger_kind"):
        build_data_recipe_plan(_recipe_read(trigger_kind="inspection-trigger"))


def test_rejects_lone_positive_create():
    # A create with no expect_rejection is now a *positive* (D-115) — but a lone
    # create (no read + assert) is an incomplete positive: the triple is required.
    recipe = _recipe_read(observation_realization=_negative_body(expect_rejection=False))
    with pytest.raises(PlanTranslationError, match="ReadStep -> AssertStep"):
        build_data_recipe_plan(recipe)


def test_rejects_flagged_create_with_extra_step():
    # A create-rejected negative is single-step (D-110.2); a 2-step negative
    # carries the flag on the MUTATION, never the setup create (D-203).
    extra = [{"kind": "read", "step_id": "read-back",
              "target": {"ref_kind": "logical", "entity_type": "Object", "external_id": "Lead"}}]
    recipe = _recipe_read(observation_realization=_negative_body(extra_steps=extra))
    with pytest.raises(PlanTranslationError, match="must not carry expect_rejection"):
        build_data_recipe_plan(recipe)


def test_rejects_non_create_single_step():
    # A lone read (no create) is not a behavioral negative.
    body = DataRecipeBody.model_validate({
        "api_choice": "rest", "identity_context": "system",
        "execution_mechanism": "direct_api",
        "steps": [{"kind": "read", "step_id": "r",
                   "target": {"ref_kind": "logical", "entity_type": "Object", "external_id": "Lead"}}],
    })
    with pytest.raises(PlanTranslationError, match="begin with a CreateStep"):
        build_data_recipe_plan(_recipe_read(observation_realization=body))


def test_rejects_pinned_create_target():
    recipe = _recipe_read(observation_realization=_negative_body(target_pinned=True))
    with pytest.raises(PlanTranslationError, match="LogicalRef"):
        build_data_recipe_plan(recipe)


def test_translation_error_carries_recipe_id():
    rid = uuid4()
    with pytest.raises(PlanTranslationError) as exc:
        build_data_recipe_plan(_recipe_read(recipe_id=rid, recipe_kind="ui-recipe"))
    assert exc.value.recipe_id == rid


# ---------------------------------------------------------------------------
# Plan-model structure
# ---------------------------------------------------------------------------

def test_planned_create_is_frozen():
    plan = build_data_recipe_plan(_recipe_read())
    with pytest.raises(Exception):
        plan.steps[0].step_id = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# D-203 — the 2-step negative (setup create -> rejected update/delete)
# ---------------------------------------------------------------------------

def _two_step_body(*, mutation_kind="update", flag_mutation=True,
                   mutation_object="Lead") -> DataRecipeBody:
    target = {"ref_kind": "logical", "entity_type": "Object", "external_id": "Lead"}
    mut_target = {"ref_kind": "logical", "entity_type": "Object",
                  "external_id": mutation_object}
    mutation = {"kind": mutation_kind, "step_id": f"{mutation_kind}-violating",
                "target": mut_target}
    if mutation_kind == "update":
        mutation["field_changes"] = {"Lead.AnnualRevenue": 2000000}
    if flag_mutation:
        mutation["expect_rejection"] = {"error_code": _VR_CODE}
    return DataRecipeBody.model_validate({
        "api_choice": "rest", "identity_context": "system",
        "execution_mechanism": "direct_api",
        "steps": [{"kind": "create", "step_id": "create-setup",
                   "target_object": target,
                   "field_values": {"Lead.Company": "Acme"}},
                  mutation],
    })


def test_decodes_two_step_update_rejected_to_plan():
    plan = build_data_recipe_plan(
        _recipe_read(observation_realization=_two_step_body()))
    assert len(plan.steps) == 2
    setup, mutation = plan.steps
    assert isinstance(setup, PlannedCreate) and setup.expect_rejection is None
    assert setup.step_id == "create-setup"
    assert isinstance(mutation, PlannedUpdate)
    assert mutation.step_id == "update-violating"
    assert mutation.setup_step_id == "create-setup"          # positional binding
    assert mutation.field_changes == {"Lead.AnnualRevenue": 2000000}
    assert mutation.expect_rejection.error_code == _VR_CODE


def test_decodes_two_step_delete_rejected_to_plan():
    plan = build_data_recipe_plan(
        _recipe_read(observation_realization=_two_step_body(mutation_kind="delete")))
    setup, mutation = plan.steps
    assert isinstance(mutation, PlannedDelete)
    assert mutation.setup_step_id == "create-setup"
    assert mutation.expect_rejection.error_code == _VR_CODE


def test_unflagged_create_update_pair_routes_to_positive_and_fails_loud():
    # No step carries expect_rejection → the positive vertical, whose triple
    # gate names the mismatch (an ordinary update step is a 5b-2 concern).
    recipe = _recipe_read(
        observation_realization=_two_step_body(flag_mutation=False))
    with pytest.raises(PlanTranslationError, match="ReadStep -> AssertStep"):
        build_data_recipe_plan(recipe)


def test_rejects_mutation_on_a_different_object_than_setup():
    recipe = _recipe_read(observation_realization=_two_step_body(
        mutation_object="Account"))
    with pytest.raises(PlanTranslationError,
                       match="must act on the record the setup creates"):
        build_data_recipe_plan(recipe)


def test_rejects_three_step_negative():
    body = _two_step_body()
    three = DataRecipeBody.model_validate({
        "api_choice": "rest", "identity_context": "system",
        "execution_mechanism": "direct_api",
        "steps": ([s.model_dump(mode="json") for s in body.steps]
                  + [{"kind": "read", "step_id": "read-after",
                      "target": {"ref_kind": "logical", "entity_type": "Object",
                                 "external_id": "Lead"}}]),
    })
    recipe = _recipe_read(observation_realization=three)
    with pytest.raises(PlanTranslationError, match="N-step negatives are deferred"):
        build_data_recipe_plan(recipe)


def test_rejects_lone_flagged_update():
    # The common floor still demands the recipe begin with a CreateStep — a
    # flagged update with no setup has no record to mutate.
    body = DataRecipeBody.model_validate({
        "api_choice": "rest", "identity_context": "system",
        "execution_mechanism": "direct_api",
        "steps": [{"kind": "update", "step_id": "update-violating",
                   "target": {"ref_kind": "logical", "entity_type": "Object",
                              "external_id": "Lead"},
                   "field_changes": {"Lead.Status": "x"},
                   "expect_rejection": {"error_code": _VR_CODE}}],
    })
    with pytest.raises(PlanTranslationError, match="begin with a CreateStep"):
        build_data_recipe_plan(_recipe_read(observation_realization=body))


def test_planned_update_is_frozen():
    plan = build_data_recipe_plan(
        _recipe_read(observation_realization=_two_step_body()))
    with pytest.raises(Exception):
        plan.steps[1].step_id = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# D-205 — the N-create positive chain
# ---------------------------------------------------------------------------

def _chain_body(*, insert_read_between=False) -> DataRecipeBody:
    account = {"ref_kind": "logical", "entity_type": "Object", "external_id": "Account"}
    contact = {"ref_kind": "logical", "entity_type": "Object", "external_id": "Contact"}
    steps = [
        {"kind": "create", "step_id": "create-account",
         "target_object": account, "field_values": {"Account.Name": "PQA Chain"}},
    ]
    if insert_read_between:
        steps.append({"kind": "read", "step_id": "read-mid", "target": account,
                      "soql": "SELECT Id FROM Account", "fields_to_capture": ["Id"]})
    steps += [
        {"kind": "create", "step_id": "create-contact",
         "target_object": contact,
         "field_values": {"Contact.Email": "pqa@example.com",
                          "Contact.AccountId": "$create-account.id"}},
        {"kind": "read", "step_id": "read-contact", "target": contact,
         "soql": "SELECT Email FROM Contact WHERE Id = '$create-contact.id'",
         "fields_to_capture": ["Contact.Email"]},
        {"kind": "assert", "step_id": "assert-email",
         "predicate": {"subject_ref": "read-contact.Contact.Email",
                       "predicate": "equals", "value": "pqa@example.com"}},
    ]
    return DataRecipeBody.model_validate({
        "api_choice": "rest", "identity_context": "system",
        "execution_mechanism": "direct_api", "steps": steps,
    })


def test_decodes_two_create_chain_to_plan():
    plan = build_data_recipe_plan(
        _recipe_read(observation_realization=_chain_body()))
    kinds = [type(s).__name__ for s in plan.steps]
    assert kinds == ["PlannedCreate", "PlannedCreate", "PlannedDataRead",
                     "PlannedAssertion"]
    first, second = plan.steps[0], plan.steps[1]
    assert first.step_id == "create-account"
    assert second.step_id == "create-contact"
    # the cross-step reference survives projection VERBATIM (the executor
    # resolves it at run time).
    assert second.field_values["Contact.AccountId"] == "$create-account.id"
    assert first.expect_rejection is None and second.expect_rejection is None


def test_rejects_read_between_creates():
    # The chain is creates-then-read-then-assert; an interleaved read is not a
    # plannable shape (deferred until a consumer needs it, D-205 residual 2).
    recipe = _recipe_read(
        observation_realization=_chain_body(insert_read_between=True))
    with pytest.raises(PlanTranslationError, match="ReadStep -> AssertStep"):
        build_data_recipe_plan(recipe)


def test_positive_projection_carries_expect_acceptance():
    # D-305.1 (review B1): the flag must SURVIVE recipe->plan projection —
    # dropping it made the acceptance grade dead code on every real dispatch.
    from primeqa.execution_engine.bridge import build_data_recipe_plan
    from primeqa.test_representation.models.recipes.data_recipe import (
        AssertStep, CreateStep, DataRecipeBody, ReadStep)
    from primeqa.test_representation.models.primitives import AssertionPredicate
    from primeqa.test_representation.models.references import LogicalRef
    from primeqa.test_representation.models.triggers.data_mutation import (
        DataMutationTriggerBody)
    from primeqa.test_representation.models.environment import (
        AuthAssumption, ExecutionEnvironmentBody)
    from types import SimpleNamespace
    from uuid import uuid4

    body = DataRecipeBody(
        api_choice="rest", identity_context="system",
        execution_mechanism="direct_api",
        steps=[
            CreateStep(step_id="create-record",
                       target_object=LogicalRef(entity_type="Object",
                                                external_id="Opportunity"),
                       field_values={"Opportunity.Amount": 1},
                       expect_acceptance=True),
            ReadStep(step_id="read-created",
                     target=LogicalRef(entity_type="Object",
                                       external_id="Opportunity"),
                     soql="SELECT Id FROM Opportunity WHERE Id = '$create-record.id'",
                     fields_to_capture=["Id"]),
            AssertStep(step_id="assert-exists",
                       predicate=AssertionPredicate(
                           subject_ref="read-created.Id", predicate="exists")),
        ])
    recipe = SimpleNamespace(
        recipe_id=uuid4(), version_seq=1, claim_test_id=uuid4(),
        claim_version_seq=1, trigger_kind="data-mutation-trigger",
        recipe_kind="data-recipe", observation_realization=body,
        execution_environment=ExecutionEnvironmentBody(
            auth_assumptions=[AuthAssumption(auth_kind="data_api_user",
                                             details="x")]),
        causal_initiation=DataMutationTriggerBody(
            operation="create",
            target=LogicalRef(entity_type="Object", external_id="Opportunity"),
            identity_context="system", volume="single"),
        api_choice="rest")
    plan = build_data_recipe_plan(recipe)
    assert plan.steps[0].expect_acceptance is True


# ---------------------------------------------------------------------------
# D-306: the positive update-then-observe projection (lever 7d)
# ---------------------------------------------------------------------------

def _positive_update_body(
    *, expect_acceptance=False, qualified=True, empty_changes=False,
    wrong_object=False, two_updates=False, update_after_read=False,
    object_api="Account",
) -> DataRecipeBody:
    """The S2 body of an update-then-observe recipe (D-306): create →
    positive update (NO expect_rejection) → read-back → assert."""
    target = {"ref_kind": "logical", "entity_type": "Object", "external_id": object_api}
    changes_key = f"{object_api}.Status__c" if qualified else "Status__c"
    update = {"kind": "update", "step_id": "update-trigger",
              "target": ({"ref_kind": "logical", "entity_type": "Object",
                          "external_id": "Lead"} if wrong_object else target),
              "field_changes": {} if empty_changes else {changes_key: "Renewed"}}
    if expect_acceptance:
        update["expect_acceptance"] = True
    read = {"kind": "read", "step_id": "read-created", "target": target,
            "soql": f"SELECT Status__c FROM {object_api} WHERE Id = '$create-record.id'",
            "fields_to_capture": ["Status__c"]}
    assertion = {"kind": "assert", "step_id": "assert-value",
                 "predicate": {"subject_ref": "read-created.Status__c",
                               "predicate": "equals", "value": "Renewed"}}
    create = {"kind": "create", "step_id": "create-record",
              "target_object": target, "field_values": {"Status__c": "Active"}}
    if update_after_read:
        steps = [create, read, update, assertion]
    elif two_updates:
        steps = [create, update, {**update, "step_id": "update-again"}, read, assertion]
    else:
        steps = [create, update, read, assertion]
    return DataRecipeBody.model_validate({
        "api_choice": "rest", "identity_context": "system",
        "execution_mechanism": "direct_api", "steps": steps,
    })


def test_decodes_update_then_observe_recipe_to_plan():
    plan = build_data_recipe_plan(
        _recipe_read(observation_realization=_positive_update_body()))

    assert len(plan.steps) == 4
    create, update, read, assertion = plan.steps
    assert isinstance(create, PlannedCreate) and create.expect_rejection is None
    assert isinstance(update, PlannedUpdate)
    assert update.expect_rejection is None                      # the positive form
    assert update.expect_acceptance is False
    assert update.field_changes == {"Account.Status__c": "Renewed"}  # verbatim (qualified)
    assert update.setup_step_id == "create-record"              # bound to the terminal create
    assert isinstance(read, PlannedDataRead)
    assert isinstance(assertion, PlannedAssertion)


def test_positive_update_carries_expect_acceptance():
    # The D-305.1 lesson applied forward: the flag must SURVIVE projection —
    # a dropped flag makes the update leg's defining failure grade dead code.
    plan = build_data_recipe_plan(_recipe_read(
        observation_realization=_positive_update_body(expect_acceptance=True)))
    assert plan.steps[1].expect_acceptance is True


def test_rejects_positive_update_on_wrong_object():
    recipe = _recipe_read(
        observation_realization=_positive_update_body(wrong_object=True))
    with pytest.raises(PlanTranslationError, match="record under observation"):
        build_data_recipe_plan(recipe)


def test_rejects_positive_update_with_empty_changes():
    recipe = _recipe_read(
        observation_realization=_positive_update_body(empty_changes=True))
    with pytest.raises(PlanTranslationError, match="no field_changes"):
        build_data_recipe_plan(recipe)


def test_rejects_two_positive_updates():
    recipe = _recipe_read(
        observation_realization=_positive_update_body(two_updates=True))
    with pytest.raises(PlanTranslationError, match=r"UpdateStep x 0\.\.1"):
        build_data_recipe_plan(recipe)


def test_rejects_positive_update_after_read():
    recipe = _recipe_read(
        observation_realization=_positive_update_body(update_after_read=True))
    with pytest.raises(PlanTranslationError, match=r"UpdateStep x 0\.\.1"):
        build_data_recipe_plan(recipe)


def test_flagged_update_still_routes_to_the_negative_projection():
    # The dispatch boundary (D-306 changes nothing here): an UpdateStep
    # CARRYING expect_rejection routes negative even when read/assert follow —
    # and the negative projection fails loud on that 4-step shape.
    body = _positive_update_body()
    dumped = body.model_dump(mode="json")
    dumped["steps"][1]["expect_rejection"] = {"error_code": _VR_CODE}
    flagged = DataRecipeBody.model_validate(dumped)
    recipe = _recipe_read(observation_realization=flagged)
    with pytest.raises(PlanTranslationError, match="behavioral negative"):
        build_data_recipe_plan(recipe)
