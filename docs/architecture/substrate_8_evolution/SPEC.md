# Substrate 8 — Evolution Engine — SPEC

**Status:** Realized through Phase 6 (program). The **grounding-validity predicate** is built over the realized surface — three legs (recipe-grounding D-113, claim-grounding D-139, field-value-validity D-140) composed two-level (D-141) — plus the thin mechanics: a recorded-verdict store (D-142) + an S1-sync recompute trigger (D-143). Admissibility + the heavy mechanics stay fenced (§6).

**Last substantive update:** 2026-06-03 (Phase 6 — predicate legs + the in-substrate mechanics; D-139 / D-140 / D-141 / D-142 / D-143)

---

## Purpose

Substrate 8 is PrimeQA's **evolution layer**: it governs whether a test remains **meaningfully true** as the org evolves. When a validation rule is edited, a field renamed, a picklist value removed — does the test still test what it claims? S8 is the faculty that can *answer that*, before any decision to update, regenerate, or flag.

It is **not** a test-maintenance automation that mutates tests when the org changes. That framing — "references update, tests adjust, re-verified, flagged" (PLATFORM_VISION §S8) — is the **mechanics**, and the mechanics are downstream of, and gated by, a prior semantic judgment: *is this artifact's grounding still true?* S8's semantic core is that judgment. The mechanics are explicitly deferred (§6).

## 1. The keystone — evolution is a grounding-axis event, never an identity-axis event

A test artifact has two orthogonal axes, and the existing substrates already separated them:

- **Identity** (what the test *means*) is **org-independent by construction.** A claim's `identity_hash` is computed over `{archetype, claim_kind, canonical(asserted_truth), canonical(semantic_conditions)}`, and `IdentityBearingRef` canonicalizes to `{entity_id, entity_type}` only — **`version_seq` and `external_id` are stripped** (the C0/C1 drift invariants, `canonicalization.py`). A field rename, a formula edit, an S1 re-sync **cannot** change a claim's identity. "Is this the same claim?" is permanently, deliberately stable.
- **Grounding** (whether the test is still *true* against the live org) is the axis org evolution actually moves.

Therefore: **org evolution is never an identity-axis event — it is always a grounding-axis event.** S8 governs **grounding continuity under identity preservation**: it never changes what a test means; it evaluates (and, in the mechanics phase, repairs) whether a stable meaning is still grounded. The S2 constitution already mandates this asymmetry — "S8 may autonomously create new versions iff `identity_hash` and `identity_hash_version` are unchanged" (S2 SPEC §7).

The one-sentence form, the keystone the rest of this SPEC rests on:

> **S8 re-asks generation's grounding questions against the current org.**

## 2. The semantic core — the grounding-validity predicate

The faculty is a single deterministic **pure function**:

```
grounding_validity(artifact, current_org) → intact | drifted | broken
```

- **intact** — every grounding assumption still holds: the subject resolves, the recipe's payload still violates the current rule, the admissibility verdict is unchanged.
- **drifted** — an assumption changed in a way that *may* alter truth (the formula was edited; a required-ness changed) — the verdict must be re-evaluated, and re-grounding *may* be warranted.
- **broken** — an assumption no longer holds at all (the object/field was deleted; *no active rule remains to ground against*) — definitively stale. *Deactivation is `broken` only when nothing else active catches the payload; if another active rule still rejects it, the verdict is `intact` (D-113).*

