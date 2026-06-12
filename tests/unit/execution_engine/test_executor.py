"""Unit tests for the S4 metadata-inspection executor (D-108.1) — stub client,
no org, no PG.

Covers the full plan -> translate -> read -> eval -> evidence path and all three
grounded outcomes (passed / failed / errored), plus the fail-loud paths
(unsupported predicate, unresolvable subject_ref). The client is injected, so a
stub drives every case without a live org.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from primeqa.execution_engine import (
    AssertEvidence,
    ReadEvidence,
    RunEvidence,
    execute_metadata_inspection,
)
from primeqa.execution_engine.errors import (
    AssertionResolutionError,
    UnsupportedPredicateError,
)
from primeqa.execution_engine.bridge import build_metadata_inspection_plan
from primeqa.execution_engine.plan import (
    MetadataInspectionPlan,
    PlannedAssertion,
    PlannedRead,
)
from primeqa.generation.emission import GroundedNegative, _Endpoint, author_emission
from primeqa.integrations.exceptions import SFRequestError
from primeqa.test_representation.coordinator import RecipeRead
from primeqa.test_representation.models.primitives import AssertionPredicate
from primeqa.test_representation.models.references import LogicalRef

_NOW = datetime(2026, 5, 27, tzinfo=timezone.utc)
_ENV_ID = 7


# ---------------------------------------------------------------------------
# Stub client + a real emitted plan (via the slice-1 bridge)
# ---------------------------------------------------------------------------

class _StubClient:
    """A ToolingReadClient stand-in: returns canned rows or raises."""

    def __init__(self, rows=None, raises=None):
        self._rows = list(rows or [])
        self._raises = raises
        self.queries: list[str] = []

    def query(self, soql: str) -> list[dict]:
        self.queries.append(soql)
        if self._raises is not None:
            raise self._raises
        return list(self._rows)


def _emitted_plan(recipe_id=None, version_seq=4, claim_test_id=None):
    """Build a real metadata-inspection plan from an emitted prohibition recipe
    (read APPLIES_TO over Lead + assert exists), via the slice-1 bridge."""
    bundle = author_emission(GroundedNegative(
        archetype="data_behavior", claim_kind="prohibition-claim",
        operation_hint="delete", version_seq=7,
        subject=_Endpoint(entity_id=uuid4(), entity_type="Object", external_id="Lead"),
        requirement_excerpt="Users must not delete a Lead without a reason."))
    recipe = RecipeRead(
        recipe_id=recipe_id or uuid4(), version_seq=version_seq,
        valid_from=_NOW, valid_to=None,
        claim_test_id=claim_test_id or uuid4(), claim_version_seq=None,
        trigger_kind="inspection-trigger", recipe_kind="metadata-recipe",
        causal_initiation=bundle.causal_initiation,
        observation_realization=bundle.observation_realization,
        execution_environment=bundle.execution_environment,
        priority=0, status="generated_unapproved",
        created_at=_NOW, updated_at=_NOW)
    return build_metadata_inspection_plan(recipe)


# ---------------------------------------------------------------------------
# The three grounded outcomes
# ---------------------------------------------------------------------------

def test_passed_when_read_returns_rows_and_exists_holds():
    client = _StubClient(rows=[{"Id": "03d", "ValidationName": "Require_Reason"}])
    ev = execute_metadata_inspection(_emitted_plan(), client=client, environment_id=_ENV_ID)

    assert isinstance(ev, RunEvidence)
    assert ev.outcome == "passed"
    assert ev.environment_id == _ENV_ID
    read, assertion = ev.steps
    assert isinstance(read, ReadEvidence) and read.row_count == 1
    assert "EntityDefinition.QualifiedApiName = 'Lead'" in read.query
    assert isinstance(assertion, AssertEvidence)
    assert assertion.held is True and assertion.evaluated_row_count == 1
    # exactly one scoped query was issued.
    assert len(client.queries) == 1


def test_failed_when_read_returns_no_rows():
    client = _StubClient(rows=[])
    ev = execute_metadata_inspection(_emitted_plan(), client=client, environment_id=_ENV_ID)

    assert ev.outcome == "failed"
    read, assertion = ev.steps
    assert read.row_count == 0 and assertion.held is False
    assert ev.error is None  # a failed assertion is not an error


def test_failed_run_records_query_and_filter_for_s6():
    # Evidence richness: a 0-row read records the query + the structured filter
    # (subject), so S6 can later tell absent-object from present-but-no-VR. S4
    # does NOT infer which — it records.
    ev = execute_metadata_inspection(_emitted_plan(), client=_StubClient(rows=[]),
                                     environment_id=_ENV_ID)
    read = ev.steps[0]
    assert read.subject_entity_type == "Object"
    assert read.subject_external_id == "Lead"
    assert read.edge == "APPLIES_TO"
    assert "EntityDefinition.QualifiedApiName = 'Lead'" in read.query
    assert read.row_count == 0


def test_errored_when_read_transport_fails():
    client = _StubClient(raises=SFRequestError("boom", status_code=503))
    ev = execute_metadata_inspection(_emitted_plan(), client=client, environment_id=_ENV_ID)

    assert ev.outcome == "errored"
    # the walk stops at the failed read — the assertion is never reached.
    assert len(ev.steps) == 1
    read = ev.steps[0]
    assert read.error is not None
    assert read.error.phase == "read"
    assert read.error.error_type == "SFRequestError"
    # top-level error surface mirrors it.
    assert ev.error is not None and ev.error.error_type == "SFRequestError"


def test_run_id_is_minted_at_run_start_and_carried():
    # The run self-identifies from birth: the executor mints a fresh run_id
    # (uuid4) per call, carried on the evidence (slice-3 PK / slice-4 last_run_id).
    from uuid import UUID
    client = _StubClient(rows=[{"Id": "1"}])
    ev1 = execute_metadata_inspection(_emitted_plan(), client=client, environment_id=_ENV_ID)
    ev2 = execute_metadata_inspection(_emitted_plan(), client=client, environment_id=_ENV_ID)
    assert isinstance(ev1.run_id, UUID)
    assert ev1.run_id != ev2.run_id        # fresh per run


def test_evidence_carries_recipe_and_claim_identity():
    rid, ctid = uuid4(), uuid4()
    ev = execute_metadata_inspection(
        _emitted_plan(recipe_id=rid, version_seq=9, claim_test_id=ctid),
        client=_StubClient(rows=[{"Id": "1"}]), environment_id=_ENV_ID)
    assert ev.recipe_id == rid
    assert ev.recipe_version_seq == 9
    assert ev.claim_test_id == ctid
    assert ev.api_choice == "metadata_api"


# ---------------------------------------------------------------------------
# existence execution (D-127) — the self-read path through the same executor
# ---------------------------------------------------------------------------

def _existence_plan(entity_type="Object", external_id="Account"):
    """A real metadata-inspection plan from an emitted existence recipe — a
    self-read of the subject's own metadata + assert `exists`."""
    from primeqa.generation.emission import GroundedExistence
    bundle = author_emission(GroundedExistence(
        archetype="configuration", claim_kind="existence-claim", version_seq=7,
        subject=_Endpoint(entity_id=uuid4(), entity_type=entity_type, external_id=external_id),
        requirement_excerpt=f"{external_id} exists in the org"))
    recipe = RecipeRead(
        recipe_id=uuid4(), version_seq=1, valid_from=_NOW, valid_to=None,
        claim_test_id=uuid4(), claim_version_seq=None,
        trigger_kind="inspection-trigger", recipe_kind="metadata-recipe",
        causal_initiation=bundle.causal_initiation,
        observation_realization=bundle.observation_realization,
        execution_environment=bundle.execution_environment,
        priority=0, status="generated_unapproved", created_at=_NOW, updated_at=_NOW)
    return build_metadata_inspection_plan(recipe)


