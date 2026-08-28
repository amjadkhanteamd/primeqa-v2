# LLD Phase 4 — Multi-standard views: one run, three standards, no re-run

Status: DESIGN (this commit); implementation follows on its own GO.
Branch: `phase-4-standards` (from main @00efcc3).
Derives from: SDLC plan v2 §8 phase 4, SF-14 (six coverages, six is the
ceiling), D-462 ("rules are atoms, standards are maps"; the engine is
not the accessibility authority — Plimsol makes the normative claim),
D-281 (recorded membership), D-465/D-466 (attestation: nothing acquits
without positive evidence).

**The claim this phase makes: adding a standard costs zero execution.**
The same 72 rules, the same stored verdicts, projected through
additional clause maps at read time.

---

## a. Standard maps as PURE PROJECTION

**Where the projection happens: read/report time, over stored
verdicts.** `s6_ui_verdicts` rows carry `plimsol_rule_id`. A standard
view joins rule → `s5_standard_maps` → that standard's clauses and rolls
the verdicts up per criterion. Nothing upstream of the report moves.

**Proof of the no-re-execution property** — four independent legs, each
checkable in the code as it stands today:

1. **Claim identity excludes standards.** The frozen v1 identity is
   `plimsol_rule_id × surface natural key` (3A-2, `SURFACE_KEY_FIELDS_V1`).
   No standard, criterion or profile appears in the hash input, so
   adding a map cannot change any claim's identity — therefore cannot
   cause a re-enumeration.
2. **Enumeration is RELEASE-driven, not standard-driven — verified, not
   assumed.** `enumerate_claims` iterates `release["members"]`;
   `standard_profile` is written onto the `claim_sets` row as metadata
   and is read by nothing. `rule_registry.active_rules_for_profile` —
   whose docstring calls it "S3 enumeration's feed" — has **zero callers
   in the codebase** (grep, 2026-08-28). The enumeration path never
   touches `s5_standard_maps`. *(Consequence: that docstring is
   aspirational and should be corrected to describe the function's real
   role, which this phase makes the PROJECTION read.)*
3. **The scan cannot change.** The manifest's `engine_run_set` (D-466)
   is derived from the release's ENGINE BINDINGS, not from standard
   maps. Adding a clause map leaves the run set byte-identical, so the
   pinned scan is unchanged and no re-scan is implied.
4. **Verdicts cannot change.** A verdict's basis cites engine ids and
   attestation, never clauses. `decide_verdict` reads only capability,
   applicability and the observation. Standard maps are not an input.

**Therefore:** EN 301 549 and Section 508 land as additional maps over
the EXISTING rule set — no re-enumeration, no re-scan, no new claims, no
new verdicts, no new manifests. The only writes are catalogue metadata.

## b. THE MAPPING SOURCE — the honesty problem

### What the engine actually carries (measured, vendored axe 4.13.0)

| tag family | present | example |
|---|---|---|
| WCAG criterion | 29 | `wcag111`, `wcag412`, `wcag258` |
| WCAG version | 6 | `wcag2a`, `wcag2aa`, `wcag21aa`, `wcag22aa`, `wcag2a-obsolete` |
| EN 301 549 | `EN-301-549` + 24 clause tags | `EN-9.1.1.1` |
| Section 508 | `section508` + ~8 clause tags | `section508.22.a` |

Three findings that decide the design:

- **axe's Section 508 tags use the SUPERSEDED numbering.**
  `section508.22.a` is §1194.22(a) — the pre-2017 paragraph scheme. The
  2017 Refresh replaced those paragraphs with **incorporation by
  reference of WCAG 2.0 Level A + AA**. Deriving 508 clauses from axe
  would bind Plimsol to a numbering that is no longer normative.
  **Refused.**
- **EN tags cover only the WCAG 2.0/2.1 subset.** `target-size`
  (WCAG 2.5.8, 2.2-only) carries `wcag22aa,wcag258` and **no EN tag** —
  correct, because EN 301 549 V3.2.1 binds WCAG 2.1. The engine's
  silence here is right, and the map must be equally silent.
- **The ACC-05 pair carries neither.** `duplicate-id` and
  `duplicate-id-active` are tagged `wcag2a-obsolete, wcag411,
  deprecated` with **no EN and no 508 tag**, even though 4.1.1 is live
  in WCAG 2.0/2.1. Their maps must be **authored**, not derived (§c).

### Decision, with the lean defended