It is **on-demand** and **stateless**: computed when asked, from the grounding references already embedded per-artifact (the `IdentityBearingRef.version_seq` pins, the recipe's derived payload). No standing dependency manifest, no recorded verdict, no trigger (§6). This mirrors S6's `attribute_run` shape — a pure function over `(artifact, current-org reader)`, the substrate's read-through to S1.

**The predicate is not a new computation — it is generation's own grounding checks, re-asked against today's org.** Its legs are *generation's grounding faculties*; this is the **initial, explicitly-extensible** set:

| Leg | The question | Re-consumes | Status |
|---|---|---|---|
| **recipe-grounding** | does the payload still *violate*? | the neutral `formula.evaluate` primitive (D-113 — **not** `derive`) | **realized** (D-113) |
| **claim-grounding** | does the subject still resolve? | S1 entity-resolution (`get_entities` by `sf_api_name`) | **realized** (D-139) |
| **field-value-validity** | does the payload's value still *exist*? | S1 picklist values (`get_picklist_values`) | **realized** (D-140) |
| **admissibility** | LAYER_1 ↔ LAYER_2 — is the negative still (un)derivable? | S3 admissibility logic | **deferred — S3-blocked** (the synthesis→intent contract, S8-Q-004) |

**Realized — the two-level composition + the in-substrate mechanics (D-141 … D-143).** `grounding_validity(artifact, *, subjects, vrs, picklists)` composes the three legs claim-level + per-recipe (`broken` > `drifted` > `intact`; field-value `broken` un-masks a recipe-grounding `intact`), composed never collapsed (D-141). A per-tenant `s8_grounding_validity` store persists the verdict + an `evaluated_at_version_seq` (D-142); an S1-sync recompute trigger (scheduler tick) re-grounds a tenant's current claims when S1 advances, freshness-gated off the store (no watermark table) and bounded by a per-tick cap (D-143). **Admissibility is deferred** (verified S3-blocked: on the negative vertical it duplicates recipe-grounding, and its independent positive content needs the synthesis→intent contract — S8-Q-004).

The admissibility leg **closes G3**: `caveat_required` / `admissibility_layer` stop being a frozen emission-time snapshot and become a **re-evaluable function of `(claim, current org)`** — a formula becoming derivable lifts a caveat; ceasing to be derivable imposes one.

**The leg set grows.** The change taxonomy (§5) already hints a fourth: a **field-value-validity** leg for the *picklist-value-removed* case — the payload's value no longer *exists*, which is distinct from no-longer-*violates* (a create rejected for an invalid value is not the same as a create the rule lets through). Whether re-derivation subsumes it or it is its own leg is the **full taxonomy → leg mapping**, a later design pass; this SPEC names the principle, the known legs, and that the set is open.

**Two-level — the verdict is composed, not collapsed (Fork C).** Claim-grounding and recipe-grounding depend on **different org facts and drift independently** — and one claim has many recipes. A claim's subject can still resolve (claim **intact**) while a recipe's payload no longer violates (recipe **drifted**). So grounding-validity is evaluated **claim-level (one)** and **recipe-level (per-recipe)**, composed — never collapsed into a single verdict.

**Realized — recipe-grounding leg (D-113).** The first leg is built, **object-level + evaluate-based**: it reads the recipe's stored payload (S2 — `CreateStep.field_values` + `target_object`) and the current **active** VR formula(s) on that object (S1, via the `APPLIES_TO` reader), and asks the neutral `formula.evaluate` primitive whether the payload still fires an active rule. **intact** = ≥1 active VR's current formula evaluates `True`; **drifted** = none triggered but ≥1 active *evaluable* VR exists (re-groundable); **broken** (reason-tagged) = no active VR triggered and no re-groundable foundation — `no_active_vr` *or* `formula_non_evaluable`. Two bounds the leg makes explicit: it is **object-level** (the recipe pins no VR — only a generic `FIELD_CUSTOM_VALIDATION_EXCEPTION` — so an `intact` can mask the loss of one *specific* VR when another active VR catches the payload; a VR-pin is the deferred generation-side sharpening), and its **`broken/formula_non_evaluable` is *structural only*** (the formula left the single-object subset). The other "can't evaluate" sources stay with later legs: **field/object-gone → claim-grounding** (schema resolution), and **picklist-value-removed → field-value-validity** (a known false-`intact` here — the formula still evaluates `True` on the now-invalid value).

## 3. The dependency law (the keystone boundary)

```
S8 → { S1 entity-resolution, S3 derive (D-107), S3 admissibility }
S8 ∥ S6   (parallel; never S6 → S8)
```

S8 stands on the **foundational faculties generation itself used**, re-asked. It is **parallel to S6** — never consumed by it and never consuming it for the predicate.

The decisive reason is **epistemic** (not dependency-direction): S6's drift attribution judges with the **actual run evidence in hand** — `_attribute_not_enforced(create: CreateAttemptEvidence, vrs)` reads the create's real `field_values` and the *observed* success, **on top of** the re-derivation. S8's predicate is **pre-run, derivation-only** (no run has happened). S6 therefore holds **strictly better ground** for "did the payload violate" — it saw what Salesforce actually did. **A stronger-evidence faculty must never route through a weaker one.** They are parallel because they stand on **different evidence bases**, not because of any value-chain ordering. (The narrow-vs-broad coupling — S6 needs only the still-violates check, while S8's verdict is broad — stands alongside, but is secondary to the epistemic reason.)

