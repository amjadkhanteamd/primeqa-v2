"""Generate the S5 rule-seed fixture + seed SQL from the PINNED axe artifact.

Authoring-time tool (LLD 3A-1 §3) — run manually, never at runtime:
    venv/bin/python scripts/gen_s5_seed.py
Reads primeqa/browser_worker/vendor/axe.min.js via a local chromium
(about:blank, offline), derives the WCAG-mapped non-experimental rule set,
assigns FROZEN deterministic PLM-A11Y ids (engine rule ids sorted
lexicographically, numbered from 001), and writes:
  migrations/seeds/s5_rule_seed_axe4130_wcag22.json   (the reviewable fixture)
  migrations/063_s5_rule_seed.sql                     (idempotent seed SQL)
Re-running must reproduce the rules array identically while the artifact is
unchanged — ids never renumber (the fixture, once committed, is the frozen
record). The committed fixture's provenance additionally carries
POST-GENERATION review records (ACC-05 cross-list, collision rulings): on any
regeneration those records must be preserved/merged, never blindly
overwritten.
WCAG 2.2 note: criterion 4.1.1 (parsing) is REMOVED in WCAG 2.2; any
wcag411-tagged mapping is excluded, with the exclusion recorded in
provenance.
"""
import hashlib
import json
import re
from pathlib import Path

AXE = Path("primeqa/browser_worker/vendor/axe.min.js")
FIXTURE = Path("migrations/seeds/s5_rule_seed_axe4130_wcag22.json")
SEED_SQL = Path("migrations/063_s5_rule_seed.sql")
CRIT = re.compile(r"^wcag(\d)(\d)(\d+)$")
LEVELS = {"wcag2a": "A", "wcag21a": "A", "wcag22a": "A",
          "wcag2aa": "AA", "wcag21aa": "AA", "wcag22aa": "AA",
          "wcag2aaa": "AAA"}
_LEVEL_ORDER = {"A": 0, "AA": 1, "AAA": 2}


def derive():
    from playwright.sync_api import sync_playwright
    sha = hashlib.sha256(AXE.read_bytes()).hexdigest()
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        p = b.new_context().new_page()
        p.goto("about:blank")
        p.add_script_tag(path=str(AXE))
        rules = p.evaluate("() => axe.getRules().map(r => ({id: r.ruleId, "
                           "description: r.description, help: r.help, tags: r.tags}))")
        version = p.evaluate("() => axe.version")
        b.close()
    return sha, version, rules


