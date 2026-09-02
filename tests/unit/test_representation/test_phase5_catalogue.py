"""Phase 5 Part 1 — the criterion catalogue parsers over the PINNED
artifacts. Pure: no DB. Every count below is the published one, and the
parsers refuse to guess — a mutated document fails the ingest loudly."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from primeqa.knowledge import criterion_catalogue as CC

pytestmark = pytest.mark.unit


def test_every_pin_matches_the_vendored_bytes():
    for (name, version), spec in CC.CATALOGUE_ARTIFACTS.items():
        p = CC.artifact_path(name, version)
        assert p.exists(), p
        assert hashlib.sha256(p.read_bytes()).hexdigest() == spec["sha256"]
        assert p.stat().st_size == spec["byte_size"]
        CC.verify_artifact(name, version)      # the same check, the API way


def test_wcag20_is_61_criteria_25_13_23():
    rows = CC.parse_wcag20(CC.artifact_path("wcag", "2.0"))
    assert len(rows) == 61
    assert CC._level_census(rows) == {"A": 25, "AA": 13, "AAA": 23}
    assert rows[0].criterion == "1.1.1" and rows[0].title == "Non-text Content"
    assert rows[-1].criterion == "4.1.2"
    assert {r.criterion for r in rows if r.criterion == "4.1.1"}   # Parsing is live in 2.0


def test_wcag21_is_78_criteria_30_20_28():
    rows, removed = CC.parse_wcag2x(CC.artifact_path("wcag", "2.1"), 78)
    assert len(rows) == 78 and removed == []
    assert CC._level_census(rows) == {"A": 30, "AA": 20, "AAA": 28}
    by = {r.criterion: r for r in rows}
    assert by["2.1.3"].level == "AAA"          # the Phase 4 residue's true level
    assert by["1.4.6"].level == "AAA"
    assert by["4.1.1"].level == "A"


def test_wcag22_is_86_criteria_with_411_recorded_as_removed():
    rows, removed = CC.parse_wcag2x(CC.artifact_path("wcag", "2.2"), 86)
    assert len(rows) == 86
    assert CC._level_census(rows) == {"A": 31, "AA": 24, "AAA": 31}
    assert [r["criterion"] for r in removed] == ["4.1.1"]
    assert "Obsolete and removed" in removed[0]["title"]
    assert "4.1.1" not in {r.criterion for r in rows}
    by = {r.criterion: r for r in rows}
    assert by["2.5.8"].level == "AA"           # Target Size (Minimum), new in 2.2
    assert by["2.4.11"].level == "AA"          # Focus Not Obscured (Minimum)


def test_en301549_is_50_clauses_binding_wcag21_a_and_aa():
    w21, _ = CC.parse_wcag2x(CC.artifact_path("wcag", "2.1"), 78)
    rows, void, notes = CC.parse_en301549(
        CC.artifact_path("en301549", "V3.2.1"), w21)
    assert len(rows) == 50
    assert CC._level_census(rows) == {"A": 30, "AA": 20}
    assert void == ["9.1.4.6", "9.1.4.7", "9.1.4.8", "9.1.4.9", "9.2.1.3"]
    assert notes["annex_c_cross_check"] == "agrees"
    by = {r.criterion: r for r in rows}
    assert by["9.1.1.1"].binds_wcag_sc == "1.1.1"
    assert by["9.1.1.1"].title == "Non-text content"        # EN's own casing
    assert by["9.4.1.1"].binds_wcag_sc == "4.1.1"           # Parsing, live in 2.1
    assert by["9.4.1.3"].binds_wcag_sc == "4.1.3"           # Status messages
    assert all(r.criterion == f"9.{r.binds_wcag_sc}" for r in rows)
    assert all(r.level in ("A", "AA") for r in rows)


def test_section508_is_the_38_incorporated_wcag20_a_and_aa():
    w20 = CC.parse_wcag20(CC.artifact_path("wcag", "2.0"))
    rows, statement = CC.parse_section508(
        CC.artifact_path("section508", "2017-Refresh"), w20)
    assert len(rows) == 38
    assert CC._level_census(rows) == {"A": 25, "AA": 13}
    assert statement["clause"] == "E205.4" and statement["ibr"] == "702.10.1"
    assert "Level A and Level AA" in statement["sentence"]
    assert [e["criterion"] for e in statement["non_web_document_exceptions"]] \
        == ["2.4.1", "2.4.5", "3.2.3", "3.2.4"]
    assert "4.1.1" in {r.criterion for r in rows}   # Parsing, live in 2.0
    assert all(r.binds_wcag_sc == r.criterion for r in rows)  # no renumbering


def test_catalogues_assemble_with_provenance_and_are_reproducible_by_hash():
    seen = {}
    for std, expected in (("WCAG22", 86), ("EN301549", 50), ("SECTION508", 38)):
        a = CC.catalogue_for(std)
        b = CC.catalogue_for(std)
        assert len(a.rows) == expected
        assert a.rows_hash() == b.rows_hash()          # same bytes, same rows
        assert a.provenance["generated"] is False
        assert all("sha256" in v for v in a.provenance["artifacts"].values())
        seen[std] = a.rows_hash()
    assert len(set(seen.values())) == 3                # three distinct catalogues


def test_a_mutated_document_fails_the_ingest_loudly(tmp_path: Path):
    """Strip one conformance level from a copy of WCAG 2.2: the parser
    must refuse, not silently drop or guess the criterion."""
    src = CC.artifact_path("wcag", "2.2").read_text(encoding="utf-8")
    i = src.find('<h4 id="x2-5-8-target-size-minimum"')
    j = src.find('class="conformance-level">(Level AA)', i)
    assert i > 0 and j > i
    mutated = src[:j] + 'class="conformance-level">' + src[j + len('class="conformance-level">(Level AA)'):]
    p = tmp_path / "wcag22.html"
    p.write_text(mutated, encoding="utf-8")
    with pytest.raises(CC.CatalogueIngestError, match="2.5.8.*no conformance level"):
        CC.parse_wcag2x(p, 86)


def test_a_tampered_artifact_is_refused_before_parsing(tmp_path: Path, monkeypatch):
    p = tmp_path / "wcag20.html"
    p.write_bytes(CC.artifact_path("wcag", "2.0").read_bytes() + b"\n")
    monkeypatch.setattr(CC, "_VENDOR", tmp_path)
    with pytest.raises(CC.CatalogueIngestError, match="unpinned document"):
        CC.verify_artifact("wcag", "2.0")


def test_rows_hash_is_order_independent_and_content_sensitive():
    rows = CC.parse_wcag20(CC.artifact_path("wcag", "2.0"))
    h1 = CC.rows_hash(rows)
    h2 = CC.rows_hash(list(reversed(rows)))
    assert h1 == h2
    rows[0].level = "AA"
    assert CC.rows_hash(rows) != h1
