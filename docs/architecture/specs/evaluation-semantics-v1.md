# Evaluation Semantics v1 (settles ADR-001 §4 for `bva`)

**Status:** Accepted (design) · the S6 build implements; schema lands with the build
**Date:** 2026-06-25
**Depends on / cited from:** [ADR-001 — Evidence Model & Evaluation Strategy](../ADRs/ADR-001-evidence-model-evaluation-strategy.md) (D-270). This spec does **not** redefine ADR-001's layering, the three contracts, or the `Verified` projection — it **fills in ADR-001 §4's open semantic decisions** with the now-locked answers and **cites ADR-001 for everything else**. It also does not pin any S6 internal or any schema (ADR-001 §4: persistence lands with the build).

---

## 1. Purpose & scope

ADR-001 §4 enumerated the **evaluation-semantic decisions** (mandatory probes, failure aggregation, timeout, retry, warning, conditional, skip) as **OPEN — owned by the S6 build**. They are now **SETTLED for `bva` v1**. This spec records those settled answers as the contract the S6 build references.

**Scope.** This spec governs the **`bva`** strategy (boundary-value analysis) — the first multi-probe Evaluation Strategy (ADR-001 §3b) — and is **the contract that future strategy variants extend** (equivalence-partitioning, decision-table, pairwise, state-transition; ADR-001 §3b "Future kinds").

**`single` is UNCHANGED by all of this.** Every existing single-probe claim behaves exactly as today; `single` is the **degenerate N=1 case** of the rules below (ADR-001 §3b: "one passing recipe → `Verified = true`," a pure pass-through). Nothing in §1–§6 alters the live one-recipe-per-claim path.

---

## 2. The settled decisions

Each decision below resolves the correspondingly-numbered open item in **ADR-001 §4** (the §4 reference is given per item).

### 2.1 Mandatory probes — *all applicable probes are mandatory* (ADR-001 §4.1)

All **applicable** boundary probes are **mandatory**. **Applicability is decided at GENERATION** — only applicable probes are minted (a field with a floor but no ceiling gets **no** `max` probe; a field with no derivable bound gets no boundary set, see §2.6) — **never at runtime**. There is **no "optional probe" in v1**: if a probe exists for a claim, it must run and behave for the claim to be `Verified`.

### 2.2 Failure aggregation — *strict-AND* (ADR-001 §4.2)

