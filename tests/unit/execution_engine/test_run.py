"""Unit tests for the run path (D-108.4) — stub client + spy coordinator, no DB.

run_recipe_execution chains select -> bridge -> execute -> finalize. These tests
drive a spy coordinator (select + report_run_outcome) + a stub client, so the
whole chain runs with no org and no DB — the slice-2/3/4 no-DB boundary holds
here too. Covers: the ran path, the no-eligible-recipe branch, errored
passthrough, and the injected-client default-bypass.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from primeqa.execution_engine.run import RunPathResult, run_recipe_execution
from primeqa.generation.emission import _inspection_recipe
from primeqa.test_representation.coordinator import RecipeRead

_NOW = datetime(2026, 5, 27, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

class _StubClient:
    def __init__(self, rows=None, raises=None):
        self._rows, self._raises = list(rows or []), raises
        self.queries = []

    def query(self, soql):
        self.queries.append(soql)
        if self._raises is not None:
            raise self._raises
        return list(self._rows)


class _SpyCoordinator:
    """Returns a canned RecipeRead from select, records report_run_outcome."""

    def __init__(self, recipe):
        self._recipe = recipe
        self.select_calls = []
        self.report_calls = []
        self.persisted = []   # rows added via finalize's persist (fake session)

    def select_recipe_for_execution(self, session, test_id, *, available_environment, replay_mode):
        self.select_calls.append((test_id, available_environment, replay_mode))
        return self._recipe

    def report_run_outcome(self, session, **kwargs):
        self.report_calls.append(kwargs)
        return {"runtime_state": "stub", "last_run_outcome": kwargs["last_run_outcome"]}


class _FakeSession:
    def __init__(self):
        self.added = []

    def add(self, row):
        self.added.append(row)

    def flush(self):
        pass


def _inspection_recipe_read(recipe_id=None):
    """A real metadata-inspection RecipeRead (the bridge accepts it). Built
    directly from the inspection-recipe builder — D-293 removed the prohibition
    -> inspection emission this used to source from (prohibitions now emit a
    behavioural reject recipe); the run path is unchanged, so any inspection
    recipe drives it identically."""
    trigger, recipe, env = _inspection_recipe(
        read_entity_type="Object", read_external_id="Lead", capture_field="APPLIES_TO",
        env_detail="read Lead metadata to verify a validation rule applies")
    return RecipeRead(
        recipe_id=recipe_id or uuid4(), version_seq=3, valid_from=_NOW, valid_to=None,
        claim_test_id=uuid4(), claim_version_seq=None,
        trigger_kind="inspection-trigger", recipe_kind="metadata-recipe",
        causal_initiation=trigger,
        observation_realization=recipe,
        execution_environment=env,
        priority=0, status="approved", created_at=_NOW, updated_at=_NOW)


# ---------------------------------------------------------------------------
# The ran path
# ---------------------------------------------------------------------------

def test_runs_end_to_end_passed():
    rid, tid = uuid4(), uuid4()
    recipe = _inspection_recipe_read(recipe_id=rid)
    coord = _SpyCoordinator(recipe)
    client = _StubClient(rows=[{"Id": "03d", "ValidationName": "Req"}])

    result = run_recipe_execution(
        _FakeSession(), tid, environment_id=9, client=client, coordinator=coord)

    assert isinstance(result, RunPathResult)
    assert result.ran is True
    assert result.selected_recipe_id == rid
    assert result.evidence.outcome == "passed"
    # selection happened with the run-path's default inspection env + test_id.
    assert coord.select_calls[0][0] == tid
    assert coord.select_calls[0][2] == "live"
    # posture reported once with the run's run_id (finalize wired through).
    assert len(coord.report_calls) == 1
    assert coord.report_calls[0]["last_run_id"] == result.evidence.run_id
    assert coord.report_calls[0]["last_run_outcome"] == "passed"
    # the live read actually ran via the injected client (resolve bypassed).
    assert len(client.queries) == 1


def test_failed_when_no_rows():
    coord = _SpyCoordinator(_inspection_recipe_read())
    result = run_recipe_execution(
        _FakeSession(), uuid4(), environment_id=1, client=_StubClient(rows=[]),
        coordinator=coord)
    assert result.ran is True
    assert result.evidence.outcome == "failed"
    assert coord.report_calls[0]["last_run_outcome"] == "failed"


def test_errored_run_still_finalizes():
    from primeqa.integrations.exceptions import SFRequestError
    coord = _SpyCoordinator(_inspection_recipe_read())
    result = run_recipe_execution(
        _FakeSession(), uuid4(), environment_id=1,
        client=_StubClient(raises=SFRequestError("boom", status_code=503)),
        coordinator=coord)
    # errored is truth — the run still finalizes (persist + posture).
    assert result.ran is True
    assert result.evidence.outcome == "errored"
    assert len(coord.report_calls) == 1
    assert coord.report_calls[0]["last_run_outcome"] == "errored"


# ---------------------------------------------------------------------------
# The no-eligible-recipe branch
# ---------------------------------------------------------------------------

def test_no_eligible_recipe_is_a_distinct_result_not_a_run():
    class _NoRecipeCoord(_SpyCoordinator):
        def select_recipe_for_execution(self, *a, **k):
            return None

    coord = _NoRecipeCoord(None)
    client = _StubClient(rows=[{"Id": "1"}])
    result = run_recipe_execution(
        _FakeSession(), uuid4(), environment_id=1, client=client, coordinator=coord)

    assert result.ran is False
    assert result.reason == "no_eligible_recipe"
    assert result.evidence is None and result.runtime_state is None
    # nothing executed, nothing reported.
    assert client.queries == []
    assert coord.report_calls == []


def test_passes_explicit_available_environment_through_to_select():
    from primeqa.test_representation.models.environment import (
        AuthAssumption, ExecutionEnvironmentBody)
    coord = _SpyCoordinator(_inspection_recipe_read())
    custom_env = ExecutionEnvironmentBody(
        auth_assumptions=[AuthAssumption(auth_kind="metadata_api_user")],
        org_kind="sandbox")
    run_recipe_execution(
        _FakeSession(), uuid4(), environment_id=1, available_environment=custom_env,
        client=_StubClient(rows=[{"Id": "1"}]), coordinator=coord)
    assert coord.select_calls[0][1] is custom_env       # explicit env, not the default
