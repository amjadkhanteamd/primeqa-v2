"""D-219 slice-1 unit tests — the pure mappers of the substrate dashboard
source (no DB): grid-status vocabulary, recommendation→state map, and the
emulated-shape golden keys the v1 templates depend on."""
from __future__ import annotations

from primeqa.intelligence.substrate_dashboard import (
    _STATE_BY_RECOMMENDATION, _grid_status, _group_blockers,
)


def _row(outcome=None):
    return {"latest_run": ({"outcome": outcome} if outcome else None)}


def test_state_map_covers_all_recommendations():
    assert _STATE_BY_RECOMMENDATION == {
        "go": "GO", "conditional_go": "CONDITIONAL GO", "no_go": "NO-GO"}


def test_grid_status_failed_dominates():
    assert _grid_status([_row("passed"), _row("failed")]) == "failed"
    assert _grid_status([_row("errored")]) == "failed"


def test_grid_status_full_green_is_passed():
    assert _grid_status([_row("passed"), _row("passed")]) == "passed"


def test_grid_status_partial_coverage_is_blocked():
    assert _grid_status([_row("passed"), _row(None)]) == "blocked"


def test_grid_status_no_evidence_is_untested():
    assert _grid_status([_row(None), _row(None)]) == "untested"
    assert _grid_status([]) == "untested"


def _blk(test_id, keys, cause):
    return {"test_id": test_id, "external_keys": keys, "verdict": None,
            "outcome": "failed", "cause": cause}


def test_group_blockers_collapses_same_requirement_and_cause():
    """Six claims under one requirement with one cause → ONE line, count=6."""
    blocking = [_blk(f"t{i}", ["REQ-A", "req-302"], "grounding broken")
                for i in range(6)]
    out = _group_blockers(blocking)
    assert len(out) == 1
    assert out[0]["count"] == 6
    assert out[0]["external_keys"] == ["REQ-A", "req-302"]


def test_group_blockers_keeps_distinct_causes_and_keys_apart():
    blocking = [_blk("t1", ["REQ-A"], "grounding broken"),
                _blk("t2", ["REQ-A"], "a validation rule rejected the update"),
                _blk("t3", ["REQ-B"], "grounding broken")]
    out = _group_blockers(blocking)
    assert len(out) == 3
    assert all(b["count"] == 1 for b in out)


def test_group_blockers_preserves_decision_order():
    blocking = [_blk("t1", ["REQ-A"], "x"), _blk("t2", ["REQ-B"], "y"),
                _blk("t3", ["REQ-A"], "x")]
    out = _group_blockers(blocking)
    assert [b["external_keys"][0] for b in out] == ["REQ-A", "REQ-B"]


def test_dashboard_evidence_is_env_and_org_scoped():
    """The page is env-scoped, so the evidence assembly must be too — an
    org-blind read blends the worst grounding verdict across every connected
    org into this env's verdict (the 53-BROKEN regression)."""
    import inspect

    from primeqa.intelligence import substrate_dashboard as m
    src = inspect.getsource(m.get_substrate_dashboard_data)
    assert "environment_id=environment_id" in src
    assert "connected_org_id=org_id" in src
    assert "get_connected_org_for_environment" in src


def test_emulated_shape_golden_keys():
    """The keys dashboard_release.html / dashboard_shared.html bind — a
    drift guard on the drop-in contract (D-189 pattern)."""
    import inspect

    from primeqa.intelligence import substrate_dashboard as m
    src = inspect.getsource(m.get_substrate_dashboard_data)
    for key in ('"environment"', '"latest_run"', '"state"', '"state_reason"',
                '"risk"', '"gates"', '"substrate_checks"', '"ticket_grid"',
                '"ticket_counts"', '"trends"', '"empty"'):
        assert key in src, f"emulated shape lost the {key} key"
