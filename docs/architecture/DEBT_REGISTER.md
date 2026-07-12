# Technical Debt Register

**Extracted 2026-07-13 (Reasoning Architecture V1 mission).** Companion to
`REASONING_ARCHITECTURE.md`. Three categories, kept separate: **DEBT** (a
present cost the code pays), **ENHANCEMENT** (a capability the architecture
already shapes but does not need yet), **RESEARCH** (a question that needs
evidence before it deserves design). Items are removed when closed; the log
entry that closes one should cite it.

## Debt

| # | Description | Architectural impact | Benchmark affected | Effort | Priority |
|---|---|---|---|---|---|
| D1 | **Bare-api-name derivation duplicated ~25×** — `rsplit(".", 1)[-1]` inline across governance_core + entity_attributes; one drifted variant would silently mis-key a field map. | Cross-cutting naming seam with no single owner. | none directly (latent) | S (helper + mechanical sweep + suite) | Medium — fold into the next governance-touching slice, not standalone. |
| D2 | **Gate-harness screens are stale by design-lag** — the scratch live-gate script flags every PLS_FB automation bind as a false positive, so every gate since Slice 1 prints FAIL and the operator must re-derive the verdict from claim content. | Erodes trust in gate output; masks a real wrong bind arriving amid legitimate ones. | FB-V1 gate runs (evidence quality, not product) | S (teach the script IR-verified binds; scratch-side) | Medium — before the FL03 gate. |
| D3 | **AC-level coverage blind to control-level loss** — measured by the D-361 lifecycle telemetry, uncorrected by decision (promotion boundary). The debt is the *decision debt*: choose Phase-1 promotion or its rejection after FB-V1 evidence accumulates. | Recovery loop can silently drop a control while the AC stays covered. | Both benchmarks' scoreboards | M (Phase 1 design exists in the D-361 plan) | High — schedule the decision, not necessarily the build. |
| D4 | **Pre-D-362 refusal rows carry no provenance tags** — readers must know the cutover date to interpret old outcomes. | Historical-read ambiguity only. | none (historical) | S (backfill script) or zero (document the cutover) | Low. |

## Enhancement

| # | Description | Architectural impact | Benchmark affected | Effort | Priority |
|---|---|---|---|---|---|
| E1 | **IR guard vocabulary → S2 `Condition` convergence** — IR guards are bare tuples; rejection-condition clauses are typed S2 conditions. FL03's negation-context chains will stress the tuple form. | One guard language across declarative + procedural reasoning. | FL03+ | M | High — fold into FL03's design, not before. |
| E2 | **Witness-synthesis module boundary** — value generators live in three homes (verified_negative, decision_branch `_satisfy`, governance transform witness). Correct today; a fourth generator should force one entry-point module. | Discoverability + reuse of typed-value discipline. | FL03 (band-interval witnesses are that fourth class) | M | High — do *as* FL03's witness work, extracting rather than adding. |
| E3 | **`regex_matching_value` grammar widening** (letter classes, small alternations) + wiring matching-value synthesis into the negative-derivation engine's `REGEX true` branch (currently Undecidable by design). | Wider format-rule coverage for both prohibitions and transforms. | Any org with richer format rules | S–M | Medium. |
| E4 | **Recovery for RecordType / CustomMetadata** — allowlisted (D-362) but pool-less until consumers exist. | Completes the lexical-recovery contract. | none yet | S | Low. |
| E5 | **After-save IR grammar** (recordCreates/Updates as IR behaviours with guards) — unlocks B2's cross-object counts/absence differentials on IR rails instead of the D-318 glance. | Single behaviour representation for both save phases. | FL04/05/07/09-15 | L | Deferred to the B2 arc by design. |
| E6 | **Per-full-run scoreboard determinism** — claim identity is deterministic; the *set* of side-claims varies run-to-run (model breadth). A scoreboard-level determinism definition (core-set vs halo) would sharpen gates. | Gate semantics. | FB-V1 | S (definition) | Medium. |

## Research

| # | Description | Why research, not design | Trigger to promote |
|---|---|---|---|
| R1 | **General differential combinator** — dimension + mutation + expectation-flip as one declared shape (today: per-capability implementations that share discipline, not code). | Two instances share structure; three would reveal whether the abstraction is real or ceremonial. | A third differential dimension (e.g. permission-context or after-save). |
| R2 | **Order-of-execution composition (B3)** — stating the FL02×VR01 interplay as a claim ("lowercase input is accepted *because* normalization precedes validation") rather than consuming it silently at witness time. | Needs a claim-kind design with attribution semantics for *rule interactions*, not just automations. | B2 landing; or a benchmark AC that names the interplay. |
| R3 | **Journey execution** — multi-transition accumulated state (D-310/D-312 banked blueprint). | Blocked on a product decision about run-model shape, not on architecture. | AK greenlight. |
| R4 | **Control-oriented recovery promotion** (D-361 Phases 1–3). | Deliberately deferred until FB-V1 provides independent evidence. | FB-V1 scoreboard reaching FL03+. |
