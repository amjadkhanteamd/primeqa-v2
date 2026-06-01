# Substrate 3 — Generation Engine — Deferred Items (Phase-3 ledger)

The single forward-looking list. Phase 1 (design, D-070–D-094) and Phase 2 (implementation, D-095–D-106) are complete; the realized surface is a thin vertical (two emittable claim kinds — see SPEC §9). This document consolidates everything those phases deliberately deferred, each with its D-entry reference, so Phase 3 has one place to read first instead of re-combing 37 decision entries.

Scope note: D-098 superseded the original D-097.1 debut (data-behavior value-claim → configuration metadata-relationship). Consequently "data-behavior value-claim positive" is now squarely a Phase-3 deferral, not a v1 surface.

Authored at Phase-2 closeout (2026-05-24). Append corrections as a dated note; do not silently rewrite.

---

## Update 2026-05-25 — D-107 (verified negatives) landed; residual deferrals

**§1's "Formula parser → Layer-2 verified negatives" (D-100.1) LANDED — as *static* Layer-2 verification.** Validation-rule negatives whose formula parses and yields a derivable violating value now emit `LAYER_2` with the caveat dropped (SPEC §10; DECISIONS_LOG D-107). The §1 bullet stays as the historical statement of the deferral; this note records its resolution. Still open from this phase:

