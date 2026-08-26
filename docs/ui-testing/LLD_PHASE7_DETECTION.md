# LLD Phase 7 — Release-over-release Detection + Causal Attribution

Status: DESIGN (this commit); implementation follows on its own GO.
Branch: `phase-7-detection` (from main @e849f848, Phase 3A merged).
Derives from: HLD DE-13 (causal assessment) + DE-18 (comparability
ladder), D-461 (manifest pins), the spike's observation-level comparator
(`browser_worker/compare.py`: SAME / DIFFERS / NOT_COMPARABLE /
TOOL_DRIFT — arm C proven), and the 3A substrate (claim_sets, verdicts,
`owner_bundle_ref`, the LightningComponentBundle version history).

**The thesis' other half**: detection says something changed;
attribution says what it broke. The spike proved the shape at
OBSERVATION level; Phase 7 lifts it to VERDICT level and adds causal
classification. Arms D/E/F prove here.

## a. Comparison unit + comparability preconditions

**Unit: the verdict-grain diff between two PROCESSING RUNS** (each a
`s6_ui_processing_runs` row with its `s6_ui_verdicts` set) — baseline A
vs candidate B, joined per claim identity (`test_id`; identity hash
equal by construction when the claim is the same row).

**The DE-18 ladder, applied in order — each rung must hold before the
next is examined; classification is the LAST step:**

1. **Identity** (hard precondition): both runs reference claim_sets
   over the **same inventory version**. A cross-inventory-version pair
   is **REFUSED** — an inventory change is a DECLARED act (D-281), not
   drift; the comparator returns a refusal naming both versions, never
   a transition set. Same claim identity per compared pair of verdict
   rows (the join key).
2. **Environment**: the two runs' org-environment snapshots (§c) are
   diffed. Deltas are RECORDED as environment-dimension drift
   candidates — they do not refuse the comparison.
3. **Execution context** (TOOL dimension): engine sha, engine version,
   catalogue release id + content hash, bindings snapshot hash,
   playwright version. Any difference is **TOOL-dimension drift,
   recorded and subtracted** — a verdict transition under a moved tool
   pin is reported as DRIFT (tool-attributed), **never silently mixed
   into regression**. (The spike's TOOL_DRIFT rung, lifted to verdict
   grain: there it refused comparison outright; here the comparison
   proceeds with the tool dimension recorded, because §d ranks it as a
   candidate — the honesty is in the labeled output, not a refusal.)
4. **State context — CONDITIONAL (amended per the 2026-08-26 GO)**:
   per surface, the stored structural fingerprints.
   `fingerprint_A != fingerprint_B` on a claim's surface yields
   **NOT_COMPARABLE ONLY when no captured dimension moved** — no
   environment delta at rung 2, no tool delta at rung 3, and no
   owning-bundle version change in the (A, B) window for that claim's
   surface. When a dimension DID move, the fingerprint delta ATTACHES
   to the transition's causal evidence and classification proceeds —
   a structural change caused by the release IS the release change,
   not incomparability. Rationale on record: DE-18's signed ordering
   places component/config-change after state context; this
   operationalizes it. An unexplained structural change (nothing
   captured moved) remains the arm-C NOT_COMPARABLE with the delta
   attached — the world changed under the claim and nothing on record
   explains it; no transition is minted.
5. **Classify** (§b + §d) — only pairs that survived rungs 1–4.

## b. Verdict-transition taxonomy — exact semantics

Defined over the determinate verdicts {PASS, FAIL}. A side carrying
NEEDS_HUMAN or NOT_DETERMINED is verdict-indeterminate → the pair is
**NOT_COMPARABLE** with reason `indeterminate_side` (never coerced).

