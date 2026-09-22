"""Round 4, part A (AUD-028): the run row exists BEFORE the first record is
provisioned, and an interrupted run leaves a FAILED run — never an orphan.

No DB: a recording sink stands in for ``StrandedRecordSink`` and the stub
client / stub S1 of the positive-path suite drive the real executor.

1. ORDER — ``run_opened`` is called before the client's first ``create``;
2. RETURN — a run that produced evidence is handed back (``run_returned``),
   so nothing is left for the interrupt-close;
3. INTERRUPT — the worker's SIGTERM (``KeyboardInterrupt`` raised inside
   ``client.create``, D-341) closes the opened run BEFORE it propagates;
4. the interrupted evidence names ``worker_shutdown`` for a shutdown and the
   exception class otherwise, and finalize COMPLETES an opened row instead of
   inserting a second one;
5. a sink that cannot open the run refuses BEFORE any create (fail-loud);
6. the negative 1-step path opens nothing (it provisions nothing).
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from primeqa.execution_engine.data_executor import execute_data_recipe
from primeqa.execution_engine.plan import DataRecipePlan, PlannedCreate
from primeqa.execution_engine.result_store import (
    RUNNING, S4ExecutionRun, WORKER_SHUTDOWN, interrupted_evidence, persist_run_evidence)
from primeqa.test_representation.models.primitives import RejectionExpectation
from primeqa.test_representation.models.references import LogicalRef
from tests.unit.execution_engine.test_data_executor_positive import (
    _StubClient, _plan, _s1, _success)

pytestmark = pytest.mark.unit
_ENV = 7


class _RecordingSink:
    """The sink's lifecycle surface, recording the ORDER of every call."""

    def __init__(self, open_raises=None):
        self.calls = []
        self.open_raises = open_raises
        self.opened = None

    def run_opened(self, **identity):
        self.calls.append(("run_opened", identity["run_id"]))
        if self.open_raises is not None:
            raise self.open_raises
        self.opened = dict(identity, environment_id=_ENV)

    def run_returned(self, run_id):
        self.calls.append(("run_returned", run_id))

    def close_interrupted(self, exc):
        self.calls.append(("close_interrupted", type(exc).__name__))
        return self.opened["run_id"] if self.opened else None

    def created(self, run_id, sobject, record_id, created_seq):
        self.calls.append(("created", record_id))

    def cleaned(self, run_id, record_id):
        self.calls.append(("cleaned", record_id))


class _OrderedClient(_StubClient):
    """Records its creates on the SAME call list as the sink, so order is one list."""

    def __init__(self, sink, **kw):
        super().__init__(**kw)
        self._sink = sink

    def create(self, sobject, field_values):
        self._sink.calls.append(("client.create", sobject))
        return super().create(sobject, field_values)


def test_the_run_is_opened_before_the_first_create_and_returned_after():
    sink = _RecordingSink()
    client = _OrderedClient(sink, create_result=_success("001Z"),
                            query_result=[{"Status__c": "Active"}])
    ev = execute_data_recipe(_plan(), client=client, environment_id=_ENV, s1=_s1(),
                             record_sink=sink)
    names = [c[0] for c in sink.calls]
    assert names[0] == "run_opened", names
    assert names.index("run_opened") < names.index("client.create")
    assert sink.calls[0][1] == ev.run_id                     # the SAME run id the evidence carries
    assert ("run_returned", ev.run_id) in sink.calls
    assert "close_interrupted" not in names
    assert ev.outcome == "passed"


def test_a_shutdown_inside_create_closes_the_opened_run_before_propagating():
    sink = _RecordingSink()
    client = _OrderedClient(sink, create_raises=KeyboardInterrupt("SIGTERM 15"))
    with pytest.raises(KeyboardInterrupt):
        execute_data_recipe(_plan(), client=client, environment_id=_ENV, s1=_s1(),
                            record_sink=sink)
    names = [c[0] for c in sink.calls]
    assert names.index("run_opened") < names.index("client.create") < names.index("close_interrupted")
    assert ("close_interrupted", "KeyboardInterrupt") in sink.calls
    assert "run_returned" not in names


