# VERIFICATION Phase 4 — multi-standard views

Executed 2026-08-31 on scratch (`plimsol_3a3`, and a FRESH
`plimsol_p4` for the migration transcript), plus ONE read-only
production read: P-1's stored verdicts were copied out to render the
three standards over the real run. **No production row was written and
no Railway act was performed.**

Re-runnable: `tests/unit/test_representation/test_phase4_standards.py`
(pure, merge-gated); `tests/integration/test_phase4_standards.py`
(scratch); `tests/browser_worker/test_run_set_determinism.py`
(`SPIKE_BROWSER=1`).

---

## What landed

| file | change |
|---|---|
| `migrations/065_s5_standard_map_sets.sql` | `s5_standard_map_sets` + `map_set_id`/`provenance` on `s5_standard_maps`; single-ACTIVE-per-standard partial index; retrofits the pre-existing WCAG22 maps into their own set; widens map uniqueness to be PER SET |
| `migrations/066_s5_phase4_standards.sql` | lifecycle REPLAY (the 064 precedent) of the ACC-05 pair, catalogue release 3, and both ratified map sets |
| `knowledge/standard_derivation.py` | candidate derivation + engine cross-check; the scope table; the refusal of axe's superseded 508 tags |
| `knowledge/rule_lifecycle.py` | `create_map_set` / `transition_map_set`; `add_standard_map` gains the map-set authoring gate |
| `knowledge/rule_registry.py` | the aspirational docstring corrected (GO item 2) |
| `interpretation/standard_view.py` | the projection read, roll-up, coverage split, honesty header |

## a. Migration transcript (fresh database)

`plimsol_p4` built from 001 → 062 → 063 → 064 (baseline: 72 ACTIVE
rules, releases 1–2), then 065 and 066:

- both apply clean; **both re-apply clean** (idempotent — the only output
  is `NOTICE … already exists, skipping`);
- read-back: **74 ACTIVE rules**, releases **1,2,3**, release 3 carries
  **74 members**, three ACTIVE map sets
  (WCAG22 80 maps / EN301549 77 / SECTION508 75);
- **0 duplicate rows after applying twice**;
- ACC-05 pair WCAG22 maps: **0**;
- single-ACTIVE partial index present and enforced (§c);
- D-459 guard green (these are numbered SQL migrations, not alembic;
  the guard is unaffected and still passes in the unit suite).

## b. NO-RE-EXECUTION PROOF — empirical, not argued

Fingerprints captured around a real map-set landing, removal and
re-landing:

```
A  (both map sets ACTIVE)  claims=2831 hash=0a682fd3…  verdicts=4689 hash=0fd5630e…  run_set=74 hash=879fc38d…
B  (map sets REMOVED)      claims=2831 hash=0a682fd3…  verdicts=4689 hash=0fd5630e…  run_set=74 hash=879fc38d…
C  (map sets RE-LANDED)    claims=2831 hash=0a682fd3…  verdicts=4689 hash=0fd5630e…  run_set=74 hash=879fc38d…
A == B == C : True
```

Landing or removing a standard projection changes **nothing**
downstream: 2,831 claim identities, 4,689 verdict rows and release 3's
74-id engine run set are byte-identical throughout. That is §a's four
legs demonstrated rather than asserted.

## c. Map-set lifecycle

- authoring is legal in **DRAFT** and refused from **REVIEW** onward:
  `map authoring requires the SET in DRAFT; map set N is ACTIVE —
  content is frozen from REVIEW onward, exactly as for rule versions`;
- ratification recorded with a **real actor**: `reviewed_by=7`,
  `reviewed_at` set, `content_hash` frozen over the set's recorded maps
  at APPROVED;
- **single-ACTIVE enforced at DB level** — a second ACTIVE row for the
  same standard is refused by
  `s5_standard_map_sets_single_active` on both INSERT and UPDATE
  (`UniqueViolation`);
