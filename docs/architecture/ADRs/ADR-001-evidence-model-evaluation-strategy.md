# ADR — Evidence Model & Evaluation Strategy (the interpretation-boundary layer)

**Status:** Accepted (design) · implementation pending the S6 build
**Date:** 2026-06-25
**Scope:** S2 (claim/recipe representation) ↔ S4 (execution/evidence) ↔ S6 (interpretation/verdict)
**Origin:** opened as the §4a boundary-generation recon; **upgraded** to a keystone interpretation-boundary decision (§1).
**Home:** this file (`docs/architecture/ADRs/ADR-001-evidence-model-evaluation-strategy.md`) + a one-line **D-270** ratification pointer in `DECISIONS_LOG.md` (§7). *No persisted schema in this ADR.*

---

## 1. Context & the architectural shift

The pipeline today is implicitly **Claim → Recipe → Execution → Verdict**. In that shape the **recipe conflates two concerns**: *how to execute* (the operational steps, the SF API choreography) **and** *what evidence is sufficient* for the claim to count as satisfied. That conflation has been invisible only because there is **exactly one recipe per claim** today:

- Each `_author_*` returns **one `EmissionBundle` with one primary recipe** (single-probe authoring, this thread's recon).
- `EmissionBundle.secondary_recipes` (D-228) are **run-one fallbacks** — weaker/alternative realizations of the *same* probe, ordered by priority.
- `select_recipe_for_execution` returns **`matching[0]`** — exactly one recipe per execution (priority DESC); fallbacks run only if the primary's environment isn't satisfiable.

So "one recipe ran, did it pass?" was a complete account of evaluation. **Multiple probes break that.** Boundary-value analysis (BVA) for `0 ≤ x ≤ 100` is five distinct probes (`min−1`, `min`, `nominal`, `max`, `max+1`) spanning **both polarities** — and a single recipe can carry many *accept* steps but **at most one *reject*** (`DataRecipeBody._at_most_one_expect_rejection`, verified). The moment a claim needs N probes that must *all* run, "did the recipe pass?" is the wrong question; the question becomes **"do these N evidence results, together, verify the claim?"** — a *different concern* than how any single probe executed.

**The shift** makes that concern an explicit layer:

> **Claim → Evaluation Strategy → Recipe(s) → Execution → Evidence → Verdict**

`Recipe(s)` and `Execution` stay where they are. The new layer sits at the **interpretation boundary** (S6) and answers one question over the evidence S4 produced.

This is why a "boundary-generation" task (§4a) became an architecture decision: §4a's authoring is trivial *once we know what evidence verifies a multi-probe claim*. That "what verifies it" is the keystone — and it does not belong in the recipe.

## 2. Why this layer exists (durable rationale)

**The relationship between execution evidence and claim truth is not fixed.** Different testing methodologies require *different evidence* before a claim may be considered satisfied:
- "the field saves as X" → **one** create-and-verify probe must pass;
- "x is constrained to [0,100]" → the **whole boundary set** must behave (accepts accepted, rejects rejected);
- a future "the page renders correctly" → a **human or AI visual judgment**, not a DB read.

If that variable relationship is buried inside recipe generation or execution, then **every new methodology forces a change to claim identity or to the execution infrastructure** — the two things that must stay most stable. The Evaluation Strategy layer **isolates evidence semantics** from recipe generation and execution, so a new methodology (BVA, equivalence partitioning, decision-table, …) is added as a *strategy*, leaving claim identity (`identity_hash`) and the S4 run path untouched.

This is the platform principle, made literal across the substrate boundary:
- **Identity defines what is true** — the claim (S2). Authoring N probes must **not** change the claim's identity (the D-107 / Option-C invariant: the claim body is byte-identical regardless of how it's probed; probe payloads live in recipes, never the claim).
- **Execution gathers evidence** — S4 runs probes and reports what happened, without judging sufficiency.
- **Interpretation decides whether the evidence satisfies the truth** — S6. **Evaluation Strategy is the contract S6 applies** at that boundary, and its primary output is the `Verified` predicate (§3b).

## 3. The three contracts

*Responsibilities / Inputs / Outputs / Required guarantees / Open decisions. No schema, no columns, no persistence — contracts only.*

### 3a. Evidence production — **S4-owned**