- **Conservative derivation bail-cases.** `derive()` (D-107 slice 3) returns not-derivable → caveated for: non-numeric ordering (`<` / `>` on string or boolean literals), `NOT(ISBLANK)` / `NOT(ISPICKVAL)` (no certain non-blank / alternate value), bare-boolean predicates, field-to-field comparisons, cross-object dotted refs, and org-state functions (`PRIORVALUE` / `ISCHANGED` / `ISNEW`). These are *correct, honest* caveats today — the bar is single-object / create-time / certain — expandable as derivation coverage grows. — **D-107.1 / D-100.1**
- **D-100.2 — the behavioral expect-rejection recipe — LANDED (2026-05-27, D-110.3, S3-thin).** A verified negative now emits the **behavioral** recipe: `_author_negative` constructs the violating *create* (the D-107 parser's `violating_payload`, previously discarded) + `expect_rejection`, replacing the inspection re-verify; S4 executes it and matches the org's rejection (live-proven end-to-end against a real VR). (i) The recipe-model expect-rejection step is S2's `expect_rejection`/`RejectionExpectation` (D-110.1). (ii) The "v2 `ProhibitionClaimBody` carrying the violating payload" was **superseded** — the payload lives in the *recipe* (operational), not the claim, so the claim `identity_hash` stays stable (Option C honored, no claim-body change). **Residual:** caveated negatives (non-derivable formula) stay inspection — widening = the parser's future work; required-field population (S3-A) is deferred-not-needed for VR-enforced prohibitions (D-110.3 result). — **D-100.2 → D-110.3**

The §3 capability-gated probe-envelope item: the verified-negative half now has a passing live-eligible probe (`verified-prohibition-negative`); the existence / property config half remains. S1-side residuals (cross-object dotted REFERENCES; `references_status` backfill; the Fork-C junction framework) live in S1 `PHASE_2_PLAN_corrections.md` §17.

---

## Update 2026-05-25 — D-106.4 (production integration) landed; deferred/tracked

**D-106.4 — the S3 production-integration service layer LANDED.** `run_generation` (D-106.1) is now wrapped by a per-tenant job queue (`s3_generation_jobs` + attempts, two-layer idempotency), caller-fed intake, a worker consumer, a v1-side enqueue endpoint, and a stale-job reaper — pilot-drivable end-to-end (enqueue → consume → complete → ledger). See SPEC §11; DECISIONS_LOG D-106.4 (+ slice 1–5 amendments). Deferred / tracked from this phase:

- **HTTP-route test.** A Flask-client test of the enqueue / status / cancel routes needs a combined v1+substrate test DB (the test infra splits v1/Railway from the substrate/governance DB; production is one DB). Behavior is covered at the service level. — **D-106.4 (slice-4 amendment)**
- **Requeue-with-cap.** Stale/failed jobs are terminal today; automatic requeue bounded by `attempt_count` is a clean future increment (the `s3_generation_job_attempts` machinery exists). — **D-106.4 (slice-5 amendment)**
- **Tenant-enumeration unify.** The worker discovers tenants via `information_schema`; the reaper via `shared.tenants`. Both are resilient; unify on one source. — **D-106.4 (slices 3 / 5)**
- **Mid-run heartbeat.** The consumer heartbeats only at claim/start (`run_generation` is one blocking call, no hook); a mid-run heartbeat needs threading. The reaper's generous timeout compensates. — **D-106.4 (slice-3 amendment)**
- **Connection-held-across-LLM-latency scale mitigation.** The S1-read connection is held across the batch (pilot-acceptable; keepalives + small batches). — **D-106.4 / D-106.1**
- **Batch-claiming / finer granularity.** One job per (requirement, version), claimed one-per-tenant-per-tick; batch claiming + finer progress is deferred. — **D-106.4**
- **Dedicated generation process.** The consumer co-locates in the existing worker (Fork A); a dedicated process is deferred. — **D-106.4**

Not included (by design): **best-effort-continue** — abort-on-error is retained (D-106.3).

---

## 1. Emission and claim-kinds

The realized emittable set (`emission.EMITTABLE`) is 3 of substrate-2's 16 claim kinds: `configuration / metadata-relationship-claim`, `data_behavior / prohibition-claim`, and `data_behavior / value-claim` (D-115.3). The rest:

- **Non-config emission (the umbrella).** Authoring for the remaining data_behavior + other-archetype claim kinds. `emission-deferred` (D-105) is its runtime face — grounded-but-unbuilt kinds refuse gracefully. — **D-097.6** (runtime face **D-105.2**)
- **`value-claim` positives — FULLY LANDED (D-115.3 mechanism + D-115.4 live reach); only the periodic live confirmation remains.** The S3 path is complete: `_author_positive` (side A) + the **governance grounding stash** (`resolve_intent` grounds a field-and-value `GroundedPositive` — verify-at-grounding on the **named** field; the value rides `target_subject_hint.expected_value` verbatim) + `EMITTABLE += value-claim` + the **propose prompt** (frozen `generation@v2`: supply `field_name` as the fully-qualified `Object.Field` + `expected_value`; no fabrication when no value is stated) + an **offline+live eval probe** (`value-claim-positive-draft`). A real grounded value-claim now `PROCEED`s to emit; field-but-no-value → `EMISSION_DEFERRED`; unknown field → `insufficient_grounding`. **Residual:** only the *periodic* live confirmation — a real-LLM run (with `ANTHROPIC_API_KEY`) proving a requirement emits end-to-end; the live probe is authored but skipped in CI, exactly as the verified-negative's live twin. — **D-100.3 / D-115 / D-115.1 / D-115.3 / D-115.4**
- **`state-transition-claim` and `automation-effect-claim` emission.** (automation-effect also Apex-tier-gated — see §6.) — **D-100.3 / D-100.5**
- **Configuration `existence-claim` / `property-claim`.** Tied to the S1 detail-read increment (§6). — **D-098.4 / D-100.3**
- **Remaining archetypes — permission, UI, integration.** No emittable kinds today. — **D-100.4** (D-080 / D-081 / D-082)
- **Formula parser → Layer-2 verified negatives.** The Phase-3 differentiation headline: upgrades validation-rule negatives from Layer-1-plausible (caveated) to Layer-2-verified (caveat drops). — **D-100.1** (origin D-070 §2.2 / D-078 / D-083e)
- **Expect-rejection recipe observation mode.** The recipe model has no expect-rejection/expect-error step; a behavioral negative is double-gated on parser + this recipe-model addition. — **D-100.2** (parser-gated test, D-101.2)

## 2. Decomposition and selection

- **Multi-candidate flow (dormant infra).** The ≥2-grounded → `select_canonical` → `accept_selection` path is built and `accept_selection` is implemented + tested, but the `DecompositionController` emits a single canonical candidate per failure mode in v1, so the selection turn never fires. — **D-086 / D-095.3**
- **Bounded top-K enumeration.** Top-K candidates per failure mode (K configurable per `governance_context`); v1 enumerates one. — **D-083d**

## 3. Eval and probes

- **Full eval corpus.** v1 target 200–500 cases; the shipped corpus is a handful of fixtures. — **D-102.3 / D-090d**
- **Continuous-production drift sampling.** — **D-102.3**
- **Performance evals.** Cost / latency from `llm_calls` telemetry. — **D-102.3 / D-090c**
- **Rotating / hidden / adversarial probes.** Mitigation against per-probe envelopes becoming hidden prompt-training fixtures. — **D-104.5**
- **Per-archetype × model quality envelopes.** v1 envelopes are relative to the canonical routing profile only. — **D-092.c / D-104.5**
- **Capability-gated probe-envelope revisit.** The live-eval per-probe envelopes encode today's emittable set as invariants: coherent existence/property config readings and a (hypothetical) non-caveated verified negative currently surface as refusals that **auto-fail** the must-draft / caveat invariants. Revisit those invariants when their emission lands (§1 — existence/property `D-098.4`; Layer-2 verified negatives `D-100.1`): the auto-fails become acceptable drift. — **D-104**

## 4. Production-integration (the layer wrapping the in-process runner)

`run_generation` (D-106) is the in-process core; everything that turns it into a service is deferred:

- **Trigger / intake, auth, async job queue, retry / idempotency.** Re-running an aborted batch conflicts on the `request_id` PK — fresh id per attempt or upsert is an API-layer strategy. — **D-106.4**
- **Best-effort-continue error policy.** v1 is abort-on-error (with per-requirement-committed isolation); best-effort-continue needs a runtime error hook. — **D-106.3**
- **Connection lifetime for scale.** The S1-read connection is held across LLM latency (pilot-acceptable with keepalives + small batches); flagged for scale. — **D-106.4**

## 5. Ledger and provenance

- **Semantic-ledger retirement into substrate-2 `get_provenance`.** `generation_requests` / `generation_outcomes` migrate to S2 provenance when that interface ships. — **D-074**
- **Full replay / regeneration controller.** Drift events, lineage comparison, `transparency_policy_version` migration. — **D-096.4 / D-100.5**
- **`transparency_policy_version` machinery.** Bump rules + replay-under-version-migration. — **D-075** (Theme 6)
- **`llm_calls` archival / aggregation policy.** Named when storage pressure surfaces. — **D-074**
- **`llm_calls` substrate boundary.** May eventually move to a future observability substrate. — **D-074**

## 6. S1-tier-gated admissibility

These lift automatically as substrate-1 deepens; the v1 admissibility ceiling tracks the current S1 tier.

- **Apex / S1 Tier 2 → effect-tractable depth.** automation-effect-claim, platform-event/callout/inbound-effect admissibility beyond existence-only. — **D-078 / D-082 / D-100.5**
- **StandardValueSet / standard-picklist detection → value-claim accepted-values.** ~95 StandardValueSets exist but no edges to standard fields (S1 §22). — **D-097.2 / D-098.1**
- **Integration interaction-topology admissibility.** Cross-system causality, external observability, temporal sequencing, protocol semantics — its own framework. — **D-082 / D-084**
- **Permission run-as-execution upgrade.** When S1 Tier 2 ships sharing rules / OWD / Apex sharing, currently-refused complex permission claims upgrade. — **D-080**
- **UI Lightning composition (S1 Tier 3).** element-state-claim beyond layout-derivable elements. — **D-081**

## 7. Engine internals

- **Multi-hop `traverse`.** Neighborhood scoping is single-hop today. — **D-096.1**
- **Layer B as full semantic-support verifier.** v1 Layer B is a reject-only sanity floor (excerpt-anchoring length); full semantic verification is gated on the same end-state as Layer 2. — **D-096.3**
- **Free-form prose / LLM-generated rationale in `attempted_interpretation`.** v1 is structured-only; prose canonicalization is heavier architecture. — **D-075**
- **UI + integration prompt fragments.** v1 ships base + 3 fragments (data-behavior, configuration, permission). — **D-089 / D-103.2**

## 8. Taxonomy reservations (reserved, non-breaking)

- **CONFIDENCE dismissal_reason category.** Reserved slot, no v1 entries. — **D-076**
- **dismissal_reason ordering + weighting.** v1 unordered / unweighted. — **D-076**
- **Refusal multiplicity hierarchy / sequencing.** v1 flat-list; causality DAG / repair-path ordering reserved. — **D-073**
- **Future operational refusal kinds.** e.g. `operational-model-unavailable`, `operational-rate-limit-exhausted`. — **D-088**
- **`inspection-trigger` bifurcation.** Possible later split into invariant vs observational inspection. — **D-099.5**

## 9. Governance theory (unresolved) and Theme-7 calibration

- **Semantic-adjudication theory.** Validity-as-space vs validity-as-point in ambiguous enterprise QA; v1 commits to reproducibility within a wider acceptability space. — **D-090.f / D-093.d**
- **Principled mechanical theory of semantic improvement.** v1 ships maintainer-judged evolution adjudication bounded by invariants. — **D-093.b**
- **Fixed numerical quality thresholds.** v1 ships provisional per-archetype ranges. — **D-092.d–e / D-093**
- **Automated drift-judgment classifier.** v1 ships documented signatures + maintainer judgment. — **D-093.c**
- **Routing calibration.** Per-archetype × model behavioral profiles; the Sonnet cost-win is contingent on callers setting `archetype_hint`. — **Theme 7** (D-091 / D-092 / D-093 / D-106.2)
- **Cross-model identity-stability validation.** Model routing affects `explanation_hash` but must not affect `identity_hash`; validated empirically in Theme 7. — **D-091.a / D-092.c**
- **Admissibility-confidence calibration.** Per-archetype tuning of the `policy_restraint` threshold (now governance_context per D-094). — **D-084 / D-094** (Theme 7)
- **Per-archetype within-batch model routing.** v1 is single-model-per-batch; finer routing deferred until per-model profiles are characterized. — **D-091 / D-092.c**

---

## References

- Design rationale: `DECISIONS_LOG.md` D-070–D-106.
- Realized state: `SPEC.md` §9.
- Architectural invariants (the calibration floor that bounds all of the above): `SUBSTRATE_3_WORLDVIEW.md` §3.
- Build history: `EVOLUTION.md`.