def test_a_sink_that_cannot_open_refuses_before_any_create():
    sink = _RecordingSink(open_raises=RuntimeError("database unavailable"))
    client = _OrderedClient(sink, create_result=_success("001Z"))
    with pytest.raises(RuntimeError, match="database unavailable"):
        execute_data_recipe(_plan(), client=client, environment_id=_ENV, s1=_s1(),
                            record_sink=sink)
    assert client.creates == [], "a record was created for a run that has no row"
    assert [c[0] for c in sink.calls] == ["run_opened", "close_interrupted"]


def test_the_one_step_negative_path_opens_nothing():
    target = LogicalRef(entity_type="Object", external_id="Account")
    plan = DataRecipePlan(
        recipe_id=uuid4(), recipe_version_seq=1, claim_test_id=uuid4(),
        claim_version_seq=None, api_choice="rest",
        steps=(PlannedCreate(step_id="create-record", target_object=target,
                             field_values={"Status__c": "X"},
                             expect_rejection=RejectionExpectation(
                                 error_code="FIELD_CUSTOM_VALIDATION_EXCEPTION",
                                 error_message_pattern=None)),))
    sink = _RecordingSink()
    client = _OrderedClient(sink, create_result={
        "api_response": {"status_code": 400, "body": [
            {"errorCode": "FIELD_CUSTOM_VALIDATION_EXCEPTION", "message": "no",
             "fields": ["Status__c"]}]},
        "http_status": 400, "success": False, "record_id": None})
    execute_data_recipe(plan, client=client, environment_id=_ENV, s1=None,
                        record_sink=sink)
    assert "run_opened" not in [c[0] for c in sink.calls]


def _opened(run_id=None):
    return {"run_id": run_id or uuid4(), "recipe_id": uuid4(), "recipe_version_seq": 3,
            "claim_test_id": uuid4(), "claim_version_seq": 2, "environment_id": _ENV,
            "started_at": datetime(2026, 9, 22, 2, 0, tzinfo=timezone.utc)}


def test_interrupted_evidence_names_the_shutdown_or_the_exception():
    o = _opened()
    ev = interrupted_evidence(o, KeyboardInterrupt("SIGTERM 15"))
    assert (ev.run_id, ev.outcome, ev.steps) == (o["run_id"], "errored", ())
    assert ev.error.error_type == WORKER_SHUTDOWN
    assert ev.error.phase == "construct"
    assert ev.claim_test_id == o["claim_test_id"] and ev.environment_id == _ENV
    ev2 = interrupted_evidence(o, ValueError("boom"))
    assert (ev2.error.error_type, ev2.error.message) == ("ValueError", "boom")


class _SessionWithRow:
    """A session holding one existing row for ``get``; records adds."""

    def __init__(self, row):
        self.row, self.added, self.flushed = row, [], 0

    def get(self, cls, pk):
        return self.row if (self.row is not None and self.row.run_id == pk) else None

    def add(self, row):
        self.added.append(row)

    def flush(self):
        self.flushed += 1


def test_finalize_completes_an_opened_row_instead_of_inserting_a_second():
    o = _opened()
    running = S4ExecutionRun(run_id=o["run_id"], outcome=RUNNING, started_at=o["started_at"],
                             finished_at=None, evidence={"run_state": "running"})
    session = _SessionWithRow(running)
    ev = interrupted_evidence(o, KeyboardInterrupt("SIGTERM 15"))
    assert persist_run_evidence(session, ev) == o["run_id"]
    assert session.added == [], "a second row was inserted for an opened run"
    assert running.outcome == "errored" and running.finished_at is not None
    assert running.evidence["error"]["error_type"] == WORKER_SHUTDOWN
    assert session.flushed == 1


def test_finalize_of_a_finalized_run_id_still_inserts_and_so_still_fails_loud():
    """The D-108.3 layer is unchanged: only a RUNNING row is completed in place."""
    o = _opened()
    done = S4ExecutionRun(run_id=o["run_id"], outcome="passed", started_at=o["started_at"],
                          finished_at=o["started_at"], evidence={})
    session = _SessionWithRow(done)
    persist_run_evidence(session, interrupted_evidence(o, ValueError("x")))
    assert len(session.added) == 1                            # the PK will refuse it at flush
    assert done.outcome == "passed"
