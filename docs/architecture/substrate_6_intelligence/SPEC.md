# Substrate 6 — Observation & Interpretation (Intelligence / Attribution) — SPEC

**Status:** Realized through Phase 5 (program). The deterministic interpreter covers the **full realized S4 execution surface** — inspection (existence + property) + the data-recipe behavioral negatives (1-step create-rejected AND the D-203 2-step update/delete-rejected, graded against the rejection-bearing mutation step) + the positive value-claim — persists eagerly in the run-path (D-111.2), and is consumable: cross-run clustering (D-116), LLM phrasing (D-117), and an in-substrate **read API + phrasing live-fire** (D-137).

**Last substantive update:** 2026-06-03 (Phase 5 — full-surface verdicts + the in-substrate consumer; D-136 / D-137)

---

## Purpose

Substrate 6 is PrimeQA's **interpretation layer**: it takes the **captured truth** S4 produces — a grounded run outcome (`passed` / `failed` / `errored`) plus rich evidence — and turns it into a **structured, QA-readable interpretation**: *what was tested, what happened, and the semantic attribution* (what the outcome means and why). It is the bridge from a *run* to an *answer a release reviewer can act on*.

S4 answers "did the assertion hold?"; S6 answers "what does that tell us about the requirement, and what should a human do about it?"

## 1. The boundary — S4 captures truth, S6 interprets it

The boundary is sharp and one-directional:

- **S4 captures truth** (and S4 owns it): it executes the recipe, captures evidence, and renders the **grounded run outcome**. That outcome is S4's, final, and not S6's to recompute.
- **S6 interprets** that truth: it consumes `evidence.outcome` + the evidence and produces a semantic interpretation — classification, attribution, explanation, and (later) clustering. **S6 does NOT execute, re-run, or re-judge the outcome.** It never flips a `failed` to `passed` or vice versa.

The signal that matters most lives in the **combination S4 records but does not interpret**: a **`verified` claim with a `failed` run** — well-grounded at generation, yet it did not hold against the live org — is exactly what S6 surfaces (a prohibition that should enforce but didn't; an asserted relationship that vanished). S4 records both layers truthfully; S6 explains the *why*.

```
S4 run → RunEvidence (outcome + evidence)  --consumes-->  S6 interpret → Interpretation
         (truth, S4-owned)                                 (meaning, S6-owned; references the evidence)
```

## 2. Deterministic-first

The interpreter is **deterministic**: it maps structured evidence → a structured `Interpretation` with **no LLM**. Attribution is *derived from the evidence*, not generated — S6 never invents a root cause. A behavioral-negative `passed` means *the prohibition enforced* because the evidence shows a matched rejection; the interpreter reads that, it does not guess it.

LLMs enter **later, in separate slices, for phrasing + clustering only** — turning a structured `Interpretation` into reviewer-friendly prose, or grouping interpretations across runs. They **never** produce the attribution itself; the deterministic core is the source of truth, and an LLM layer is an additive presentation/aggregation surface over it. This mirrors S4's discipline (S4 captures, S6 interprets) one level up: the deterministic interpreter attributes, the LLM only phrases.

## 3. The `Interpretation`

Structured, evidence-referencing, and therefore **reviewable / editable / versionable** (a release reviewer reads it, can correct it, and the correction is a tracked artifact — the same lifecycle discipline as S2 claims). Carries:

- **Identity / provenance:** the `run_id` it interprets + the claim / recipe references (so it traces back to S4's run and S2's claim).
- **Outcome (carried, not recomputed):** `evidence.outcome` verbatim — S6 restates it, never re-derives it.
- **Verdict (semantic):** a structured classification of what the outcome *means* for the requirement — behavioral negative (`prohibition_enforced` / `prohibition_not_enforced` / `rejected_unasserted_reason`), positive value-claim (`value_persisted` / `value_not_persisted`), inspection presence (`asserted_metadata_present` / `…_absent`) and inspection value (`asserted_value_matches` / `asserted_value_differs`), plus `not_evaluated` (errored). A closed taxonomy that grows with recipe kinds (D-136).
- **Attribution (what + why):** the deterministic explanation, derived from the evidence (the matched VR error; the unexpected success; the actual error codes; the absent edge).
- **Supporting evidence refs:** pointers into the `RunEvidence` (which step, which fields) backing the verdict — so the interpretation is auditable, not opaque.

## 4. Slice arc (the plan, not locked contracts)

1. **`Interpretation` model + the deterministic interpreter** — `interpret_run(RunEvidence) → Interpretation`, covering **both** built verticals' outcomes (inspection + behavioral-negative). Produce-only (no persistence yet — mirrors how the S4 executor started). No LLM.
2. **Deeper attribution** — cross-reference S1 for a *non-enforcing* VR (e.g. inactive / misconfigured) so `prohibition_not_enforced` carries a candidate cause, not just the fact.
3. **Clustering across runs** — group interpretations (recurring non-enforcement, flapping outcomes) for a release-level view.
4. **LLM phrasing** — reviewer-friendly prose over the structured interpretation (additive; never the attribution source).
5. **Interpretation persistence** — the S6-owned store + the reviewer edit/version lifecycle.

---

## Status

**Realized through Phase 5 (2026-06-03).** The boundary (S4 captures truth, S6 interprets) and the deterministic-first commitment are locked (D-111). The build arc (see `EVOLUTION.md`): the deterministic interpreter + `Interpretation` model (D-111) → deeper VR-cause attribution through S1 (D-111.1 / D-114) → eager run-path persistence (D-111.2) → cross-run clustering (D-116) → LLM phrasing (D-117) → **full-surface verdicts** (D-136: the positive value-claim fix + property value verdicts, so the interpreter is correct + precise over everything S4 executes today) → **the in-substrate consumer** (D-137: a pure read API + the clustering reads re-exported + the phrasing live-fire, so S6 is no longer write-only). **Deferred:** the user-facing UI/dashboard over substrate runs + a standing production consumer (Phase-7 cutover); the dormant verticals' verdicts (ui/event/callout — no executor emits their evidence); cause attribution for positive/property failures; the reviewer edit/version lifecycle (S6-Q-005). See `DEFERRED_ITEMS.md`.
