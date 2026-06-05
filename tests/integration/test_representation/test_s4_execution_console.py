"""S4 execution console bridge — governance (D-168, UI Area 3 slice 3a).

Covers the bridge's own logic without a live Salesforce client: the pure
run-result mapping (`_map_run_result`, duck-typed), the S6 run read
(`_read_claim_runs` over the substrate `session`), and the best-effort wrappers
(a bad tenant returns ok/available=False, never raises). The full live run (a real
metadata-recipe → outcome) is proven by `test_s4_run_path.py` + live proving.
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from primeqa.intelligence.s4_execution_console import (
    _approve_claim,
    _map_run_result,
    _read_claim_runs,
    _read_run_detail,
    approve_claim,
    read_claim_runs,
    read_run_detail,
    trigger_claim_run,
)
from primeqa.execution_engine.run import _MIN_AVAILABLE_ENV
from primeqa.generation.emission import GroundedNegative, _Endpoint, author_emission
from primeqa.test_representation import SemanticTransactionCoordinator

from ._fixtures import empty_conditions, make_value_claim


def _seed_draft_claim_with_recipe(session, coord):
    """A draft claim + a generated_unapproved metadata recipe under it — the
    shape S3 generation produces (neither runnable until approved)."""
    body = make_value_claim(value="Tech")
    cr = coord.write_claim(
        session, actor="s3", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body, semantic_conditions=empty_conditions())
    bundle = author_emission(GroundedNegative(
        archetype="data_behavior", claim_kind="prohibition-claim",
        operation_hint="delete", version_seq=7,
        subject=_Endpoint(entity_id=uuid4(), entity_type="Object", external_id="Lead"),
        requirement_excerpt="Users must not delete a Lead without a reason."))
    coord.write_recipe(
        session, actor="s3", recipe_id=None, claim_test_id=cr.test_id,
        trigger_kind="inspection-trigger", recipe_kind="metadata-recipe",
        causal_initiation=bundle.causal_initiation,
        observation_realization=bundle.observation_realization,
        execution_environment=bundle.execution_environment,
        claim_version_seq=cr.version_seq)
    session.flush()
    return cr.test_id


def test_map_run_result_ran_with_verdict():
    rid = uuid4()
    result = SimpleNamespace(
        ran=True, reason=None, selected_recipe_id=rid,
        evidence=SimpleNamespace(outcome="passed"),
        interpretation=SimpleNamespace(verdict="prohibition_enforced"))
    assert _map_run_result(result) == {
        "ok": True, "ran": True, "recipe_id": str(rid),
        "outcome": "passed", "verdict": "prohibition_enforced"}


def test_map_run_result_ran_without_interpretation():
    # the best-effort S6 interpret can fail -> interpretation None -> verdict None,
    # while the S4 outcome stays authoritative.
    result = SimpleNamespace(
        ran=True, reason=None, selected_recipe_id=uuid4(),
        evidence=SimpleNamespace(outcome="failed"), interpretation=None)
    out = _map_run_result(result)
    assert out["ran"] is True and out["outcome"] == "failed" and out["verdict"] is None


def test_map_run_result_no_eligible_recipe():
    result = SimpleNamespace(ran=False, reason="no_eligible_recipe")
    assert _map_run_result(result) == {
        "ok": True, "ran": False, "reason": "no_eligible_recipe"}


def test_read_claim_runs_empty(session):
    # a claim with no runs -> [].
    assert _read_claim_runs(session, uuid4()) == []


def test_read_claim_runs_orders_by_recency(session):
    # two S4 runs for one claim, different finish times -> newest-first (the D-168
    # 3a review fix: order by s4_execution_runs.finished_at, not the S6 run_id).
    from sqlalchemy import text
    tid = uuid4()
    for fin, outcome in [("2026-06-01T10:00:00+00:00", "failed"),    # older
                         ("2026-06-02T10:00:00+00:00", "passed")]:   # newer
        session.execute(text(
            "INSERT INTO s4_execution_runs (run_id, recipe_id, recipe_version_seq, "
            "claim_test_id, environment_id, outcome, started_at, finished_at, evidence) "
            "VALUES (CAST(:r AS uuid), CAST(:rec AS uuid), 1, CAST(:t AS uuid), 7, "
            "CAST(:o AS run_outcome), CAST(:f AS timestamptz), CAST(:f AS timestamptz), "
            "CAST('{}' AS jsonb))"),
            {"r": str(uuid4()), "rec": str(uuid4()), "t": str(tid), "o": outcome, "f": fin})
    session.flush()
    got = _read_claim_runs(session, tid)
    assert [r["outcome"] for r in got] == ["passed", "failed"]   # newest-first
    assert got[0]["finished_at"] is not None
    assert got[0]["verdict"] is None                              # no S6 row -> NULL via LEFT JOIN


def test_trigger_claim_run_best_effort_bad_tenant():
    # tenant -1 has no schema -> run_recipe_execution_for_tenant raises -> ok=False.
    assert trigger_claim_run(-1, str(uuid4()), 1)["ok"] is False


def test_read_claim_runs_best_effort_bad_tenant():
    assert read_claim_runs(-1, str(uuid4()))["available"] is False


# --- approve a claim (draft -> runnable) -------------------------------------

def test_approve_claim_makes_draft_runnable(session):
    coord = SemanticTransactionCoordinator()
    test_id = _seed_draft_claim_with_recipe(session, coord)
    # not runnable: draft claim (no approved version) + generated_unapproved recipe
    assert coord.get_current_approved_claim(session, test_id) is None
    assert coord.select_recipe_for_execution(
        session, test_id, available_environment=_MIN_AVAILABLE_ENV) is None

    out = _approve_claim(session, test_id)
    assert out["ok"] is True and out["recipes_approved"] >= 1

    # now approved AND runnable (claim approved + recipe promoted)
    assert coord.get_current_approved_claim(session, test_id) is not None
    assert coord.select_recipe_for_execution(
        session, test_id, available_environment=_MIN_AVAILABLE_ENV) is not None


def test_approve_claim_best_effort_bad_tenant():
    assert approve_claim(-1, str(uuid4()))["ok"] is False


# --- run detail (evidence steps + S6 verdict) --------------------------------

def test_read_run_detail(session):
    import json
    from sqlalchemy import text
    run_id = uuid4()
    evidence = {"api_choice": "tooling", "error": None, "steps": [
        {"kind": "read", "ordinal": 0, "query": "SELECT Id FROM Lead", "row_count": 1},
        {"kind": "assert", "ordinal": 1, "predicate": "exists", "held": True},
    ]}
    session.execute(text(
        "INSERT INTO s4_execution_runs (run_id, recipe_id, recipe_version_seq, "
        "claim_test_id, environment_id, outcome, started_at, finished_at, evidence) "
        "VALUES (CAST(:r AS uuid), CAST(:rec AS uuid), 1, CAST(:t AS uuid), 7, "
        "CAST(:o AS run_outcome), NOW(), NOW(), CAST(:ev AS jsonb))"),
        {"r": str(run_id), "rec": str(uuid4()), "t": str(uuid4()), "o": "passed",
         "ev": json.dumps(evidence)})
    session.flush()

    d = _read_run_detail(session, run_id)
    assert d is not None
    assert d["run_id"] == str(run_id) and d["outcome"] == "passed"
    assert d["api_choice"] == "tooling"
    assert len(d["steps"]) == 2 and d["steps"][1]["held"] is True
    assert d["interpretation"] is None              # no S6 row for this run


def test_read_run_detail_not_found(session):
    assert _read_run_detail(session, uuid4()) is None


def test_read_run_detail_best_effort_bad_tenant():
    assert read_run_detail(-1, str(uuid4()))["available"] is False
