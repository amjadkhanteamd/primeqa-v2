# Substrate 6 — Observation & Interpretation — Evolution Log

Append-only. One entry per session that made substantive changes to this substrate's docs or code.

---

## 2026-05-27 — Substrate opened; deterministic-attribution foundation (D-111 / D-111.1)

Substrate 6 opened — the interpretation layer: it takes S4's captured truth (a grounded run outcome + evidence) and produces a structured, QA-readable **interpretation** (what was tested, what happened, the semantic attribution). The boundary is sharp and locked (D-111): **S4 captures truth and owns the outcome; S6 consumes `evidence.outcome` and explains it, never re-judging it.** Deterministic-first: the interpreter attributes from the evidence; LLMs (later) phrase + cluster only, never invent the attribution. Package `primeqa/interpretation/` (distinct from v1's `primeqa/intelligence/`). SPEC + D-111 committed.

**Slice 1 — the deterministic interpreter (D-111).** `interpret_run(RunEvidence) → Interpretation` — pure, no LLM, dispatching on vertical (behavioral negative vs inspection) + outcome to a semantic `verdict` (`prohibition_enforced` / `prohibition_not_enforced` / `rejected_unasserted_reason` / `asserted_metadata_present`/`…_absent` / `not_evaluated`), an evidence-derived `attribution`, and `evidence_refs` into the real `RunEvidence`. The outcome is **carried verbatim** (a constructed-inconsistent run is restated, never flipped). Produce-only.

**Slice 2 — deeper attribution (D-111.1).** `attribute_run(interpretation, evidence, *, s1) → Interpretation` enriches the two *failed* behavioral verdicts with a structured `Cause` (one of five `cause_kind`s), pass-through for the rest, never re-judging the outcome.
  - **2a (offline):** the `Cause` model + the `S1VrReader` port + the classification logic — `prohibition_not_enforced` → `vr_inactive` / `vr_formula_drift` (re-derive via the D-107 parser) / `enforcement_gap`; `rejected_unasserted_reason` → `other_vr_fired` (match the rejection message to S1's per-VR `error_message`) / `platform_constraint`. Stub-driven.
  - **2b (S1-backed):** the production `S1ValidationRuleReader` reads a subject Object's VRs **through S1's query interface** (`SemanticOrgModel`) — the inter-substrate read-through pattern (S6-3), no raw S6-local SQL. It composes `get_entities` + `get_related(APPLIES_TO, inbound)` + the new **`SemanticOrgModel.get_entity_details`** (a general, S1-owned detail-table read — the consumer-driven increment D-022 anticipates — added because `is_active` lives on `validation_rule_details`, not in `attributes`). Live-proven against real S1 (seeded), end-to-end through `attribute_run`.

The deterministic-attribution foundation is complete: a run's evidence → a structured, evidence-cited interpretation with a deeper cause for the failure modes that matter. On `phase-5-substrate-4-crud` (PR #5).
