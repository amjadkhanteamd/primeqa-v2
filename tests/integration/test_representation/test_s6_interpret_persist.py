"""Integration: the S6 interpret stage wired into the S4 run path (D-111.2).

The run path now, after ``finalize_run``, **interprets + persists** the run
eagerly and best-effort: ``interpret_run`` → ``attribute_run`` (contemporaneous
S1) → ``persist_interpretation``, all inside a SAVEPOINT. This suite proves the
wiring + the two invariants that matter:

  1. **happy path** — a finalized run yields a persisted, attributed
     :class:`S6Interpretation` row + ``RunPathResult.interpretation``;
  2. **best-effort isolation (the key one)** — an injected S6 failure (the S1
     reader raising, or a real DB error in persist) rolls back to the savepoint
     and leaves ``interpretation=None``, while the **run truth** ``finalize_run``
     flushed *before* the savepoint (the ``s4_execution_runs`` row + the S2
     posture) survives and is still queryable. Evidence-first: a softer S6
     failure never rolls back captured truth.

Reuses this package's ``session`` fixture (``connection.begin()`` +
``Session(bind=connection)`` — structurally identical to the production
``get_tenant_connection``, so ``begin_nested`` nests a real SAVEPOINT) and the
``_seed_approved_inspection_recipe`` pattern from ``test_s4_run_path``. An
inspection recipe needs no S1 seed (``attribute_run`` is pass-through for the
inspection verdicts) — the deeper-cause attribution is proven separately in
``test_s6_s1_reader.py``.
"""
from __future__ import annotations

import logging
from uuid import uuid4

from sqlalchemy import text

from types import SimpleNamespace

from primeqa.execution_engine.result_store import S4ExecutionRun
from primeqa.execution_engine.run import run_recipe_execution
from primeqa.generation.emission import _inspection_recipe
from primeqa.interpretation.result_store import S6Interpretation
from primeqa.test_representation import SemanticTransactionCoordinator

from ._fixtures import arrange_approved_claim


class _StubClient:
    """A metadata-inspection read client: returns the seeded rows verbatim."""

    def __init__(self, rows=None):
        self._rows = list(rows or [])
        self.queries = []

    def query(self, soql):
        self.queries.append(soql)
        return list(self._rows)


def _seed_approved_inspection_recipe(session, coord, *, subject="Lead"):
    """Approved claim + an approved inspection recipe (real emitted bodies) —
    the same arrangement test_s4_run_path uses."""
    test_id, _ = arrange_approved_claim(session, coord)
    # D-293 removed the prohibition -> inspection emission this used to source
    # from; build the inspection recipe directly (the interpret path is unchanged).
    _trigger, _recipe, _env = _inspection_recipe(
        read_entity_type="Object", read_external_id=subject, capture_field="APPLIES_TO",
        env_detail=f"read {subject} metadata to verify a validation rule applies")
    bundle = SimpleNamespace(
        causal_initiation=_trigger, observation_realization=_recipe,
        execution_environment=_env)
    written = coord.write_recipe(
        session, actor="human", recipe_id=None, claim_test_id=test_id,
        trigger_kind="inspection-trigger", recipe_kind="metadata-recipe",
        causal_initiation=bundle.causal_initiation,
        observation_realization=bundle.observation_realization,
        execution_environment=bundle.execution_environment)
    session.execute(text(
        "UPDATE test_recipes SET status = 'approved' "
        "WHERE recipe_id = :r AND version_seq = :v"),
        {"r": str(written.recipe_id), "v": written.version_seq})
    session.flush()
    return test_id, written.recipe_id


# ---------------------------------------------------------------------------
# 1. Happy path — the interpretation is produced, attributed, persisted.
# ---------------------------------------------------------------------------

def test_run_path_persists_interpretation(session):
    coord = SemanticTransactionCoordinator()
    test_id, recipe_id = _seed_approved_inspection_recipe(session, coord, subject="Lead")

    result = run_recipe_execution(
        session, test_id, environment_id=7,
        client=_StubClient(rows=[{"Id": "03d", "ValidationName": "Req"}]),
        coordinator=coord)

    # ran + the run produced an interpretation (the new D-111.2 field).
    assert result.ran is True
    assert result.evidence.outcome == "passed"
    interp = result.interpretation
    assert interp is not None
    # outcome carried verbatim; inspection-passed → asserted_metadata_present.
    assert interp.outcome == "passed"
    assert interp.verdict == "asserted_metadata_present"
    assert interp.run_id == result.evidence.run_id
    assert interp.recipe_id == recipe_id

    # the s6_interpretations row landed — typed columns + detail JSONB.
    row = (session.query(S6Interpretation)
           .filter(S6Interpretation.run_id == result.evidence.run_id).one())
    assert row.recipe_id == recipe_id
    assert row.claim_test_id == test_id
    assert row.outcome == "passed"
    assert row.verdict == "asserted_metadata_present"
    assert isinstance(row.detail["attribution"], str) and row.detail["attribution"]
    assert isinstance(row.detail["evidence_refs"], list)
    assert row.detail["cause"] is None              # inspection → no deeper cause