- **Responsibilities:** execute one probe and report *what happened*, with zero judgment about whether it verifies the claim.
- **Inputs:** a single probe specification (a recipe, or one boundary-probe of a claim) + the target environment.
- **Outputs — `ProbeResult` (conceptual shape, not schema):** execution `duration`; the `actual value` observed (the read-back field value, the accept/reject outcome); `diagnostics` (error code/message, rejection signal, step trace); a probe-local `pass/fail` *against that probe's own expectation*; and `artifacts` (references to richer evidence — today the captured `RunEvidence` steps; in future a screenshot, a replay log).
- **Required guarantees:** evidence is **descriptive, never interpretive** — a `ProbeResult` records the observation and the probe's own polarity expectation, but does **not** decide claim verification. Evidence is **methodology-agnostic in shape**: one `ProbeResult` envelope carries results from any evidence kind.
- **Critical scoping (state plainly):** **today there is effectively ONE evidence kind — directly-set-state execution** (S4 Slice 1 / D-115: create the record, read it back, assert the value). Everything S4 produces flows through `RunEvidence` → `outcome ∈ {passed, failed, errored}`. **UI replay, API replay, human verification, and AI visual inspection are FUTURE evidence kinds** the `ProbeResult` contract must *accommodate by shape* — they are **not** built and must not be assumed. The contract keeps the envelope wide enough that adding them later is an S4 extension, not a re-layering.

### 3b. Evaluation Strategy — the new layer (S6-applied)

- **Responsibilities:** given the **set of `ProbeResult`s for one claim**, decide whether the claim is verified under a named methodology.
- **Inputs:** the claim (its asserted truth) + the labelled set of `ProbeResult`s produced for it + the strategy kind.
- **PRIMARY OUTPUT — the `Verified` predicate:** a **single per-claim boolean** — *"given the evidence, is this claim verified?"* **`Verified` is the coverage layer's SOLE input.** Coverage reads the boolean and **never sees probes, recipes, or roll-up rules** — the entire complexity of N probes and their aggregation is collapsed here, behind one bit, before coverage ever looks. (The richer state behind the boolean is the Verdict, §3c; `Verified` is its coverage-facing projection.)
- **First strategy kinds (built first):**
  - **`single`** — today's behavior: one recipe; **one passing recipe → `Verified = true`.** A pure pass-through, so the existing one-recipe-per-claim path is the degenerate case of the new layer (zero behavior change for existing claims).
  - **`bva`** — boundary-value analysis: **all *required* boundary probes pass → `Verified = true`** (each accept accepted, each reject rejected). *Which* probes are "required," and the exact pass rule, are **open decisions** (§4).
