"""Unit: D-231 — the Results "failures front door" filter logic. The pure WHERE
builder (`s4_execution_console._runs_where`) + the `since` parser
(`views._parse_runs_since`). No DB, no app request — just the filter semantics that
turn `/runs/substrate?outcome=failed&since=today` into a correct query."""
from primeqa.intelligence.s4_execution_console import _RUN_OUTCOMES, _runs_where


def test_runs_where_empty_is_no_clause():
    where, params = _runs_where(None, None, None, None)
    assert where == ""
    assert params == {}


def test_runs_where_outcome_only():
    where, params = _runs_where("failed", None, None, None)
    assert where == " WHERE r.outcome::text = :outcome"
    assert params == {"outcome": "failed"}


def test_runs_where_all_facets_are_AND_joined():
    where, params = _runs_where("failed", "prohibition_not_enforced", 59, "2026-06-13")
    assert where.startswith(" WHERE ")
    assert where.count(" AND ") == 3                # 4 clauses, 3 ANDs
    assert "r.outcome::text = :outcome" in where
    assert "i.verdict::text = :verdict" in where
    assert "r.environment_id = :env" in where
    assert "r.finished_at >= :since" in where
    assert params == {"outcome": "failed", "verdict": "prohibition_not_enforced",
                      "env": 59, "since": "2026-06-13"}


def test_runs_where_skips_unset_facets():
    # env=0 is a real environment id (falsy-but-not-None) — must still bind.
    where, params = _runs_where(None, None, 0, None)
    assert where == " WHERE r.environment_id = :env"
    assert params == {"env": 0}


def test_run_outcomes_are_the_enum():
    assert _RUN_OUTCOMES == {"passed", "failed", "errored", "skipped"}


def test_parse_runs_since_today_iso_and_bad_input():
    from primeqa.views import _parse_runs_since
    assert _parse_runs_since("today") is not None                  # start of today UTC
    assert _parse_runs_since("2026-06-13").isoformat().startswith("2026-06-13")
    assert _parse_runs_since("garbage") is None                    # ignored, not an error
    assert _parse_runs_since(None) is None
    assert _parse_runs_since("") is None