**Bind these versions, and record them:**
- **Section 508** — the 2017 Refresh (36 CFR 1194 App. A), which
  incorporates **WCAG 2.0 A + AA** by reference.
- **EN 301 549** — **V3.2.1**, whose web chapter binds **WCAG 2.1 AA**
  with clause numbering `9.<WCAG SC>`. (A later EN version binding
  WCAG 2.2 lands as its own map set when we choose to support it — the
  set is versioned precisely so a second EN version can coexist.)

**Where the mapping comes from: derived CANDIDATE, human-ratified — a
mix, and the mix is the point.**
1. **Derive** the candidate mechanically from the WCAG criterion each
   rule already carries in `s5_standard_maps` (standard `WCAG22`), plus
   the version gate: a rule whose criterion exists in WCAG 2.0 A/AA gets
   a 508 candidate; a rule whose criterion exists in WCAG 2.1 AA gets an
   EN candidate at clause `9.<SC>`. A rule whose only criterion is
   2.2-only gets **no candidate for either** — it renders NOT COVERED
   for that standard (§d), never as a pass.
2. **Cross-check** against axe's `EN-9.x.y.z` tags. Agreement is
   recorded; **disagreement is surfaced to the reviewer, never silently
   resolved**. The engine tag is corroboration, not authority — D-462
   unchanged: "the engine is not the accessibility authority; Plimsol
   makes it."
3. **Author** by hand the cases derivation cannot reach — the ACC-05
   pair, and any rule whose WCAG mapping is itself a Plimsol judgment
   (the heading/landmark rules PLM-A11Y-069..072 already are).
4. **Ratify.** Nothing lands ACTIVE without a review record.

Each map therefore carries provenance: `derived` / `engine_corroborated`
/ `authored`, plus the disagreement note where one exists.

### The lifecycle problem, and the structural fix

`add_standard_map` calls `_require_draft` — authoring writes are legal
only while the RULE VERSION is DRAFT. Adding EN and 508 maps to 72
**ACTIVE** rule versions is therefore impossible today without cutting
v2 of every rule — which would falsely record that 72 rules CHANGED
when only their projection was added, and would break the atom/map
separation the platform already claims.

**Decision: standard map sets get their own lifecycle, mirroring
catalogue releases.**

- New `s5_standard_map_sets`: `id`, `standard`, `standard_version`
  (e.g. `"EN 301 549 V3.2.1"`), `state` (DRAFT → REVIEW → APPROVED →
  ACTIVE → RETIRED), `provenance` JSONB, `created_by`, `reviewed_by`,
  timestamps, `content_hash`.
- `s5_standard_maps` gains `map_set_id` (FK). Its existing CHECK already
  admits `EN301549` and `SECTION508` — **no widening needed** (verified
  in migration 062).
- The authoring gate moves from *"the rule version is DRAFT"* to
  *"the map set is DRAFT"*. Maps stay bound to `(rule_id, rule_version)`
  — a projection is asserted of a SPECIFIC rule version, so a future
  rule v2 must re-assert its maps.
- **The content-freeze law is untouched:** you still cannot change what
  a rule CHECKS after REVIEW. You may assert an additional PROJECTION of
  it, reviewed as its own unit. That is exactly "rules are atoms,
  standards are maps" made structural.
- Single-ACTIVE per `(standard, standard_version)` by partial unique
  index, the s5_rules precedent.

## c. The ACC-05 closure — first live proof across standards

D-462 left ACC-05 at **14 CLOSED + 1 PARTIAL of 15**; the PARTIAL is
"duplicate IDs" (`duplicate-id-aria` seeded, the generic pair awaiting
"the first non-WCAG22 standard map", ids assigned at append time and
**never reserved**). This phase is that moment.

- Create `duplicate-id` and `duplicate-id-active` as **new rules at
  next-available ids** through the real lifecycle — create → engine
  binding → standard maps → REVIEW → APPROVED → VERSIONED → ACTIVE —
  then cut **catalogue release 3**, exactly as PLM-A11Y-069..072 were.
- **They map under 4.1.1 Parsing, and under EN/508 only.** 4.1.1 is
  removed in WCAG 2.2 (axe tags them `wcag2a-obsolete`), so they take
  **no WCAG22 map** — while remaining live in WCAG 2.0 and 2.1, which is
  precisely where 508 and EN bind. 508 → WCAG 2.0 4.1.1; EN → clause
  9.4.1.1.