We **do not** rest this boundary on the value-chain-grain argument ("S6 depending on S8 inverts the flow"): the platform vision is ambiguous there — its graph draws an S8→S6 arrow, while its prose lists S6's dependencies as S4 + S1 only — and a keystone boundary cannot rest on a contestable reading.

**Code-confirmed, not aspirational.** The shared primitive is S3's `derive` (D-107), and it already has exactly two importers: `emission.py` (S3, grounding-time) and `attribution.py` (S6, post-run). S8 lands as the **third sibling** on the same primitive — the parallel-consumer structure already exists in the tree. (**Post-D-114:** the parallel siblings are realized on the neutral **`evaluate`** — its two consumers are S6 `attribution.py` (post-run) + S8 `recipe_grounding.py` (pre-run); `derive` returns to S3-internal (`emission.py`).)

**Refinement (D-113).** The recipe-grounding leg's *precise* dependency is a new **neutral `formula.evaluate` primitive** (added beside `parse` / `walk` / `nodes` in `primeqa/semantic/formula/`), **not** `S3 derive` — D-112's line was approximate (`derive` *solves* formula → a violating assignment; the leg needs *evaluate*, formula + payload → fires?). The parallel-siblings law holds and is **cleaner**: `evaluate` is a neutral formula-semantics primitive that S6 (post-run drift) and S8 (pre-run grounding) consume independently — S6 ↛ S8 either way. (S6's `_attribute_not_enforced` was **aligned to consume `evaluate` in D-114** — removing the false-drift misattribution + the duplicated proxy, and splitting its over-broad drift bucket into `vr_formula_indeterminate` / `no_active_vr`; S6 now stands on the neutral primitive, off S3's `derive`.)

## 4. The supersession law

When grounding **drifts** and is repaired (the mechanics phase), the repair is an **identity-preserving supersession**: a **new artifact version with the *same* `identity_hash`**, on the existing lineage spine (stable `test_id` + monotonic `version_seq` + `valid_to IS NULL` = current), stamped with the provenance the constitution already reserves (`recipe_s8_rewrite` event_kind, `'s8'` actor; `grounding_evolution` regeneration_kind).

The invariant: **meaning is preserved; grounding evolves.** S8 never creates a *different* test — a genuinely different subject is a *new* claim, authored by S3, not an evolution. This is **governed semantic evolution**: the org changes, the grounding is re-established, the meaning is untouched, and the lineage records that the *same* test was re-grounded.

## 5. The change taxonomy (semantic illustration)

Each org change, framed as a semantic question — *claim still valid? recipe still valid? lineage preserved?*:

