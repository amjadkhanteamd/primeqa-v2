"""Coverage floor (D-247) — the deterministic cross-check that catches model
AC under-declaration. Pure parser + reconciliation; no LLM, no DB."""
from __future__ import annotations

from primeqa.generation.coverage import (
    compute_uncovered,
    floor_shortfall,
    parse_acceptance_criteria,
)


def _idx(text):
    return [a.index for a in parse_acceptance_criteria(text)]


# --- marker forms ---

def test_jira_ordered_list_hash_markers():
    text = "_Acceptance criteria_\n# first thing happens\n# second thing happens\n# third thing\n"
    acs = parse_acceptance_criteria(text)
    assert [a.index for a in acs] == [1, 2, 3]
    assert acs[0].label == "first thing happens"


def test_numbered_list():
    assert _idx("1. alpha\n2. beta\n3) gamma\n") == [1, 2, 3]


def test_bulleted_list():
    assert _idx("- alpha\n* beta\n• gamma\n") == [1, 2, 3]


def test_explicit_ac_markers_take_precedence():
    # AC\d+ form wins over a stray numbered line in the same text.
    text = "AC1: must do X\nAC2 - must do Y\n3. some prose that looks numbered\n"
    acs = parse_acceptance_criteria(text)
    assert [a.index for a in acs] == [1, 2]
    assert acs[0].label == "must do X"


# --- dedup (the pre-1513cc1 Jira-import duplication artifact) ---

def test_duplicated_block_dedupes_by_normalized_span():
    one = "# create an SLA record ({{Case_SLA__c}})\n# notes are retained\n"
    acs = parse_acceptance_criteria(one + one)  # the whole block twice
    assert [a.index for a in acs] == [1, 2]     # 4 lines -> 2 unique


# --- graceful no-op ---

def test_freeform_prose_returns_empty():
    assert parse_acceptance_criteria(
        "Support cases must be governed by SLA tracking. Escalations must be justified.") == []


def test_single_item_returns_empty():
    assert parse_acceptance_criteria("# only one criterion here") == []


def test_empty_text_returns_empty():
    assert parse_acceptance_criteria("") == []
    assert parse_acceptance_criteria(None) == []  # type: ignore[arg-type]


# --- the real SQ-212 shape: floor detects the 8 '#' items, MISSES inline AC9 ---

SQ212 = (
    "_Acceptance criteria_\n"
    "# When any Case is created, the system creates a related SLA ({{Case_SLA__c}}).\n"
    "# When Priority = High, Escalation Status ({{Escalation_Status__c}}) is set to Escalated.\n"
    "# A Case On Hold with a blank {{Escalation_Reason__c}} is rejected.\n"
    "# The Notes ({{Notes__c}}) entered are stored exactly as entered.\n"
    "_Configuration guarantees_\n"
    "# The org provides a Case SLA object ({{Case_SLA__c}}).\n"
    "# The SLA Code ({{Case_SLA__c.SLA_Code__c}}) holds an 8-character code.\n"
    "# The SLA Target Hours ({{Case_SLA__c.Target_Hours__c}}) (precision 4, scale 1).\n"
    "# The validation rule ({{Escalation_Reason_Required}}) governs the Case object.\n"
    "_(Optional, cross-object)_ 9. When an Escalation ({{Escalation__c}}) is created, Case Status is Escalated.\n"
)


def test_sq212_floor_detects_eight_misses_inline_ac9():
    # The floor is conservative: 8 line-leading '#' items; the inline '9.' (after
    # '_(Optional...)_') is NOT line-leading, so the floor misses it. The MODEL is
    # expected to declare all 9 — and floor_shortfall(9, 8) == 0 (no false flag).
    acs = parse_acceptance_criteria(SQ212)
    assert len(acs) == 8
    assert floor_shortfall(9, len(acs)) == 0


def test_sq212_duplicated_still_eight():
    acs = parse_acceptance_criteria(SQ212 + SQ212)
    assert len(acs) == 8


# --- compute_uncovered ---

def test_compute_uncovered_returns_complement_sorted():
    assert compute_uncovered({1, 2, 3, 4, 5, 6, 7, 8, 9}, {1, 2, 3, 6, 7, 9}) == [4, 5, 8]


def test_compute_uncovered_empty_when_all_tagged():
    assert compute_uncovered([1, 2, 3], {1, 2, 3}) == []


# --- floor_shortfall ---

def test_floor_shortfall():
    assert floor_shortfall(9, 8) == 0      # model declared more than floor saw -> no flag
    assert floor_shortfall(7, 8) == 1      # model under-declared by >=1 -> flag
    assert floor_shortfall(9, 0) == 0      # no structure detected -> no flag
    assert floor_shortfall(0, 0) == 0
