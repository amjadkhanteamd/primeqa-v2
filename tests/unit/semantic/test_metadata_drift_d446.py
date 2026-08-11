"""D-446 pins: the MESSAGE facet and the RELATED_ENTITY_CHANGED passes.

Direction pins: a message-only edit is ITS OWN event class (never FORMULA,
never silent); one-side-unextractable stays UNKNOWN; a referenced field's
type change and a RecordType rename each name the RULE they affect — and a
rule that does not reference the changed entity is never named.
"""
from primeqa.semantic.metadata_drift import (
    FORMULA, MESSAGE, RELATED_ENTITY_CHANGED, UNKNOWN,
    _related_field_type_events, _related_recordtype_events,
    _rule_fields_index, diff_vr_chains)


def _row(name, vf, vt, formula="Amount > 10", active=True, message="msg"):
    attrs = {"Metadata": {"errorConditionFormula": formula,
                          "active": active},
             "ErrorMessage": message}
    return {"sf_api_name": name, "valid_from_seq": vf, "valid_to_seq": vt,
            "attributes": attrs}


# ---------------------------------------------------------------------------
# MESSAGE facet
# ---------------------------------------------------------------------------

def test_message_only_edit_is_a_message_event():
    events = diff_vr_chains([
        _row("Opportunity.R", 1, 70, message="Amount too low"),
        _row("Opportunity.R", 70, None, message="Amount must exceed 10"),
    ])
    assert [e.kind for e in events] == [MESSAGE]
    e = events[0]
    assert (e.before, e.after) == ("Amount too low", "Amount must exceed 10")
    assert "D-297" in e.note and "naming key" in e.note


def test_message_and_formula_edit_yield_both_events():
    events = diff_vr_chains([
        _row("Opportunity.R", 1, 70, formula="A > 1", message="old"),
        _row("Opportunity.R", 70, None, formula="A > 2", message="new"),
    ])
    assert sorted(e.kind for e in events) == [FORMULA, MESSAGE]


def test_message_unextractable_one_side_is_unknown():
    r1 = _row("Opportunity.R", 1, 70)
    del r1["attributes"]["ErrorMessage"]
    events = diff_vr_chains([r1, _row("Opportunity.R", 70, None)])
    assert [e.kind for e in events] == [UNKNOWN]
    assert "message" in events[0].note


def test_identical_message_emits_nothing():
    events = diff_vr_chains([
        _row("Opportunity.R", 1, 70),
        _row("Opportunity.R", 70, None),
    ])
    assert events == []


# ---------------------------------------------------------------------------
# RELATED_ENTITY_CHANGED — referenced-field type change
# ---------------------------------------------------------------------------

_RULES = _rule_fields_index([
    ("Opportunity.NeedsAmount", "ISBLANK(Amount) && Stage_Locked__c"),
    ("Opportunity.RTGate", 'RecordType.DeveloperName = "Enterprise" && X__c > 1'),
    ("Case.Unrelated", "ISBLANK(Reason__c)"),
])


def _f(vf, vt, ftype):
    return {"sf_api_name": "Opportunity.Amount", "valid_from_seq": vf,
            "valid_to_seq": vt, "field_type": ftype}


def test_field_type_change_names_the_referencing_rule_only():
    events = _related_field_type_events(
        _RULES, {"Opportunity.Amount": [_f(1, 80, "currency"),
                                        _f(80, None, "string")]}, {})
    assert [e.kind for e in events] == [RELATED_ENTITY_CHANGED]
    e = events[0]
    assert e.rule == "Opportunity.NeedsAmount"
    assert (e.before, e.after) == ("Opportunity.Amount: currency",
                                   "Opportunity.Amount: string")
    assert "text is unchanged" in e.note


def test_field_type_stable_emits_nothing():
    events = _related_field_type_events(
        _RULES, {"Opportunity.Amount": [_f(1, 80, "currency"),
                                        _f(80, None, "currency")]}, {})
    assert events == []


def test_same_bare_field_on_other_object_never_matches():
    rows = {"Case.Amount": [
        {"sf_api_name": "Case.Amount", "valid_from_seq": 1,
         "valid_to_seq": 80, "field_type": "currency"},
        {"sf_api_name": "Case.Amount", "valid_from_seq": 80,
         "valid_to_seq": None, "field_type": "string"}]}
    assert _related_field_type_events(_RULES, rows, {}) == []


# ---------------------------------------------------------------------------
# RELATED_ENTITY_CHANGED — RecordType rename (stable sf_id join)
# ---------------------------------------------------------------------------

def _rt(vf, api, sid="012000000000001"):
    return {"sf_id": sid, "sf_api_name": api, "valid_from_seq": vf}


def test_recordtype_rename_names_the_comparing_rule():
    events = _related_recordtype_events(
        _RULES, [_rt(1, "Opportunity.Enterprise"),
                 _rt(90, "Opportunity.Enterprise_EMEA")], {})
    assert [e.kind for e in events] == [RELATED_ENTITY_CHANGED]
    e = events[0]
    assert e.rule == "Opportunity.RTGate"
    assert e.before.endswith("Enterprise") and e.after.endswith("Enterprise_EMEA")
    assert "renamed" in e.note


def test_recordtype_rename_not_in_any_formula_emits_nothing():
    events = _related_recordtype_events(
        _RULES, [_rt(1, "Opportunity.Partner"),
                 _rt(90, "Opportunity.Partner_V2")], {})
    assert events == []


def test_distinct_recordtypes_never_pair():
    events = _related_recordtype_events(
        _RULES, [_rt(1, "Opportunity.Enterprise", sid="012000000000001"),
                 _rt(90, "Opportunity.Enterprise_EMEA", sid="012000000000002")],
        {})
    assert events == []
