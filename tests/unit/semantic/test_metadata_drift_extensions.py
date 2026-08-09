"""Unit pins for the D-437 drift-detector extensions — pure cores only.

Direction pins: capture-mark transitions summarize to ONE event and suppress
per-value MEMBERSHIP; truncated/NULL capture yields UNKNOWN never silence;
values arriving with their set fold; VERSION_MOVED asserts nothing about
content; first capture is never an event.
"""
from primeqa.semantic.metadata_drift import (
    ACTIVATION, CAPTURE_GENERATION, LIFECYCLE, MEMBERSHIP, UNKNOWN,
    VERSION_MOVED, diff_flow_chains, diff_picklist_chains)


# ---------------------------------------------------------------------------
# Flow activation
# ---------------------------------------------------------------------------

def _frow(name, vf, vt, active=True, version=1):
    return {"sf_api_name": name, "valid_from_seq": vf, "valid_to_seq": vt,
            "is_active": active, "version_number": version}


def test_flow_activation_flip():
    ev = diff_flow_chains([
        _frow("SQ205_Create_Case_SLA", 1, 50, active=True),
        _frow("SQ205_Create_Case_SLA", 50, None, active=False),
    ])
    assert [e.kind for e in ev] == [ACTIVATION]
    assert (ev[0].before, ev[0].after) == ("active", "inactive")


def test_flow_version_moved_carries_no_content_claim():
    ev = diff_flow_chains([
        _frow("FL07", 1, 180, version=1),
        _frow("FL07", 180, None, version=3),
    ])
    assert [e.kind for e in ev] == [VERSION_MOVED]
    assert (ev[0].before, ev[0].after) == ("1", "3")
    assert "content unknown" in ev[0].note
    assert "open-snapshot" in ev[0].note


def test_flow_missing_details_row_is_unknown_never_silent():
    a = _frow("F", 1, 60)
    b = {"sf_api_name": "F", "valid_from_seq": 60, "valid_to_seq": None,
         "is_active": None, "version_number": None}
    kinds = [e.kind for e in diff_flow_chains([a, b])]
    assert kinds and set(kinds) == {UNKNOWN}


def test_flow_first_capture_silent_appeared_later_lifecycle():
    ev = diff_flow_chains([
        _frow("Old", 1, None),
        _frow("New", 127, None),
    ])
    assert [(e.kind, e.rule) for e in ev] == [(LIFECYCLE, "New")]
    assert "appeared in capture" in ev[0].note


def test_flow_activation_and_version_move_same_seq_never_collapsed():
    ev = diff_flow_chains([
        _frow("F", 1, 60, active=True, version=1),
        _frow("F", 60, None, active=False, version=2),
    ])
    assert sorted(e.kind for e in ev) == [ACTIVATION, VERSION_MOVED]


# ---------------------------------------------------------------------------
# Picklists
# ---------------------------------------------------------------------------

def _vrow(name, vf, vt, set_row="S1", active=True):
    return {"sf_api_name": name, "valid_from_seq": vf, "valid_to_seq": vt,
            "is_active": active, "set_row_id": set_row}


def _srow(rid, name, vf, vt):
    return {"id": rid, "sf_api_name": name, "valid_from_seq": vf,
            "valid_to_seq": vt}


def _field(name, vf, vt, mark="inline", set_row="S1"):
    return {"sf_api_name": name, "valid_from_seq": vf, "valid_to_seq": vt,
            "picklist_capture": mark, "set_row_id": set_row}


def test_membership_added_inside_stable_set():
    ev = diff_picklist_chains(
        value_rows=[_vrow("INLINE:O.F.A", 1, None),
                    _vrow("INLINE:O.F.B", 90, None)],
        set_rows=[_srow("S1", "INLINE:O.F", 1, None)],
        field_rows=[_field("O.F", 1, None)],
    )
    assert [(e.kind, e.rule) for e in ev] == [(MEMBERSHIP, "INLINE:O.F.B")]
    assert "added" in ev[0].note


def test_membership_removed_inside_stable_set():
    ev = diff_picklist_chains(
        value_rows=[_vrow("INLINE:O.F.A", 1, None),
                    _vrow("INLINE:O.F.B", 1, 95)],
        set_rows=[_srow("S1", "INLINE:O.F", 1, None)],
        field_rows=[_field("O.F", 1, None)],
    )
    assert [(e.kind, e.rule) for e in ev] == [(MEMBERSHIP, "INLINE:O.F.B")]
    assert "removed" in ev[0].note


def test_value_activation_flip():
    ev = diff_picklist_chains(
        value_rows=[_vrow("INLINE:O.F.A", 1, 70, active=True),
                    _vrow("INLINE:O.F.A", 70, None, active=False)],
        set_rows=[_srow("S1", "INLINE:O.F", 1, None)],
        field_rows=[_field("O.F", 1, None)],
    )
    assert [e.kind for e in ev] == [ACTIVATION]
    assert (ev[0].before, ev[0].after) == ("active", "inactive")