Aggregation is **STRICT-AND**. A claim is `Verified` **only if every applicable probe behaves as its polarity expects** — accepts are accepted, rejects are rejected. There is **no threshold or weighted rule in v1** (e.g. no "≥90% of boundary probes pass"); a threshold/weighted aggregation is **named as a future strategy variant**, not built. (This is the evaluation-side strict-AND; it is consistent with — but distinct from — coverage's multi-*claim* strict-AND in the Coverage Model spec §5b.)

### 2.3 Probe outcome is three-way — *pass / fail / indeterminate* (ADR-001 §4.3)

A probe outcome is **three-way: `pass` / `fail` / `indeterminate`.** **`indeterminate` is the broad class** — timeout, infrastructure/credential error, environment-not-satisfiable: **anything that leaves the outcome unknown.** It is **NOT a special-cased timeout flag** (ADR-001 §4.3 asked specifically about timeouts; the settled answer subsumes timeouts into the general indeterminate class rather than treating them as a one-off state).

### 2.4 The invariant — *`Verified` = evidence-complete* (the load-bearing rule)

**`Verified` means evidence-complete.** An **indeterminate** probe ⇒ the evidence for that claim is **incomplete** ⇒ the claim is **NOT `Verified`** — *even though nothing "failed"* — ⇒ the claim is **re-runnable**.

Indeterminate **reuses the existing surface**: the `errored` / "could not be evaluated — re-run" verdict (`not_evaluated`). It does **NOT mint a new failure state**. An indeterminate result is honestly reported as *"not yet verified because the evidence is incomplete,"* never as *"verified"* and never as *"failed."* This is the rule §2.3's three-way split exists to serve, and it is the foundation the whole strategy rests on.

### 2.5 Retry — *indeterminate class only* (ADR-001 §4.4)

Retry applies to the **indeterminate class ONLY**. **Never retry a clean fail** (a clean fail is real evidence; re-running it would only mask a true negative). A **pass-after-retry counts as a pass**. A probe that **oscillates pass/fail across retries** routes to the existing **flaky-quarantine** path — it is **not silently settled** to either outcome.

### 2.6 Skip / N-A — *handled at generation, not runtime* (ADR-001 §4.7)

Skip / N-A is **handled at generation, not runtime**: because **only applicable probes are minted** (§2.1), there is **no N/A probe to score** at runtime. A constraint that **cannot be derived to a boundary set at all** ⇒ the claim is **Needs-clarification / Incomplete** — it is **NOT `Verified`** and **NOT a silent pass**. (Whether the verdict enum gains explicit `Needs-clarification` / `Incomplete` states is the ADR-001 §3c open decision, owned by the S6 build; the invariant here is only that such a claim must not read as `Verified` or as a pass.)

---

## 3. Deferred — future strategy-variant extension points

Named so the contract is explicit about its edges. **Map nothing, build nothing for these in v1.**

- **Warning tier** (ADR-001 §4.5) — **no warning outcome in v1**; probe outcomes stay **pass / fail / indeterminate** (§2.3). A "concerning but passing" observation is an **S6 annotation**, not a verdict state, and does not affect `Verified`.
- **Conditional probes** (ADR-001 §4.6) — **none in v1**; all applicable probes run **unconditionally**. No probe is gated on another probe's result (e.g. there is no "run `max+1` only if `max` accepted" in v1).

These slot into this contract later as future strategy variants, exactly as ADR-001 §3b's future kinds slot into the Evaluation Strategy contract — without changing claim identity or the S4 run path.

---

## 4. What this implies (citing ADR-001)

- **The persisted model holds these decisions, and lands WITH the build.** Per ADR-001 §4 ("Each answer changes what must be stored per probe and per claim — hence no schema until §4 is settled"), settling §4 is precisely what unblocks the persisted model. This spec **does not design that schema**; it lands **with the S6 build**, shaped by the decisions above (a per-probe three-way outcome incl. indeterminate; per-claim strategy kind + the `Verified` boolean).
- **`Verified` is the coverage layer's sole input.** Per the [Coverage Model spec](coverage-model-spec.md) (D-271) and ADR-001 §3b, coverage consumes the per-claim **`Verified`** boolean and nothing else. `Verified` is the **boolean projection of the verdict** (ADR-001 §3c) — these semantics define **when** that projection is `true` (strict-AND over all applicable probes, none indeterminate, §2.2 + §2.4) and **when it is not** (any fail, any indeterminate, or a non-derivable constraint).

---

## 5. Decision & status

**Decision:** Settle ADR-001 §4 for **`bva` v1**: all applicable probes **mandatory** (applicability decided at **generation**); **strict-AND** aggregation; a **three-way** probe outcome whose **`indeterminate`** class (timeout / infra / credential / environment-not-satisfiable) means **evidence-incomplete ⇒ not `Verified` ⇒ re-runnable**, reusing the existing `errored`/`not_evaluated` "could not be evaluated — re-run" surface (**no new failure state**); **retry indeterminate-only**, with oscillation routed to **flaky-quarantine**; **no N/A scoring** (non-derivable constraint ⇒ Needs-clarification/Incomplete, never a silent pass); **warnings** and **conditional probes** deferred to future strategy variants. **`single` is unchanged.** The schema lands **with the build**.

**Status:** **Accepted (design).** The S6 build implements these semantics (it does not re-decide them). `single` behavior is preserved exactly.

**Pointers:**
- This file is the spec of record for `bva` v1 evaluation semantics.
- A one-line **D-272** pointer in `docs/architecture/DECISIONS_LOG.md` records the ratification.
- Defines the contract for: the S6 Evaluation Strategy applier (ADR-001 §3b), the per-probe three-way outcome (ADR-001 §3a evidence), and — downstream — the per-claim `Verified` that the Coverage Model spec (D-271) consumes.
