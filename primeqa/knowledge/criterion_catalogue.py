"""S5 criterion catalogue — ratified criterion lists as hash-pinned
ARTIFACTS (LLD_PHASE5_AUTHORING Part 1 §a–§d).

Plimsol generates nothing here. Each catalogue is parsed out of a
document a human obtained from the published normative source, vendored
under ``primeqa/knowledge/vendor/criteria/`` and pinned by sha256 in
``s5_artifacts`` (the axe-core discipline, D-461). The parsers below
fail loudly on anything they cannot read; they never supply a missing
number, title or level. The ingest is reproducible: the same artifact
under the same parser yields the same rows, and ``rows_hash`` makes that
checkable.

What each standard's catalogue IS:

* ``WCAG22``  — every Success Criterion of WCAG 2.2 as published, with
  its level. 4.1.1 Parsing is published as "(Obsolete and removed)" and
  is recorded as removed, never as a criterion. The bound scope of the
  set "WCAG 2.2 AA" is the A+AA subset; AAA rows are kept so a rule that
  maps to one renders at its TRUE level, outside the gate.
* ``EN301549`` — clause 9 (Web) of EN 301 549 V3.2.1: every 9.x.y.z
  clause that binds a WCAG 2.1 Success Criterion ("Where ICT is a web
  page, it shall satisfy WCAG 2.1 Success Criterion …"). The clause's
  level is the level of the SC it binds, read from the pinned WCAG 2.1
  artifact — EN states no levels of its own. "Void" clauses (numbering
  placeholders, NOTE 5) are recorded and excluded; clause 9.5 (AAA,
  informative) and 9.6 (conformance requirements) are outside the
  criterion denominator and are recorded as such.
* ``SECTION508`` — the WCAG 2.0 Level A and AA Success Criteria that
  E205.4 incorporates by reference (702.10.1); 508 does not renumber.
  The four non-Web-document exceptions are recorded in provenance; they
  do not apply to web pages.
"""
from __future__ import annotations

import functools
import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import text

_VENDOR = Path(__file__).parent / "vendor" / "criteria"

ARTIFACT_KIND = "criterion_catalogue"
RETRIEVED_AT = "2026-09-01"          # AK's retrieval date for all five

# The pins. Duplicated into s5_artifacts by migration 067 (generated from
# this table, never hand-typed); the DB-real test asserts DB == module ==
# file for every row.
CATALOGUE_ARTIFACTS: dict = {
    ("wcag", "2.0"): {
        "file": "wcag20.html",
        "source_url": "https://www.w3.org/TR/WCAG20/",
        "sha256": "3a438f1a4aa7b6a0848ce9dcd8cc0388577b6109c3bace7c68bd44df159d19c3",
        "byte_size": 191633},
    ("wcag", "2.1"): {
        "file": "wcag21.html",
        "source_url": "https://www.w3.org/TR/WCAG21/",
        "sha256": "233ac31974ce8575c08932ee1bd71c93879cf9b8426b2bc9b961b3ea8afb8ab6",
        "byte_size": 476496},
    ("wcag", "2.2"): {
        "file": "wcag22.html",
        "source_url": "https://www.w3.org/TR/WCAG22/",
        "sha256": "6e3c5fe397257cae509a2fb4752b73062cf8cbeb92c2cec618989b17e4cf7057",
        "byte_size": 512457},
    ("en301549", "V3.2.1"): {
        "file": "en301549_v3.2.1.pdf",
        "source_url": "https://www.etsi.org/deliver/etsi_en/301500_301599/301549/03.02.01_60/en_301549v030201p.pdf",
        "sha256": "1eee3a1841a94567da8e59f3b19a782ce9ab081c386b6a2a763b8cde13ff5b49",
        "byte_size": 2285361},
    ("section508", "2017-Refresh"): {
        "file": "section508_2017.html",
        "source_url": "https://www.access-board.gov/ict/",
        "sha256": "0ca015e924da9016282392c0299201302b584d1f8e0f23b7ebcdc2b2d8082781",
        "byte_size": 493043},
}

# Which pinned artifacts each standard's catalogue is parsed from.
STANDARD_SOURCES = {
    "WCAG22": [("wcag", "2.2")],
    "EN301549": [("en301549", "V3.2.1"), ("wcag", "2.1")],
    "SECTION508": [("section508", "2017-Refresh"), ("wcag", "2.0")],
}

