"""Step 2 — readiness from the run stamp, the pure parts
(LLD_STEP_2_CONTEMPORANEITY §b, §c).

* the resolver's state machine over a fake session: NEVER_RUN / unstamped
  → CANNOT_DETERMINE / resolver refusal → CANNOT_DETERMINE / no coverage →
  CANNOT_DETERMINE / changed reads → STALE / else CURRENT;
* the sentences — the TA's Fork-3 wording verbatim on the unstamped case;
* the composer REFUSES a non-current scope and writes NO decision row;
* the compute under D9: ungraded blocks GO/CONDITIONAL GO, a graded
  blocker still makes NO GO, STALE is a warning, ungrounded is ungraded.
"""
from __future__ import annotations

from unittest import mock

import pytest

from primeqa.sync import readiness as R

pytestmark = pytest.mark.unit


class _Session:
    """A fake session: scripted answers per SQL fragment, in call order."""
    def __init__(self, latest=None, current=None, coverage=True, changed=()):
        self.latest, self.current, self.coverage, self.changed = latest, current, coverage, changed

    def execute(self, stmt, params=None):
        sql = str(stmt)
        res = mock.MagicMock()
        if "FROM s4_execution_runs" in sql:
            res.mappings.return_value.first.return_value = self.latest
        elif "FROM logical_versions" in sql:
            res.first.return_value = self.current
        elif "FROM test_claim_coverage WHERE" in sql and "LIMIT 1" in sql:
            res.scalar.return_value = 1 if self.coverage else None
        elif "entity_closed" in sql:
            res.all.return_value = list(self.changed)
        return res


_RUN = {"run_id": "r1", "org_version_seq": 240, "connected_org_id": "org-a"}


def test_never_run():
    r = R.resolve_run_readiness(_Session(latest=None), claim_test_id="c", environment_id=59)
    assert r.state == R.READY_NEVER_RUN and r.sentence == R.SENTENCE_NEVER_RUN
    assert r.source == "run_stamp" and r.axis == "org_sequence"


def test_unstamped_run_is_cannot_determine_with_the_ta_sentence():
    r = R.resolve_run_readiness(_Session(latest={**_RUN, "org_version_seq": None}),
                                claim_test_id="c", environment_id=59)
    assert r.state == R.READY_CANNOT_DETERMINE and r.reason == R.REASON_UNSTAMPED
    assert r.sentence == ("Freshness unknown — this run predates run-level "
                          "environment stamping. Run again to establish current "
                          "readiness.")
    assert r.run_id == "r1"


def test_resolver_refusal_is_cannot_determine_with_its_reason():
    r = R.resolve_run_readiness(_Session(latest=_RUN, current=None),
                                claim_test_id="c", environment_id=59)
    assert r.state == R.READY_CANNOT_DETERMINE and r.reason == R.REASON_ORG_NEVER_SYNCED
    assert r.stamp_seq == 240


def test_no_coverage_is_cannot_determine_never_current_by_absence():
    r = R.resolve_run_readiness(_Session(latest=_RUN, current=(249, None), coverage=False),
                                claim_test_id="c", environment_id=59)
    assert r.state == R.READY_CANNOT_DETERMINE and r.reason == R.REASON_NO_COVERAGE
    assert r.current_seq == 249 and r.stamp_seq == 240


def test_a_changed_covered_read_is_stale_and_named():
    r = R.resolve_run_readiness(
        _Session(latest=_RUN, current=(249, None),
                 changed=[("CustomField", "Amount", "entity_closed"),
                          ("CustomField", "Amount", "entity_closed")]),
        claim_test_id="c", environment_id=59)
    assert r.state == R.READY_STALE
    assert r.changed_reads == (("CustomField", "Amount", "entity_closed"),)   # deduped
    assert "1 thing(s) this test reads changed" in r.sentence
    assert "240 → 249" in r.sentence


def test_nothing_changed_is_current():
    r = R.resolve_run_readiness(_Session(latest=_RUN, current=(249, None)),
                                claim_test_id="c", environment_id=59)
    assert r.state == R.READY_CURRENT and r.sentence == "Current against org seq 249."
    assert r.as_dict()["state"] == "CURRENT" and r.as_dict()["sentence"]


# ---- the composer refuses a non-current scope -----------------------------

def test_composer_refuses_non_current_scope_and_records_nothing(monkeypatch):
    import primeqa.intelligence.substrate_decision as sd
    from primeqa.release.decision_composer import evaluate_and_record

    class _Release:
        id, name, decision_criteria = 7, "r7", {}

    class _Repo:
        def __init__(self): self.decisions = []
        def list_requirements(self, rid, tenant_id=None): return [{"id": 1, "jira_key": "SQ-1"}]
        def create_decision(self, **kw): self.decisions.append(kw); return kw

    monkeypatch.setattr(sd, "release_scope_readiness", lambda t, k: {
        "available": True, "non_current": 1, "environments": [59], "claim_count": 1,
        "items": [{"test_id": "t1", "external_keys": ["SQ-1"], "environment_id": 59,
                   "state": "NEVER_RUN", "reason": None, "sentence": "No run in this environment.",
                   "stamp_seq": None, "current_seq": None}]})
    called = []
    monkeypatch.setattr(sd, "get_release_substrate_decision",
                        lambda *a, **k: called.append(1) or {"available": True, "applicable": True})
    repo = _Repo()
    env = evaluate_and_record(None, _Release(), 1, release_repo=repo)
    assert env["refused"] is True and env["reason"] == "scope_not_current"
    assert env["recommendation"] is None
    assert env["items"][0]["external_keys"] == ["SQ-1"]
    assert repo.decisions == []                    # nothing recorded
    assert called == []                            # the engine was not even asked


