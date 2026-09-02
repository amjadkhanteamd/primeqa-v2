"""Standard-map derivation (LLD Phase 4 §b) — candidates, not authority.

EN 301 549 and Section 508 both bind WCAG success criteria by reference,
so their maps are largely DERIVABLE from the WCAG mapping each rule
already carries plus each standard's own clause numbering. This module
produces the CANDIDATE and the cross-check; a human ratifies it, and
nothing lands ACTIVE without a review record (D-462 unchanged: the
engine is not the accessibility authority — Plimsol makes the claim).

**What is derived and from what.** Whether a criterion is inside a
standard's bound scope is a fact about which WCAG VERSION contains it.
That fact is read from the vendored engine's own version tags
(``wcag2a`` / ``wcag2aa`` / ``wcag21a`` / ``wcag21aa`` / ``wcag22aa`` /
``wcag2a-obsolete``), which is a factual reading of the engine's
metadata, not a normative claim. The normative claim — "this rule
satisfies that clause" — remains Plimsol's and is ratified by a human.

**A known precision limit, recorded not hidden.** The WCAG ``level``
each map carries came from the 063 seed, which propagated the axe
RULE's level tag to every criterion of that rule. A rule covering both
an A and a AAA criterion therefore records both as A (e.g.
``scrollable-region-focusable`` records 2.1.3 as A, where WCAG makes
2.1.3 AAA). The level gate below is applied honestly against the data
we hold; correcting the catalogue's per-criterion levels is a
content change with its own review, not a side effect of this phase.

**What is refused.** axe also ships ``section508.22.x`` tags. Those are
the PRE-2017 §1194.22 paragraph numbers; the 2017 Refresh replaced them
with incorporation by reference of WCAG 2.0 A+AA. Deriving 508 clauses
from those tags would bind Plimsol to a superseded numbering, so this
module never reads them.
"""
from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import text

_AXE_PATH = (Path(__file__).parents[1] / "browser_worker" / "vendor"
             / "axe.min.js")

# The bound scope of each standard, expressed as the axe version tags
# whose criteria fall inside it.
#   Section 508 (2017 Refresh, 36 CFR 1194 App. A) -> WCAG 2.0 A + AA
#   EN 301 549 V3.2.1 (web chapter)                -> WCAG 2.1 AA
# `wcag2a-obsolete` marks a criterion that WAS in WCAG 2.0/2.1 and was
# REMOVED in 2.2 (4.1.1 Parsing) — inside both standards' scope, and the
# reason the ACC-05 pair can close here but not under WCAG22.
_SCOPE = {
    "SECTION508": frozenset({"wcag2a", "wcag2aa", "wcag2a-obsolete"}),
    "EN301549": frozenset({"wcag2a", "wcag2aa", "wcag2a-obsolete",
                           "wcag21a", "wcag21aa"}),
    # WCAG 2.2 A+AA — the existing profile, declared here so the same
    # denominator machinery serves all three views. NOTE the deliberate
    # absence of `wcag2a-obsolete`: 4.1.1 Parsing is REMOVED in 2.2, which
    # is exactly why the ACC-05 pair maps for EN/508 and not for WCAG22.
    "WCAG22": frozenset({"wcag2a", "wcag2aa", "wcag21a", "wcag21aa",
                         "wcag22aa"}),
}
_STANDARD_VERSION = {
    "SECTION508": "Section 508 (2017 Refresh, 36 CFR 1194 App. A)",
    "EN301549": "EN 301 549 V3.2.1",
    "WCAG22": "WCAG 2.2 AA",
}
# 508 incorporates WCAG by reference and does NOT renumber, so the
# criterion stays the WCAG SC and the binding clause rides provenance.
_SECTION508_BINDING_CLAUSE = "E205.4"


def engine_rule_tags(axe_path: Path | None = None) -> dict:
    """{engine_rule_id: [tags]} parsed from the vendored engine."""
    src = (axe_path or _AXE_PATH).read_text(encoding="utf-8",
                                            errors="replace")
    starts = [m.start() for m in re.finditer(r'\{id:"', src)]
    out: dict = {}
    for i, pos in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else pos + 4000
        block = src[pos:end]
        name = re.match(r'\{id:"([^"]+)"', block).group(1)
        tags = re.search(r"tags:\[([^\]]*)\]", block)
        if tags and name not in out:
            out[name] = re.findall(r'"([^"]+)"', tags.group(1))
    return out


def clause_for(standard: str, criterion: str) -> str:
    """The standard's own numbering for a WCAG success criterion."""
    if standard == "EN301549":
        return f"9.{criterion}"          # EN's web chapter is 9.<SC>
    return criterion                      # 508 does not renumber


