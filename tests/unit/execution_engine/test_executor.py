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
    plan = _plan_with_assertion(
        AssertionPredicate(subject_ref="read-subject", predicate="equals", value="x"))
    with pytest.raises(UnsupportedPredicateError, match="equals"):
        execute_metadata_inspection(plan, client=_StubClient(rows=[{"Id": "1"}]),
                                    environment_id=_ENV_ID)


def test_unresolvable_subject_ref_fails_loud():
    plan = _plan_with_assertion(
        AssertionPredicate(subject_ref="ghost", predicate="exists"))
    with pytest.raises(AssertionResolutionError, match="ghost"):
        execute_metadata_inspection(plan, client=_StubClient(rows=[{"Id": "1"}]),
                                    environment_id=_ENV_ID)