| transition | exact condition |
|---|---|
| **NEW_FAIL** | A=PASS, B=FAIL (rungs 1–4 held) — the regression signal |
| **FIXED** | A=FAIL, B=PASS |
| **STILL_FAILING** | A=FAIL, B=FAIL |
| **STILL_PASSING** | A=PASS, B=PASS |
| **NEW_CLAIM** | claim in B's set membership, absent from A's (enumerated later — e.g. a new catalogue release's added rules) |
| **RETIRED_CLAIM** | claim in A's, absent from B's (revoked / deprecated between runs) |
| **NOT_COMPARABLE** | rung-4 fingerprint delta WITH no captured dimension moved (delta attached verbatim — the unexplained-change case), or an indeterminate side, or an unmapped/no-verdict side — reason always named. A fingerprint delta WITH a moved dimension does NOT land here: it classifies, delta-in-evidence (§a rung 4 as amended) |
| **NOT_RUN** | an EXECUTION-STATUS side: the claim's surface was NOT_REACHED / NO_ACCESS / ERROR in either run (the 3A-4 `no_verdict_members` record) — a status, surfaced as a status |

**Statuses never masquerade as transitions**: NOT_RUN and
NOT_COMPARABLE are terminal report rows with reasons, excluded from
regression counts; a run that couldn't look (or couldn't prove) is
never counted as fixed OR failed.

## c. The drift dimensions + the environment-capture gap

**Recorded today, per run (ground truth of the 3A substrate):**
- Manifest pins (build time): `axe_version`, `axe_sha256`,
  `playwright_version`, `catalogue_release_id`,
  `catalogue_content_hash`. NOT carried: `worker_image_digest` (a
  spike-era pin the claim_set builder dropped) — restored as part of
  this slice's pin block.
- Processing run: `engine`, `engine_version`, `bindings_hash`,
  `surface_statuses`, `no_verdict_members`.
- Per verdict: `owner_bundle_ref` (3A-5) — the client-change join key.
- S1: the LightningComponentBundle version history (which bundle
  versions changed, and when, per SF-08 source hashes).

**What causal classification needs and prod does NOT yet capture — the
one genuinely new design problem:** the org's platform release/version
and the installed-package inventory AT RUN TIME. A NEW_FAIL after a
Salesforce seasonal release or a managed-package upgrade must be
attributable to ENVIRONMENT, and today nothing records either.

**Design — org-environment snapshot, captured at MANIFEST BUILD time
(the defended lean):**
- New tenant table `org_environment_snapshots`: `id`, `captured_at`,
  `platform_api_version` (the org's latest from `GET /services/data`),
  `instance_name` + `organization_type` (from the `Organization`
  sObject), `packages` JSONB (the `InstalledSubscriberPackage` set via
  Tooling API: namespace, name, version id + number), and a
  `content_hash` over the normalised record. Snapshots are immutable;
  identical content reuses the existing row (hash-keyed).
- The manifest's pin block gains `org_env_snapshot_id` — the manifest
  records the world it was built for, exactly the D-461 pin
  philosophy. The comparator diffs the two referenced snapshots.
- **Why build-time capture, not S1 sync**: the snapshot must describe
  the environment at (or bounded-near) EXECUTION; S1's daily sync
  cadence can miss a same-day package install, and the platform
  release/package inventory is run-scoped environmental CONTEXT, not
  org behavior — it has no claims to ground, no edges, no bitemporal
  diff consumers, so an S1 entity would be machinery without a
  consumer. Build-to-scan latency is queue-bounded (minutes) and
  accepted for R1, stated on the snapshot (`captured_at` vs the job's
  execution timestamps makes the bound auditable). The browser worker
  never captures anything (no org API, no credentials — D-460 posture
  unchanged).

## d. Causal assessment (DE-13 as signed)

Output per NEW_FAIL (and available for FIXED — a fix wants attribution
too): **primary suspected cause + confidence + contributing changes +
evidence per candidate**, ranked by SPECIFICITY:

1. **CLIENT — bundle version diff** (most specific): the verdict's
   `owner_bundle_ref` resolves to a bundle whose S1 version history
   shows a source-hash change in the (A, B) window → candidate with
   the bundle NAMED, the version pair, and the changed-file manifest
   as evidence.
2. **ENVIRONMENT — package delta**: the snapshot diff shows installed-
   package changes (install / upgrade / removal) → candidate naming
   the packages.
3. **ENVIRONMENT — platform delta**: platform api version moved →
   candidate naming the release pair.
4. **TOOL — pin delta**: engine sha / catalogue release / bindings
   hash / playwright / image digest moved → the transition itself is
   already labeled DRIFT (§a rung 3); tool is the primary and the row
   never enters the regression headline.

R1 confidence is deliberately simple: the highest-rank candidate that
actually moved is primary; confidence HIGH when exactly one dimension
moved, MEDIUM when several (all retained as contributing), LOW when
none did (an honest "no captured dimension moved — unexplained"). The
headline may read "REGRESSION — likely client change (loanWidget
v3→v4)"; **the model retains every dimension that moved** — nothing is
discarded to make the headline cleaner. The MODEL is causal from day
one; only the ranking is R1-simple.

