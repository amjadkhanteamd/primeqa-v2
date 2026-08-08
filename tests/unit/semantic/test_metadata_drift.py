"""Unit pins for the S1 metadata-drift detector (D-434) — pure core only.

Direction pins, not just behaviour: first capture is NEVER a lifecycle event;
a broken chain or unextractable facet is UNKNOWN, never a silent no-change and
never a guessed change; dead-rule formula edits carry the NEW-ENFORCEMENT /
neutralization call-outs.
"""
from primeqa.semantic.metadata_drift import (
    ACTIVATION, FORMULA, LIFECYCLE, UNKNOWN, diff_vr_chains)


def _row(name, vf, vt, formula="Amount > 10", active=True):
    attrs = {"Metadata": {"errorConditionFormula": formula,
                          "active": active}}
    return {"sf_api_name": name, "valid_from_seq": vf, "valid_to_seq": vt,
            "attributes": attrs}


def _kinds(events):
    return [e.kind for e in events]


# ---------------------------------------------------------------------------
# The three classes
# ---------------------------------------------------------------------------

def test_activation_flip_true_to_false():
    events = diff_vr_chains([
        _row("Opportunity.R", 1, 66, active=True),
        _row("Opportunity.R", 66, None, active=False),
    ])
    assert _kinds(events) == [ACTIVATION]
    e = events[0]
    assert e.seq == 66
    assert (e.before, e.after) == ("active", "inactive")


def test_activation_flip_false_to_true_also_reported():
    events = diff_vr_chains([
        _row("Opportunity.R", 1, 40, active=False),
        _row("Opportunity.R", 40, None, active=True),
    ])
    assert _kinds(events) == [ACTIVATION]
    assert (events[0].before, events[0].after) == ("inactive", "active")


def test_formula_change_carries_before_and_after():
    events = diff_vr_chains([
        _row("Opportunity.Amount", 1, 58, formula="Amount  > 10000"),
        _row("Opportunity.Amount", 58, None, formula="Amount  > 999999"),
    ])
    assert _kinds(events) == [FORMULA]
    assert events[0].before == "Amount  > 10000"
    assert events[0].after == "Amount  > 999999"
    assert events[0].note == ""


def test_dead_to_live_formula_is_new_enforcement_appearing():
    events = diff_vr_chains([
        _row("X.Dead", 1, 70, formula="false"),
        _row("X.Dead", 70, None, formula="ISBLANK(F__c)"),
    ])
    assert _kinds(events) == [FORMULA]
    assert "NEW ENFORCEMENT APPEARING" in events[0].note


def test_live_to_dead_formula_flagged_as_neutralization():
    events = diff_vr_chains([
        _row("X.R", 1, 70, formula="ISBLANK(F__c)"),
        _row("X.R", 70, None, formula="false"),
    ])
    assert _kinds(events) == [FORMULA]
    assert "neutralized" in events[0].note


def test_activation_and_formula_same_seq_are_two_events_never_collapsed():
    events = diff_vr_chains([
        _row("X.R", 1, 70, formula="A > 1", active=True),
        _row("X.R", 70, None, formula="A > 2", active=False),
    ])
    assert sorted(_kinds(events)) == [ACTIVATION, FORMULA]


# ---------------------------------------------------------------------------
# Lifecycle vs first capture (absence discipline)
# ---------------------------------------------------------------------------

def test_first_capture_at_baseline_is_not_an_event():
    events = diff_vr_chains([_row("X.R", 1, None)])
    assert events == []


def test_appeared_after_baseline_is_lifecycle():
    events = diff_vr_chains([
        _row("X.Old", 1, None),                 # baseline holder
        _row("X.New", 90, None),                # appeared later
    ])
    assert _kinds(events) == [LIFECYCLE]
    e = events[0]
    assert e.rule == "X.New" and e.seq == 90 and e.before is None
    assert "appeared in capture" in e.note


def test_disappeared_is_lifecycle_at_the_close_seq():
    events = diff_vr_chains([
        _row("X.Old", 1, None),                 # baseline holder
        _row("X.Gone", 1, 95),                  # closed, no successor
    ])
    assert _kinds(events) == [LIFECYCLE]
    e = events[0]
    assert e.rule == "X.Gone" and e.seq == 95 and e.after is None
    assert "disappeared from capture" in e.note


def test_lifecycle_wording_is_capture_not_org():
    events = diff_vr_chains([
        _row("X.Old", 1, None),
        _row("X.New", 90, None),
    ])
    assert "cannot distinguish" in events[0].note


# ---------------------------------------------------------------------------
# Fail-loud discipline
# ---------------------------------------------------------------------------

def test_broken_chain_is_unknown_not_a_guessed_change():
    events = diff_vr_chains([
        _row("X.R", 1, 50, active=True),
        _row("X.R", 60, None, active=False),    # gap: 50 != 60
    ])
    assert _kinds(events) == [UNKNOWN]
    assert "chain broken" in events[0].note


def test_unextractable_active_on_one_side_is_unknown_never_activation():
    a = _row("X.R", 1, 70, active=True)
    b = _row("X.R", 70, None)
    b["attributes"] = {"Metadata": {"errorConditionFormula": "Amount > 10"}}
    events = diff_vr_chains([a, b])
    assert ACTIVATION not in _kinds(events)
    assert UNKNOWN in _kinds(events)


def test_unnamed_row_is_unknown():
    r = _row("X.R", 1, None)
    r["sf_api_name"] = None
    events = diff_vr_chains([r])
    assert _kinds(events) == [UNKNOWN]
    assert "no sf_api_name" in events[0].note


# ---------------------------------------------------------------------------
# Noise resistance + shape
# ---------------------------------------------------------------------------

def test_no_change_chain_yields_zero_events():
    events = diff_vr_chains([
        _row("X.R", 1, 40),
        _row("X.R", 40, 80),
        _row("X.R", 80, None),
    ])
    assert events == []


def test_attribute_noise_without_facet_change_is_silent():
    a = _row("X.R", 1, 70)
    b = _row("X.R", 70, None)
    b["attributes"] = dict(b["attributes"])
    b["attributes"]["ExtraKey"] = "serialisation noise"
    assert diff_vr_chains([a, b]) == []


def test_rules_are_independent_and_events_seq_ordered():
    events = diff_vr_chains([
        _row("X.A", 1, 66, active=True),
        _row("X.A", 66, None, active=False),
        _row("X.B", 1, 58, formula="F > 1"),
        _row("X.B", 58, None, formula="F > 2"),
    ])
    assert [(e.seq, e.kind) for e in events] == [
        (58, FORMULA), (66, ACTIVATION)]


def test_version_times_stamp_events():
    events = diff_vr_chains(
        [_row("X.R", 1, 66, active=True),
         _row("X.R", 66, None, active=False)],
        version_times={66: "2026-06-15T08:28:57+00:00"},
    )
    assert events[0].at == "2026-06-15T08:28:57+00:00"
