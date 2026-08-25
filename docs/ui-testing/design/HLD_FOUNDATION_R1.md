# High-Level Design — Foundation + Release 1
**Derived from:** Requirements Baseline v3.1 (frozen). **Scope:** Foundation + R1 only; every other requirement is explicitly deferred in the RTM.
**Date:** 2026-08-21 · **Status:** v1.1 — TA P0 fixes applied; Gate 2 CONDITIONAL APPROVAL received; spike authorized
**Grounding:** re-grounded 2026-08-21 by DIRECT repo read via GitHub (HEAD `2c4932d`, main): `primeqa/execution_engine/run.py` and `primeqa/worker.py` read in full; Procfile verified. Items marked ✔ cite verified code. The 2026-08-19 CC recon remains valid where not superseded.

---

## 1. Design elements (DE)

### DE-01 — Rule Registry + Catalogue Store (S5) ✔
A NEW versioned-artifact store (recon: no suitable store exists; evidence today is one JSONB blob per run).
Holds: Plimsol rules (`PLM-A11Y-nnn`) with lifecycle DRAFT→REVIEW→APPROVED→VERSIONED→ACTIVE→RETIRED, immutable when ACTIVE; the pinned engine artifact (axe-core vendored as a versioned asset, never fetched at run time); the versioned map Plimsol-rule → engine-rule(s); the standard maps (WCAG 2.2 / EN 301 549 / 508 / customer profiles).
Tables: `s5_rules`, `s5_rule_versions`, `s5_engine_bindings`, `s5_standard_maps`, `s5_artifacts`.
Satisfies: FND-08(rule lifecycle analog), SF-14 partially, ACC-02/03, D2.

### DE-02 — `conformance-claim` kind (S2) ✔
Registered via `BodyRegistry` keyed `(kind, body_schema_version)` with a per-kind canonicalizer — recon confirms the identity-hash envelope is fixed but hashed content is per-kind (precedent D-305). Identity content: `plimsol_rule_id × surface_id`. **Cost verified (CC recon 2026-08-21): ~14 files per the D-305 path plus the evidence_contract and readable_body kind-keyed registries. Closest precedent: `LayoutClaimBody` (composite two-ref key, generic hash walk) — conformance-claim follows an existing shape.** Persona is inside `surface_id` (FND-01/D2: the R1 instantiation). Engine version, catalogue version, browser, viewport-as-execution, session: operational, excluded.
Satisfies: FND-01/01a/01b/01c, D1, D2.

### DE-03 — Surface + LightningComponentBundle entities (S1) ✔
Two new entity types via the ApprovalProcess precedent (D-308: sync phase + widen the closed CHECK list, migrate-first). `Surface` = declared inventory row: site, path, persona-scope, record-context-ref, version. **Viewport and browser are execution context, not surface identity** — except where a criterion makes viewport semantic (reflow), in which case that criterion carries it (FND-01b applied; prevents inventory explosion: 100 pages × 4 personas stays 400 surfaces, not ×browsers×viewports). `LightningComponentBundle` = custom LWC; **version = normalised metadata/source hash + sync timestamp** (identity = Salesforce metadata identity), so attribution reads "hash A → hash B", never an abstract version — the attribution instrument.
Satisfies: SF-01/02 (R1 slice), ACC-08/09 groundwork, FND-01c (record context pinned in surface).

### DE-04 — Persona + credential vault ✔
`Persona` entity: user ref, auth mode (NONE / TOTP-PROVISIONED / EXEMPT / UNSUPPORTED — UNSUPPORTED rejected at registration), expected entitlements, config version. Credentials + TOTP seeds Fernet-encrypted (existing pattern on connections), ciphertext in DB, **decryption key present only in the browser-worker service environment** (D4+D8 as amended).
Satisfies: FND-22/22a/23/24, ACC-07 groundwork.

