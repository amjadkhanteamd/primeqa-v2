# Substrate 8 — Evolution Engine — Deferred Items

The forward-looking list. The semantic core (the grounding-validity predicate + the supersession
law) is open (SPEC; DECISIONS_LOG D-112), and its first leg (recipe-grounding) is built (D-113). This
consolidates what the opening + slice 1 deliberately deferred. The umbrella deferral is the
**evolution mechanics** — everything that is impact/trigger machinery rather than the grounding
*judgment* (the regeneration-infrastructure local maximum the substrate is deliberately not building
at the opening).

Authored at the slice-1 milestone (2026-05-31). Append corrections as a dated note; do not silently
rewrite.

---

## 1. Evolution mechanics (the SPEC §6 fence)

Out of the semantic core — impact/trigger machinery, named so they are not later litigated as
in-scope:

- **Standing dependency manifest.** Promoting the per-artifact grounding refs (today embedded in the recipe / `IdentityBearingRef.version_seq` pins) to a first-class queryable index. The predicate derives its inputs on-demand; it needs no manifest. — **D-112**
- **Change → impact reverse index (G5).** "Which artifacts are hit by *this* org change?" — the join from an S1 entity-change to affected artifacts. The impact *trigger*, not the predicate. **Still deferred** — the D-143 trigger is **recompute-all** (every current claim on each S1 advance); the reverse index is the optimization that would narrow it (correct without it, just unoptimized). — **D-112 / D-143**
- **S1-sync trigger. LANDED — thin (D-143).** A scheduler tick (`s8_grounding_tick` → `run_s8_grounding_tick` → `recompute_tenant_grounding`) re-grounds a tenant's current claims when S1 advances, freshness-gated off the store (`evaluated_at_version_seq == current S1 seq` — no watermark table) and bounded by a per-tick cap. **Still deferred:** the reverse-index narrowing (above) + the per-artifact job queue (the freshness+inline approach is the simpler correct v1). — **D-112 / D-143**
- **Standing recorded verdict. LANDED — thin (D-142).** A per-tenant `s8_grounding_validity` store persists the verdict + `evaluated_at_version_seq`; "show me all drifted tests" is the `overall`-indexed `list_grounding_validity`. The **refresh** is the D-143 trigger; the verdict is a **snapshot** as-of its `evaluated_at_version_seq`. — **D-112 / D-142**
- **Coverage-version gap (G4).** `test_claim_coverage` records `(claim, entity_id)` but not the entity's version at derivation time; coverage reproducibility across versions is a mechanics-phase repair. — **D-112**
- **The one S8 → S6 edge.** A *drift-trigger signal* ("this test keeps drifting across recent runs → re-evaluate"). The only legitimate S8→S6 read, and it is a trigger input, not part of the predicate. — **D-112**

## 2. The held S8↔S6 NonEvaluable-symmetry pass

- The recipe-grounding leg (D-113) does `≥1 False → drifted`, **skipping** `NonEvaluable` — so a `False` + `NonEvaluable` mix resolves to `drifted`. S6's conservative line (D-114) puts any `NonEvaluable` ahead of drift (→ `vr_formula_indeterminate`). For symmetry the leg should match — `NonEvaluable` present + nothing violated → **`broken/formula_non_evaluable` ahead of `drifted`**. **Grounded on frequency first:** an empirical probe found this mix marginal relative to the *dominant* object-level imprecision — `intact` masking recipe drift when an unrelated required-field (`ISBLANK`) VR fires on the minimal payload — whose real fix is the generation-side VR-pin (§3), not the symmetry pass. So the symmetry fix is *cheap-and-correct* but secondary; the pin is the substantive one. — **D-114**

## 3. Predicate leg extensibility

- **claim-grounding leg — LANDED (D-139).** Does the subject still resolve? Re-resolves **every** `IdentityBearingRef` in the claim by `sf_api_name` through S8's own `SubjectResolver` port; the predicate's second axis (composes with recipe-grounding into the two-level verdict). Owns the *field/object-gone → broken* case. By external_id (rename-faithful), not entity_id. — **D-139**
- **field-value-validity leg — LANDED (D-140).** The picklist-value-removed false-`intact`: the recipe-grounding leg returns `intact` (the formula still evaluates `True` on the now-invalid value), so a distinct leg checks whether the payload's value still *exists* in the field's active set (`PicklistReader.active_values`). Behavioral-negative-only v1; **still deferred:** the multi-select (semicolon-delimited) picklist value split. — **D-140**
- **admissibility leg — deferred, S3-blocked.** LAYER_1 ↔ LAYER_2 re-evaluation, making the admissibility verdict a re-evaluable function of `(claim, current org)`. **Verified not realizable-now:** on the built negative vertical it *duplicates* recipe-grounding (`evaluate` vs `derive` over the same VRs), and its independent positive-vertical content is blocked on the synthesis→intent contract (S8-Q-004 / D-115.1, an S3-grounding task). Buildable once S3 threads `{field, expected_value}` + the positive vertical lands. — **D-113 / D-141**
- **The generation-side VR-pin (the real object-level sharpening).** The recipe pins no VR (only a generic error code), so the leg is object-level — an `intact` can mask the loss of one *specific* VR when a different active VR catches the payload. Pinning the grounding VR on the recipe at emission time is the dominant object-level fix — but it is an **S3-emission change, not an S8 read**, so it stays deferred. — **D-113**

## 4. The mechanics phase (re-grounding execution)

- **Re-grounding orchestration + supersession execution.** The supersession *law* is recorded (D-112), but actually *performing* a re-grounding — re-deriving a payload, authoring a new identity-preserving recipe version, stamping the provenance — is the mechanics phase, gated behind the predicate + the impact machinery (§1). — **D-112**

---

## References

- Design rationale: `DECISIONS_LOG.md` D-112 / D-113 / D-114.
- Realized state: `SPEC.md` §Status + §2/§3 (the realized recipe-grounding leg).
- Build history: `EVOLUTION.md`.
