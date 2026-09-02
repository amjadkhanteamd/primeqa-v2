"""S6 standard views — one run, many standards (LLD Phase 4 §a/§d/§e).

A standard view is a READ-TIME PROJECTION over stored verdicts. It
recomputes nothing: `s6_ui_verdicts` rows carry `plimsol_rule_id`, and
this module joins rule -> the standard's ACTIVE map set -> that
standard's clauses, then rolls the verdicts up per criterion. No claim
is enumerated, no scan is run, no verdict is written.

Two axes, never conflated (SF-14):
  * COVERAGE  — "can we test this criterion?"  AUTOMATED / HUMAN_ONLY /
    NOT_COVERED, computed from the bound rules alone.
  * DETERMINATION — "did we, in this run?" the verdict roll-up.
A criterion can be COVERED and UNDETERMINED; that is not a failure of
the report, it is the honest state after D-466.

Roll-up is WORST-WINS and attestation-respecting: FAIL if any
contributing verdict FAILs; else NEEDS_HUMAN; else NOT_DETERMINED; else
PASS only when every contributing verdict is an attested PASS. A
criterion never passes on unattested parts — D-466's law lifted from
rule grain to clause grain.
"""
from __future__ import annotations

import re
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

AUTOMATED = "AUTOMATED"
HUMAN_ONLY = "HUMAN_ONLY"
NOT_COVERED = "NOT_COVERED"

_RANK = {"FAIL": 4, "NEEDS_HUMAN": 3, "NOT_DETERMINED": 2, "PASS": 1}


class StandardViewError(ValueError):
    """A refused projection — the message names the cause."""


def active_map_set(session: Session, standard: str) -> dict:
    row = session.execute(text("""
        SELECT id, standard_version, content_hash, activated_at, provenance
        FROM s5_standard_map_sets
        WHERE standard = :s AND state = 'ACTIVE'
    """), {"s": standard}).fetchone()
    if row is None:
        raise StandardViewError(
            f"no ACTIVE map set for {standard!r} — a standard renders only "
            "through a ratified projection")
    return {"map_set_id": row[0], "standard_version": row[1],
            "content_hash": (row[2] or "").strip() or None,
            "activated_at": row[3], "provenance": row[4]}


def bound_criteria(standard: str) -> dict:
    """The standard's bound criterion DENOMINATOR, so a criterion with no
    rule can render NOT_COVERED rather than being silently absent.

    HONESTY: this is derived from the vendored engine's tag census — the
    criteria the ENGINE knows about that fall inside the standard's bound
    WCAG version. It is therefore a LOWER BOUND on the standard's true
    scope: a criterion no engine rule addresses at all is not in this
    denominator and cannot be shown as NOT_COVERED. The return value
    says so, and callers must surface it. A ratified criterion catalogue
    is the durable fix (FIX PLAN, 2026-08-28).
    """
    from primeqa.knowledge.standard_derivation import _SCOPE, engine_rule_tags

    if standard not in _SCOPE:
        raise StandardViewError(f"no bound scope defined for {standard!r}")
    scope = _SCOPE[standard]
    criteria: set = set()
    for tags in engine_rule_tags().values():
        version_tags = {t for t in tags if t.startswith("wcag2")
                        and not re.fullmatch(r"wcag\d{3,4}", t)}
        if not (version_tags & scope):
            continue
        for t in tags:
            m = re.fullmatch(r"wcag(\d)(\d)(\d+)", t)
            if m:
                criteria.add(f"{m.group(1)}.{m.group(2)}.{m.group(3)}")
    return {"criteria": sorted(criteria),
            "provenance": "engine_tag_census",
            "complete": False,
            "limitation": "lower bound: criteria no engine rule addresses "
                          "are absent from this denominator"}


def _clause_of(standard: str, wcag_criterion: str) -> str:
    from primeqa.knowledge.standard_derivation import clause_for
    return clause_for(standard, wcag_criterion)


