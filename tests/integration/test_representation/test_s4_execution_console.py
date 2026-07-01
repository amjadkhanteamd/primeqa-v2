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
    _list_runs,
    _read_run_detail,
    approve_claim,
    list_runs,
    read_claim_runs,
    read_run_detail,
    trigger_claim_run,
)
from primeqa.execution_engine.run import _MIN_AVAILABLE_ENV
from primeqa.generation.emission import _inspection_recipe
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
    # D-293 removed the prohibition -> inspection emission this used to source
    # from; build the inspection recipe directly (console listing is unchanged).
    _trigger, _recipe, _env = _inspection_recipe(
        read_entity_type="Object", read_external_id="Lead", capture_field="APPLIES_TO",
        env_detail="read Lead metadata to verify a validation rule applies")
    bundle = SimpleNamespace(
        causal_initiation=_trigger, observation_realization=_recipe,
        execution_environment=_env)
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


# --- the global runs index ---------------------------------------------------

def test_list_runs_orders_and_paginates(session):
    from sqlalchemy import text
    for fin, outcome in [("2026-06-01T10:00:00+00:00", "failed"),    # older
                         ("2026-06-02T10:00:00+00:00", "passed")]:   # newer
        session.execute(text(
            "INSERT INTO s4_execution_runs (run_id, recipe_id, recipe_version_seq, "
            "claim_test_id, environment_id, outcome, started_at, finished_at, evidence) "
            "VALUES (CAST(:r AS uuid), CAST(:rec AS uuid), 1, CAST(:t AS uuid), 7, "
            "CAST(:o AS run_outcome), CAST(:f AS timestamptz), CAST(:f AS timestamptz), "
            "CAST('{}' AS jsonb))"),
            {"r": str(uuid4()), "rec": str(uuid4()), "t": str(uuid4()), "o": outcome, "f": fin})
    session.flush()

    total, runs = _list_runs(session, limit=10, offset=0)
    assert total == 2
    assert [r["outcome"] for r in runs] == ["passed", "failed"]   # newest-first

    total1, runs1 = _list_runs(session, limit=1, offset=0)        # page size 1
    assert total1 == 2 and len(runs1) == 1 and runs1[0]["outcome"] == "passed"


def test_list_runs_best_effort_bad_tenant():
    assert list_runs(-1)["available"] is False


# --- D-226: lifecycle guards (the 0.4-audit F1 fix) ---------------------------

def test_approve_claim_refuses_deprecated(session):
    # Deprecation is a reasoned human decision — the generic approve path must
    # never silently undo it (and auto-enqueue runs of a superseded recipe).
    coord = SemanticTransactionCoordinator()
    test_id = _seed_draft_claim_with_recipe(session, coord)
    claim = coord.get_latest_claim(session, test_id)
    coord.deprecate_claim(session, actor="human", test_id=test_id,
                          version_seq=claim.version_seq,
                          reason="superseded by a corrected claim")
    out = _approve_claim(session, test_id)
    assert out["ok"] is False
    assert "deprecated" in out["error"]
    # still deprecated — nothing was promoted
    assert coord.get_latest_claim(session, test_id).status == "deprecated"
    assert coord.get_current_approved_claim(session, test_id) is None


def test_approve_claim_never_resurrects_deprecated_recipe(session):
    # A deprecated recipe under a draft claim stays deprecated through approve.
    coord = SemanticTransactionCoordinator()
    test_id = _seed_draft_claim_with_recipe(session, coord)
    recipe = coord.list_active_recipes(session, test_id)[0]
    coord.deprecate_recipe(session, actor="human", recipe_id=recipe.recipe_id,
                           version_seq=recipe.version_seq,
                           reason="recipe shape superseded")
    out = _approve_claim(session, test_id)
    assert out["ok"] is True
    assert out["recipes_approved"] == 0          # nothing promoted
    statuses = {r.recipe_id: r.status
                for r in coord.list_active_recipes(session, test_id)}
    assert statuses[recipe.recipe_id] == "deprecated"


# --- D-228 (F3): the deprecate bridge — explicit supersession governance ------

def test_deprecate_claim_records_reason_and_blocks_selection(session):
    from sqlalchemy import text
    from primeqa.intelligence.s4_execution_console import _deprecate_claim
    coord = SemanticTransactionCoordinator()
    test_id = _seed_draft_claim_with_recipe(session, coord)
    _approve_claim(session, test_id)
    assert coord.select_recipe_for_execution(
        session, test_id, available_environment=_MIN_AVAILABLE_ENV) is not None

    out = _deprecate_claim(session, test_id,
                           "superseded by the corrected staged-trigger claim")
    assert out == {"ok": True, "status": "deprecated", "already": False}

    # the claim no longer grades or runs (selection requires an approved claim)
    assert coord.get_latest_claim(session, test_id).status == "deprecated"
    assert coord.select_recipe_for_execution(
        session, test_id, available_environment=_MIN_AVAILABLE_ENV) is None
    # the recipes keep their own status — claim-only deprecation
    assert {r.status for r in coord.list_active_recipes(session, test_id)} \
        == {"approved"}
    # the reason landed in provenance (D-ε-5), not on the claim row
    reason = session.execute(text(
        "SELECT event_data->>'reason' FROM test_provenance "
        "WHERE claim_test_id = CAST(:t AS uuid) "
        "AND event_kind = 'claim_deprecated'"), {"t": str(test_id)}).scalar()
    assert reason == "superseded by the corrected staged-trigger claim"


def test_deprecate_claim_requires_reason(session):
    from primeqa.intelligence.s4_execution_console import _deprecate_claim
    coord = SemanticTransactionCoordinator()
    test_id = _seed_draft_claim_with_recipe(session, coord)
    for bad in ("", "   ", None):
        out = _deprecate_claim(session, test_id, bad)
        assert out["ok"] is False and "reason" in out["error"].lower()
    assert coord.get_latest_claim(session, test_id).status == "draft"


def test_deprecate_claim_idempotent(session):
    from primeqa.intelligence.s4_execution_console import _deprecate_claim
    coord = SemanticTransactionCoordinator()
    test_id = _seed_draft_claim_with_recipe(session, coord)
    assert _deprecate_claim(session, test_id, "first reason")["already"] is False
    out = _deprecate_claim(session, test_id, "second reason")
    assert out == {"ok": True, "status": "deprecated", "already": True}


def test_deprecate_claim_best_effort_bad_tenant():
    from primeqa.intelligence.s4_execution_console import deprecate_claim
    assert deprecate_claim(-1, str(uuid4()), "a reason")["ok"] is False
