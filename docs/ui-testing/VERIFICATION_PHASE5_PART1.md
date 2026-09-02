# VERIFICATION — Phase 5 Part 1: the criterion catalogue

Executed 2026-09-02 on scratch (`plimsol_3a3`, and a FRESH `plimsol_p5`
for the migration transcript), plus ONE read-only production export:
B-1's run (manifest, job, 2 results, 148 verdicts, processing run,
claim set) copied into scratch so the views render over the real
baseline. **No production row was written and no Railway act was
performed.** Branch `phase-5-authoring` (base `3800bd5`); merge gated.

Re-runnable: `tests/unit/test_representation/test_phase5_catalogue.py`
(pure — parsers over the pinned bytes; merge-gated);
`tests/integration/test_phase5_catalogue.py` (scratch, 062–067 + B-1).

---

## What landed

| file | change |
|---|---|
| `primeqa/knowledge/vendor/criteria/` | the five normative documents AK obtained 2026-09-01, vendored unmodified (3.8 MB) |
| `primeqa/knowledge/criterion_catalogue.py` | the pins; four parsers (WCAG 2.0 XHTML, WCAG 2.1/2.2 ReSpec, EN 301 549 PDF clause 9, Section 508 E205.4); `ingest_catalogue`, `level_mismatch_report`, `backfill_map_levels`, `catalogue_denominator`; `rows_hash` reproducibility digest |
| `migrations/067_s5_criterion_catalogue.sql` | `revision` on standard sets + the unique widened to `(standard, version, revision)`; `s5_criteria`; the five artifact pins; the LIFECYCLE REPLAY (revision-2 sets ACTIVE, revision 1 RETIRED, 174 criteria, 230 maps) |
| `primeqa/knowledge/rule_lifecycle.py` | `create_map_set(revision=…)`; APPROVED hashes **catalogue + maps under one digest**; new `remove_standard_map` (DRAFT-gated, audited, provenance-recorded) |
| `primeqa/interpretation/standard_view.py` | denominator = the ACTIVE set's ratified catalogue when present (census fallback otherwise, per standard); level from the catalogue never the map; A+AA scope gate; `denominator {size, covered}`; out-of-scope rows at true level |
| `primeqa/knowledge/standard_derivation.py` | the A/AA derivation gate reads the catalogue level (`level_source` recorded) |
| `requirements.txt` | `pypdf==6.16.2` (the EN extractor; its version rides the set's provenance) |

## a. The pins

Five rows of `kind='criterion_catalogue'` in `s5_artifacts`, each with
`sha256`, `repo_path`, `source_url`, `byte_size`, `retrieved_at =
2026-09-01`. Generated from the module's table, never hand-typed; the
DB-real test asserts **DB == module == vendored bytes** for all five.

| artifact | sha256 (12) | bytes | source |
|---|---|---|---|
| wcag 2.0 | `3a438f1a4aa7` | 191,633 | https://www.w3.org/TR/WCAG20/ |
| wcag 2.1 | `233ac31974ce` | 476,496 | https://www.w3.org/TR/WCAG21/ |
| wcag 2.2 | `6e3c5fe39725` | 512,457 | https://www.w3.org/TR/WCAG22/ |
| en301549 V3.2.1 | `1eee3a1841a9` | 2,285,361 | ETSI deliverable PDF |
| section508 2017-Refresh | `0ca015e924da` | 493,043 | https://www.access-board.gov/ict/ |

A tampered byte (one appended newline) is refused before parsing:
`refusing to parse an unpinned document`.

## b. The ingest — counts, all the published ones

The parsers fail loudly and never guess: a WCAG 2.2 copy with one
conformance level stripped fails with `Success Criterion 2.5.8 … no
conformance level and is not marked removed`.

| standard | set (scratch / fresh) | rows | levels | in scope (A+AA) | recorded, outside the denominator |
|---|---|---|---|---|---|
| WCAG 2.2 AA | 18 / 4 | **86** | 31 A / 24 AA / 31 AAA | **55** | 4.1.1 "(Obsolete and removed)" recorded as removed |
| EN 301 549 V3.2.1 | 19 / 5 | **50** | 30 A / 20 AA | **50** | 5 Void clauses (9.1.4.6–9, 9.2.1.3); 9.5 AAA (informative); 9.6 conformance requirements. Annex C cross-check: agrees |
| Section 508 (2017) | 20 / 6 | **38** | 25 A / 13 AA | **38** | E205.4 sentence verbatim; IBR 702.10.1; 4 non-Web exceptions recorded |

EN levels are read from the pinned WCAG 2.1 artifact for the SC each
clause binds (EN states none); every clause number aligns with its
bound SC (`9.<SC>`), asserted. 508's catalogue is WCAG 2.0's A+AA rows,
assembled only after the incorporation sentence is found.

**Reproducible by hash.** `stored_rows_hash(set) == catalogue_for(std).rows_hash()`
on scratch AND on the fresh replay: WCAG22 `5646d23b6177…`, EN
`657646f9dae8…`, 508 `631de41dbbef…`. A second ingest into a set is
refused; an ingest into a non-DRAFT set is refused; a WCAG22 ingest into
a SECTION508 set is refused.

## c. The level-mismatch report — loud, preserved

Run against the copied revision-1 maps before any backfill; the report
is stored on each set's provenance.

| set | maps | agree | mismatch | orphan |
|---|---|---|---|---|
| WCAG22 | 80 | 79 | **PLM-A11Y-059 → 2.1.3: map A, catalogue AAA** (`scrollable-region-focusable`, the Phase 4 residue) — backfilled | 0 |
| EN301549 | 77 | 76 | 0 | **PLM-A11Y-059 → 9.2.1.3**: a Void clause; not a criterion |
| SECTION508 | 75 | 74 | 0 | **PLM-A11Y-059 → 2.1.3**: AAA in WCAG 2.0, not incorporated by E205.4 |

**Two orphans withdrawn** (AK GO 2026-09-02) through the new
`remove_standard_map` — DRAFT-gated, `s5.rule.unmap_standard` audited,
recorded under `provenance.withdrawn_maps` with the reason. The rule
keeps its valid 2.1.1 / 9.2.1.1 map in every set. Post-backfill: 0
mismatches, 0 orphans on all three ACTIVE sets.

**The 1.4.6 residue, precisely.** It did NOT surface as a mismatch: the
revision-1 WCAG22 set already carried 1.4.6 (and 2.2.4, 2.4.9, 3.2.5) at
AAA. The level was right; what Phase 4 lacked was a **scope gate at
render** — every mapped criterion counted, AAA included. That gate now
exists (§e).

## d. Ratification — the lifecycle, a real actor, one hash

Each revision-2 set: DRAFT → REVIEW → APPROVED → ACTIVE via
`rule_lifecycle.transition_map_set`, `actor_user_id=1` (AK's GO recorded
verbatim under `provenance.ratification.review_act`). ACTIVE retired
revision 1 atomically; the single-ACTIVE index holds (1 per standard).
APPROVED's `content_hash` now covers **maps + criteria**; the DB-real test
recomputes it exactly as the lifecycle does and asserts it differs from a
maps-only digest: WCAG22 `31a1ccc133b4…`, EN `98bbf44c9c83…`, 508
`2277bc8f429f…`. Zero rule versions touched (`MAX(version) = 1`).

## e. `standard_view` over B-1 — "N of M", for the first time

Headers now carry `denominator_provenance: ratified_catalogue`,
`denominator_complete: true`, `denominator_limitation: null`,
`catalogue_set_id`, `catalogue_rows_hash`, `catalogue_artifacts` — beside
the engine (axe 4.13.0), `engine_run_set_size 74`, release 3.

| standard | covered N | of M | NOT_COVERED | in-scope roll-up | FAIL criteria (in scope) |
|---|---|---|---|---|---|
| WCAG 2.2 AA | **21** | **55** | 34 | 5 PASS / **2 FAIL** / 14 ND / 34 none | 1.3.1, 2.4.1 |
| EN 301 549 | **21** | **50** | 29 | 4 PASS / 2 FAIL / 15 ND / 29 none | 9.1.3.1, 9.2.4.1 |
| Section 508 | **19** | **38** | 19 | 3 PASS / 2 FAIL / 14 ND / 19 none | 1.3.1, 2.4.1 |

- These reconcile exactly with Phase 4's AUTOMATED counts against the
  census (26 = 21 + 5 AAA-mapped; 22 = 21 + 1 orphan; 20 = 19 + 1 orphan).
  The census denominators were 28 / 24 / 20; the true ones are 55 / 50 /
  38.
- **NOT_COVERED now means bound-scope-with-no-rule.** The 34-row WCAG list
  (1.2.3, 1.2.4, 1.2.5, 1.3.2, 1.3.3, 1.3.4, 1.4.5, 1.4.10, 1.4.11,
  1.4.13, 2.1.2, 2.1.4, 2.3.1, 2.4.3, 2.4.5, 2.4.6, 2.4.7, 2.4.11, 2.5.1,
  2.5.2, 2.5.3, 2.5.4, 2.5.7, 3.2.1, 3.2.2, 3.2.3, 3.2.4, 3.2.6, 3.3.1,
  3.3.3, 3.3.4, 3.3.7, 3.3.8, 4.1.3) is the programme's first honest
  coverage gap — the set a human or a custom rule must cover.
- **B-1's 1.4.6 FAIL is OUTSIDE the AA gate, still visible, at AAA.**
  WCAG22 `out_of_scope.criteria` = {1.4.6 FAIL, 2.1.3 ND, 2.2.4 ND, 2.4.9
  PASS, 3.2.5 ND}; 26 further AAA criteria carry no rule and are a count,
  not rows. In-scope FAIL count is **2, not 3** — the run's record is
  untouched; the view stopped counting AAA inside an AA claim.
- `level_source == "catalogue"` on every rendered row; 0 orphans;
  `derive_candidates("EN301549")` reads catalogue levels and gates 2.1.3
  out as AAA with `level_source: catalogue`.

## f. Migration transcript (fresh database)

`plimsol_p5` built from 001 → 062 → 063 → 064 → 065 → 066 → 067: every
file applies clean; **067 re-applies clean** (idempotent; only
`already exists, skipping` notices). Read-back: sets 1–3 revision 1
**RETIRED**, sets 4–6 revision 2 **ACTIVE** with 86/50/38 criteria and
80/76/74 maps, 174 criteria total = 174 distinct `(set, criterion)`, 5
pins. The fresh rows reproduce the parse by hash (§b) and report 0
mismatches / 0 orphans. One transaction (`BEGIN`…`COMMIT`); the D-459
autocommit guard passes.

## g. Suites (D-468)

- **Unit: 4,914 passed** (branch base `3800bd5` — the 8 consume-contract
  tests live on `main` after the base; they re-run at merge).
- **DB-real: 41 passed** across all seven prior suites + Phase 5's seven.
- **Browser-gated: 61 passed, 11 skipped** (unaffected; run for D-468).

Two Phase 4 assertions were stale under the corrected semantics and are
updated, not worked around: `denominator_complete is False` → `is True`
with `ratified_catalogue`; the ACC-05 map count now scopes to the ACTIVE
set (maps exist per revision).

## Residual, stated plainly

- **The two standards' catalogues are A+AA by their normative scope**
  (EN 9.1–9.4; 508 E205.4). WCAG22 carries its 31 AAA rows so a rule
  mapped to one renders at its true level; EN's Table 9.1 AAA list and
  WCAG 2.0's 23 AAA criteria are not ingested. A future AAA profile is a
  new standard set, not an edit to these.
- `retrieved_at` is a date (AK's hand-off), stored as midnight UTC.
- The revision model means a projection change is a new revision of the
  same version; **nothing here versions a rule** (D-462 intact).
- Non-goals untouched: no authoring (Part 2), no UI, no re-scan, no new
  verdicts, no prod write.
