"""Step 1 — the requirement identity, the pure parts
(LLD_STEP_1_PROVENANCE §a, §b).

* the display form IS the key (TA invariant 4 — no ``#N`` exists);
* the key a row decorates: ``external_key``, else the byte-identical
  pre-072 derivation;
* ``classify_origin`` — evidence only, in order: explicit override
  (R-probe) → declared fixture prefix → the live row's source → else
  ``CANNOT_CLASSIFY`` (R-jira-gap: a Jira-SHAPED key with no row does
  NOT classify ``jira``);
* every classification carries the RULE that produced it.
"""
from __future__ import annotations

import pytest

from primeqa.test_representation.identity import (
    CANNOT_CLASSIFY,
    FIXTURE,
    HIDDEN_BY_DEFAULT,
    JIRA,
    MANUAL,
    ORIGINS,
    PROBE,
    classify_origin,
    display_key,
    key_for_requirement_row,
)

pytestmark = pytest.mark.unit


class _Row:
    def __init__(self, **kw):
        self.id = kw.get("id")
        self.source = kw.get("source")
        self.jira_key = kw.get("jira_key")
        self.external_key = kw.get("external_key")


# ---- invariant 4: display id is not identity -----------------------------

@pytest.mark.parametrize("key", ["SQ-205", "req-282", "REQ-ARC-APPROVED"])
def test_display_form_is_the_key_verbatim(key):
    assert display_key(key) == key


def test_no_hash_form_survives():
    assert display_key("req-282") == "req-282" != "#282"


# ---- the key a row decorates ---------------------------------------------

def test_external_key_wins_when_present():
    assert key_for_requirement_row(_Row(id=9, jira_key="SQ-1",
                                        external_key="SQ-1")) == "SQ-1"


def test_pre_072_row_derives_the_identical_key():
    # the derivation 072 backfilled — a row that predates it still resolves
    assert key_for_requirement_row(_Row(id=282, jira_key=None)) == "req-282"
    assert key_for_requirement_row(_Row(id=284, jira_key="SQ-205")) == "SQ-205"


def test_accepts_a_mapping_too():
    assert key_for_requirement_row({"id": 7, "jira_key": None,
                                    "external_key": None}) == "req-7"


# ---- classification by evidence ------------------------------------------

def test_declared_fixture_prefixes_classify_fixture():
    for key in ("REQ-ARC-APPROVED", "REQ-D299-LIVE-1", "REQ-D300-BVA-2",
                "REQ-L7A-BANDS-1", "REQ-L7F-APPR-3B", "REQ-L7G-DIAG"):
        c = classify_origin(key)
        assert c.origin == FIXTURE, key
        assert c.evidence["rule"] == "fixture_prefix"
        assert c.evidence["cited"].startswith("D-")      # every family cited


def test_a_live_jira_row_classifies_jira_with_its_evidence():
    c = classify_origin("SQ-205", requirement_row=_Row(
        id=284, source="jira", jira_key="SQ-205"))
    assert c.origin == JIRA
    assert c.evidence == {"rule": "requirement_row", "requirement_id": 284,
                          "source": "jira", "jira_key": "SQ-205"}


def test_a_live_manual_row_classifies_manual():
    c = classify_origin("req-301", requirement_row=_Row(id=301, source="manual"))
    assert c.origin == MANUAL and c.evidence["requirement_id"] == 301


def test_r_jira_gap_a_jira_shaped_key_with_no_row_is_cannot_classify():
    # RULED (R-jira-gap): a Jira key is a fact of the ROW, not of the string.
    for key in ("SQ-206", "SQ-211"):
        c = classify_origin(key)
        assert c.origin == CANNOT_CLASSIFY, key
        assert c.evidence == {"rule": "none"}


def test_r_probe_lands_only_by_an_explicit_recorded_override():
    # RULED (R-probe): prose in a summary is not evidence; the override is
    # data, and it carries its reason and its citation.
    c = classify_origin("req-322", requirement_row=_Row(id=322, source="manual"))
    assert c.origin == PROBE
    assert c.evidence["rule"] == "override"
    assert c.evidence["cited"] == "D-457" and c.evidence["reason"]
    # without the override the same shape is plain manual
    assert classify_origin("req-321",
                           requirement_row=_Row(id=321, source="manual")).origin == MANUAL


def test_a_dangling_req_key_is_cannot_classify_not_manual():
    assert classify_origin("req-282").origin == CANNOT_CLASSIFY


def test_an_override_beats_a_fixture_prefix_and_a_row():
    # order matters: the override is the operator's recorded decision
    c = classify_origin("req-322", requirement_row=_Row(id=322, source="jira",
                                                        jira_key="X-1"))
    assert c.origin == PROBE


def test_every_classification_names_its_rule():
    for key, row in (("REQ-ARC-X", None), ("SQ-1", _Row(id=1, source="jira", jira_key="SQ-1")),
                     ("req-2", _Row(id=2, source="manual")), ("nothing", None)):
        assert classify_origin(key, requirement_row=row).evidence.get("rule")


def test_the_hidden_set_is_fixture_and_probe_only():
    assert set(HIDDEN_BY_DEFAULT) == {FIXTURE, PROBE}
    # a gap's origin is never in the hidden set by construction of the rules
    assert CANNOT_CLASSIFY not in HIDDEN_BY_DEFAULT
    assert set(ORIGINS) == {JIRA, MANUAL, FIXTURE, PROBE, CANNOT_CLASSIFY}
