# LLD 3A-4 — Manifest-from-claim_set + the Result Processor (observations → verdicts)

Status: DESIGN (this commit); implementation follows on its own GO.
Derives from: HLD DE-06/DE-11, SAD A10 + D-460 (worker–interpretation
boundary, MANDATORY here), D-461 (manifest pin), D-281 (recorded
membership), 3A-1 (S5 registry + releases), 3A-2 (kinds + RECIPE_MODES),
3A-3 (claim_sets + applicability). Branch: `phase-3a-substrate`.

## The boundary, stated in code terms first (D-460)

**Verdicts are computed in exactly one place:
`primeqa/interpretation/ui_conformance.py` (S6 — NEW).** The browser
worker (`primeqa/browser_worker/*`) produces ENGINE OBSERVATIONS and
nothing else — its modules keep the existing string-level ban on the
word "verdict", and this slice EXTENDS that guard test to cover every
`browser_worker` module including any 3A-4 additions. The manifest
builder lands in `primeqa/execution_engine/ui_manifest.py` (S4 —
dispatch orchestration; it is where RECIPE_MODES lives). Any design
that moves mapping, applicability, ownership, or verdict logic into
the worker is rejected by principle. The processor runs web/worker-
service-side (never inside the browser-worker image), reading stored
observation rows after the fact — the worker does not even know the
processor exists.

## a. Manifest ← claim_set

`build_manifest_for_claim_set(session, *, claim_set_id, ...)` in
`execution_engine/ui_manifest.py`:

- **Membership by reference (D-461 + D-281):** the builder reads the
  APPROVED claim_set's recorded member rows — refusing 'draft'/'revoked'
  sets — and stamps `claim_set_id` into the manifest payload. Nothing
  downstream ever reconstructs membership from (persona × inventory ×
  release) parts; the processor reads the SAME member rows by
  `claim_set_id`. Members revoked between approval and build are
  excluded AT BUILD TIME and recorded in the manifest payload as
  `excluded_revoked: [test_id…]` (visible, never silent).
- **The recipe-per-claim → one-scan-per-surface collapse happens HERE
  (the 3A-3 §b deferral). Fan-out rule, precisely:**
  1. *Collapse (build time):* a surface enters `manifest.surfaces`
     exactly once (keyed by the frozen canonical `surface_key`) iff at
     least one non-revoked member on that surface satisfies —
     `applicability = APPLICABLE AND executable = TRUE`, **or** the
     member's rule capability in the PINNED release is
     `HUMAN_WITH_CANDIDATE` (its scan feeds candidates). Members that
     are `HUMAN_ONLY`, `NOT_APPLICABLE`, or NOT-executable
     (AUTO_WITH_ACTION, Mode B) contribute NO surface — and if such a
     member's surface enters via a sibling, that member still gets no
     verdict (§c).
  2. *Fan-out (processing time):* one scan observation per
     `(job, surface_key)` fans out to EVERY member claim whose claim
     body's canonical surface key equals that `surface_key` — the
     member's own verdict computed independently from the shared
     observation, filtered to the member's `plimsol_rule_id` via the
     engine bindings (§b). The observation is shared; verdicts are
     never shared.
  Capability is derived from the manifest's pinned release on BOTH
  sides — deterministic, since the release is immutable recorded
  membership.
- **The two parked 3A-2 wirings land here:**
  - *Enqueue consults RECIPE_MODES:* the browser-plane enqueue path
    asserts `mode_for("ui-inspection") == READ_ONLY` before creating a
    job — the declared table, not the kind name, authorizes browser
    dispatch; an undeclared kind refuses with the D6 error.
  - *Catalogue pins source from S5:* `manifest.pins` gains
    `catalogue_release_id` + the release `content_hash`, and the axe
    artifact pin is READ from `s5_artifacts` via
    `rule_registry.pinned_artifact` — with **hash equality asserted**
    against the vendored engine recorded in the S5 row (`axe_sha256 ==
    s5 sha256`, refuse the build on mismatch). Pins stop being
    hand-carried constants.

## b. The processor (result side, never worker)

`process_job(session, *, job_id)` in
`interpretation/ui_conformance.py` — deterministic, LLM-free:

```
per scan job (must reference a claim_set-built manifest)
  → per (surface_key, observation) result row
    → resolve engine observations to PLM rules via s5_engine_bindings
      (bindings_for_engine at the manifest's pinned engine version)
    → per non-revoked claim_set member on that surface:
        applicability gate (the MEMBER ROW — the 3A-3 snapshot,
        never recomputed)
        → verdict per §c, written per §f
```

- **Unmapped engine rule ids** (observed ids absent from the bindings
  map) are surfaced HONESTLY: recorded per job in the processing
  record (§f) as `unmapped_engine_ids`, counted, reportable — never
  dropped, and never themselves a verdict (no PLM rule → no claim →
  nothing to judge). This is `resolve_engine_rules`' existing
  contract applied at the verdict boundary.
- The processor is idempotent per `(job_id, test_id)` — reprocessing
  a job UPSERTs identical verdict rows (deterministic inputs), and a
  changed outcome on reprocess is evidence of a bindings change, which
  the processing record captures via the engine-bindings snapshot hash.

## c. Verdict semantics — exact