- **Future kinds (extension points, NAMED not built):** equivalence-partitioning, decision-table, pairwise, state-transition-coverage. Each slots into this contract — no change to claim identity or S4.
- **Required guarantees:** strategy selection is **deterministic** and **claim-derived** (the strategy follows from the claim's constraint shape, never invented per run); applying a strategy is **pure over the `ProbeResult` set** (no execution, no S1/SF access); and a strategy **must reduce N probe results to exactly one `Verified` boolean**.
- **Open decisions:** the roll-up semantics that produce `Verified` — see §4 (owned by the S6 build).

### 3c. Verdict — the richer states behind `Verified`

- **Responsibilities:** turn the strategy's judgment (+ the evidence) into the **claim-level state** the platform consumes (the GO/NO-GO decision engine, the results UI, grounding) — the *explainable* form of which `Verified` is a one-bit summary.
- **Inputs:** the strategy result + the `ProbeResult` set + the run-level outcome.
- **Outputs — reconcile against what already exists, do NOT mint fresh vocabulary in the abstract:**
  - **S4/S6 already emit** `passed` / `failed` / `errored` (the live runs UI), and S6 already carries a richer verdict vocabulary (`value_persisted` / `value_not_persisted`, `asserted_metadata_present` / `absent`, `prohibition_enforced`, `not_evaluated`, …) which the consumer surfaces translate to plain language.
  - A multi-probe verdict must **map onto** that existing set first (a `bva` claim that verifies → `passed`; one boundary mis-behaving → `failed`; a probe that couldn't run → contributes to `not_evaluated`/`errored`).
- **`Verified` is the coverage-facing PROJECTION of the verdict** — **independent of how many states the verdict enum ends up with.** However many verdict states S6 lands on, exactly one of them maps to `Verified = true` (or a defined subset does); coverage depends only on that projection, never on the enum's cardinality. This is what lets the verdict vocabulary grow without ever touching the coverage spec.
- **Open decision (for the S6 build):** whether the final state set is **extended** beyond `passed/failed/errored` with states multi-probe evaluation makes meaningful — e.g. **Blocked** (a prerequisite probe couldn't run), **Needs-clarification** (the constraint wasn't derivable to a boundary set), **Incomplete** (some probes ran, some skipped/timed out). **This ADR does not decide the final state set** — it fixes only that (a) the set degrades cleanly to today's three, and (b) `Verified` is its stable projection.

## 4. Open semantic decisions — **owned by the S6 build** (enumerated, deliberately unanswered)

These determine the eventual persisted model — **which is why persistence is deferred until they're decided**:
1. **Mandatory probes** — are all probes in a strategy mandatory, or may some be optional/skippable without flipping `Verified`?
2. **Failure aggregation** — one-failure-fails (strict AND), or a threshold / weighted rule (e.g. "≥90% of boundary probes")?
3. **Timeout semantics** — is a timed-out probe a failure, an "incomplete", or retryable?
4. **Retry semantics** — are probes retried, and does a retried-pass count the same as a first-pass?
5. **Warning vs failure** — can a probe register a *warning* (noted, non-fatal) distinct from a *failure*?
6. **Conditional probes** — may a probe be gated on another's result (run `max+1` only if `max` accepted)?
7. **Skip / N-A** — how is "this boundary doesn't apply to this field type" recorded so it neither passes nor fails (and how it affects `Verified`)?

Each answer changes what must be stored per probe and per claim — hence **no schema until §4 is settled**.

## 5. Non-goals (required)

This layer does **NOT**:
- generate recipes (S3 authoring / §4a),
- execute recipes or call Salesforce (S4),
- diagnose *why* a probe failed or attribute a cause (S6 attribution / the repair agent),
- **determine coverage** — it **emits `Verified`; the coverage layer *consumes* `Verified`** and never recomputes it from evidence,
- prioritize risk or decide release GO/NO-GO (the decision engine).

It answers **exactly one question:** *given the evidence collected, has this claim been sufficiently evaluated?* — and expresses the answer as `Verified`.

## 6. Sequencing & relationships

- **§4a (boundary generation) folds into this + S6.** Authoring N probes becomes near-trivial *once the evaluation semantics are fixed* — the authoring layer just emits the probe set the strategy defines; the hard part (what the set means → `Verified`) lives here, not in emission.
- **The coverage ladder is LOCKED: `Has Claim → Approved → Verified`, with the headline number = `Verified`.** Coverage's **only** dependency on this ADR is **the definition of `Verified`** — it reads that one boolean per claim and **never interprets evidence, probes, recipes, or roll-up rules**. That single, narrow dependency is precisely why **this ADR precedes locking the coverage spec**: lock coverage first and you bake in an implicit single-probe definition of `Verified`.
- **The persisted model lands WITH the S6 build, not now.** Contracts are stable; storage waits on §4.
- **The recurring decoupling pattern.** This is the substrate's repeated move — separating a stable identity from a variable surface: **Claim / TestCaseView** (identity vs presentation, D-267 register split), **Claim / Recipe** (truth vs realization), **Narrative / Traceability** (business spine vs technical detail, D-267), and now **Evidence / Verdict** (what was observed vs whether it verifies the truth). Each isolates change so identity stays fixed.
- **Execution-side dependency (carry-over from §4a recon):** boundary probes must **all run**, but `select_recipe_for_execution` returns **one** and `secondary_recipes` are run-one fallbacks. So this S6 layer presupposes an S4 **run-all** path over a claim's probe set — a sibling slice the S6 build assumes, not a free given.

## 7. Decision & status

**Decision:** Adopt the **Evidence Model & Evaluation Strategy** as the explicit **interpretation-boundary layer** (`Claim → Evaluation Strategy → Recipe(s) → Execution → Evidence → Verdict`), whose **primary output is the per-claim `Verified` predicate** consumed by the locked coverage ladder. **Define the three contracts now** (§3); **defer persistence and the semantic decisions (§4) to the S6 build.** Strategy `single` preserves today's behavior exactly (degenerate one-probe case); `bva` is the first new kind; further kinds are extension points.

**Status:** **Accepted (design).** Implementation pending the S6 build.

**Pointers:**
- This file is the ADR of record.
- A one-line **D-270** pointer in `docs/architecture/DECISIONS_LOG.md` records the ratification.
- Per-substrate SPEC touch-ups (S4 `RunEvidence`/`ProbeResult` envelope note; S6 `EvaluationStrategy` + `Verified` section) land **with the S6 build**, not now.