_SC = r"\d+\.\d+\.\d+"


class CatalogueIngestError(RuntimeError):
    """A pinned document the ingester could not read as expected. The
    message names what was expected and what was found; the ingest
    never guesses past it."""


@dataclass
class Criterion:
    criterion: str          # the standard's OWN numbering
    title: str
    level: str | None       # A / AA / AAA — None only transiently for EN
    ordinal: int
    binds_wcag_sc: str | None = None
    source_ref: dict = field(default_factory=dict)


@dataclass
class Catalogue:
    standard: str
    standard_version: str
    rows: list
    provenance: dict

    def rows_hash(self) -> str:
        return rows_hash(self.rows)


def rows_hash(rows: list) -> str:
    """The reproducibility digest: canonical rows, order-independent."""
    lines = sorted(
        "|".join([r.criterion, r.title, r.level or "", str(r.ordinal),
                  r.binds_wcag_sc or ""]) for r in rows)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Artifact access — the file must match its pin before anything is parsed
# --------------------------------------------------------------------------

def artifact_path(name: str, version: str) -> Path:
    spec = CATALOGUE_ARTIFACTS[(name, version)]
    return _VENDOR / spec["file"]


def verify_artifact(name: str, version: str) -> dict:
    """Assert the vendored bytes match the pin. Returns the spec plus the
    observed sha so a report can show both."""
    spec = CATALOGUE_ARTIFACTS[(name, version)]
    path = artifact_path(name, version)
    if not path.exists():
        raise CatalogueIngestError(
            f"vendored artifact missing: {path} ({name} {version})")
    data = path.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != spec["sha256"]:
        raise CatalogueIngestError(
            f"artifact {name} {version} at {path.name} hashes {actual[:12]}…, "
            f"pin says {spec['sha256'][:12]}… — refusing to parse an "
            "unpinned document")
    if len(data) != spec["byte_size"]:
        raise CatalogueIngestError(
            f"artifact {name} {version}: {len(data)} bytes, pin says "
            f"{spec['byte_size']}")
    return {**spec, "name": name, "version": version, "path": str(path),
            "observed_sha256": actual}


def _clean(s: str) -> str:
    s = html.unescape(re.sub(r"<[^>]+>", "", s))
    return re.sub(r"\s+", " ", s).strip()


# --------------------------------------------------------------------------
# WCAG 2.0 — the 2008 XHTML TR page
# --------------------------------------------------------------------------

def parse_wcag20(path: Path) -> list:
    """<div class="sc" id="…"><strong class="sc-handle">N.N.N Title:</strong>
    … (Level X) — 61 success criteria as published."""
    src = path.read_text(encoding="utf-8")
    blocks = re.findall(r'<div class="sc" id="([^"]+)">(.*?)(?=<div class="sc" id=|<div class="div3"|<div class="div2"|</body>)',
                        src, re.S)
    rows: list = []
    for ordinal, (anchor, body) in enumerate(blocks, start=1):
        handle = re.search(
            rf'<strong class="sc-handle">({_SC}) ([^<]+?):?\s*</strong>', body)
        level = re.search(r"\(Level (A{1,3})\)", body)
        if not handle or not level:
            raise CatalogueIngestError(
                f"WCAG 2.0 block #{ordinal} (id={anchor!r}) lacks a handle "
                f"or a level — refusing to guess")
        rows.append(Criterion(criterion=handle.group(1),
                              title=_clean(handle.group(2)),
                              level=level.group(1), ordinal=ordinal,
                              binds_wcag_sc=handle.group(1),
                              source_ref={"anchor": anchor}))
    _require_unique_numbers(rows, "WCAG 2.0")
    if len(rows) != 61:
        raise CatalogueIngestError(
            f"WCAG 2.0: expected 61 success criteria as published, parsed "
            f"{len(rows)}")
    return rows


# --------------------------------------------------------------------------
# WCAG 2.1 / 2.2 — the ReSpec TR pages
# --------------------------------------------------------------------------

_H4 = re.compile(
    rf'<h4 id="([^"]+)"><bdi class="secno">Success Criterion ({_SC}) </bdi>'
    r'([^<]*)</h4>(.*?)(?=<h4 id="|<h3 id="|</section>\s*</section>\s*<section)',
    re.S)