def main():
    sha, version, rules = derive()
    assert version == "4.13.0", f"artifact reports {version}, expected 4.13.0"
    seed, excluded_411 = [], []
    eligible = []
    for r in rules:
        tags = r["tags"]
        if "experimental" in tags:
            continue
        crits = sorted({f"{m.group(1)}.{m.group(2)}.{m.group(3)}"
                        for m in (CRIT.match(t) for t in tags) if m})
        if "4.1.1" in crits:
            excluded_411.append(r["id"])
            crits = [c for c in crits if c != "4.1.1"]
        if not crits:
            continue          # best-practice-only (or 4.1.1-only): not seeded
        levels = sorted({LEVELS[t] for t in tags if t in LEVELS},
                        key=_LEVEL_ORDER.get)
        eligible.append((r["id"], r, crits, levels[0] if levels else None))
    eligible.sort(key=lambda e: e[0])          # deterministic: engine id order
    for n, (eid, r, crits, level) in enumerate(eligible, start=1):
        seed.append({
            "plm_id": f"PLM-A11Y-{n:03d}",
            "engine_rule_id": eid,
            "name": r["help"][:200],
            "description": r["description"],
            "automation_capability": "AUTO",
            "human_review_required": False,
            "wcag22": [{"criterion": c, "level": level} for c in crits],
        })
    fixture = {
        "derivation": {
            "engine": "axe-core", "engine_version": version,
            "artifact_sha256": sha,
            "artifact_repo_path": str(AXE),
            "method": "axe.getRules() on the pinned vendored artifact, "
                      "offline, at seed-authoring time (LLD 3A-1 §3)",
            "id_assignment": "engine rule ids sorted lexicographically, "
                             "numbered PLM-A11Y-001.. — FROZEN, never renumbered",
            "wcag411_exclusion": {
                "reason": "criterion 4.1.1 (parsing) is removed in WCAG 2.2",
                "engine_rules_affected": sorted(excluded_411)},
            "capability_note": "All seeded rules are engine-automated -> AUTO. "
                "The HUMAN_WITH_CANDIDATE population (incomplete-prone rules) "
                "arrives with the result-processor Class-3 feed as lifecycle "
                "version bumps — not fabricated at seed time.",
            "acc05_cross_list": {
                "status": "ACC-05 = the R1 automated rule list (requirements "
                          "baseline: 'ACC-05/06 automated ... rule lists stand'). "
                          "Mapped set-wide: every seeded AUTO rule. The v1 "
                          "per-item list is not present in repo-held docs; the "
                          "item-level cross-list completes when AK supplies it.",
                "mapped_rules": "ALL (automation_capability=AUTO, 100% of seed)"},
            "set_review_principle": "D3 claim_set analog: set-level human review "
                "(this fixture's PR) with per-item inspectability (line-reviewable).",
        },
        "rules": seed,
    }
    FIXTURE.write_text(json.dumps(fixture, indent=1) + "\n")

    # ---- idempotent seed SQL ----
    def q(s):
        return "'" + s.replace("'", "''") + "'"
    L = []
    L.append("-- 063: S5 rule-catalogue SEED — axe-core 4.13.0 -> WCAG 2.2 (LLD 3A-1 §3).")
    L.append("-- GENERATED by scripts/gen_s5_seed.py from "
             "migrations/seeds/s5_rule_seed_axe4130_wcag22.json — edit the")
    L.append("-- generator/fixture, never this file by hand. Idempotent (ON CONFLICT")
    L.append("-- DO NOTHING); re-run is a no-op. Seeded rows are the DECLARED bootstrap")
    L.append("-- exception: v1 direct-to-ACTIVE with seed_provenance.bootstrap=true;")
    L.append("-- the service layer refuses that state for any non-bootstrap row.")
    L.append("BEGIN;")
    L.append(f"INSERT INTO s5_artifacts (kind, name, version, sha256, repo_path, source_url, byte_size)\n"
             f"VALUES ('engine', 'axe-core', {q(version)}, {q(sha)}, {q(str(AXE))},\n"
             f"        'https://registry.npmjs.org/axe-core/-/axe-core-4.13.0.tgz', {AXE.stat().st_size})\n"
             f"ON CONFLICT (kind, name, version) DO NOTHING;")
    for r in seed:
        prov = json.dumps({"bootstrap": True, "engine_rule_id": r["engine_rule_id"],
                           "derived_from": f"axe-core {version}"})
        L.append(f"INSERT INTO s5_rules (rule_id, owner) VALUES ({q(r['plm_id'])}, 'plimsol') "
                 f"ON CONFLICT (rule_id) DO NOTHING;")
        L.append(f"INSERT INTO s5_rule_versions (rule_id, version, name, description, "
                 f"automation_capability, human_review_required, state, seed_provenance)\n"
                 f"VALUES ({q(r['plm_id'])}, 1, {q(r['name'])}, {q(r['description'])}, "
                 f"'AUTO', FALSE, 'ACTIVE', {q(prov)}::jsonb)\n"
                 f"ON CONFLICT (rule_id, version) DO NOTHING;")
        L.append(f"INSERT INTO s5_engine_bindings (rule_id, rule_version, engine, engine_version, engine_rule_id)\n"
                 f"VALUES ({q(r['plm_id'])}, 1, 'axe-core', {q(version)}, {q(r['engine_rule_id'])})\n"
                 f"ON CONFLICT (rule_id, rule_version, engine, engine_version, engine_rule_id) DO NOTHING;")
        for c in r["wcag22"]:
            lvl = q(c["level"]) if c["level"] else "NULL"
            L.append(f"INSERT INTO s5_standard_maps (rule_id, rule_version, standard, criterion, level)\n"
                     f"VALUES ({q(r['plm_id'])}, 1, 'WCAG22', {q(c['criterion'])}, {lvl})\n"
                     f"ON CONFLICT (rule_id, rule_version, standard, criterion) DO NOTHING;")
    members = [f"{r['plm_id']}:1" for r in seed]
    content_hash = hashlib.sha256("\n".join(members).encode()).hexdigest()
    L.append("-- catalogue release 1: the D-461 pin target; membership RECORDED (D-281 law)")
    L.append(f"INSERT INTO s5_catalogue_releases (id, notes, content_hash)\n"
             f"VALUES (1, 'R1 seed release: axe-core {version} -> WCAG 2.2, {len(seed)} rules', {q(content_hash)})\n"
             f"ON CONFLICT (id) DO NOTHING;")
    for r in seed:
        L.append(f"INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) "
                 f"VALUES (1, {q(r['plm_id'])}, 1) ON CONFLICT DO NOTHING;")
    L.append("SELECT setval(pg_get_serial_sequence('s5_catalogue_releases','id'), "
             "GREATEST((SELECT MAX(id) FROM s5_catalogue_releases), 1));")
    L.append("COMMIT;")
    SEED_SQL.write_text("\n".join(L) + "\n")
    print(f"fixture: {FIXTURE} ({len(seed)} rules)")
    print(f"seed sql: {SEED_SQL} ({len(L)} statements-ish)")
    print(f"artifact sha256: {sha}")
    print(f"4.1.1-excluded engine rules: {sorted(excluded_411) or 'none'}")
    print(f"release content_hash: {content_hash[:16]}…")


if __name__ == "__main__":
    main()