- **This is the first live proof that a rule can be in scope for one
  standard and out of scope for another** — the atom/map separation
  demonstrated across standards rather than asserted.
- **Two deliberate judgments to record** (the heading/landmark shape):
  (i) axe marks both rules `deprecated`; Plimsol nonetheless asserts them
  for standards whose bound WCAG version still contains 4.1.1 — a
  normative choice, made by us, recorded with its rationale.
  (ii) both are `enabled:false` in axe 4.13.0, so **they only execute
  because D-466 pins an explicit run set** — release 3's run set names
  them and the engine runs them. Without the D-466 fix these two rules
  would have produced exactly the silent passes that fix exists to
  prevent. That interaction is the design's own regression story and
  belongs in the review record.
- On closure ACC-05 reads **15 of 15 CLOSED**.

## d. Coverage honesty per standard (SF-14)

**Six coverages; six is the ceiling. This phase adds NO seventh** — no
"standards coverage" dimension. Instead:

| coverage | standard-relative? | how it reads |
|---|---|---|
| requirement | no | unchanged; requirements are not standard-scoped |
| surface | no | the same declared surfaces are scanned whatever the standard |
| metadata | no | S1 coverage is standard-invariant |
| journey | no | R3+; standard-invariant |
| **accessibility** | **YES — recomputed per standard** | per criterion in the bound scope: **AUTOMATED** (≥1 bound rule with AUTO capability), **HUMAN-ONLY** (bound rules are all HUMAN_*), **NOT COVERED** (no bound rule) |
| **execution** | **YES — projected per standard** | of the criteria that ARE covered, how many carry a DETERMINATE verdict in this run |

Three honesty rules, each load-bearing:

1. **A criterion with no bound rule renders NOT COVERED — never absent,
   never implied-pass.** The denominator is the standard's full bound
   scope, not the set of criteria we happen to have rules for. Section
   508's scope is WCAG 2.0 A+AA in full; EN V3.2.1's is WCAG 2.1 AA in
   full, plus the non-web clauses which are **out of scope and shown as
   such**, not silently dropped.
2. **"Covered" and "determined" are different axes and are never
   conflated.** After D-466, a criterion can be COVERED by an AUTO rule
   and still UNDETERMINED in a given run (unattested, engine-incomplete,
   not executed). Accessibility coverage answers *"can we test this?"*;
   execution coverage answers *"did we, in this run?"*.
3. **The automated/human split is per standard**, because the same rule
   set projects onto different criterion sets: a rule that is a
   standard's ONLY automated coverage for a criterion makes it
   AUTOMATED there and may leave the corresponding criterion NOT COVERED
   in a standard that does not bind it.

## e. What a report shows — the data layer only

No UI in this phase. The data layer must expose enough that a later UI
needs **no further substrate change**:

`standard_view(session, *, claim_set_id | job_id, standard,
standard_version)` → one row per criterion in the standard's bound
scope:

- `criterion`, `title`, `level`, `clause` (the standard's own numbering);
- `coverage` ∈ {AUTOMATED, HUMAN_ONLY, NOT_COVERED};
- `contributing_rules[]` — rule id, name, capability, map provenance;
- `contributing_verdicts[]` — verdict, reason, evidence refs, surface;
- `criterion_verdict` — the roll-up;
- `standard_version` and the `map_set` id + content hash, so any rendered
  report names the exact projection it used.

**The roll-up rule (worst-wins, attestation-respecting):** a criterion
is **FAIL** if any contributing verdict is FAIL; else **NEEDS_HUMAN** if
any is; else **NOT_DETERMINED** if any is; else **PASS** only when every
contributing verdict is an attested PASS. A criterion never passes on
unattested parts — D-466's law lifted from rule grain to clause grain.

Reporting must also carry the **run's own honesty header**: the pinned
engine + run set, the catalogue release, and the map set — so "this
surface conforms to EN 9.1.1.1" is always readable as "under THIS rule
set, THIS engine run set, and THIS ratified projection".

## f. Non-goals

- **No UI** — data layer only.
- **No new engine rules** beyond the two ACC-05 closure rules, which are
  bindings of engine rules that already ship.
- **No re-scan, no re-enumeration, no new verdicts** (§a is the proof).
- **No custom or customer-specific standards** — phase 5.
- **No Mode B**, no interaction rules (ACC-06 stays where it is).
- **No seventh coverage** — SF-14's ceiling holds.
- No change to the worker, the manifest builder, or the processor: this
  phase touches the catalogue and the read path only.