def parse_wcag2x(path: Path, expected: int) -> tuple:
    """Returns (rows, removed). A heading with no conformance level is
    accepted ONLY when its published title says "(Obsolete and removed)"
    — WCAG 2.2's 4.1.1 — and is recorded as removed, not as a criterion.
    Any other level-less heading fails the ingest."""
    src = path.read_text(encoding="utf-8")
    rows: list = []
    removed: list = []
    ordinal = 0
    for anchor, number, title, body in _H4.findall(src):
        title = _clean(title)
        level = re.search(r'class="conformance-level">\(Level (A{1,3})\)',
                          body)
        if level is None:
            if "(Obsolete and removed)" in title:
                removed.append({"criterion": number, "title": title,
                                "anchor": anchor})
                continue
            raise CatalogueIngestError(
                f"{path.name}: Success Criterion {number} {title!r} carries "
                "no conformance level and is not marked removed")
        ordinal += 1
        rows.append(Criterion(criterion=number, title=title,
                              level=level.group(1), ordinal=ordinal,
                              binds_wcag_sc=number,
                              source_ref={"anchor": anchor}))
    _require_unique_numbers(rows, path.name)
    if len(rows) != expected:
        raise CatalogueIngestError(
            f"{path.name}: expected {expected} success criteria as "
            f"published, parsed {len(rows)} (+{len(removed)} removed)")
    return rows, removed


def _require_unique_numbers(rows: list, label: str) -> None:
    seen: set = set()
    for r in rows:
        if r.criterion in seen:
            raise CatalogueIngestError(
                f"{label}: criterion {r.criterion} parsed twice")
        seen.add(r.criterion)


# --------------------------------------------------------------------------
# EN 301 549 V3.2.1 — clause 9 of the ETSI PDF
# --------------------------------------------------------------------------

_EN_SATISFY = re.compile(
    rf"^Where ICT is a web page, it shall satisfy WCAG 2\.1 Success "
    rf"Criterion ({_SC}) (.+)$")
_EN_CLAUSE = re.compile(r"^(9\.\d\.\d+\.\d+) (.+?)\s*$")


@functools.lru_cache(maxsize=4)
def _pdf_pages(path_str: str) -> tuple:
    """Page texts of a pinned PDF, extracted once per process. Keyed by
    path; callers verify the pin before reaching here."""
    from pypdf import PdfReader
    return tuple((p.extract_text() or "") for p in PdfReader(path_str).pages)


def _en_body_text(path: Path) -> tuple:
    """Text of clause 9's body pages (from "9 Web / 9.0 General" up to
    the page carrying the "10 Non-web documents" body heading)."""
    pages = list(_pdf_pages(str(path)))
    start = end = None
    for i, t in enumerate(pages):
        if start is None and re.search(r"^9 Web\s*$", t, re.M) \
                and "9.0 General (informative)" in t:
            start = i
        elif start is not None and re.search(
                r"^10 Non-web documents\s*$", t, re.M):
            end = i
            break
    if start is None or end is None:
        raise CatalogueIngestError(
            f"{path.name}: could not locate clause 9's body pages "
            f"(start={start}, end={end})")
    return "\n".join(pages[start:end + 1]), (start + 1, end + 1), len(pages)