### DE-05 — Enumeration generator + set approval (S3)
Claim set = active rules × surface inventory version. Deterministic; no LLM. Refuses surfaces outside inventory. Approval unit = (persona × inventory version) as one act (D3), issuing claim_set_id **with real actor attribution (user id, not the literal "human"), a batch audit record, and an activity_log write — the current bulk-approval route has none of these (CC recon: audit gap), and the claim_set approval fixes it rather than inheriting it**; per-claim inspection preserved via the existing paginated review surfaces (recon: all human surfaces paginate at 50; bulk approval precedent exists requirement-scoped at views.py:2922 — a new scope, same machinery shape).
Approval issues a stable **claim_set_id** (tenant, inventory version, rule-catalogue version, standard profile, persona scope, approved_by, approval version); manifests reference the claim_set_id — approval is never reconstructed from parts. **Rule applicability runs before manifest generation** (APPLICABLE / NOT-APPLICABLE / HUMAN-REVIEW per rule × surface); the worker never decides applicability.
Satisfies: ACC-01, D3, F10.

### DE-06 — `ui-inspection` recipe kind + explicit read-only flag (S4) ✔
Pre-work (D6): replace the inferred read-only discriminator (`recipe_kind == metadata-recipe`) at the single `_authorize_dispatch` chokepoint with an explicit per-recipe-kind property — **verified at HEAD 2c4932d: the inference and its own docstring warning ("when a metadata-write recipe kind is added, this gate MUST also check the mode") are present in run.py.** The D-245 pattern also verified: the async production-role decision happens at the ENQUEUE boundary (the worker runs as system, caller_tier=None) — the ui-inspection enqueue must replicate that boundary check. Then register the kind (~5 files per recon; reuse-vs-new decision on the modeled-but-dead `ui-recipe` kind resolved in Phase 3A LLD). Dispatch remains hardcoded; the seam generalizes at the third kind, not before.
Satisfies: FND-19/21 (R1 slice), D6.

### DE-07 — Browser worker service (new, second service class) ✔
Recon: no browser runtime exists anywhere in v2. New Railway service: orchestrator → job queue → ephemeral worker (Chromium + Playwright + pinned engine from DE-01). **Execution Batch model:** a Run Manifest decomposes into batches; one batch = one browser context + one login + sequential surface execution; batches parallelise across workers with isolated contexts. **R1 operational default: one batch per persona (= the prior job decision).** Sharding a persona's surfaces across batches later changes batch composition only — never the manifest. This caps neither scale nor session efficiency. Job = batch (P0-4 decision: portal login is the expensive fragile step; TOTP-per-surface risks lockout; session reused inside one ephemeral browser context, never serialised across jobs; per-tenant policy override to fresh-login-per-surface). Clean browser context per job.
**Lease model (P0-1):** states QUEUED→CLAIMED→RUNNING→COMPLETED/ERROR with lease_owner, lease_until, heartbeat_at, attempt_number; expired lease → reclaimable; all attempts recorded, original failure preserved.
**Idempotent finalisation (P0-1):** results and evidence keyed deterministically (manifest_id × surface × attempt); retries upsert, never duplicate results, evidence, claims, or audit events; completion + finalisation transactional.
Queue: Postgres-backed job table first (no new infrastructure, observable, deterministic ordering); migration to a dedicated broker is a logged TAD change if throughput demands.
**Precedents verified in-repo (2026-08-21):** the per-tenant queue + consumer-tick + `FOR UPDATE SKIP LOCKED` + stall-reap + attempts-cap idiom (`ai_enrichment_queue`, `s4_execution_jobs` in worker.py); the async three-bracket run path holding no DB connection across external I/O (D-129/D-230.2); `StrandedRecordSink` write-ahead durability with its own committed transactions + crash reaper (D-230) — the model for evidence durability; per-probe SAVEPOINT failure isolation and synthesized-errored-evidence (run-all, D-277) — the model for "a failed surface never aborts the batch, and ran-and-errored is distinguishable from never-ran." The browser worker composes these verified idioms; the genuinely new pieces are the Chromium image, per-job heartbeat, and the manifest-executor contract.
Satisfies: FND-16, TAD realization.

### DE-08 — Session substrate
Login as persona through the portal login page; TOTP generation when provisioned; session reused across the surfaces of one job (per the P0-4 job boundary), never across jobs or tenants; auth failure = classified run-level ERROR (never page-level). Bot-protection path per site: allowlist / exemption / session-injection; absent path → run-level error (v3.1 boundary). **Session injection requires explicit customer authorisation recorded as the persona's authentication policy, and results carry auth mode = session-injection — an injected-session run tested the authenticated surface, not the login flow, and reports must say so.**
Satisfies: FND-20 partially, FND-23/24, K5.

