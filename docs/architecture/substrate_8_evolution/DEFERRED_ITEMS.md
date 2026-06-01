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
- **Change → impact reverse index (G5).** "Which artifacts are hit by *this* org change?" — the join from an S1 entity-change to affected artifacts. The impact *trigger*, not the predicate. — **D-112**
- **S1-sync trigger.** Reacting to a new S1 `version_seq` by diffing and evaluating. The predicate is identical whether invoked on sync or on query; the trigger is mechanics. — **D-112**
- **Standing recorded verdict.** Persisting + refreshing a grounding-validity verdict per artifact (for "show me all drifted tests"). The core is a pure function; recording it is an optimization. — **D-112**
- **Coverage-version gap (G4).** `test_claim_coverage` records `(claim, entity_id)` but not the entity's version at derivation time; coverage reproducibility across versions is a mechanics-phase repair. — **D-112**
- **The one S8 → S6 edge.** A *drift-trigger signal* ("this test keeps drifting across recent runs → re-evaluate"). The only legitimate S8→S6 read, and it is a trigger input, not part of the predicate. — **D-112**

## 2. The held S8↔S6 NonEvaluable-symmetry pass

- The recipe-grounding leg (D-113) does `≥1 False → drifted`, **skipping** `NonEvaluable` — so a `False` + `NonEvaluable` mix resolves to `drifted`. S6's conservative line (D-114) puts any `NonEvaluable` ahead of drift (→ `vr_formula_indeterminate`). For symmetry the leg should match — `NonEvaluable` present + nothing violated → **`broken/formula_non_evaluable` ahead of `drifted`**. **Grounded on frequency first:** an empirical probe found this mix marginal relative to the *dominant* object-level imprecision — `intact` masking recipe drift when an unrelated required-field (`ISBLANK`) VR fires on the minimal payload — whose real fix is the generation-side VR-pin (§3), not the symmetry pass. So the symmetry fix is *cheap-and-correct* but secondary; the pin is the substantive one. — **D-114**

## 3. Predicate leg extensibility (the not-yet-built legs)

- **claim-grounding leg** — does the subject still resolve? Re-consumes S1 entity-resolution; the predicate's second axis (composes with recipe-grounding into the two-level verdict). Also owns the *field/object-gone → broken* case (schema resolution), which the recipe-grounding leg's structural `broken` does not cover. — **D-113**
- **admissibility leg** — LAYER_1 ↔ LAYER_2 re-evaluation (a formula becoming derivable lifts a caveat; ceasing to be imposes one) — making the admissibility verdict a re-evaluable function of `(claim, current org)` rather than a frozen emission-time snapshot. — **D-113**
- **field-value-validity leg** — the picklist-value-removed case: the recipe-grounding leg returns a **false `intact`** (the formula still evaluates `True` on the now-invalid value, but SF rejects the create for an invalid value, not the rule). A distinct leg checks whether the payload's value still *exists*. — **D-113**
- **The generation-side VR-pin (the real object-level sharpening).** The recipe pins no VR (only a generic error code), so the leg is object-level — an `intact` can mask the loss of one *specific* VR when a different active VR catches the payload. Pinning the grounding VR on the recipe at emission time sharpens drifted-vs-broken to the VR that actually grounds the negative. The dominant object-level fix (cf. §2). — **D-113**

## 4. The mechanics phase (re-grounding execution)

- **Re-grounding orchestration + supersession execution.** The supersession *law* is recorded (D-112), but actually *performing* a re-grounding — re-deriving a payload, authoring a new identity-preserving recipe version, stamping the provenance — is the mechanics phase, gated behind the predicate + the impact machinery (§1). — **D-112**

---

## References

- Design rationale: `DECISIONS_LOG.md` D-112 / D-113 / D-114.
- Realized state: `SPEC.md` §Status + §2/§3 (the realized recipe-grounding leg).
- Build history: `EVOLUTION.md`.