def parse_en301549(path: Path, wcag21_rows: list) -> tuple:
    """Returns (rows, void, notes). Each 9.x.y.z clause must be followed
    by its 'shall satisfy WCAG 2.1 Success Criterion N.N.N Title.'
    sentence (joined across wrapped lines) or be a 'Void' placeholder;
    anything else is a loud failure. Levels come from ``wcag21_rows``."""
    body, page_range, n_pages = _en_body_text(path)
    lines = [ln.rstrip() for ln in body.split("\n")]
    by_sc = {r.criterion: r for r in wcag21_rows}

    rows: list = []
    void: list = []
    ordinal = 0
    i = 0
    stop_at = None
    for k, ln in enumerate(lines):
        if re.match(r"^9\.5 WCAG 2\.1 AAA Success Criteria\s*$", ln):
            stop_at = k
            break
    if stop_at is None:
        raise CatalogueIngestError(
            f"{path.name}: clause 9.5 heading not found — cannot bound the "
            "normative clause list")
    while i < stop_at:
        m = _EN_CLAUSE.match(lines[i])
        if not m:
            i += 1
            continue
        clause, title = m.group(1), m.group(2).strip()
        if title == "Void":
            void.append(clause)
            i += 1
            continue
        # the satisfy sentence, possibly wrapped over following lines
        j = i + 1
        sentence = ""
        while j < stop_at:
            piece = lines[j].strip()
            if not piece:
                j += 1
                continue
            sentence = (sentence + " " + piece).strip() if sentence else piece
            if sentence.endswith("."):
                break
            j += 1
        sm = _EN_SATISFY.match(sentence)
        if not sm:
            raise CatalogueIngestError(
                f"{path.name}: clause {clause} {title!r} is not followed by "
                f"a 'shall satisfy WCAG 2.1 Success Criterion' sentence "
                f"(found {sentence[:80]!r})")
        sc = sm.group(1)
        cited = sm.group(2).rstrip(".").strip()
        ref = by_sc.get(sc)
        if ref is None:
            raise CatalogueIngestError(
                f"{path.name}: clause {clause} binds WCAG 2.1 SC {sc}, which "
                "the pinned WCAG 2.1 artifact does not contain")
        if ref.level not in ("A", "AA"):
            raise CatalogueIngestError(
                f"{path.name}: clause {clause} binds {sc} at level "
                f"{ref.level}; EN 9.1–9.4 bind A+AA only")
        if clause != f"9.{sc}":
            raise CatalogueIngestError(
                f"{path.name}: clause {clause} binds {sc} — numbering does "
                "not align (EN clause 9 mirrors WCAG numbering)")
        ordinal += 1
        rows.append(Criterion(
            criterion=clause, title=title, level=ref.level, ordinal=ordinal,
            binds_wcag_sc=sc,
            source_ref={"wcag21_title_as_cited": cited,
                        "level_from": "WCAG 2.1 artifact (EN states none)"}))
        i = j + 1
    _require_unique_numbers(rows, "EN 301 549 clause 9")
    if len(rows) != 50:
        raise CatalogueIngestError(
            f"{path.name}: expected 50 clauses binding WCAG 2.1 A+AA "
            f"(30 A + 20 AA), parsed {len(rows)}")
    if sorted(void) != ["9.1.4.6", "9.1.4.7", "9.1.4.8", "9.1.4.9", "9.2.1.3"]:
        raise CatalogueIngestError(
            f"{path.name}: unexpected Void clause set {sorted(void)}")
    # cross-check against Annex C (the test procedures name every clause)
    all_text = "\n".join(_pdf_pages(str(path)))
    annex = set(re.findall(r"\nC\.(9\.\d\.\d+\.\d+) ", all_text))
    expected_annex = {r.criterion for r in rows} | set(void)
    if annex != expected_annex:
        raise CatalogueIngestError(
            f"{path.name}: Annex C clause set differs from clause 9 body: "
            f"annex-only={sorted(annex - expected_annex)} "
            f"body-only={sorted(expected_annex - annex)}")
    notes = {"body_pages": list(page_range), "pdf_pages": n_pages,
             "void_clauses": sorted(void),
             "outside_denominator": {
                 "9.5": "WCAG 2.1 AAA success criteria — informative (Table 9.1)",
                 "9.6": "WCAG conformance requirements — not criteria"},
             "annex_c_cross_check": "agrees"}
    return rows, sorted(void), notes


# --------------------------------------------------------------------------
# Section 508 (2017 Refresh) — E205.4 incorporation by reference
# --------------------------------------------------------------------------

_E205_4 = re.compile(
    r"Electronic content shall conform to Level A and Level AA Success "
    r"Criteria and Conformance Requirements in WCAG 2\.0 \(incorporated by "
    r"reference, see (702\.10\.1)\)")
_E205_4_EXC = re.compile(
    r"EXCEPTION: Non-Web documents shall not be required to conform to the "
    r"following four WCAG 2\.0 Success Criteria: (.*?)\.(?=\s+E205\.4\.1)")


