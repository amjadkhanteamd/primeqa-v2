"""Unit: the S6 ``Verified`` strategy applier (D-274, Slice 2a) — pure, standalone,
wired into nothing.

Covers the ``single`` arm = N=1 identity of today's latest-run ``passed`` rule,
the S4-outcome→ProbeResult adapter, fail-loud arity/kind, a live-parity de-risk
for Slice 2b, and a guard that the module couples to no I/O.
"""
from __future__ import annotations

import ast
import inspect
import os

import pytest

pytestmark = pytest.mark.unit

from primeqa.interpretation.strategy import (
    ProbeResult,
    apply_strategy,
    probe_result_from_outcome,
)


# --- 1. single truth table ---------------------------------------------------

@pytest.mark.parametrize("outcome,expected", [
    ("pass", True), ("fail", False), ("indeterminate", False),
])
def test_single_truth_table(outcome, expected):
    assert apply_strategy(None, "single", [ProbeResult(outcome=outcome)]) is expected


# --- 2. adapter mapping ------------------------------------------------------

@pytest.mark.parametrize("s4_outcome,probe_outcome", [
    ("passed", "pass"),
    ("failed", "fail"),
    ("errored", "indeterminate"),
    ("skipped", "indeterminate"),
])
def test_adapter_maps_every_s4_outcome(s4_outcome, probe_outcome):
    pr = probe_result_from_outcome(s4_outcome, expect_rejection=False)
    assert pr.outcome == probe_outcome
    assert pr.boundary == "single"


def test_adapter_unmodeled_outcome_raises():
    with pytest.raises(ValueError):
        probe_result_from_outcome("exploded", expect_rejection=False)


def test_adapter_polarity_from_expect_rejection():
    assert probe_result_from_outcome("passed", expect_rejection=True).polarity == "reject"
    assert probe_result_from_outcome("passed", expect_rejection=False).polarity == "accept"


def test_polarity_does_not_change_single_verdict():
    # a reject-polarity pass is STILL Verified — the single arm reads only outcome.
    pr = probe_result_from_outcome("passed", expect_rejection=True)
    assert pr.polarity == "reject"
    assert apply_strategy(None, "single", [pr]) is True


# --- 3. byte-identity (synthetic) — locks equivalence to today's rule --------

@pytest.mark.parametrize("s4_outcome", ["passed", "failed", "errored", "skipped"])
def test_single_is_byte_identical_to_today_passed_rule(s4_outcome):
    # apply_strategy("single", [<one run>]) == (latest_run.outcome == "passed"),
    # the rule compute_substrate_decision uses today — locked BEFORE any wiring.
    pr = probe_result_from_outcome(s4_outcome, expect_rejection=False)
    assert apply_strategy(None, "single", [pr]) == (s4_outcome == "passed")


# --- 4. live-parity (MANDATORY, read-only) — the de-risk for Slice 2b --------

@pytest.mark.integration
def test_single_matches_live_latest_run_rule():
    """Over real claims, assert apply_strategy('single', [<latest run>]) equals
    today's ``latest_run.outcome == 'passed'`` for EVERY claim with a run. Proves
    the applier tracks live data identically before 2b wires it in. Read-only;
    no writes. Skips if the Railway DB isn't reachable."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        try:
            from dotenv import load_dotenv
            load_dotenv(os.path.join(os.path.dirname(__file__),
                                     "..", "..", "..", ".env"))
            url = os.environ.get("DATABASE_URL")
        except Exception:
            url = None
    if not url:
        pytest.skip("no DATABASE_URL — live-parity needs the Railway DB")

    from primeqa import db
    if getattr(db, "engine", None) is None:
        db.init_db(url)
    from sqlalchemy.orm import Session
    from primeqa.semantic.connection import get_tenant_connection
    from primeqa.intelligence.substrate_decision import _assemble_claim_evidence

    with get_tenant_connection(1) as conn:
        s = Session(bind=conn)
        try:
            ev = _assemble_claim_evidence(s, ["SQ-205", "SQ-212"], tenant_id=1)
        finally:
            s.close()

    checked = 0
    for c in ev:
        lr = c.get("latest_run")
        if lr is None:                 # never-run: N=0 is the decision engine's
            continue                   # has_runs path, not the applier's
        outcome = lr["outcome"]
        # polarity is irrelevant to the single verdict → expect_rejection=False.
        pr = probe_result_from_outcome(outcome, expect_rejection=False)
        verified = apply_strategy(None, "single", [pr])
        assert verified == (outcome == "passed"), (c["test_id"], outcome)
        checked += 1
    assert checked > 0, "live-parity exercised no claims with runs"


# --- 5. fail-loud ------------------------------------------------------------

def test_single_zero_probes_raises():
    with pytest.raises(ValueError):
        apply_strategy(None, "single", [])


def test_single_two_probes_raises():
    with pytest.raises(ValueError):
        apply_strategy(None, "single",
                       [ProbeResult(outcome="pass"), ProbeResult(outcome="pass")])


def test_bva_kind_is_not_implemented():
    with pytest.raises(NotImplementedError):
        apply_strategy(None, "bva", [ProbeResult(outcome="pass")])


def test_unknown_kind_raises():
    with pytest.raises(ValueError):
        apply_strategy(None, "weird", [ProbeResult(outcome="pass")])


def test_unrecognized_probe_outcome_raises():
    # a ProbeResult constructed outside the adapter with a bad outcome must not
    # silently default — the single arm raises.
    with pytest.raises(ValueError):
        apply_strategy(None, "single", [ProbeResult(outcome="banana")])  # type: ignore[arg-type]


# --- 6. purity guard — no I/O coupling --------------------------------------

def test_strategy_module_imports_no_io():
    """Static guard: strategy.py must import nothing from execution_engine / db /
    sqlalchemy / semantic / substrate_decision — so the Verified semantics never
    couple to execution or persistence. Parses the source (catches lazy imports
    inside functions too)."""
    from primeqa.interpretation import strategy
    tree = ast.parse(inspect.getsource(strategy))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(n.name for n in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = (
        "primeqa.execution_engine", "primeqa.db", "sqlalchemy",
        "primeqa.semantic", "primeqa.intelligence.substrate_decision",
    )
    bad = [m for m in imported
           if any(m == f or m.startswith(f + ".") for f in forbidden)]
    assert not bad, f"strategy.py must not import I/O modules: {bad}"