### DE-09 — Stabilisation + runtime detection
Per site: probe runtime (LWR vs Aura, shadow mode) at Discover; select ready-state ladder: navigation complete → network idle → DOM mutation quiet → Salesforce loading indicators absent → bounded max wait → else NOT-REACHED. Observable state, never a timeout-as-success. **Stabilisation policy = global defaults + per-site ready condition + per-component conditions where declared** (network-idle alone is meaningless under streaming/analytics; the per-site escape hatch is part of the model, not a workaround).
Satisfies: FND-17, SF-03 Discover.

### DE-10 — Evidence + Environment capture
Per result: screenshot, violating-node DOM fragment (only — D5), locator + resolution record, accessibility-node data, **engine observation** (the raw third-party engine output: engine rule id, node, engine result — never a Plimsol verdict). Per run: Environment object (org+release, site+template, package inventory, browser, OS, Plimsol/engine/rule-set/inventory versions, viewport, locale). Stored structured, not one blob; retention per D5 (90-day / 3-release, signed URLs).
Satisfies: FND-15/25, SF-10 groundwork.

### DE-11 — Ownership + origin classifier
Violating node → nearest custom element → origin (Salesforce Standard / Salesforce Config / Client Custom / Managed / Unmanaged / Third-party / Unknown) via documented markers (tag prefixes, data-aura-class — DOCUMENTED 2026-08-19) → owner class → confidence CONFIRMED/PROBABLE/UNKNOWN. Probabilistic never drives FAIL. **CONFIRMED requires a metadata resolution: DOM node → LightningComponentBundle via the SF-02 mapping. DOM-ancestry inference alone is at most PROBABLE.**
Satisfies: FND-26, SF-08, ACC-09.

### DE-12 — Verdict/status model
Two fields everywhere: verdict PASS/FAIL/NEEDS-HUMAN/NOT-DETERMINED; status COMPLETED/NO-ACCESS/NOT-REACHED/ERROR/CANCELLED. Waivers visible, never PASS (FND-28).
Satisfies: FND-19/28, ACC-04.

### DE-13 — Detection + change classification (S6)
Run N vs N−1 per surface, same inventory version. **Causal assessment, not single-cause classification.** A release may contain a client LWC change AND a package update AND a seasonal release simultaneously. The classifier produces: primary suspected cause (with confidence CONFIRMED/PROBABLE), contributing changes (all dimensions that moved), evidence per candidate, and tool/environment deltas. The human-facing headline may read "REGRESSION — likely client change" but the model retains every contributing dimension. R1 implementation may be simple (ranked candidates by specificity: bundle diff > package diff > platform diff > tool diff); the *model* is causal from day one. Element fingerprint = rule + role + accessible name + ancestor chain + owning component (never CSS path).
Satisfies: FND-27, ACC-08, SF-10.

### DE-14 — Attribution (S6)
CLIENT-classified failures join to LightningComponentBundle version diffs (same instrument shape as the VR detector); report clusters by component; A9 routing payload per owner.
Satisfies: ACC-09, SF-07 (R1 slice).

### DE-15 — Review surface + Class-3 queue (S7)
Result model with separate verdict and execution status, per surface×persona; standards as views; NEEDS-HUMAN queue with candidates from run one; coverage panel per SF-14's accessibility split (automated vs human-only, honestly).
Satisfies: ACC-04/10, XCU slices.

### DE-16 — Canonical test model, schema room only
FND-02..07 (typed ACTION/OBSERVATION/ASSERTION elements, variables, conditions, parameters, components, transitions) are DESIGNED as the S2 body-schema shape for future kinds; R1 builds none of the authoring — conformance claims are assertion-only instances of the model. The ASSERTION schema explicitly reserves: subject, operator, expected, timeout, poll-interval, eventual-condition, failure-classification — proving FND-18 (eventual assertions) needs no redesign at R3. This is the "don't design the engine twice" clause, and the reserved-field list is the reviewable proof.