| Org change | Claim identity | Recipe grounding | Lineage |
|---|---|---|---|
| **Field renamed** | ✅ survives (ref is by `entity_id`; `external_id` stripped) | intact semantically; only the executable surface re-resolves | preserved |
| **VR formula modified** | ✅ survives | **drifted** — payload derived from the old formula may not violate the new one | preserved |
| **Picklist value removed** (payload used it) | ✅ survives | **broken** — create errors on an invalid value, not the rule; tests the wrong thing (the field-value-validity leg) | preserved |
| **Permission narrowed** (FLS / sharing) | ✅ survives | grounding exists, but **isolation** changes — rejection may now be permission, not the rule | preserved |
| **Automation reordered** (Flow) | ✅ survives | possibly **drifted** — a different rule may fire first; ambiguous without a run | preserved |

Every row collapses to the same principle: **claim identity always survives; recipe grounding is what drifts; lineage is always preservable.** That is the keystone restated — **evolution acts on grounding under the constraint of identity preservation.**

## 6. Deferred — evolution mechanics (the fence)

Explicitly **out of the semantic core**, named here so they are not later litigated as in-scope: everything that is **impact/trigger machinery** rather than the grounding judgment.

- **Standing dependency manifest** — promoting the per-artifact grounding refs to a first-class queryable index. The predicate derives its inputs on-demand from the embedded refs; it needs no manifest.
- **Change → impact reverse index (G5)** — "which artifacts are hit by *this* org change?" (the join from an S1 entity-change to affected artifacts). Real, but it is the **impact trigger**, not the predicate.
- **S1-sync trigger** — reacting to a new S1 `version_seq` by diffing and evaluating. *(Landed thin, D-143: a scheduler tick recomputes a tenant's current claims when S1 advances, freshness-gated off the store — **recompute-all**, since the change→impact reverse index that would narrow it stays fenced below.)*
- **Standing recorded verdict** — persisting + refreshing a grounding-validity verdict per artifact. *(Landed thin, D-142: the per-tenant `s8_grounding_validity` store persists the verdict + `evaluated_at_version_seq`; "show me all drifted tests" is the `overall`-indexed `list_grounding_validity`. The refresh is the D-143 trigger.)*
- **Coverage-version gap (G4)** — `test_claim_coverage` records `(claim, entity_id)` but not the entity version at derivation time; reproducibility of coverage across versions is a mechanics-phase repair.
- **The one S8 → S6 edge** — a *drift-trigger signal* ("this test keeps drifting across recent runs → re-evaluate"). This is the only place S8 legitimately reads S6, and it is a **trigger input**, not part of the predicate. It belongs here, with the rest of the trigger machinery.

These are the **regeneration-infrastructure local maximum** we are deliberately *not* building at the opening. The semantic core **takes nothing from S6 and needs no standing state** — it is the predicate, the two-level verdict, and the supersession law, and nothing more.

---

## Status

**Realized through Phase 6 (2026-06-03).** The keystone + the dependency law + the supersession law are locked (D-112). The build arc (see `EVOLUTION.md`): the recipe-grounding leg (D-113) → S6 alignment on the shared `evaluate` (D-114) → **the claim-grounding leg** (D-139, re-resolve the subject by `sf_api_name`) → **the field-value-validity leg** (D-140, the picklist-value-removed false-`intact`) → **the two-level composition** (D-141, `grounding_validity`, composed never collapsed) → **the recorded-verdict store** (D-142, per-tenant `s8_grounding_validity` + read API) → **the S1-sync recompute trigger** (D-143, a scheduler tick re-grounds a tenant's current claims when S1 advances, freshness-gated + capped). **Deferred:** the **admissibility leg** (S3-blocked — the synthesis→intent contract, S8-Q-004); the **change→impact reverse index** (recompute-all is correct, just unoptimized); **re-grounding orchestration + supersession execution** (the artifact-mutation body, autonomy-gated, S8-Q-006); the **generation-side VR-pin** (an S3-emission change); the held **NonEvaluable-symmetry** pass. See `DEFERRED_ITEMS.md`.