def test_existence_object_passes_via_entity_definition_self_read():
    # existence on an Object routes through the EntityDefinition self-read; a
    # non-empty result grounds `passed` (the self-read IS the verification).
    client = _StubClient(rows=[{"QualifiedApiName": "Account"}])
    ev = execute_metadata_inspection(_existence_plan("Object", "Account"),
                                     client=client, environment_id=_ENV_ID)
    assert ev.outcome == "passed"
    read, assertion = ev.steps
    assert "FROM EntityDefinition" in read.query
    assert "QualifiedApiName = 'Account'" in read.query
    assert read.edge == "sf_api_name"
    assert assertion.held is True


def test_existence_field_fails_when_absent():
    # existence on a Field routes through FieldDefinition; an empty result is a
    # grounded `failed` (the asserted field does not surface), not an error.
    ev = execute_metadata_inspection(_existence_plan("Field", "Account.Industry"),
                                     client=_StubClient(rows=[]), environment_id=_ENV_ID)
    assert ev.outcome == "failed"
    read = ev.steps[0]
    assert "FROM FieldDefinition" in read.query
    assert "EntityDefinition.QualifiedApiName = 'Account'" in read.query
    assert ev.error is None


# ---------------------------------------------------------------------------
# property execution (D-128) — equals / is_null over a captured column value
# ---------------------------------------------------------------------------