def derive_candidates(session, standard: str,
                      release_id: int | None = None) -> dict:
    """Candidate maps for one standard over the ACTIVE rule set.

    Returns ``{candidates: [...], requires_authoring: [...],
    out_of_scope: [...], agreements: int, disagreements: [...]}``.
    Nothing is written; every list is reviewable before a map set is
    authored from it.
    """
    if standard not in _SCOPE:
        raise ValueError(f"no derivation rule for standard {standard!r}")
    tags_by_engine_rule = engine_rule_tags()
    scope = _SCOPE[standard]

    where = "v.state = 'ACTIVE'"
    params: dict = {}
    if release_id is not None:
        where = ("(v.rule_id, v.version) IN (SELECT rule_id, rule_version "
                 "FROM s5_catalogue_release_members WHERE release_id = :r)")
        params["r"] = release_id
    rows = session.execute(text(f"""
        SELECT v.rule_id, v.version, m.criterion, m.level, b.engine_rule_id
        FROM s5_rule_versions v
        JOIN s5_standard_maps m
          ON m.rule_id = v.rule_id AND m.rule_version = v.version
         AND m.standard = 'WCAG22'
        LEFT JOIN s5_engine_bindings b
          ON b.rule_id = v.rule_id AND b.rule_version = v.version
         AND b.engine = 'axe-core'
        WHERE {where}
        ORDER BY v.rule_id, m.criterion
    """), params).fetchall()

    # Phase 5 Part 1 (LLD §b): where the WCAG22 ACTIVE set carries a
    # ratified catalogue, the A/AA gate reads the CRITERION's level from
    # it, never the map's rule-derived one. Without a catalogue the map
    # level is all we hold, and the gate says so via level_source.
    from primeqa.knowledge.criterion_catalogue import catalogue_denominator
    _cat = catalogue_denominator(session, "WCAG22")
    cat_levels = {c["criterion"]: c["level"]
                  for c in (_cat or {}).get("criteria", [])}

    candidates, needs_authoring, out_of_scope, disagreements = [], [], [], []
    agreements = 0
    for rule_id, version, criterion, map_level, engine_rule_id in rows:
        level = cat_levels.get(criterion, map_level)
        level_source = "catalogue" if criterion in cat_levels else "map"
        tags = tags_by_engine_rule.get(engine_rule_id or "", [])
        version_tags = {t for t in tags if t.startswith("wcag2")
                        and not re.fullmatch(r"wcag\d{3,4}", t)}
        if not version_tags:
            # No engine version tag: either a Plimsol-authored mapping
            # (the heading/landmark rules axe tags best-practice) or an
            # unbound rule. Derivation cannot place it — a human must.
            needs_authoring.append({
                "rule_id": rule_id, "version": version,
                "criterion": criterion, "level": level,
                "engine_rule_id": engine_rule_id,
                "reason": "engine carries no WCAG version tag for this "
                          "rule; scope membership is a human judgment"})
            continue
        if (level or "").upper() not in ("A", "AA"):
            out_of_scope.append({
                "rule_id": rule_id, "criterion": criterion,
                "level": level,
                "level_source": level_source,
                "reason": f"level {level!r} is outside {standard}'s bound "
                          "conformance level (A + AA only)"})
            continue
        if not (version_tags & scope):
            out_of_scope.append({
                "rule_id": rule_id, "criterion": criterion,
                "engine_version_tags": sorted(version_tags),
                "reason": f"criterion is outside {standard}'s bound WCAG "
                          "version — NO map (renders NOT COVERED, never a "
                          "pass)"})
            continue
        clause = clause_for(standard, criterion)
        prov = {"origin": "derived",
                "from": "WCAG22 map + engine version tag",
                "level_source": level_source,
                "engine_rule_id": engine_rule_id,
                "engine_version_tags": sorted(version_tags),
                "standard_version": _STANDARD_VERSION[standard]}
        if standard == "SECTION508":
            prov["binding_clause"] = _SECTION508_BINDING_CLAUSE
            prov["binding_note"] = (
                "508 incorporates WCAG 2.0 A+AA by reference and does not "
                "renumber; axe's section508.22.x tags are the SUPERSEDED "
                "pre-2017 numbering and are deliberately not used")
        if standard == "EN301549":
            # A rule commonly carries SEVERAL EN clause tags (one per
            # criterion it covers), so the cross-check is MEMBERSHIP of
            # the derived clause in that set — never equality against an
            # arbitrarily-chosen first tag, which would manufacture
            # disagreements that do not exist.
            engine_clauses = sorted(
                t[len("EN-"):] for t in tags
                if t.startswith("EN-") and t != "EN-301-549")
            if not engine_clauses:
                prov["engine_cross_check"] = "no EN clause tag on this rule"
            elif clause in engine_clauses:
                agreements += 1
                prov["origin"] = "engine_corroborated"
                prov["engine_cross_check"] = f"agrees ({clause})"
            else:
                disagreements.append({
                    "rule_id": rule_id, "criterion": criterion,
                    "derived_clause": clause,
                    "engine_clauses": engine_clauses})
                prov["engine_cross_check"] = (
                    f"DISAGREES: derivation says {clause}, engine tags "
                    f"{engine_clauses} — surfaced for the reviewer, "
                    "Plimsol's ratified value wins")
        candidates.append({"rule_id": rule_id, "version": version,
                           "criterion": clause, "wcag_criterion": criterion,
                           "level": level, "provenance": prov})
    return {"standard": standard,
            "standard_version": _STANDARD_VERSION[standard],
            "candidates": candidates,
            "requires_authoring": needs_authoring,
            "out_of_scope": out_of_scope,
            "agreements": agreements,
            "disagreements": disagreements}
