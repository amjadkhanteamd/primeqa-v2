"""Unit tests for the D-331 per-AC coverage assembly (pure — no PG, no LLM).

``_assemble_ac_coverage`` joins the propose call's declared ACs + intents with
the outcome's per-intent partial refusals into the requirement page's
"why isn't X tested" rows. Intent-level truth only — no claim mapping.
"""
from __future__ import annotations

from primeqa.intelligence.s3_generation_console import _assemble_ac_coverage


def _rp(acs, intents):
    return {"acceptance_criteria": acs, "intent_descriptors": intents}


def test_proposed_partial_refused_untestable_unaddressed():
    rp = _rp(
        [{"index": 1, "label": "mandatory fields"},
         {"index": 2, "label": "loan exceeds property"},
         {"index": 3, "label": "kyc gate"},
         {"index": 4, "label": "approval log"},
         {"index": 5, "label": "never mentioned"}],
        [
            {"ac_ref": 1}, {"ac_ref": 1},                 # both ground
            {"ac_ref": 2},                                # refused below
            {"ac_ref": 3}, {"ac_ref": 3},                 # one of two refused
            {"ac_ref": 4, "no_admissible_test": True,     # declared untestable
             "no_admissible_test_reason": "audit log is not org metadata"},
        ])
    prs = [
        {"ac_ref": 2, "refusal_kind": "emission-deferred",
         "payload": {"detail": "no derivable reject recipe"}},
        {"ac_ref": 3, "refusal_kind": "emission-deferred",
         "payload": {"detail": "field not found"}},
    ]
    rows = {r["index"]: r for r in _assemble_ac_coverage(rp, prs)}
    assert rows[1]["status"] == "proposed" and rows[1]["intents"] == 2
    assert rows[2]["status"] == "refused" and rows[2]["reason"]
    assert rows[3]["status"] == "partial" and rows[3]["intents"] == 2
    assert rows[4]["status"] == "untestable"
    assert rows[4]["reason"] == "audit log is not org metadata"
    assert rows[5]["status"] == "unaddressed" and rows[5]["intents"] == 0


def test_empty_and_malformed_inputs_are_tolerated():
    assert _assemble_ac_coverage(None, None) == []
    assert _assemble_ac_coverage({}, []) == []
    # a malformed AC entry (no index) is skipped, not fatal
    rows = _assemble_ac_coverage(
        _rp([{"label": "no index"}, {"index": 1, "label": "ok"}],
            [{"ac_ref": 1}]), None)
    assert len(rows) == 1 and rows[0]["status"] == "proposed"