def _property_plan(property_name, expected_value, external_id="Account.Description"):
    """A real metadata-inspection plan from an emitted property recipe — a
    self-read capturing a mapped Field property + an equals/is_null assert."""
    from primeqa.generation.emission import GroundedProperty
    bundle = author_emission(GroundedProperty(
        archetype="configuration", claim_kind="property-claim", version_seq=7,
        subject=_Endpoint(entity_id=uuid4(), entity_type="Field", external_id=external_id),
        property_name=property_name, expected_value=expected_value,
        requirement_excerpt=f"{external_id}.{property_name} == {expected_value!r}"))
    recipe = RecipeRead(
        recipe_id=uuid4(), version_seq=1, valid_from=_NOW, valid_to=None,
        claim_test_id=uuid4(), claim_version_seq=None,
        trigger_kind="inspection-trigger", recipe_kind="metadata-recipe",
        causal_initiation=bundle.causal_initiation,
        observation_realization=bundle.observation_realization,
        execution_environment=bundle.execution_environment,
        priority=0, status="generated_unapproved", created_at=_NOW, updated_at=_NOW)
    return build_metadata_inspection_plan(recipe)


def test_property_equals_passes_when_value_matches():
    # length == 255 and the org's FieldDefinition.Length is 255 → passed.
    client = _StubClient(rows=[{"Length": 255}])
    ev = execute_metadata_inspection(_property_plan("length", 255), client=client,
                                     environment_id=_ENV_ID)
    assert ev.outcome == "passed"
    assert "SELECT Length FROM FieldDefinition" in client.queries[0]
    read, assertion = ev.steps
    assert assertion.predicate == "equals" and assertion.held is True


def test_property_equals_fails_when_value_differs():
    ev = execute_metadata_inspection(_property_plan("length", 255),
                                     client=_StubClient(rows=[{"Length": 100}]),
                                     environment_id=_ENV_ID)
    assert ev.outcome == "failed"
    assert ev.steps[1].held is False
    assert ev.error is None


def test_property_equals_coerces_string_vs_int_representation():
    # Tooling JSON may render a number as a string; the coercion fallback matches
    # "255" against 255 without masking a real mismatch.
    ev = execute_metadata_inspection(_property_plan("length", 255),
                                     client=_StubClient(rows=[{"Length": "255"}]),
                                     environment_id=_ENV_ID)
    assert ev.outcome == "passed"


