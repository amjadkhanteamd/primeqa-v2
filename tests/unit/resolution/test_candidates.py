"""Phase A — object admission + the unique-match field/state ladders."""
from __future__ import annotations

from primeqa.resolution import candidates as cand
from tests.unit.resolution import world


def test_object_candidates_admission_and_features():
    t = world.table()
    got = cand.object_candidates("Order__c", t,
                                 requirement_text="PLS FB Order records")
    apis = [ev.sf_api_name for _, ev in got]
    # the wrong-but-real trap members are all admitted (recall), ordering is
    # the SOLVER's job
    assert "Order" in apis and "PLS_FB_Order__c" in apis
    by_api = {ev.sf_api_name: ev.features for _, ev in got}
    assert by_api["Order"]["exact_api"] is False        # term is Order__c
    assert by_api["Order"]["exact_label"] is False
    assert by_api["PLS_FB_Order__c"]["context"] == 3    # label in requirement


def test_object_candidates_exact_label_admits_regardless_of_similarity():
    t = world.table()
    got = cand.object_candidates("PLS FB Order", t)
    assert any(ev.features["exact_label"] for _, ev in got
               if ev.sf_api_name == "PLS_FB_Order__c")


def test_object_candidates_empty_term_yields_nothing():
    assert cand.object_candidates("  ", world.table()) == []


def test_field_ladder_exact_bare_suffix_label():
    fb = world.fb_order()
    # exact qualified
    assert cand.resolve_field(
        fb, "PLS_FB_Order__c.PLS_FB_Priority__c").api_name == "PLS_FB_Priority__c"
    # unique bare (ci)
    assert cand.resolve_field(fb, "pls_fb_priority__c").api_name == "PLS_FB_Priority__c"
    # unique suffix: the prefix-stripped guess
    assert cand.resolve_field(fb, "Priority__c").api_name == "PLS_FB_Priority__c"
    # unique label
    assert cand.resolve_field(fb, "Tier").api_name == "PLS_FB_Tier__c"
    # miss -> None, never a guess
    assert cand.resolve_field(fb, "Nonexistent__c") is None
    assert cand.resolve_field(fb, None) is None


def test_field_ladder_never_guesses_on_ambiguity():
    from tests.unit.resolution.world import fld
    from primeqa.resolution.symbols import ObjectSymbol
    from uuid import uuid4
    obj = ObjectSymbol(
        entity_id=uuid4(), api_name="X__c", label="X",
        fields=(fld("A_Priority__c", "X__c", "Priority A"),
                fld("B_Priority__c", "X__c", "Priority B")))
    # two suffix hits -> None
    assert cand.resolve_field(obj, "Priority__c") is None


def test_state_ladder_matches_api_or_label_uniquely():
    fb = world.fb_order()
    status = cand.resolve_field(fb, "PLS_FB_Status__c")
    assert cand.resolve_state(status, "Submitted") == "Submitted"
    assert cand.resolve_state(status, "submitted") == "Submitted"
    assert cand.resolve_state(status, "Cancelled") is None
    amount = cand.resolve_field(fb, "PLS_FB_Amount__c")
    assert cand.resolve_state(amount, "50000") is None   # non-picklist