def parse_section508(path: Path, wcag20_rows: list) -> tuple:
    """Returns (rows, statement). The incorporation sentence must be
    present verbatim; the catalogue is then the WCAG 2.0 A+AA rows."""
    src = path.read_text(encoding="utf-8")
    i = src.find("E205.4 Accessibility Standard")
    if i < 0:
        raise CatalogueIngestError(
            f"{path.name}: heading 'E205.4 Accessibility Standard' not found")
    txt = _clean(src[i:i + 4000])
    m = _E205_4.search(txt)
    if not m:
        raise CatalogueIngestError(
            f"{path.name}: E205.4 incorporation sentence not found in the "
            "expected form")
    exc = _E205_4_EXC.search(txt)
    if not exc:
        raise CatalogueIngestError(
            f"{path.name}: E205.4 non-Web exception not found")
    exceptions = []
    for item in re.split(r",\s*(?:and\s+)?", exc.group(1)):
        im = re.fullmatch(rf"({_SC}) (.+)", item.strip())
        if im:
            exceptions.append((im.group(1), im.group(2)))
    if len(exceptions) != 4:
        raise CatalogueIngestError(
            f"{path.name}: expected four non-Web exceptions, parsed "
            f"{len(exceptions)}: {exc.group(1)!r}")
    ibr = re.search(r"702\.10\.1\s+WCAG 2\.0\s+Web Content Accessibility "
                    r"Guidelines \(WCAG\) 2\.0[^.]{0,80}?W3C Recommendation, "
                    r"December 11, 2008", _clean(src))
    if not ibr:
        raise CatalogueIngestError(
            f"{path.name}: IBR entry 702.10.1 for WCAG 2.0 not found")
    rows: list = []
    ordinal = 0
    for r in wcag20_rows:
        if r.level not in ("A", "AA"):
            continue
        ordinal += 1
        rows.append(Criterion(
            criterion=r.criterion, title=r.title, level=r.level,
            ordinal=ordinal, binds_wcag_sc=r.criterion,
            source_ref={"incorporated_by": "E205.4", "ibr": m.group(1),
                        "wcag20_anchor": r.source_ref.get("anchor")}))
    if len(rows) != 38:
        raise CatalogueIngestError(
            f"{path.name}: expected 38 incorporated criteria (25 A + 13 AA), "
            f"assembled {len(rows)}")
    statement = {"clause": "E205.4", "sentence": m.group(0),
                 "ibr": m.group(1),
                 "ibr_entry": ibr.group(0),
                 "non_web_document_exceptions":
                     [{"criterion": c, "title": t.strip()} for c, t in exceptions],
                 "exception_note": "applies to non-Web documents only; web "
                                   "pages keep all 38 criteria"}
    return rows, statement


# --------------------------------------------------------------------------
# Per-standard assembly
# --------------------------------------------------------------------------

def catalogue_for(standard: str) -> Catalogue:
    """Parse the pinned artifacts into the standard's catalogue. Verifies
    every pin before reading a byte."""
    if standard not in STANDARD_SOURCES:
        raise CatalogueIngestError(f"no catalogue source for {standard!r}")
    pins = {f"{n} {v}": verify_artifact(n, v)
            for n, v in STANDARD_SOURCES[standard]}
    prov = {"artifacts": {k: {"sha256": p["sha256"], "file": p["file"],
                              "source_url": p["source_url"],
                              "retrieved_at": RETRIEVED_AT}
                          for k, p in pins.items()},
            "ingester": "primeqa.knowledge.criterion_catalogue",
            "generated": False}

    if standard == "WCAG22":
        rows, removed = parse_wcag2x(artifact_path("wcag", "2.2"), 86)
        prov["removed"] = removed
        prov["levels"] = _level_census(rows)
        prov["bound_scope"] = "A+AA (the set is 'WCAG 2.2 AA'); AAA rows "\
                              "kept for true-level rendering outside the gate"
        return Catalogue("WCAG22", "WCAG 2.2 AA", rows, prov)

    if standard == "EN301549":
        w21, removed21 = parse_wcag2x(artifact_path("wcag", "2.1"), 78)
        if removed21:
            raise CatalogueIngestError("WCAG 2.1 must carry no removed SC")
        rows, void, notes = parse_en301549(
            artifact_path("en301549", "V3.2.1"), w21)
        prov.update(notes)
        prov["levels"] = _level_census(rows)
        prov["extractor"] = _pypdf_version()
        return Catalogue("EN301549", "EN 301 549 V3.2.1", rows, prov)

    if standard == "SECTION508":
        w20 = parse_wcag20(artifact_path("wcag", "2.0"))
        rows, statement = parse_section508(
            artifact_path("section508", "2017-Refresh"), w20)
        prov["incorporation"] = statement
        prov["levels"] = _level_census(rows)
        prov["wcag20_total"] = len(w20)
        return Catalogue("SECTION508",
                         "Section 508 (2017 Refresh, 36 CFR 1194 App. A)",
                         rows, prov)
    raise CatalogueIngestError(standard)