def test_values_arriving_with_their_set_fold_into_set_lifecycle():
    ev = diff_picklist_chains(
        value_rows=[_vrow("INLINE:O.G.A", 90, None, set_row="S2"),
                    _vrow("INLINE:O.G.B", 90, None, set_row="S2"),
                    _vrow("INLINE:O.F.A", 1, None)],   # baseline holder
        set_rows=[_srow("S1", "INLINE:O.F", 1, None),
                  _srow("S2", "INLINE:O.G", 90, None)],
        field_rows=[_field("O.F", 1, None),
                    _field("O.G", 1, None, set_row="S2")],
    )
    assert [(e.kind, e.rule) for e in ev] == [(LIFECYCLE, "INLINE:O.G")]
    assert "2 values arrived with the set (folded)" in ev[0].note


def test_capture_mark_transition_suppresses_membership_into_one_event():
    # marks transition at seq 162 while a set + its values appear there.
    ev = diff_picklist_chains(
        value_rows=[_vrow("INLINE:O.G.A", 162, None, set_row="S2"),
                    _vrow("INLINE:O.G.B", 162, None, set_row="S2"),
                    _vrow("INLINE:O.F.A", 1, None)],
        set_rows=[_srow("S1", "INLINE:O.F", 1, None),
                  _srow("S2", "INLINE:O.G", 162, None)],
        field_rows=[_field("O.G", 1, 162, mark=None, set_row="S2"),
                    _field("O.G", 162, None, mark="inline", set_row="S2"),
                    _field("O.F", 1, None)],
    )
    assert [e.kind for e in ev] == [CAPTURE_GENERATION]
    assert "1 sets + 2 values appeared" in ev[0].note
    assert "not the org" in ev[0].note


def test_truncated_capture_membership_is_unknown_never_silent():
    ev = diff_picklist_chains(
        value_rows=[_vrow("INLINE:O.F.A", 1, None),
                    _vrow("INLINE:O.F.B", 90, None)],
        set_rows=[_srow("S1", "INLINE:O.F", 1, None)],
        field_rows=[_field("O.F", 1, None, mark="inline_truncated")],
    )
    assert [e.kind for e in ev] == [UNKNOWN]
    assert "inline_truncated" in ev[0].note


def test_null_capture_membership_is_unknown():
    ev = diff_picklist_chains(
        value_rows=[_vrow("INLINE:O.F.A", 1, None),
                    _vrow("INLINE:O.F.B", 90, None)],
        set_rows=[_srow("S1", "INLINE:O.F", 1, None)],
        field_rows=[_field("O.F", 1, None, mark=None)],
    )
    assert [e.kind for e in ev] == [UNKNOWN]


def test_ownerless_set_is_treated_as_reliable_standard_set():
    ev = diff_picklist_chains(
        value_rows=[_vrow("SVS:LeadStatus.New", 1, None, set_row="S9"),
                    _vrow("SVS:LeadStatus.Odd", 90, None, set_row="S9")],
        set_rows=[_srow("S9", "SVS:LeadStatus", 1, None)],
        field_rows=[],
    )
    assert [e.kind for e in ev] == [MEMBERSHIP]


def test_first_capture_at_baseline_is_silent():
    ev = diff_picklist_chains(
        value_rows=[_vrow("INLINE:O.F.A", 1, None)],
        set_rows=[_srow("S1", "INLINE:O.F", 1, None)],
        field_rows=[_field("O.F", 1, None)],
    )
    assert ev == []


def test_unresolvable_set_row_is_unknown():
    ev = diff_picklist_chains(
        value_rows=[_vrow("INLINE:O.F.A", 1, None),
                    _vrow("INLINE:O.X.B", 90, None, set_row="MISSING")],
        set_rows=[_srow("S1", "INLINE:O.F", 1, None)],
        field_rows=[_field("O.F", 1, None)],
    )
    assert [e.kind for e in ev] == [UNKNOWN]
    assert "unresolvable" in ev[0].note


def test_set_disappearance_is_lifecycle_with_values_folded():
    ev = diff_picklist_chains(
        value_rows=[_vrow("INLINE:O.G.A", 1, 99, set_row="S2"),
                    _vrow("INLINE:O.F.A", 1, None)],
        set_rows=[_srow("S1", "INLINE:O.F", 1, None),
                  _srow("S2", "INLINE:O.G", 1, 99)],
        field_rows=[_field("O.F", 1, None),
                    _field("O.G", 1, None, set_row="S2")],
    )
    assert [(e.kind, e.rule) for e in ev] == [(LIFECYCLE, "INLINE:O.G")]
    assert "disappeared" in ev[0].note
