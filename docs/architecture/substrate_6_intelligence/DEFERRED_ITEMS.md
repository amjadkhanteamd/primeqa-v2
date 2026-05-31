# Substrate 6 — Observation & Interpretation — Deferred Items

The forward-looking list. The deterministic-attribution foundation (slice 1 + slice 2) is built (SPEC §4; DECISIONS_LOG D-111 / D-111.1). This consolidates what those slices deliberately deferred.

Authored at the slice-1+2 foundation milestone (2026-05-27). Append corrections as a dated note; do not silently rewrite.

---

## 1. The temporal assumption — interpret eagerly at run time

- **`attribute_run` reads the *current* S1 version.** The deeper attribution (`vr_inactive` / `vr_formula_drift` / `enforcement_gap`) reads S1 as-of `current_version_seq()` — correct for "why doesn't this VR enforce *now*", but it interprets the run's evidence against a *possibly-later* org model than the one the run executed against. **The fix is eager run-time interpretation** — interpret + attribute *immediately after* the run finalizes (the next slice), so S1's state at interpretation ≈ S1's state at execution. **A later refinement** may snapshot the run-time VR state (is_active / formula_text) into the run evidence, so a re-interpretation long after the fact reads the *contemporaneous* org model rather than today's. — **D-111.1**
- **Full payload-vs-formula evaluation (S6-1).** `vr_formula_drift` is detected by *re-deriving* the violating payload from the current `formula_text` and comparing to the create's `field_values` — a reliable drift *signal*, but not a general "does this arbitrary assignment satisfy this formula" evaluator. A full evaluator (`evaluate(formula, values) → bool`) is a parser extension, deferred until a case needs the precision. — **D-111.1**

## 2. The layers on the deterministic foundation

- **LLM phrasing.** Reviewer-friendly prose over the structured `Interpretation` + `Cause` — the presentation layer. It **never** produces the attribution (the deterministic core is the source of truth); it phrases what the core already attributed. — **D-111 §2**
- **Clustering across runs.** Group interpretations (recurring non-enforcement, flapping outcomes, the same VR failing across runs) into a release-level view. Cross-run aggregation over the per-run interpretations. — **D-111 §4**
- **Interpretation persistence.** The S6-owned store + the reviewer edit/version lifecycle (the same reviewable/editable/versionable discipline as S2 claims). Slice 1+2 are produce-only; a run does not yet yield a *durable* interpretation. (The eager-run-time-interpretation slice is the natural place to introduce the store.) — **D-111 §3/§4**

## 3. Coverage breadth

- **More verticals' verdicts.** The verdict + cause taxonomy covers the two built S4 verticals (inspection + behavioral negative). Positive CRUD, UI, event, and callout verticals add their own verdicts as those S4 recipe kinds land. — **D-111**
- **Permission-based prohibition attribution.** When S4's eval recognizes FLS/sharing rejections (the S4 DEFERRED note), S6 gains a `cause_kind` for permission-enforced prohibitions (and the S1 read extends to permission metadata). — **D-110.2 (S4) / D-111.1**

---

## References

- Design rationale: `DECISIONS_LOG.md` D-111 / D-111.1.
- Realized state: `SPEC.md` §Status.
- Build history: `EVOLUTION.md`.
