# Substrate 4 — Execution Engine — Deferred Items

The single forward-looking list. The **first vertical** (metadata-inspection) is built + merged + live-proven (SPEC §Status; DECISIONS_LOG D-108 → D-108.4). This document consolidates everything the design + the four slices deliberately deferred, each with its D-entry reference, so the next phase has one place to read first.

Authored at the first-vertical close-out (2026-05-27). Append corrections as a dated note; do not silently rewrite.

---

## 1. Orchestration and the production trigger

The synchronous run path is realized (`run_recipe_execution` + `run_recipe_execution_for_tenant`, transaction boundary A). What turns it into a fired-in-production capability is deferred:

- **Async / worker orchestration (the scaling restructure).** Boundary A holds one DB transaction across the live read — correct for the bounded, low-concurrency sync path, but it does **not** generalize. The async path must **not** hold a DB transaction across external I/O; it brackets the live read with brief transactions (orchestrating select / persist / posture directly rather than under one umbrella). — **D-108.4**
- **The run trigger (route / scheduler / worker tick).** Nothing fires runs yet — no v1 route, scheduled job, or worker tick calls the run path against a real recipe. The trigger that selects *which* recipes to run, *when*, and surfaces results is deferred (it consumes `run_recipe_execution_for_tenant`). — **D-108.4**
- **Tenant-enumeration / worker co-location.** When the trigger lands, its tenant discovery + process placement mirror the open S3 questions (worker co-location vs dedicated process; `information_schema` vs `shared.tenants`). — **D-108.4** (cf. S3 D-106.4)

## 2. The second vertical — CRUD / `data-recipe` (F4)

The metadata-inspection vertical is the thinnest spine; the behavioral verticals are deferred:

- **`data-recipe` (CRUD) executor.** A distinct runtime (create / update / delete + verify) reusing the lifted mechanical primitives; the first behavioral kind. — **D-108 F4**
- **The behavioral expect-rejection negative (D-100.2).** The CRUD vertical carries it: construct a violating mutation, observe the org's rejection (vs today's *inspection* re-verification). Pairs with the S3 side (the recipe-model expect-rejection step + the v2 `ProhibitionClaimBody` carrying the violating payload, Option C). — **D-100.2 (S3) / D-108 F4**
- **Permission-based prohibition eval (the executor's `400 = business rejection` rule is VR-shaped).** The slice-2 grounded eval treats **HTTP 400** as the business rejection (correct for validation-rule / required-field / duplicate rejections, which Salesforce surfaces as 400). Permission-based prohibitions (FLS / sharing) surface as **HTTP 403 + `INSUFFICIENT_ACCESS_OR_READONLY`** — recognizing those as a business rejection (rather than the current "couldn't attempt → errored") needs the eval extended. Deferred until permission-based negatives are in scope. — **D-110.2**
- **Test-data provisioning (F6).** Inspection needs none; CRUD needs prerequisite records + cleanup (reusing / evolving v1's `data_engine` factory + the reverse-order / `PQA_%` cleanup mechanism). — **D-108 F6**
- **The mechanical-primitive lift-to-neutral (F1, the rest of it).** The inspection vertical only needed the `integrations/` exceptions + `_oauth_token`. The CRUD increment triggers lifting the REST transport client, the `$var` resolvers, the `data_engine` primitives, and the cleanup mechanism to a neutral shared module (resolving the substrate→v1 dependency direction). Per-increment, not up front. — **D-108 F1**
- **Further recipe kinds — `ui-recipe`, `event-subscription-recipe`, `callout-intercept-recipe`.** Deferred behind CRUD (browser / durable-event / callout-intercept runtimes). — **D-108 F4**

## 3. Result store and evidence

- **The A→B promotion (per-step structured child table).** Schema A is run-entity typed columns + an `evidence` JSONB trace. Promote per-step facts (edge, subject, `row_count`, `held`, …) to a structured child table **when a real per-step query need emerges** — S6's concrete query patterns, or CRUD's N-step shape. A→B is an additive forward migration (a child table beside the run row), reversible, not a lock. — **D-108.2**
- **Richer captured trace per recipe kind.** The JSONB trace is deliberately extensible; browser traces / screenshots (`ui-recipe`), before/after state + field diffs (`data-recipe`), and the reserved read-only N/As fill in as verticals land. — **D-108 F2**

## 4. The translator and capability matching

- **Edge→SOQL neutral-module consolidation.** The translator is finite + edge-keyed; today it maps the **one** edge the vertical needs (`APPLIES_TO`). As the edge set grows (and especially when a second consumer appears), consolidate the encoded translation knowledge into a neutral module — reusing the read-half of the S1-sync Tooling fetchers' query knowledge (not the fetcher objects, which carry sync-world assumptions). — **D-108.1**
- **Predicates beyond `exists`.** The executor evaluates `exists` (the only predicate the emitted inspection recipe asserts); `equals` / `is_null` / `matches_pattern` exist in the `AssertionPredicate` enum and **fail loud** until built (when a recipe asserts them). — **D-108 slice 2**
- **Rich capability matching (F5).** `select_recipe_for_execution`'s membership match is used; capability-level fit (org-shape / edition / feature discovery driving the `available_environment`) is deferred. — **D-108 F5**
- **Bulk-fetch fallback for `APPLIES_TO` — not built (by design).** The scoped `EntityDefinition.QualifiedApiName` filter was live-verified, so the bulk-fetch + client-side-filter fallback was deliberately not pre-built. Recorded here only so a future reader knows it was considered and dropped. — **D-108.1 (Decision 2)**

## 5. Failure-path / remediation (F7, held by design)

- **S4 does not remediate.** S4 captures a failure's *truth* (the `errored` outcome + evidence); it does not fix it. The relationship between an S4 execution failure and the dormant v1 fix-and-rerun agent (G-001, `docs/v1_runtime/KNOWN_GAPS.md`) is settled later — S4 produces the evidence a remediation loop would consume. — **D-108 F7**

## 6. Smaller plumbing deferrals

- **`claim_version_seq` plumbing.** Carried through the plan / result store as nullable; v1 resolves the claim logically (recipe follows claim evolution). Pinning is reserved. — **D-108.2** (cf. S2 §6.4)
- **`select_recipe_for_execution` replay modes.** Only `replay_mode='live'` is supported; historical / semantic replay is reserved by S2. — **S2 SPEC §6.8**

---

## Cross-references (not S4-owned, tracked elsewhere)

- **S4-Q-001 — active-ness of the inspection claim (S3-owned).** Whether the inspection claim is *behaviorally* about active-ness, and whether `exists`-over-`APPLIES_TO` faithfully carries it (an active VR enforces; an inactive one does not — carrying it needs a `VR.active` constraint beyond edge-existence). Surfaced by S4 slice 2; an S3/emission representation concern (possible S1 dependency). Stays in `OPEN_QUESTIONS.md` (S4-Q-001), **not resolved in S4** — S4 realizes what the recipe asserts. — **D-108.1**

---

## References

- Design rationale: `DECISIONS_LOG.md` D-108 → D-108.4.
- Realized state: `SPEC.md` §Status + §6.
- The one parked question: `OPEN_QUESTIONS.md` S4-Q-001.
- Build history: `EVOLUTION.md`.