def _level_census(rows: list) -> dict:
    out: dict = {}
    for r in rows:
        out[r.level] = out.get(r.level, 0) + 1
    return dict(sorted(out.items()))


def _pypdf_version() -> str:
    import pypdf
    return f"pypdf {pypdf.__version__}"


# --------------------------------------------------------------------------
# DB side — ingest into a DRAFT standard set; report; backfill
# --------------------------------------------------------------------------

def require_artifact_pins(session) -> list:
    """Every catalogue artifact must be pinned in s5_artifacts with the
    module's sha; the vendored bytes must match. Returns the checked
    rows so a report can list them."""
    checked = []
    for (name, version), spec in CATALOGUE_ARTIFACTS.items():
        row = session.execute(text("""
            SELECT sha256, source_url, retrieved_at, byte_size, repo_path
            FROM s5_artifacts WHERE kind=:k AND name=:n AND version=:v
        """), {"k": ARTIFACT_KIND, "n": name, "v": version}).fetchone()
        if row is None:
            raise CatalogueIngestError(
                f"s5_artifacts has no pin for {ARTIFACT_KIND} {name} {version}")
        if row[0].strip() != spec["sha256"]:
            raise CatalogueIngestError(
                f"s5_artifacts pin for {name} {version} ({row[0][:12]}…) "
                f"differs from the ingester's ({spec['sha256'][:12]}…)")
        verify_artifact(name, version)
        checked.append({"name": name, "version": version,
                        "sha256": spec["sha256"], "source_url": row[1],
                        "retrieved_at": str(row[2]), "byte_size": row[3],
                        "repo_path": row[4]})
    return checked


def ingest_catalogue(session, *, standard: str, map_set_id: int) -> dict:
    """Write the standard's catalogue rows into a DRAFT standard set.
    Refuses a non-DRAFT set (content freeze) and a set already holding
    criteria (an ingest is one act, never an upsert)."""
    st = session.execute(text(
        "SELECT state, standard, standard_version FROM s5_standard_map_sets "
        "WHERE id=:i"), {"i": map_set_id}).fetchone()
    if st is None:
        raise CatalogueIngestError(f"no such standard set {map_set_id}")
    if st[0] != "DRAFT":
        raise CatalogueIngestError(
            f"catalogue ingest requires the set in DRAFT; set {map_set_id} "
            f"is {st[0]}")
    if st[1] != standard:
        raise CatalogueIngestError(
            f"set {map_set_id} is for {st[1]}, not {standard}")
    existing = session.execute(text(
        "SELECT COUNT(*) FROM s5_criteria WHERE set_id=:i"),
        {"i": map_set_id}).scalar_one()
    if existing:
        raise CatalogueIngestError(
            f"set {map_set_id} already holds {existing} criteria")
    require_artifact_pins(session)
    cat = catalogue_for(standard)
    for r in cat.rows:
        session.execute(text("""
            INSERT INTO s5_criteria
                (set_id, standard, standard_version, criterion, title,
                 level, ordinal, binds_wcag_sc, source_ref)
            VALUES (:s, :std, :ver, :c, :t, :l, :o, :b, CAST(:ref AS JSONB))
        """), {"s": map_set_id, "std": standard, "ver": cat.standard_version,
               "c": r.criterion, "t": r.title, "l": r.level, "o": r.ordinal,
               "b": r.binds_wcag_sc, "ref": json.dumps(r.source_ref)})
    session.execute(text("""
        UPDATE s5_standard_map_sets
        SET provenance = provenance || CAST(:p AS JSONB)
        WHERE id=:i
    """), {"p": json.dumps({"catalogue": cat.provenance,
                            "catalogue_rows_hash": cat.rows_hash(),
                            "catalogue_rows": len(cat.rows)}),
           "i": map_set_id})
    session.flush()
    return {"standard": standard, "map_set_id": map_set_id,
            "rows": len(cat.rows), "rows_hash": cat.rows_hash(),
            "levels": cat.provenance.get("levels")}