def test_property_is_null_holds_when_value_absent():
    # is_null grounds when the captured column is absent/None. (scale is None on a
    # non-decimal field.) A present value would fail the is_null assertion.
    ev = execute_metadata_inspection(_property_plan("scale", None),
                                     client=_StubClient(rows=[{"Scale": None}]),
                                     environment_id=_ENV_ID)
    assert ev.outcome == "passed"
    assert ev.steps[1].predicate == "is_null" and ev.steps[1].held is True


def test_property_unmapped_is_required_fails_loud():
    # is_required has no faithful Tooling column → UnsupportedPropertyError,
    # surfaced before any client call (never a guessed column / wrong pass).
    from primeqa.execution_engine.errors import UnsupportedPropertyError
    with pytest.raises(UnsupportedPropertyError, match="is_required"):
        execute_metadata_inspection(_property_plan("is_required", True),
                                    client=_StubClient(rows=[{}]), environment_id=_ENV_ID)


# ---------------------------------------------------------------------------
# Fail-loud paths (representation / plan defects — not run outcomes)
# ---------------------------------------------------------------------------

def _plan_with_assertion(predicate: AssertionPredicate):
    return MetadataInspectionPlan(
        recipe_id=uuid4(), recipe_version_seq=1, claim_test_id=uuid4(),
        claim_version_seq=None, api_choice="metadata_api",
        steps=(
            PlannedRead(step_id="read-subject",
                        target_entity=LogicalRef(entity_type="Object", external_id="Lead"),
                        fields_to_capture=("APPLIES_TO",)),
            PlannedAssertion(step_id="assert-edge", predicate=predicate),
        ))


def test_unsupported_predicate_fails_loud():
    # `equals`/`is_null` are supported (D-128); `matches_pattern` is not yet.
    plan = _plan_with_assertion(
        AssertionPredicate(subject_ref="read-subject", predicate="matches_pattern", value="x.*"))
    with pytest.raises(UnsupportedPredicateError, match="matches_pattern"):
        execute_metadata_inspection(plan, client=_StubClient(rows=[{"Id": "1"}]),
                                    environment_id=_ENV_ID)


def test_value_predicate_over_presence_only_read_fails_loud():
    # `equals` over an edge/existence read that captured no value column is a
    # recipe defect (a value asserted over a presence-only read) → fail-loud.
    plan = _plan_with_assertion(
        AssertionPredicate(subject_ref="read-subject", predicate="equals", value="x"))
    with pytest.raises(AssertionResolutionError, match="value column"):
        execute_metadata_inspection(plan, client=_StubClient(rows=[{"Id": "1"}]),
                                    environment_id=_ENV_ID)


def test_unresolvable_subject_ref_fails_loud():
    plan = _plan_with_assertion(
        AssertionPredicate(subject_ref="ghost", predicate="exists"))
    with pytest.raises(AssertionResolutionError, match="ghost"):
        execute_metadata_inspection(plan, client=_StubClient(rows=[{"Id": "1"}]),
                                    environment_id=_ENV_ID)


# ---------------------------------------------------------------------------
# D-224 — data-API dispatch + the INCLUDES_FIELD membership probe
# ---------------------------------------------------------------------------

from primeqa.generation.emission import GroundedCapability, GroundedLayout


class _DualStubClient(_StubClient):
    """Adds the D-224 query_data endpoint + per-call scripted tooling rows."""

    def __init__(self, *, data_rows=None, tooling_script=None):
        super().__init__(rows=[])
        self._data_rows = list(data_rows or [])
        self._tooling_script = list(tooling_script or [])
        self.data_queries: list[str] = []

    def query(self, soql: str) -> list[dict]:
        self.queries.append(soql)
        if self._tooling_script:
            return self._tooling_script.pop(0)
        return []

    def query_data(self, soql: str) -> list[dict]:
        self.data_queries.append(soql)
        return list(self._data_rows)