| verdict | condition |
|---|---|
| **FAIL** | member APPLICABLE + scan COMPLETED + a mapped violation for the member's rule present on its surface |
| **PASS** | member APPLICABLE + scan COMPLETED + NO mapped violation for that rule (the engine ran its checks; absence of violation on a completed scan is the pass signal) |
| **NEEDS_HUMAN** | member's rule capability `HUMAN_WITH_CANDIDATE` + scan COMPLETED — the engine's "incomplete" items for that rule attach as CANDIDATES on the verdict row; a human decides |
| **NOT_DETERMINED** | the honest remainder: unmapped binding dependency for the member's rule, missing/failed fingerprint, unresolvable element reference, or any state where neither PASS nor FAIL is PROVEN |

- **Execution status is a SEPARATE field, never a verdict:** a surface
  whose scan is `NOT_REACHED` / `NO_ACCESS` / `ERROR` produces **no
  verdict rows** for its members — the status is recorded on the
  processing record and (for reporting) each affected member is listed
  under it. A run that couldn't look is not a run that judged.
- **HUMAN_ONLY members**: no engine input exists — no verdict row in
  this slice (they are visible on the claim_set listing as
  HUMAN_REVIEW; their human-verdict capture surface is out of scope,
  §h).
- **NOT_EXECUTABLE (Mode B / AUTO_WITH_ACTION) members never gain a
  verdict in this slice** — enumerated, visible, unjudged.

## d. Arm H — this slice's acceptance arm

The spike deferred arm H (locator/element NOT-DETERMINED) to 3A. Its
acceptance transcript here: a scan observation carrying (i) a violation
under an engine rule id ABSENT from the bindings map, and (ii) an
element reference the fingerprint/DOM fragment cannot resolve →
**both paths yield NOT_DETERMINED (or the unmapped-record, per §b),
NEVER FAIL** — transcripted with the exact rows. The adversarial
posture: a processor that cannot prove what it saw refuses to convict.

## e. Ownership (DE-11) — processor-side by principle

The DE-11 origin classifier (markers `CONFIRMED` / `PROBABLE` /
`UNKNOWN`) runs in the PROCESSOR, from the evidence the worker already
captures: the violation node's DOM fragment / selector in the stored
observation JSON. Stated explicitly: **the spike's worker computes
fingerprints as OBSERVATIONS (structure it saw); ownership is
INTERPRETATION (whose structure it is) — therefore processor-only.**
The worker gains no ownership logic, no markers, no site-metadata
awareness. Ownership stamps the verdict row (`ownership` column);
`UNKNOWN` is an honest answer, never upgraded by guesswork.

## f. Persistence — decided, with the lean defended

**Lean: NEW tenant tables; do NOT extend `s4_ui_inspection_results`.**
The grain is wrong for extension: results are per `(job, surface)`;
verdicts are per `(job, claim)` — one surface row fans out to many
member verdicts. Extending the spike-grade results table would either
denormalise verdicts into JSON blobs (unqueryable, un-CHECKed) or
force a fake grain. The results table stays what it is — the
observation store the worker writes.

- **`s6_ui_verdicts`** (tenant): `id UUID PK`, `manifest_id`, `job_id`
  + `surface_key` (logical ref to the result row), `claim_set_id`,
  `test_id`, `plimsol_rule_id`, `verdict` CHECK IN
  ('PASS','FAIL','NEEDS_HUMAN','NOT_DETERMINED'), `verdict_basis`
  JSONB (mapped engine ids, violation node refs, candidates, the
  NOT_DETERMINED reason), `ownership` CHECK IN
  ('CONFIRMED','PROBABLE','UNKNOWN'), `evidence_state_at_write`,
  `processed_at`; `UNIQUE(job_id, test_id)` (idempotent reprocess).
- **`s6_ui_processing_runs`** (tenant): one row per processed job —
  `job_id`, `manifest_id`, `claim_set_id`, engine + version, the
  bindings snapshot hash, `unmapped_engine_ids` JSONB, per-status
  surface counts, verdict counts, `no_verdict_members` JSONB (member →
  reason: surface status / HUMAN_ONLY / NOT_EXECUTABLE), `processed_at`.
- **Evidence completeness carries over (2.5 law):** a verdict row is
  REPORTABLE-complete only when its underlying result row's evidence
  is `REFERENCED` (which the DB already guards to require keys +
  checksums + sizes + `verified_at`). `evidence_state_at_write` records
  what the processor saw; the reporting read JOINs the live evidence
  state — **no verdict is presented as evidence-complete without
  VERIFIED evidence**, and a verdict over incomplete evidence renders
  with that flag, never silently clean.

## g. Worker delta: ZERO semantic change

The worker consumes claim_set-built manifests and returns observations
exactly as today. The manifest payload gains additive fields the worker
never reads (`claim_set_id`, `excluded_revoked`, the enriched `pins`
block) — additive unread payload is not a semantic delta. No worker
code changes, no image rebuild, no new env. If implementation surfaces
any unavoidable worker change, it stops and is enumerated against
D-460 in the HOLD — the current design requires none.

## h. Non-goals (3A-4)

- No run-vs-run comparison, drift subtraction, or regression labelling
  (Phase 7 — needs release history; the spike's `compare_jobs` stays
  spike-grade).
- No S1 Surface entities (3A-5).
- No report UI beyond a minimal verdict listing on the existing
  paginated pattern (claim_set → verdicts, filterable by verdict).
- No Mode B: AUTO_WITH_ACTION members stay enumerated-but-unjudged.
- No human-verdict capture for HUMAN_ONLY / NEEDS_HUMAN members (the
  candidates are stored; the adjudication surface is a later slice).
- No scheduler wiring — jobs are enqueued manually as in the spike.
