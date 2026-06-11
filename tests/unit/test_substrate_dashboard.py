"""D-219 slice-1 unit tests — the pure mappers of the substrate dashboard
source (no DB): grid-status vocabulary, recommendation→state map, and the
emulated-shape golden keys the v1 templates depend on."""
from __future__ import annotations

from primeqa.intelligence.substrate_dashboard import (
    _STATE_BY_RECOMMENDATION, _grid_status,
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