def standard_view(session: Session, *, standard: str,
                  claim_set_id: Optional[UUID] = None,
                  job_id: Optional[UUID] = None) -> dict:
    """Render one run under one standard. Exactly one of claim_set_id /
    job_id selects the verdict scope."""
    if (claim_set_id is None) == (job_id is None):
        raise StandardViewError(
            "pass exactly one of claim_set_id / job_id")
    ms = active_map_set(session, standard)

    maps = session.execute(text("""
        SELECT m.rule_id, m.criterion, m.level, v.name,
               v.automation_capability, m.provenance
        FROM s5_standard_maps m
        JOIN s5_rule_versions v
          ON v.rule_id = m.rule_id AND v.version = m.rule_version
        WHERE m.map_set_id = :ms
        ORDER BY m.criterion, m.rule_id
    """), {"ms": ms["map_set_id"]}).fetchall()

    where = "v.claim_set_id = :k" if claim_set_id else "v.job_id = :k"
    key = str(claim_set_id or job_id)
    verdicts = session.execute(text(f"""
        SELECT v.plimsol_rule_id, v.verdict, v.verdict_basis->>'reason',
               v.surface_key, v.test_id, v.job_id, v.ownership
        FROM s6_ui_verdicts v WHERE {where}
        ORDER BY v.plimsol_rule_id, v.surface_key
    """), {"k": key}).fetchall()
    by_rule: dict = {}
    for r in verdicts:
        by_rule.setdefault(r[0], []).append(
            {"verdict": r[1], "reason": r[2], "surface_key": r[3],
             "test_id": str(r[4]), "job_id": str(r[5]), "ownership": r[6]})

    rules_by_criterion: dict = {}
    for rule_id, criterion, level, name, capability, prov in maps:
        rules_by_criterion.setdefault(criterion, []).append(
            {"rule_id": rule_id, "name": name, "capability": capability,
             "level": level, "map_provenance": prov})

    # THE DENOMINATOR (Phase 5 Part 1, LLD §c). A standard whose ACTIVE
    # set carries a ratified criterion catalogue counts against THAT —
    # the standard's true bound scope, "N of M" sayable, every criterion
    # in scope with no rule rendered NOT_COVERED. A standard without one
    # keeps the engine-census lower bound and keeps saying so. Per
    # standard, never global.
    from primeqa.knowledge.criterion_catalogue import catalogue_denominator

    catalogue = catalogue_denominator(session, standard)
    if catalogue is not None:
        cat_by = {c["criterion"]: c for c in catalogue["criteria"]}
        bound = set(cat_by)
        denom_header = {
            "denominator_provenance": catalogue["provenance"],
            "denominator_complete": True,
            "denominator_limitation": None,
            "catalogue_set_id": catalogue["set_id"],
            "catalogue_rows_hash": catalogue["catalogue_rows_hash"],
            "catalogue_artifacts": catalogue["catalogue_artifacts"],
        }
    else:
        denom = bound_criteria(standard)
        cat_by = {}
        # The census yields WCAG success-criterion numbers, while the map
        # set stores the standard's own clauses (EN renumbers to 9.<SC>;
        # WCAG22 and 508 do not). Translating before the union is what
        # stops one criterion appearing twice — once as 1.3.1 and once as
        # 9.1.3.1.
        bound = {_clause_of(standard, c) for c in denom["criteria"]}
        denom_header = {
            "denominator_provenance": denom["provenance"],
            "denominator_complete": denom["complete"],
            "denominator_limitation": denom["limitation"],
        }
    all_criteria = sorted(bound | set(rules_by_criterion))

    rows = []
    for criterion in all_criteria:
        contributing = rules_by_criterion.get(criterion, [])
        cat = cat_by.get(criterion)
        # LEVEL is read from the catalogue, never from the map, wherever a
        # catalogue exists (LLD §b). A map whose criterion the ratified
        # catalogue does not know is an ORPHAN — surfaced, never dropped.
        if cat is not None:
            level, level_source, title = cat["level"], "catalogue", cat["title"]
        elif contributing:
            level, level_source, title = contributing[0]["level"], "map", None
        else:
            level, level_source, title = None, None, None
        orphan = catalogue is not None and cat is None
        # The sets are A+AA projections: a criterion the catalogue places
        # at AAA is OUTSIDE the bound scope — it renders at its true level
        # and is never counted in "N of M". Under the census fallback the
        # scope tags already exclude AAA, so everything is in scope.
        in_scope = (level in ("A", "AA")) if cat is not None else not orphan
        if not contributing:
            rows.append({"criterion": criterion, "title": title,
                         "coverage": NOT_COVERED, "contributing_rules": [],
                         "contributing_verdicts": [],
                         "criterion_verdict": None, "level": level,
                         "level_source": level_source,
                         "in_scope": in_scope, "orphan": orphan})
            continue
        caps = {c["capability"] for c in contributing}
        coverage = AUTOMATED if "AUTO" in caps else HUMAN_ONLY
        cv = []
        for c in contributing:
            cv.extend(by_rule.get(c["rule_id"], []))
        roll = None
        if cv:
            roll = max((x["verdict"] for x in cv),
                       key=lambda v: _RANK.get(v, 0))
        rows.append({
            "criterion": criterion,
            "title": title,
            "level": level,
            "level_source": level_source,
            "in_scope": in_scope,
            "orphan": orphan,
            "coverage": coverage,
            "contributing_rules": contributing,
            "contributing_verdicts": cv,
            "criterion_verdict": roll,
        })

    cov_counts: dict = {}
    verdict_counts: dict = {}
    out_counts: dict = {}
    for r in rows:
        k = r["criterion_verdict"] or "NO_VERDICT"
        if r["in_scope"]:
            cov_counts[r["coverage"]] = cov_counts.get(r["coverage"], 0) + 1
            verdict_counts[k] = verdict_counts.get(k, 0) + 1
        elif r["contributing_rules"]:
            out_counts[k] = out_counts.get(k, 0) + 1
    in_scope_rows = [r for r in rows if r["in_scope"]]
    denominator = {
        "size": len(in_scope_rows),                       # M
        "covered": sum(1 for r in in_scope_rows
                       if r["coverage"] != NOT_COVERED),  # N
        "complete": denom_header["denominator_complete"],
    }

    return {
        # the honesty header: any rendered report names the exact
        # projection, rule set, engine run AND criterion list it was
        # produced from
        "header": {
            "standard": standard,
            "standard_version": ms["standard_version"],
            "map_set_id": ms["map_set_id"],
            "map_set_content_hash": ms["content_hash"],
            **denom_header,
            **_run_header(session, claim_set_id, job_id),
        },
        "denominator": denominator,
        "coverage_counts": cov_counts,
        "criterion_verdict_counts": verdict_counts,
        "out_of_scope": {
            # only rows with something to show: a rule mapped to a criterion
            # the catalogue places outside the A+AA scope (or an orphan).
            # The bare AAA remainder is a count, not 26 empty rows.
            "criteria": [r for r in rows
                         if not r["in_scope"] and r["contributing_rules"]],
            "uncovered_count": sum(1 for r in rows
                                   if not r["in_scope"]
                                   and not r["contributing_rules"]),
            "verdict_counts": out_counts,
            "note": "criteria the catalogue places outside the set's A+AA "
                    "bound scope (or unknown to it); rendered at their true "
                    "level, never counted in the denominator",
        },
        "criteria": rows,
    }


