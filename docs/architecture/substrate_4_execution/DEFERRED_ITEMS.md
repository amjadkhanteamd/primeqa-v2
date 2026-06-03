# Substrate 4 — Execution Engine — Deferred Items

The single forward-looking list. The **first vertical** (metadata-inspection) is built + merged + live-proven (SPEC §Status; DECISIONS_LOG D-108 → D-108.4). This document consolidates everything the design + the four slices deliberately deferred, each with its D-entry reference, so the next phase has one place to read first.

Authored at the first-vertical close-out (2026-05-27). Append corrections as a dated note; do not silently rewrite.

---

## 1. Orchestration and the production trigger

The synchronous run path is realized (`run_recipe_execution` + `run_recipe_execution_for_tenant`, transaction boundary A). What turns it into a fired-in-production capability is deferred:

- **Async / worker orchestration (the scaling restructure) — LANDED (Phase 3, D-129…D-132).** `run_recipe_execution_async` brackets the live read with brief transactions (select TX → execute holding **no DB connection** → persist+posture+interpret TX); the per-tenant `s4_execution_jobs` queue + `ExecutionJobStore` (partial-unique active-set idempotency) + the consumer/reaper ticks + the scheduler/worker firing wiring. **Residual:** the **data-path** bracketing — B0 is metadata-path only; the positive-data vertical reads S1 mid-execute, so its brief-tx bracketing is its own work (the async wrapper refuses a data recipe loudly today). — **D-108.4 / D-129 / D-130 / D-131 / D-132**
- **The run trigger — tick wiring LANDED (D-132); the *enqueue source* DEFERRED.** The scheduler + worker now fire the consumer + reaper — the production loop is **live + idle**, no-opping on the empty queue. What remains is *what enqueues a job*: a post-approval hook on a freshly-approved recipe / scheduled re-verification / a CI release gate — each implies different ownership + cadence, its own trigger design. Jobs are enqueueable programmatically via `ExecutionJobStore.create_or_get_job` until then. — **D-108.4 / D-132**
- **Tenant-enumeration / worker co-location.** When the trigger lands, its tenant discovery + process placement mirror the open S3 questions (worker co-location vs dedicated process; `information_schema` vs `shared.tenants`). — **D-108.4** (cf. S3 D-106.4)

## 2. The second vertical — CRUD / `data-recipe` (F4)

**Update (2026-05-27):** the **behavioral negative** (create-rejected) is now **realized + live-proven** (D-110.1 → D-110.3) — the first `data-recipe` kind. What remains deferred:

- **S3-A — required-field population for the behavioral create.** The S3-thin emission authors the violating create with *only* the parser's `violating_payload`. For a VR-enforced prohibition the create trips the VR immediately (proven live) — so S3-A is **deferred-not-needed** there. It becomes necessary only for an object where a *platform*-required field would short-circuit (`REQUIRED_FIELD_MISSING`) before any VR fires (inferred unlikely — SF returns all errors → the VR surfaces alongside → still matches). When built: read S1 `field_details.is_required` + a field-type→valid-value generator + picklist lookup, **gated to objects without required relationships**; required master-detail/lookup → a parent record → provisioning (F6). — **D-110.3**
- **Standard-object product-demo VR (sandbox-content).** The 5 derivable sandbox VRs are all managed-package rules; the committed live proof uses one. A clean product demo (a user's own VR on a standard object, e.g. Opportunity) needs a simple VR added to the sandbox — a sandbox-content task, not an S3/S4 build. Would also *confirm* the platform-required short-circuit inference (b above). — **D-110.3**
- **Augment-both (N-recipe `EmissionBundle`).** A verified negative currently **replaces** the inspection recipe with the behavioral one (single-recipe). Emitting *both* (inspection: "the VR is configured" + behavioral: "the VR enforces") for one claim needs the bundle/persister to carry N recipes — a future **S6-disambiguation** play. — **D-110.3 / D-110.2**
- **Positive `data-recipe` (create→read→assert) + update/delete-rejected negatives.** The mechanical-completion vertical: needs full provisioning + cleanup (a created record to read/assert/delete). — **D-110 / D-108 F4**
- **The behavioral expect-rejection negative (D-100.2) — REALIZED.** The verified negative now constructs a violating mutation + observes the org's rejection (replacing inspection re-verify); the recipe-model expect-rejection is `expect_rejection`/`RejectionExpectation` (D-110.1). The v2 `ProhibitionClaimBody`-carries-the-payload framing was **superseded**: the payload lives in the *recipe*, not the claim, so the claim `identity_hash` stays stable (Option C honored without a claim-body change). — **D-100.2 → D-110.3**
- **Permission-based prohibition eval (the executor's `400 = business rejection` rule is VR-shaped).** The slice-2 grounded eval treats **HTTP 400** as the business rejection (correct for validation-rule / required-field / duplicate rejections, which Salesforce surfaces as 400). Permission-based prohibitions (FLS / sharing) surface as **HTTP 403 + `INSUFFICIENT_ACCESS_OR_READONLY`** — recognizing those as a business rejection (rather than the current "couldn't attempt → errored") needs the eval extended. Deferred until permission-based negatives are in scope. — **D-110.2**
- **Test-data provisioning (F6).** Inspection needs none; CRUD needs prerequisite records + cleanup (reusing / evolving v1's `data_engine` factory + the reverse-order / `PQA_%` cleanup mechanism). — **D-108 F6**
- **The mechanical-primitive lift-to-neutral (F1, the rest of it).** The inspection vertical only needed the `integrations/` exceptions + `_oauth_token`. The CRUD increment triggers lifting the REST transport client, the `$var` resolvers, the `data_engine` primitives, and the cleanup mechanism to a neutral shared module (resolving the substrate→v1 dependency direction). Per-increment, not up front. — **D-108 F1**
- **Further recipe kinds — `ui-recipe`, `event-subscription-recipe`, `callout-intercept-recipe`.** Deferred behind CRUD (browser / durable-event / callout-intercept runtimes). — **D-108 F4**

## 3. Result store and evidence

- **The A→B promotion (per-step structured child table).** Schema A is run-entity typed columns + an `evidence` JSONB trace. Promote per-step facts (edge, subject, `row_count`, `held`, …) to a structured child table **when a real per-step query need emerges** — S6's concrete query patterns, or CRUD's N-step shape. A→B is an additive forward migration (a child table beside the run row), reversible, not a lock. — **D-108.2**
- **Richer captured trace per recipe kind.** The JSONB trace is deliberately extensible; browser traces / screenshots (`ui-recipe`), before/after state + field diffs (`data-recipe`), and the reserved read-only N/As fill in as verticals land. — **D-108 F2**

## 4. The translator and capability matching

- **Edge→SOQL neutral-module consolidation.** The translator is finite + capture-keyed; Phase 3 (D-127) made `translate_read` a **read-shape dispatch** (edge-read vs entity self-read: Object→`EntityDefinition`, Field→`FieldDefinition`) for existence/property, reusing the read-half of the S1-sync Tooling query knowledge. The eventual neutral-module consolidation (when a second consumer appears) still stands. — **D-108.1 / D-127**
- **capability + layout *execution* (the under-specified-recipe deferral).** Phase 3 shipped existence + property execution (pure-S4); **capability + layout did not** — their metadata recipes carry one endpoint + the edge *type*, but the second endpoint (the grant target / the placed field) lives only in env-detail prose, so the translator can't build a live `FieldPermissions` / `Layout` query. Live execution needs an **Option-X recipe enrichment** (S2 `ReadMetadataStep` + S3 emission carry the structured second endpoint), reopening S2/S3 — deferred to a follow-on S3 recipe-enrichment slice; the S4 translator branch (`GRANTS_*` / `INCLUDES_FIELD`) lands with it. — **D-133**
- **Predicates beyond `exists` — `equals` / `is_null` LANDED (D-128).** The executor evaluates `exists` (existence / metadata-relationship) + `equals` / `is_null` (property, over a captured column value). `matches_pattern` / `not_equals` remain in the `AssertionPredicate` enum and **fail loud** until a recipe asserts them. Property's column map is finite + honest: `length`/`precision`/`scale` → `FieldDefinition`; `is_required` (page-layout-derived, no column) + `field_type` (describe-vocab vs `DataType`) **refuse** (`UnsupportedPropertyError`) until a describe-backed read lands. — **D-108 slice 2 / D-128**
- **Rich capability matching (F5).** `select_recipe_for_execution`'s membership match is used; capability-level fit (org-shape / edition / feature discovery driving the `available_environment`) is deferred. — **D-108 F5**
- **Bulk-fetch fallback for `APPLIES_TO` — not built (by design).** The scoped `EntityDefinition.QualifiedApiName` filter was live-verified, so the bulk-fetch + client-side-filter fallback was deliberately not pre-built. Recorded here only so a future reader knows it was considered and dropped. — **D-108.1 (Decision 2)**

## 5. Failure-path / remediation (F7, held by design)

- **S4 does not remediate.** S4 captures a failure's *truth* (the `errored` outcome + evidence); it does not fix it. The relationship between an S4 execution failure and the dormant v1 fix-and-rerun agent (G-001, `docs/v1_runtime/KNOWN_GAPS.md`) is settled later — S4 produces the evidence a remediation loop would consume. — **D-108 F7**

## 6. Smaller plumbing deferrals

- **`claim_version_seq` plumbing.** Carried through the plan / result store as nullable; v1 resolves the claim logically (recipe follows claim evolution). Pinning is reserved. — **D-108.2** (cf. S2 §6.4)
- **`select_recipe_for_execution` replay modes.** Only `replay_mode='live'` is supported; historical / semantic replay is reserved by S2. — **S2 SPEC §6.8**

## 7. Run-path orchestration scope

- **The run-path now conducts three substrates (S2 → S4 → S6).** `run.py` selects (S2), executes + finalizes (S4), then interprets + persists (S6) — the cross-substrate wiring lives in `execution_engine`. The S4↔S6 import cycle this created (`interpretation.attribution` imports `execution_engine.evidence`; `execution_engine/__init__` imports `run`) is resolved with a **call-time lazy import** in `_interpret_and_persist` — the convention `run_recipe_execution_for_tenant` already uses for the tenant connection. Proportionate at two consumers. If cross-substrate wiring grows (more stages, or a substrate that sequences across >3 others — e.g. an S8 evolution loop reading S6), a **dedicated orchestration layer above the substrates** may be warranted, so the run-path stops being both S4's executor *and* the cross-substrate conductor. Watch for: a third lazy-import cycle, or a stage that needs to sequence across more than three substrates. — **D-111.2**

---

## Cross-references (not S4-owned, tracked elsewhere)

- **S4-Q-001 — active-ness of the inspection claim (S3-owned).** Whether the inspection claim is *behaviorally* about active-ness, and whether `exists`-over-`APPLIES_TO` faithfully carries it (an active VR enforces; an inactive one does not — carrying it needs a `VR.active` constraint beyond edge-existence). Surfaced by S4 slice 2; an S3/emission representation concern (possible S1 dependency). Stays in `OPEN_QUESTIONS.md` (S4-Q-001), **not resolved in S4** — S4 realizes what the recipe asserts. — **D-108.1**

---

## References

- Design rationale: `DECISIONS_LOG.md` D-108 → D-108.4.
- Realized state: `SPEC.md` §Status + §6.
- The one parked question: `OPEN_QUESTIONS.md` S4-Q-001.
- Build history: `EVOLUTION.md`.