def _plan_from(bundle):
    recipe = RecipeRead(
        recipe_id=uuid4(), version_seq=4, valid_from=_NOW, valid_to=None,
        claim_test_id=uuid4(), claim_version_seq=None,
        trigger_kind="inspection-trigger", recipe_kind="metadata-recipe",
        causal_initiation=bundle.causal_initiation,
        observation_realization=bundle.observation_realization,
        execution_environment=bundle.execution_environment,
        priority=0, status="generated_unapproved",
        created_at=_NOW, updated_at=_NOW)
    return build_metadata_inspection_plan(recipe)


def _capability_plan():
    return _plan_from(author_emission(GroundedCapability(
        archetype="permission", claim_kind="capability-claim", version_seq=7,
        granting_subject=_Endpoint(entity_id=uuid4(), entity_type="Profile",
                                   external_id="Admin"),
        target=_Endpoint(entity_id=uuid4(), entity_type="Field",
                         external_id="Account.AnnualRevenue"),
        granted_capability="edit", grant_type="field",
        requirement_excerpt="Admin can edit AnnualRevenue")))


def _layout_plan():
    return _plan_from(author_emission(GroundedLayout(
        archetype="ui", claim_kind="layout-claim", version_seq=7,
        layout=_Endpoint(entity_id=uuid4(), entity_type="Layout",
                         external_id="Account-Account Layout"),
        field=_Endpoint(entity_id=uuid4(), entity_type="Field",
                        external_id="Account.AnnualRevenue"),
        requirement_excerpt="AnnualRevenue is on the Account layout")))


def test_capability_run_passes_when_flag_true():
    client = _DualStubClient(data_rows=[{"PermissionsEdit": True}])
    ev = execute_metadata_inspection(_capability_plan(), client=client,
                                     environment_id=_ENV_ID)
    assert ev.outcome == "passed"
    assert client.data_queries and "FieldPermissions" in client.data_queries[0]
    assert client.queries == []          # nothing hit the tooling endpoint


def test_capability_run_fails_when_flag_false_or_row_absent():
    for rows in ([{"PermissionsEdit": False}], []):
        client = _DualStubClient(data_rows=rows)
        ev = execute_metadata_inspection(_capability_plan(), client=client,
                                         environment_id=_ENV_ID)
        assert ev.outcome == "failed"


def test_layout_probe_passes_when_field_placed():
    meta = {"layoutSections": [{"layoutRows": [{"layoutItems": [
        {"layoutComponents": [{"type": "Field", "value": "AnnualRevenue"}]}]}]}]}
    client = _DualStubClient(tooling_script=[
        [{"Id": "00h1", "EntityDefinitionId": "Account"}],
        [{"Metadata": meta}],
    ])
    ev = execute_metadata_inspection(_layout_plan(), client=client,
                                     environment_id=_ENV_ID)
    assert ev.outcome == "passed"
    assert len(client.queries) == 2
    assert "SELECT Metadata FROM Layout" in client.queries[1]


def test_layout_probe_fails_when_field_absent():
    meta = {"layoutSections": [{"layoutRows": [{"layoutItems": [
        {"layoutComponents": [{"type": "Field", "value": "Industry"}]}]}]}]}
    client = _DualStubClient(tooling_script=[
        [{"Id": "00h1", "EntityDefinitionId": "Account"}],
        [{"Metadata": meta}],
    ])
    ev = execute_metadata_inspection(_layout_plan(), client=client,
                                     environment_id=_ENV_ID)
    assert ev.outcome == "failed"


def test_layout_probe_no_matching_layout_is_honest_failed():
    client = _DualStubClient(tooling_script=[[]])
    ev = execute_metadata_inspection(_layout_plan(), client=client,
                                     environment_id=_ENV_ID)
    assert ev.outcome == "failed"
    assert len(client.queries) == 1      # never fetched Metadata
