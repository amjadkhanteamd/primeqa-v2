# Substrate 6 — Observation & Interpretation — Deferred Items

The forward-looking list. The deterministic-attribution foundation (slice 1 + slice 2) is built (SPEC §4; DECISIONS_LOG D-111 / D-111.1). This consolidates what those slices deliberately deferred.

Authored at the slice-1+2 foundation milestone (2026-05-27). Append corrections as a dated note; do not silently rewrite.

---

## 1. The temporal assumption — interpret eagerly at run time

- **`attribute_run` reads the *current* S1 version.** The deeper attribution (`vr_inactive` / `vr_formula_drift` / `enforcement_gap`) reads S1 as-of `current_version_seq()` — correct for "why doesn't this VR enforce *now*", but it interprets the run's evidence against a *possibly-later* org model than the one the run executed against. **The fix is eager run-time interpretation** — interpret + attribute *immediately after* the run finalizes (the next slice), so S1's state at interpretation ≈ S1's state at execution. **A later refinement** may snapshot the run-time VR state (is_active / formula_text) into the run evidence, so a re-interpretation long after the fact reads the *contemporaneous* org model rather than today's. — **D-111.1**
- **Full payload-vs-formula evaluation (S6-1).** `vr_formula_drift` is detected by *re-deriving* the violating payload from the current `formula_text` and comparing to the create's `field_values` — a reliable drift *signal*, but not a general "does this arbitrary assignment satisfy this formula" evaluator. A full evaluator (`evaluate(formula, values) → bool`) is a parser extension, deferred until a case needs the precision. — **D-111.1**

## 2. The layers on the deterministic foundation

- **LLM phrasing — LANDED (D-117).** The presentation layer is built: a v1 enricher (`intelligence/interpretation_phrasing.py`, Haiku via `gateway.llm_call`, best-effort, hard caps) restates the deterministic `{outcome, verdict, attribution, cause}` into QA-readable `{headline, explanation}` — invent-nothing (only those facts reach the model). The LLM stays out of `interpretation/`: the schema + a pure-SQL `set_phrasing` writer live in the substrate (a nullable `phrasing` column, alembic `20260601_0020`); the v1 → substrate caching is the allowed direction. On-demand + cache via `get_or_phrase`; per-tenant flag `llm_enable_interpretation_phrasing` (migration 050, default off). **Live-fired (D-137):** `read_and_phrase` (v1) now fires `get_or_phrase` on a real read path — flag-as-param, with `interpretation_phrasing_enabled` reading the v1-side flag (a targeted single-column SELECT, fails closed); the substrate read API (`read_interpretation` / `list_interpretations`) makes the store readable. **Still deferred:** the **user-facing UI/dashboard consumer** (Phase-7 cutover) + an always-on production trigger; the real-Haiku output is verified periodically, not CI-gated (the call is stub-tested). — **D-111 §2 / D-117 / D-137**
- **Clustering across runs — LANDED (D-116).** The deterministic cross-run layer is built: `cause_kind` / `vr_name` promoted to indexed columns + a read-only `clustering.py` service (`cluster_recurring_causes` / `cluster_by_vr` / `cluster_flapping`). Grain = per-cause / per-VR / per-claim, tenant-wide or by `recipe_id`. The clustering reads are now **re-exported as a named S6 consumer API** (D-137, `interpretation/__init__`). **Still deferred:** the **release-grain** view (no release→runs key yet) and a **dashboard/route consumer** (Phase-7 cutover). Consuming S8's drift signals stays held (no S8 signal channel — S6-Q-008). — **D-111 §4 / D-116 / D-137**
- **Interpretation persistence.** The S6-owned store + the reviewer edit/version lifecycle (the same reviewable/editable/versionable discipline as S2 claims). Slice 1+2 are produce-only; a run does not yet yield a *durable* interpretation. (The eager-run-time-interpretation slice is the natural place to introduce the store.) — **D-111 §3/§4**

## 3. Coverage breadth

- **More verticals' verdicts.** **Positive value-claim + property value verdicts LANDED (D-136)** — the interpreter now covers the **full realized S4 surface** (inspection existence/property + behavioral negative + positive value-claim), correct + precise over everything S4 executes today. **Still deferred:** the **dormant verticals** (UI, event, callout) add their verdicts as those S4 recipe kinds land; **cause attribution for positive/property failures** (the *why* a value didn't persist / a value differs — needs org-change correlation, deeper than the VR-cause path). — **D-111 / D-136**
- **Permission-based prohibition attribution.** When S4's eval recognizes FLS/sharing rejections (the S4 DEFERRED note), S6 gains a `cause_kind` for permission-enforced prohibitions (and the S1 read extends to permission metadata). — **D-110.2 (S4) / D-111.1**

---

## References

- Design rationale: `DECISIONS_LOG.md` D-111 / D-111.1.
- Realized state: `SPEC.md` §Status.
- Build history: `EVOLUTION.md`.
- **Cutover sequencing (D-146):** the Phase-7 / cutover deferrals above — the user-facing UI/dashboard consumer + always-on trigger, and the clustering release-grain / dashboard consumer — now have a gated home in `docs/architecture/greenfield_cutover/SEQUENCE.md` (step 2), with the GO/NO-GO folding at step 4.