## e. Persistence

Tenant-schema, immutable per comparison run, idempotent re-compare:

- **`s6_ui_comparison_runs`**: `id UUID`, `baseline_job_id`,
  `candidate_job_id` (each referencing a processed job),
  `claim_set_id`(s), `inventory_version`, `outcome`
  (`completed` / `refused`), `refusal_reason`, `tool_drift` JSONB (the
  rung-3 record), `env_delta` JSONB (the snapshot diff),
  `transition_counts` JSONB, `created_at`.
  `UNIQUE(baseline_job_id, candidate_job_id)` — re-compare UPSERTs
  byte-identical rows from the same immutable inputs (the 3A-4
  reprocess posture).
- **`s6_ui_verdict_transitions`**: `(comparison_id, test_id)` PK,
  `transition`, `from_verdict`, `to_verdict`, `fingerprint_delta`
  JSONB (NOT_COMPARABLE rows), `causal` JSONB (`primary`,
  `confidence`, `contributing[]`, `evidence{}`), `surface_key`,
  `plimsol_rule_id`.

Comparator home: `primeqa/interpretation/ui_comparison.py` (S6 — it
reads verdicts and interprets change; the worker and S4 are untouched).

## f. Acceptance = arms D / E / F (+ arm C at verdict level), on fixtures

Two full runs (enumerate → approve → manifest → consume → process)
against the fixture server, with ONE planted delta per arm:

- **Arm D (CLIENT, strengthened per the amendment)**: the planted
  client change is STRUCTURAL — fingerprint-visible — between the two
  runs, with the owning bundle gaining a new S1 version in the window
  → the NEW_FAIL classifies CLIENT **with the bundle named AND the
  fingerprint delta in the causal evidence** (proving the conditional
  rung 4: a moved dimension turns a structural delta into evidence,
  not incomparability). A style-only variant may be included
  additionally, never substituted.
- **Arm E (ENVIRONMENT)**: a planted package-inventory delta between
  the two snapshots → primary ENVIRONMENT naming the package.
- **Arm F (TOOL)**: a planted engine/catalogue pin delta → the verdict
  diff reported as **DRIFT, not regression** (tool primary; regression
  headline count 0 for that pair).
- **Arm C at verdict level (unchanged)**: page content changed between
  runs with NO planted dimension (no env, tool, or bundle movement) →
  the claim's pair is NOT_COMPARABLE with the structural delta
  attached — **never a transition**.
- Idempotence: re-compare → byte-identical rows.
- Refusal: a cross-inventory-version pair → REFUSED naming both
  versions.

## g. Non-goals (Phase 7 R1)

- No scheduling / auto-compare triggers — comparisons are invoked.
- No report UI beyond a listing on the existing paginated pattern.
- No Mode B; NOT_EXECUTABLE members remain unjudged and uncompared.
- No impact-analysis / smart test selection (R5).
- No cross-tenant anything.
- No S8 wiring (grounding-validity consumes bundle history in its own
  arc, not this one).