def _run_header(session: Session, claim_set_id, job_id) -> dict:
    """Engine, run set, catalogue release — so 'conforms to 9.1.1.1' is
    always readable as 'under THIS engine run and THIS rule set'."""
    if job_id is not None:
        row = session.execute(text("""
            SELECT p.engine, p.engine_version, p.bindings_hash,
                   m.payload->'pins'
            FROM s6_ui_processing_runs p
            JOIN s4_ui_run_manifests m ON m.id = p.manifest_id
            WHERE p.job_id = :j"""), {"j": str(job_id)}).fetchone()
    else:
        row = session.execute(text("""
            SELECT p.engine, p.engine_version, p.bindings_hash,
                   m.payload->'pins'
            FROM s6_ui_processing_runs p
            JOIN s4_ui_run_manifests m ON m.id = p.manifest_id
            WHERE p.claim_set_id = :c
            ORDER BY p.processed_at DESC LIMIT 1"""),
            {"c": str(claim_set_id)}).fetchone()
    if row is None:
        return {"run": "no processing run found for this scope"}
    pins = row[3] or {}
    return {"engine": row[0], "engine_version": row[1],
            "bindings_hash": row[2],
            "catalogue_release_id": pins.get("catalogue_release_id"),
            "engine_run_set_hash": pins.get("engine_run_set_hash"),
            "engine_run_set_size": len(pins.get("engine_run_set") or [])
            if pins.get("engine_run_set") is not None else None}