def test_composer_proceeds_when_the_scope_is_current(monkeypatch):
    import primeqa.intelligence.substrate_decision as sd
    from primeqa.release.decision_composer import evaluate_and_record

    class _Release:
        id, name, decision_criteria = 7, "r7", {}

    class _Repo:
        def __init__(self): self.decisions = []
        def list_requirements(self, rid, tenant_id=None): return []
        def create_decision(self, **kw): self.decisions.append(kw); return kw

    monkeypatch.setattr(sd, "release_scope_readiness",
                        lambda t, k: {"available": True, "non_current": 0, "items": [],
                                      "environments": [], "claim_count": 0})
    monkeypatch.setattr(sd, "get_release_substrate_decision", lambda *a, **k: {
        "available": True, "applicable": True, "recommendation": "go", "confidence": 0.95,
        "reasoning": [], "criteria_met": {}, "metrics": {}, "risk": {"score": 0, "level": "low"}})
    repo = _Repo()
    env = evaluate_and_record(None, _Release(), 1, release_repo=repo)
    assert env.get("refused") is None and env["recommendation"] == "go"
    assert len(repo.decisions) == 1


# ---- the compute under D9 (the pure ladder) --------------------------------

def _row(state="CURRENT", outcome="passed", never=False, ungrounded=False,
         overall="intact", reason=None):
    return {"test_id": "t-" + state, "approved_seq": 1,
            "grounding": None if ungrounded else {"overall": overall, "stale": False,
                                                  "evaluated_at_version_seq": 1},
            "latest_run": None if never else {"run_id": "r", "outcome": outcome, "verdict": None,
                                              "finished_at": "2026-06-10T10:00:00+00:00",
                                              "version_unknown": False},
            "superseded_newer_run": False, "never_run": never, "flaky": False,
            "recent_outcomes": [] if never else [outcome],
            "sequence": {"state": "CURRENT", "current_seq": 9, "source": "org_current",
                         "axis": "org_sequence", "reason": None},
            "readiness": {"state": state, "source": "run_stamp", "axis": "org_sequence",
                          "reason": reason, "changed_reads": []},
            "ungrounded": ungrounded}


def test_d9_ungraded_blocks_go_and_conditional_go_but_a_graded_blocker_stands():
    from datetime import datetime, timezone
    from primeqa.intelligence.substrate_decision import compute_substrate_decision
    now = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
    checks = lambda o: {r["check"]: r["status"] for r in o["reasoning"]}   # noqa: E731
    # all current, all green → go
    assert compute_substrate_decision([_row(), _row()], now=now)["recommendation"] == "go"
    # one never run beside a green → cannot_determine (unknown blocks)
    o = compute_substrate_decision([_row(), _row("NEVER_RUN", never=True)], now=now)
    assert o["recommendation"] == "cannot_determine" and checks(o)["readiness"] == "fail"
    assert "never run" in [r for r in o["reasoning"] if r["check"] == "readiness"][0]["detail"]
    # one unstamped beside a green → cannot_determine, the sentence's reason named
    o = compute_substrate_decision([_row(), _row("CANNOT_DETERMINE", reason="unstamped")], now=now)
    assert o["recommendation"] == "cannot_determine"
    assert "without a stamp" in [r for r in o["reasoning"] if r["check"] == "readiness"][0]["detail"]
    # ungrounded with a current run → ungraded (R-ungrounded), never NO GO alone
    o = compute_substrate_decision([_row(), _row(ungrounded=True)], now=now)
    assert o["recommendation"] == "cannot_determine"
    assert "no grounding verdict" in [r for r in o["reasoning"] if r["check"] == "readiness"][0]["detail"]
    # a graded blocker beside an ungraded input → NO GO stands (D9)
    o = compute_substrate_decision([_row(outcome="failed"), _row("NEVER_RUN", never=True)], now=now)
    assert o["recommendation"] == "no_go" and o["metrics"]["readiness"]["ungraded"] == 1
    # stale is graded-but-old → a warning → conditional_go at best
    o = compute_substrate_decision([_row("STALE")], now=now)
    assert o["recommendation"] == "conditional_go" and checks(o)["readiness"] == "warn"
    # a row with no readiness at all is treated as CANNOT_DETERMINE (fail closed)
    r = _row(); del r["readiness"]
    assert compute_substrate_decision([r], now=now)["recommendation"] == "cannot_determine"
