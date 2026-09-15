"""AUD-013 containment — an execution with no recorded plan is refused at the
chokepoints, FIRST, with the reason naming the plan.

D-486 records that every execution starts from a RECORDED plan. The audit of
2026-09-15 enqueued one with none through the jobs API (202, job 131, plan_id
NULL): the invariant held by convention of the caller. These tests pin it to
the places every execution passes — the queued intake, the sync entry, the
worker, and the three fan-outs — and pin the ORDER: the plan is checked before
the recipe gate, so a plan-less request is refused for the reason that is
true of it, never for a reason that would also be true.

No database: the recipe gate and the connection are fakes that record whether
they were reached.
"""
from __future__ import annotations

import contextlib
from uuid import uuid4

import pytest

from primeqa.execution_engine.errors import PlanRequiredError


@pytest.fixture()
def reached(monkeypatch):
    """Fake the recipe gate and the tenant connection; record every touch."""
    touched = []

    def _gate(session, tid):
        touched.append("recipe_gate")

    @contextlib.contextmanager
    def _conn(tid):
        touched.append("connection")
        yield object()

    import primeqa.execution_engine.executability as ex
    import primeqa.semantic.connection as conn_mod
    monkeypatch.setattr(ex, "gate_enqueue", _gate)
    monkeypatch.setattr(conn_mod, "get_tenant_connection", _conn)
    return touched


def test_the_reason_names_the_plan_and_the_exit():
    e = PlanRequiredError(test_id="t", where="intake")
    assert "no plan" in str(e) and "D-486" in str(e) and "plan" in str(e).lower()
    assert "requirement or release page" in str(e) and "schedule" in str(e)


def test_intake_refuses_before_the_recipe_gate(reached):
    from primeqa.execution_engine.intake import enqueue_s4_execution
    with pytest.raises(PlanRequiredError) as ei:
        enqueue_s4_execution(tenant_id=1, test_id=uuid4(), environment_id=7)
    assert ei.value.where == "intake"
    assert reached == [], "the plan must be checked before any gate or connection"


def test_intake_reaches_the_recipe_gate_when_a_plan_is_given(reached, monkeypatch):
    from primeqa.execution_engine import intake

    class _Store:
        def __init__(self, tid): pass
        def create_or_get_job(self, **kw):
            assert kw["plan_id"] == "plan-1"
            return type("J", (), {"id": 9, "status": "queued", "plan_id": "plan-1"})()

    monkeypatch.setattr(intake, "ExecutionJobStore", _Store)
    job = intake.enqueue_s4_execution(tenant_id=1, test_id=uuid4(), environment_id=7, plan_id="plan-1")
    assert job.plan_id == "plan-1" and "recipe_gate" in reached


def test_the_sync_entry_refuses_without_a_plan():
    from primeqa.execution_engine.run import run_claim_execution_for_tenant
    with pytest.raises(PlanRequiredError) as ei:
        run_claim_execution_for_tenant(1, uuid4(), environment_id=7,
                                       single_fn=lambda *a, **k: pytest.fail("ran"),
                                       runall_fn=lambda *a, **k: pytest.fail("ran"))
    assert ei.value.where == "sync run"


def test_the_worker_refuses_a_queued_job_with_no_plan():
    from primeqa.execution_engine.run import async_run_claim_execution_for_tenant
    with pytest.raises(PlanRequiredError) as ei:
        async_run_claim_execution_for_tenant(1, uuid4(), environment_id=7,
                                             single_fn=lambda *a, **k: pytest.fail("ran"),
                                             runall_fn=lambda *a, **k: pytest.fail("ran"))
    assert ei.value.where == "worker"


@pytest.mark.parametrize("fn,args", [
    ("enqueue_claims_for_keys", (1, ["SQ-1"], 7)),
    ("enqueue_all_approved_claims", (1, 7)),
])
def test_the_console_fan_outs_refuse_loudly(fn, args, reached):
    import primeqa.intelligence.s4_execution_console as con
    with pytest.raises(PlanRequiredError) as ei:
        getattr(con, fn)(*args)
    assert ei.value.where == fn
    assert reached == []


def test_the_release_fan_out_refuses_loudly(reached):
    from primeqa.execution_engine.intake import enqueue_claims_for_requirements
    with pytest.raises(PlanRequiredError):
        enqueue_claims_for_requirements(tenant_id=1, external_keys=["SQ-1"], environment_id=7)
    assert reached == []


def test_an_empty_key_set_still_short_circuits_before_the_plan_check(reached):
    """Nothing to run is not a refusal; the empty answer stays the empty answer."""
    from primeqa.execution_engine.intake import enqueue_claims_for_requirements
    import primeqa.intelligence.s4_execution_console as con
    assert enqueue_claims_for_requirements(tenant_id=1, external_keys=[], environment_id=7)["enqueued"] == 0
    assert con.enqueue_claims_for_keys(1, [], 7) == {"enqueued": [], "claim_count": 0}


def test_every_enqueue_call_site_passes_a_plan_or_refuses_first():
    """D-469: the call sites, read out of the source. Every product call of
    enqueue_s4_execution must either pass plan_id= or sit under a route/fan-out
    that refuses before reaching it; the planner is the one that passes it."""
    import ast, pathlib
    root = pathlib.Path(__file__).resolve().parents[2] / "primeqa"
    calls = []
    for p in root.rglob("*.py"):
        tree = ast.parse(p.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
                if name in ("enqueue_s4_execution", "enqueue"):
                    if name == "enqueue" and "planner" not in str(p):
                        continue
                    calls.append((str(p.relative_to(root)), node.lineno,
                                  any(k.arg == "plan_id" for k in node.keywords)))
    without = [(f, ln) for f, ln, ok in calls if not ok]
    # the only calls allowed WITHOUT plan_id are inside functions that refuse
    # first — the fan-outs (which pass plan_id now) and the routes (which return
    # before the call). Assert the residue is exactly that set.
    allowed = {"views.py"}                       # the routes: dead code after the refusal
    assert all(f in allowed for f, _ in without), without