### DE-17 — Run Manifest (P0-3) ✔ precedent verified
**In-repo precedent (read 2026-08-21):** `_write_batch_manifest` (D-281, run.py) already implements the manifest discipline at batch scale — expected membership recorded drift-immune, committed EARLY in its own transaction before any probe runs, so a crashed batch reads as incomplete → not-Verified. DE-17 extends this exact idiom to the run level; it does not invent a parallel mechanism.
Immutable, tenant-scoped, generated at scheduling: run id, tenant, rule-catalogue version + rule versions, inventory version, Environment intent, persona, surface set, browser profile, viewport set, locale, evidence policy, execution policy, state-context refs, artifact SHAs (engine, catalogue, standard maps, worker image digest). **Workers execute manifests, nothing else.** Retry = same manifest. Comparison = manifest vs manifest. The reproducibility object.

### DE-18 — Page/Test State Context (P0-2)
Per surface execution, capture the state the verdict was produced against: resolved record ids + a field snapshot of pinned records, persona entitlement observation, feature/config state where obtainable, locale, viewport used, and a **normalised semantic fingerprint** (element roles, accessible names, structural relationships, component identity — never raw HTML; generated IDs, timestamps, tokens, session artifacts excluded, else every run is NOT COMPARABLE).
**State-context storage levels (privacy policy, P0):** Level 1 record identity → Level 2 structural (field present/state) → Level 3 value fingerprint/hash → Level 4 raw value only where explicitly required and permitted. Default: lowest level sufficient. State context must not become a shadow PII store beside the evidence policy. Regression classification requires **context equivalence before comparison** (P0-2/P0-3 pipeline): identity same → environment equivalent → execution context equivalent → state context equivalent → component/config changed → classify. Non-equivalent context → NOT COMPARABLE, never REGRESSION.

### DE-19 — Tenant boundary in the browser plane (AK directive)
Per-tenant setup lives exclusively in tenant-scoped records: sites, surfaces, personas, credentials + TOTP seeds (ciphertext), auth modes, bot-protection paths, standards profiles, custom rules, evidence policy, schedules. Zero per-tenant configuration in Railway or any deployment artifact. Every new object (job, manifest, surface, persona, evidence, environment, run, result) carries tenant ownership; every query is tenant-scoped; browser contexts, sessions, credentials, and evidence namespaces are never shared across tenants. The worker fleet is shared platform infrastructure; the platform key decrypts only inside a job already scoped to one tenant, and decrypted credentials never leave the job process (accepted R1 posture; credential-service boundary is the logged R2 hardening).

## 2. Run data flow

Inventory(S1) × Rules(S5) → Claims(S2, DE-05 approved) → **Manifest (DE-17)** → Orchestrator (DE-07) → per surface: session (DE-08) → stabilise (DE-09) → inject engine (DE-01 artifact) → collect **raw engine observations** → evidence+environment (DE-10) → persist observations → **[worker boundary ends here]** → result processor: Plimsol rule evaluation → applicability → verdict/status (DE-12) → ownership (DE-11) → S6 causal assessment (DE-13/14) → S7 (DE-15). The worker executes engines and returns observations; it never assigns a Plimsol verdict, applicability, ownership, or severity.

## 3. RTM — Foundation + R1 (D = designed here; B = build phase; ✂ = deferred by ladder)