def stored_rows_hash(session, map_set_id: int) -> str:
    rows = session.execute(text("""
        SELECT criterion, title, level, ordinal, binds_wcag_sc
        FROM s5_criteria WHERE set_id=:i
    """), {"i": map_set_id}).fetchall()
    return rows_hash([Criterion(criterion=r[0], title=r[1], level=r[2],
                                ordinal=r[3], binds_wcag_sc=r[4])
                      for r in rows])


def level_mismatch_report(session, map_set_id: int) -> dict:
    """LLD §b: a map's stored (rule-derived) level against the catalogue's
    per-criterion level — a LOUD report, never a silent overwrite. Also
    lists maps whose criterion the catalogue does not know (orphans) and
    maps whose criterion sits outside the set's bound scope."""
    rows = session.execute(text("""
        SELECT m.rule_id, m.rule_version, m.criterion, m.level,
               c.level AS cat_level, c.title
        FROM s5_standard_maps m
        LEFT JOIN s5_criteria c
          ON c.set_id = m.map_set_id AND c.criterion = m.criterion
        WHERE m.map_set_id = :i
        ORDER BY m.criterion, m.rule_id
    """), {"i": map_set_id}).fetchall()
    mismatches, orphans, agree = [], [], 0
    for rule_id, ver, crit, map_level, cat_level, title in rows:
        if cat_level is None:
            orphans.append({"rule_id": rule_id, "criterion": crit,
                            "map_level": map_level})
        elif (map_level or "") != cat_level:
            mismatches.append({"rule_id": rule_id, "version": ver,
                               "criterion": crit, "title": title,
                               "map_level": map_level,
                               "catalogue_level": cat_level})
        else:
            agree += 1
    outside = sorted({m["criterion"] for m in mismatches
                      if m["catalogue_level"] == "AAA"})
    return {"map_set_id": map_set_id, "maps": len(rows),
            "agree": agree, "mismatches": mismatches, "orphans": orphans,
            "criteria_now_outside_bound_scope": outside}


def backfill_map_levels(session, map_set_id: int) -> int:
    """LLD §b: the map's level becomes display-only and is set from the
    catalogue. Call only after the mismatch report has been read."""
    n = session.execute(text("""
        UPDATE s5_standard_maps m
        SET level = c.level
        FROM s5_criteria c
        WHERE c.set_id = m.map_set_id AND c.criterion = m.criterion
          AND m.map_set_id = :i AND m.level IS DISTINCT FROM c.level
    """), {"i": map_set_id}).rowcount
    session.flush()
    return int(n or 0)


# --------------------------------------------------------------------------
# Read side — the denominator and per-criterion levels for the views
# --------------------------------------------------------------------------

def catalogue_denominator(session, standard: str) -> dict | None:
    """The ratified catalogue of the standard's ACTIVE set, or None when
    that set carries no criteria (the caller keeps the census fallback,
    per standard, never globally)."""
    ms = session.execute(text("""
        SELECT id, standard_version, provenance
        FROM s5_standard_map_sets WHERE standard=:s AND state='ACTIVE'
    """), {"s": standard}).fetchone()
    if ms is None:
        return None
    rows = session.execute(text("""
        SELECT criterion, title, level, ordinal, binds_wcag_sc
        FROM s5_criteria WHERE set_id=:i ORDER BY ordinal
    """), {"i": ms[0]}).fetchall()
    if not rows:
        return None
    prov = ms[2] or {}
    cat = prov.get("catalogue") or {}
    return {
        "set_id": ms[0],
        "standard_version": ms[1],
        "criteria": [{"criterion": r[0], "title": r[1], "level": r[2],
                      "ordinal": r[3], "binds_wcag_sc": r[4]} for r in rows],
        "provenance": "ratified_catalogue",
        "complete": True,
        "limitation": None,
        "catalogue_rows_hash": prov.get("catalogue_rows_hash"),
        "catalogue_artifacts": cat.get("artifacts"),
    }
