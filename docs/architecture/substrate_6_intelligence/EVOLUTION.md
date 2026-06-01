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

---

## 2026-05-31 — Interpret stage realized in the run-path (D-111.2)

Slice 5 (persistence) + the run-path wiring, ahead of clustering/phrasing. The S4 run-path now, after `finalize_run`, **interprets + persists** eagerly and best-effort: `interpret_run` → `attribute_run` (contemporaneous S1) → `persist_interpretation`, all inside a `session.begin_nested()` SAVEPOINT. A new per-tenant `s6_interpretations` table (migration `20260527_0020`) mirrors the S4 result store — typed identity/outcome/verdict columns + a `detail` JSONB (attribution, evidence_refs, cause). **Evidence-first:** any S6 failure rolls back to the savepoint; the S4 run truth (the `s4_execution_runs` row + the S2 posture) survives and the outer transaction commits; `RunPathResult.interpretation` stays `None`. `SemanticOrgModel(session.connection())` gives S6 the S1 read on the run-path's own tenant connection; the S6↔S4 import cycle is broken with a call-time lazy import. On `phase-5-substrate-4-crud` (PR #5).

---

## 2026-05-31 — F3: attribution on the shared `evaluate` (D-114)

Resolves the F3 follow-up flagged when S8 built the neutral `formula.evaluate` primitive (D-113). `_attribute_not_enforced` is rewritten **3-way** over `evaluate` (off S3's `derive` — the `verified_negative` import + the `_payload_violates` subset-proxy are deleted): per active/inactive VR, `evaluate(parse(current_formula), field_values)` → `True` / `False` / `NonEvaluable`. Precedence: confirmed violation → `enforcement_gap` / `vr_inactive`; any `NonEvaluable` (nothing violated) → **`vr_formula_indeterminate`** (new); an active VR evaluable-but-not-violated → `vr_formula_drift`; else → **`no_active_vr`** (new, matching S8's reason vocabulary). This **closes three residuals the proxy hid**: the loosened-still-violating false-drift (`99` still violates a current `Amount < 200` → enforcement gap, not drift), the old `NotDerivable → drift` collapse, and the no-VR → drift guess. The **parallel-siblings law (D-112) is now realized on `evaluate`** — its two consumers are S6 `attribution.py` (post-run) + S8 `recipe_grounding.py` (pre-run); `derive` returns to S3-internal. SPEC §3 refinement note updated. The S8↔S6 `NonEvaluable`-symmetry pass is flagged (held; grounded on frequency first). On `phase-5-substrate-4-crud` (PR #5, merged).