| Req | DE | Phase | Acceptance |
|---|---|---|---|
| FND-01/01a/01b/01c | DE-02 | 3B | identity stable across re-runs |
| FND-02,03 | DE-16 (schema), DE-06 | 3A | conformance claim executes as assertion-only instance |
| FND-04,05,06,06a,07 | DE-16 schema room | ✂ R3 build | schema review only |
| FND-08 | DE-01 | 2/3B | active rule immutable; v2 supersedes |
| FND-09,10 | DE-15 | 3B | metadata visible on claims |
| FND-11..14 | DE-11 + DE-02 | 3A/8 | no CSS-path storage; unresolved→NOT-DETERMINED |
| FND-15 | DE-10 | 3A | Environment on every run |
| FND-16 | DE-07 | 2 | ephemeral worker proven |
| FND-17 | DE-09 | 3A | no fixed sleeps in executor |
| FND-18 | ✂ R3 | — | schema room in DE-16 |
| FND-19 | DE-12 | 3A | two fields persisted |
| FND-20 | DE-07/08 partial | 3B | retries recorded, original kept |
| FND-21 | DE-06 | 3A | ui-inspection flagged read-only explicitly |
| FND-22,22a,23,24 | DE-04/08 | 3B | UNSUPPORTED rejected at registration |
| FND-25 | DE-10 | 3A | evidence chain complete on planted defect |
| FND-26 | DE-11 | 3A/8 | origin+confidence on every violation |
| FND-27 | DE-13 | 7 | three planted drifts classified correctly |
| FND-28 | DE-12/15 | 7 | waived failure visible, never PASS |
| SF-01,03 | DE-03 + matrix artifact | 3B | published matrix, six dimensions |
| SF-02 | DE-03/13 (R1 slice) | 8 | element→component→bundle join demonstrated |
| SF-04,05 | ✂ R2/R3 | — | schema room only |
| SF-06 | ✂ R3 | — | — |
| SF-07 | DE-14 (R1 slice) | 8 | package-version delta reported |
| SF-08 | DE-11 | 3A | seven-value origin |
| SF-09 | ✂ R3 | — | — |
| SF-10 | DE-10/13 | 7 | release identifier in diff |
| SF-11..13 | ✂ R3/R4 | — | AK-confirmed object, no build |
| SF-14 | DE-15 (accessibility+execution coverage) | 3B | two of six coverages live |
| ACC-01..10 | DE-01..15 | per SDLC §8 | SDLC v2 exit criteria stand |
| REG/FUN/E2E/INT/XCU-rest | ✂ | — | deferred by ladder, no design element |

## 3a. RTM additions (v1.1)

| Item | DE | Phase | Acceptance |
|---|---|---|---|
| P0-1 lease + idempotency | DE-07 | 2 | worker killed mid-job → reclaimed, retried, zero duplicates |
| P0-2 state context | DE-18 | 3A | context captured; non-equivalent context → NOT COMPARABLE |
| P0-3 manifest | DE-17 | 2 | same manifest + controlled fixture → same result set; same manifest + changed page state → NOT COMPARABLE (two distinct proofs) |
| P0-4 session boundary | DE-07/08 | 2 | one login per persona-job proven; no cross-job session |
| P0-5 feasibility | DE-08 + TAD | 2 | real portal login (TOTP path) from production architecture |
| Tenant boundary | DE-19 | 2–3A | cross-tenant isolation demonstrated in browser plane |

## 4. Design decisions for TA (Gate 2)

1. DE-07 queue = Postgres job table first. Confirm.
2. DE-16's claim: the typed-element schema gives R3 room without rework. Attack this.
3. DE-01 vendors axe-core as a versioned S5 artifact (not npm-at-runtime). Confirm.
4. Reuse-vs-new for the dead `ui-recipe` kind deferred to Phase 3A LLD. Confirm placement.

**Gate 2 status (round 2, TA 2026-08-21): direction APPROVED; four P0s applied in this revision (engine-observation boundary, two-part determinism proof, causal assessment model, execution-batch decomposition). Spike authorized against the 10-arm matrix below.**

**Phase 2 spike matrix (TA):** A same-everything → same verdict · B worker killed midway → reclaim, no duplicates · C portal content changed → NOT COMPARABLE · D client component changed → CLIENT primary candidate · E package changed → ENVIRONMENT candidate · F engine version changed → TOOL candidate · G credential fails → run ERROR, zero page FAILs · H locator unresolved → NOT-DETERMINED · I cross-tenant access attempt → hard denial + audit event · J evidence upload fails → no falsely-complete result.

**Failure-mode coverage (RTM annex):** every critical DE carries a failure row — auth/bad password → run ERROR; worker crash → lease reclaim; evidence upload fail → result held incomplete; state changed → NOT COMPARABLE; locator unresolved → NOT-DETERMINED; rule artifact unavailable → configuration error, run refuses to start; package changed → ENVIRONMENT candidate; bundle changed → CLIENT candidate.