def test_interpretation_run_id_is_one_to_one_with_the_run(session):
    # run_id is the PK — one interpretation per run, keyed to the s4 run row.
    coord = SemanticTransactionCoordinator()
    test_id, _ = _seed_approved_inspection_recipe(session, coord, subject="Lead")
    result = run_recipe_execution(
        session, test_id, environment_id=7,
        client=_StubClient(rows=[{"Id": "1"}]), coordinator=coord)

    s4 = (session.query(S4ExecutionRun)
          .filter(S4ExecutionRun.run_id == result.evidence.run_id).one())
    s6 = (session.query(S6Interpretation)
          .filter(S6Interpretation.run_id == result.evidence.run_id).one())
    assert s6.run_id == s4.run_id                   # the logical 1:1
    assert s6.outcome == s4.outcome                 # carried verbatim from S4


# ---------------------------------------------------------------------------
# 2. Best-effort isolation — the key invariant. A S6 failure never rolls
#    back the run truth.
# ---------------------------------------------------------------------------

def test_s1_reader_failure_is_isolated(session, monkeypatch, caplog):
    """The S1 reader raising (an S1-read failure) must not destroy the run."""

    class _BoomReader:
        def __init__(self, *_a, **_k):
            raise RuntimeError("S1 read blew up")

    # the helper lazy-imports the reader at call time → patch the source module.
    monkeypatch.setattr(
        "primeqa.interpretation.s1_reader.S1ValidationRuleReader", _BoomReader)

    coord = SemanticTransactionCoordinator()
    test_id, recipe_id = _seed_approved_inspection_recipe(session, coord, subject="Lead")

    with caplog.at_level(logging.ERROR, logger="primeqa.execution_engine.run"):
        result = run_recipe_execution(
            session, test_id, environment_id=7,
            client=_StubClient(rows=[{"Id": "1"}]), coordinator=coord)

    # the run still ran + the truth survives; interpretation is the observable None.
    assert result.ran is True
    assert result.evidence.outcome == "passed"
    assert result.interpretation is None
    # run truth (s4 row) + posture survived — flushed before the savepoint.
    assert (session.query(S4ExecutionRun)
            .filter(S4ExecutionRun.run_id == result.evidence.run_id).count() == 1)
    assert coord.get_recipe_runtime_state(session, recipe_id).last_run_outcome == "passed"
    # no interpretation persisted; the failure was logged loudly.
    assert session.query(S6Interpretation).count() == 0
    assert "S6 interpretation failed" in caplog.text


def test_persist_db_error_is_isolated_by_savepoint(session, monkeypatch, caplog):
    """The load-bearing case: a *real DB error* inside the S6 step aborts the
    subtransaction; the SAVEPOINT contains it so the outer transaction (with the
    run truth flushed before the savepoint) survives and stays usable."""

    def _bad_persist(sess, _interpretation):
        # a genuine DB error (NOT NULL violation on the PK) — PG aborts the
        # subtransaction; without the savepoint this would poison the whole
        # transaction and lose the s4 run row.
        sess.execute(text("INSERT INTO s6_interpretations (run_id) VALUES (NULL)"))
        sess.flush()

    monkeypatch.setattr(
        "primeqa.interpretation.result_store.persist_interpretation", _bad_persist)

    coord = SemanticTransactionCoordinator()
    test_id, recipe_id = _seed_approved_inspection_recipe(session, coord, subject="Lead")

    with caplog.at_level(logging.ERROR, logger="primeqa.execution_engine.run"):
        result = run_recipe_execution(
            session, test_id, environment_id=7,
            client=_StubClient(rows=[{"Id": "1"}]), coordinator=coord)

    # did not raise; interpretation is None.
    assert result.ran is True
    assert result.interpretation is None
    # the run truth survived the poisoned-subtransaction — proof the savepoint
    # contained the abort and the outer transaction is still alive + usable.
    assert (session.query(S4ExecutionRun)
            .filter(S4ExecutionRun.run_id == result.evidence.run_id).count() == 1)
    assert coord.get_recipe_runtime_state(session, recipe_id).last_run_outcome == "passed"
    assert session.query(S6Interpretation).count() == 0
    assert "S6 interpretation failed" in caplog.text
