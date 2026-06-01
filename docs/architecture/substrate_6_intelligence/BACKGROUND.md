# Substrate 6 — Observation & Interpretation — BACKGROUND

## Why this substrate exists

S4 produces *captured truth* — a grounded run outcome (`passed` / `failed` / `errored`) plus rich per-step evidence. But a release reviewer cannot act on a raw outcome: they need to know **what it means**. A `failed` behavioral negative on a claim that was *verified* at generation time is not a flaky test — it is a prohibition that should enforce and didn't (a real defect), or a rule that was edited since generation. Substrate 6 is the layer that turns S4's truth into a **structured, QA-readable interpretation**: what was tested, what happened, and the semantic attribution (the *why*).

The boundary is sharp and one-directional: **S4 captures truth and owns the outcome; S6 consumes `evidence.outcome` and explains it, never re-judging it.** S6 never executes, re-runs, or flips an outcome — it restates the outcome verbatim and attaches meaning. The signal that matters most lives in the combination S4 records but does not interpret (a `verified` claim with a `failed` run); S6 is the faculty that surfaces it.

## What substrate-6 replaces

PrimeQA v1's intelligence layer (`primeqa/intelligence/` — the LLM-gateway, explanations, failure summaries, the fix-and-rerun agent) produces explanations, but they are **LLM-authored from the outside**: a prompt is handed a failure and asked to narrate a cause. The narration can be plausible-but-wrong — it invents root causes the evidence doesn't support, because nothing structurally binds the explanation to the captured evidence.

Substrate-6 inverts this: the interpretation is **deterministic-first**. A pure interpreter maps the structured evidence to a semantic verdict and an evidence-cited attribution with **no LLM**; LLMs enter later, in separate slices, to *phrase* and *cluster* the deterministic core — never to produce the attribution. The deterministic core is the source of truth; an LLM layer is additive presentation over it. This mirrors S4's discipline one level up: S4 captures, S6 interprets; the deterministic interpreter attributes, the LLM only phrases.

## What substrate-6 is for

- **Interpret a run** — `interpret_run(RunEvidence) → Interpretation`: a semantic `verdict` (e.g. `prohibition_enforced` / `prohibition_not_enforced` / `rejected_unasserted_reason` / `asserted_metadata_present`/`…_absent` / `not_evaluated`), an evidence-derived `attribution`, and `evidence_refs` back into the real evidence (auditable, not opaque). The outcome is carried verbatim.
- **Attribute the *why*** — `attribute_run` enriches the failed behavioral verdicts with a structured `Cause` read deterministically from S1's validation-rule metadata (through S1's query interface, the read-through pattern), distinguishing `vr_inactive` / `vr_formula_drift` / `enforcement_gap` / `vr_formula_indeterminate` / `no_active_vr` / `other_vr_fired` / `platform_constraint`.
- **Persist + ready for review** — the run-path persists the `Interpretation` eagerly + best-effort (a softer S6 failure never rolls back the S4 truth); the interpretation is reviewable / editable / versionable, the S2-claim lifecycle discipline one substrate up.

LLM phrasing and cross-run clustering are deliberately deferred layers (`DEFERRED_ITEMS.md`); the deterministic-attribution foundation is what shipped first.