- **rule versions UNCHANGED throughout**: `MAX(version) = 1`, rules at
  `version > 1` = **0**. No v2 was cut to add a projection — the whole
  point of the map-set lifecycle.

**A schema bug the tests caught, and the fix.** The original
`s5_standard_maps_unique (rule_id, rule_version, standard, criterion)`
predates map sets and would have forbidden a SECOND version of a
standard's projection from asserting the same clause — blocking exactly
the "EN V3.2.1 and a future EN version coexist" property the map-set
lifecycle exists to provide. Migration 065 now widens it to
`s5_standard_maps_unique_in_set (map_set_id, rule_id, rule_version,
standard, criterion)` after the backfill, and 066's 152 `ON CONFLICT`
targets were retargeted to match.

## d. Derivation + cross-check

Against release 3 (74 rules):

| | EN 301 549 V3.2.1 | Section 508 (2017 Refresh) |
|---|---|---|
| derived candidates | 69 | 67 |
| engine agreements | **69 of 69** | n/a (508 tags refused) |
| **disagreements** | **0** | **0** |
| requires human authoring | 6 | 6 |
| out of scope (no map) | 5 | 7 |

**Disagreements: zero — and the check demonstrably ran** (69 EN
candidates each matched against the rule's own `EN-9.x.y.z` tags).

*An earlier run of this check reported 5 disagreements. Those were an
artifact of my cross-check, not real: a rule commonly carries SEVERAL EN
clause tags (`area-alt` carries both `EN-9.2.4.4` and `EN-9.4.1.2`), and
the first implementation compared the derived clause against an
arbitrarily-chosen first tag. The check now tests MEMBERSHIP of the
derived clause in the rule's tag set. Recorded because a false
disagreement would have been reported to a reviewer as real.*

The 6 requiring authoring are exactly the Plimsol-authored
heading/landmark rules (PLM-A11Y-069…072 at criteria 1.3.1 and 2.4.1);
axe tags them best-practice and carries no WCAG version tag, so
derivation cannot place them. They were authored into both sets with the
064 registry-authority judgment recorded in provenance — which matters,
because PLM-A11Y-071 carries P-1's two FAILs (§f).

Out-of-scope cases are refused a map rather than given a false one:
WCAG 2.1 criteria (1.3.4, 1.4.12) are outside 508's WCAG 2.0 binding,
and AAA-level maps are outside both standards' A+AA scope.

## e. ACC-05 closure — and the interaction that matters

- **PLM-A11Y-073** ← `duplicate-id`, **PLM-A11Y-074** ←
  `duplicate-id-active`, created at **next-available ids** (never
  reserved — the D-462 law) through the real lifecycle
  DRAFT→REVIEW→APPROVED→VERSIONED→ACTIVE, then **catalogue release 3**
  (74 members).
- Mapped **EN 9.4.1.1 and 508 4.1.1 only — WCAG22 maps: 0**, because
  4.1.1 Parsing is removed in WCAG 2.2 and live in the WCAG 2.0/2.1 that
  EN and 508 bind. **This is the first live proof that a rule can be in
  scope for one standard and out of scope for another.**
- The deprecation rationale rides the map provenance verbatim
  (GO item 1): *"engine deprecation is the engine's lifecycle signal
  about its own rule, not a normative statement about the criterion;
  4.1.1 is live in WCAG 2.0/2.1 and required by the standards binding
  them."*
- **The interaction, proven with a real browser.** Both rules are
  `enabled:false` in axe 4.13.0:

```
duplicate-id           in release-3 run set: True | REPORTED under the pin: True | WITHOUT the pin: False
duplicate-id-active    in release-3 run set: True | REPORTED under the pin: True | WITHOUT the pin: False
run set size=74; engine reported 74 rules under the pin, 89 under the engine default
```

  They execute **only** because D-466 pins an explicit run set. Without
  that fix these two rules would have produced precisely the silent
  passes it exists to prevent.
- **ACC-05 now reads 15 of 15 CLOSED.**

## f. The three standards over P-1's run

P-1's stored verdicts (**0 PASS / 142 NOT_DETERMINED / 2 FAIL**) copied
read-only from production and rendered:

| | WCAG 2.2 AA | EN 301 549 V3.2.1 | Section 508 (2017) |
|---|---|---|---|
| AUTOMATED | 26 | 22 | 20 |
| NOT_COVERED | 2 | 2 | 0 |
| criterion NOT_DETERMINED | 24 | 19 | 17 |
| criterion FAIL | 2 | 2 | 2 |
| **criteria reading PASS** | **0** | **0** | **0** |

**Nothing reads PASS — the roll-up rule's first real test passes.** A
criterion never passes on unattested parts, so a run holding no PASS
verdict yields no passing criterion, in any standard.

The SAME underlying failure surfaces in all three, under each standard's
own numbering — the phase's thesis, demonstrated:

```
WCAG22      FAIL 1.3.1, 2.4.1
SECTION508  FAIL 1.3.1, 2.4.1      (508 incorporates WCAG and does not renumber)
EN301549    FAIL 9.1.3.1, 9.2.4.1  (EN renumbers to 9.<SC>)
```

NOT_COVERED criteria are present with their coverage class, not absent
(EN shows `9.1.3.4`, `9.2.5.3`). The honesty header carries the
projection and the run: map set id + content hash, standard version,
engine + version, catalogue release, and the engine run-set hash —
`run_set_size` reads **None** for P-1, correctly, because P-1's manifest
predates the run-set pin.

*A double-count bug the render caught:* the first version listed both
`1.3.1` and `9.1.3.1` for EN, because the census emits WCAG numbering
while the map set stores EN clauses. The denominator is now translated
into the standard's own numbering before the union.

## g. Suites

- **Unit: 4,904 passed** (was 4,896; +8).
- **DB-real: 27 passed** across all six Phase suites.
- **Browser-gated: 3 passed.**

### A verification gap in the PREVIOUS slice, found here and fixed

Running the full DB-real set exposed **4 failures that were not Phase 4's
doing**: the Phase 7 and 3A-4 integration suites had been RED since the
verdict-semantics merge (`3ba0c9f`). Their planted observations predate
attestation, so under the corrected semantics every non-violation
decides `legacy_unattested` → the comparator reads
`NOT_COMPARABLE (indeterminate_side)` and no verdict can be PASS. **The
product behaviour is correct — the tests encoded pre-attestation
assumptions.** I did not re-run those two suites after that merge (I ran
only `test_prod_vault` and the browser test), so the staleness went
unnoticed until now. Both suites' planted observations now carry
`run_set` / `passes_ids` / `inapplicable_ids`, so they test the
comparator and the fan-out rather than the superseded verdict rules.

## Residual, stated plainly

- **The NOT_COVERED denominator is an engine-census LOWER BOUND.** It is
  derived from the criteria the vendored engine knows about that fall in
  the standard's bound WCAG version; a criterion no engine rule
  addresses at all cannot be shown as NOT_COVERED. `standard_view`
  returns `denominator_complete: false` with the reason, and the header
  carries it, so no report can imply full-scope coverage. A ratified
  criterion catalogue is the durable fix (FIX PLAN).
- **The WCAG `level` on each map is rule-derived, not per-criterion.**
  The 063 seed propagated the axe RULE's level tag to every criterion of
  that rule, so a rule covering both an A and a AAA criterion records
  both as A. The A+AA scope gate is applied honestly against the data we
  hold; correcting per-criterion levels is a catalogue content change
  with its own review (FIX PLAN).
- The retrofitted WCAG22 map set carries no `content_hash` (it was
  inserted by migration rather than passing through APPROVED). Harmless
  — the header renders it empty rather than claiming one.
- Non-goals untouched: no UI, no re-scan, no custom standards, no Mode B,
  no seventh coverage.
