"""Phase 4 pure merge-gate tests — the projection's rules, the refusal of
the superseded 508 numbering, and the roll-up."""
from __future__ import annotations

from pathlib import Path

import pytest

from primeqa.interpretation import standard_view as SV
from primeqa.knowledge import standard_derivation as SD

pytestmark = pytest.mark.unit

_SRC = (Path(__file__).parents[3] / "primeqa" / "knowledge"
        / "standard_derivation.py").read_text("utf-8")


def test_en_renumbers_and_the_others_do_not():
    assert SD.clause_for("EN301549", "1.1.1") == "9.1.1.1"
    assert SD.clause_for("SECTION508", "1.1.1") == "1.1.1"
    assert SD.clause_for("WCAG22", "1.1.1") == "1.1.1"


def test_scope_composition_is_the_acc05_logic():
    # 4.1.1 lives in WCAG 2.0/2.1 (`wcag2a-obsolete`) and is REMOVED in
    # 2.2 — which is exactly why the ACC-05 pair maps for EN/508 and not
    # for WCAG22.
    assert "wcag2a-obsolete" in SD._SCOPE["SECTION508"]
    assert "wcag2a-obsolete" in SD._SCOPE["EN301549"]
    assert "wcag2a-obsolete" not in SD._SCOPE["WCAG22"]
    # 508 binds WCAG 2.0 only; EN V3.2.1 reaches 2.1; WCAG22 reaches 2.2
    assert "wcag21aa" not in SD._SCOPE["SECTION508"]
    assert "wcag21aa" in SD._SCOPE["EN301549"]
    assert "wcag22aa" not in SD._SCOPE["EN301549"]
    assert "wcag22aa" in SD._SCOPE["WCAG22"]


def test_the_superseded_508_tags_are_never_read():
    """axe's section508.22.x tags are the pre-2017 §1194.22 numbering; the
    2017 Refresh replaced them with incorporation of WCAG 2.0 A+AA.
    The module may NAME them (it records the refusal) but must never READ
    them: no code path selects a tag by a section508 prefix."""
    import re
    reads = re.findall(r'startswith\(\s*["\']section', _SRC)
    assert reads == [], f"derivation reads a section508 tag: {reads}"
    # the derivation's only tag reads are the WCAG version tags and the
    # EN clause tags — enumerate them so a new read cannot slip in
    assert sorted(set(re.findall(r'startswith\(\s*["\']([\w-]+)', _SRC))) == \
        ["EN-", "wcag2"]
    assert "SUPERSEDED" in _SRC          # the refusal is recorded in prose


def test_engine_tag_census_parses_the_vendored_engine():
    tags = SD.engine_rule_tags()
    assert len(tags) > 90
    assert "wcag111" in tags["image-alt"]
    # the ACC-05 pair: obsolete + deprecated, and NO EN/508 tag — which is
    # why their maps must be authored rather than derived
    for rule in ("duplicate-id", "duplicate-id-active"):
        assert "wcag2a-obsolete" in tags[rule]
        assert not [t for t in tags[rule] if t.startswith("EN-")]
        assert not [t for t in tags[rule] if t.startswith("section508")]


def test_rollup_is_worst_wins():
    r = SV._RANK
    assert r["FAIL"] > r["NEEDS_HUMAN"] > r["NOT_DETERMINED"] > r["PASS"]


def test_bound_criteria_declares_its_incompleteness():
    d = SV.bound_criteria("SECTION508")
    assert d["provenance"] == "engine_tag_census"
    assert d["complete"] is False          # honest lower bound, never implied
    assert "lower bound" in d["limitation"]
    assert d["criteria"]


def test_wcag22_denominator_excludes_the_obsolete_criterion():
    """4.1.1 must not appear in WCAG 2.2's denominator, but must appear
    in the standards that still bind it."""
    assert "4.1.1" not in SV.bound_criteria("WCAG22")["criteria"]
    assert "4.1.1" in SV.bound_criteria("SECTION508")["criteria"]
    assert "4.1.1" in SV.bound_criteria("EN301549")["criteria"]


def test_view_requires_exactly_one_scope_key():
    with pytest.raises(SV.StandardViewError, match="exactly one"):
        SV.standard_view(None, standard="WCAG22")
