# Substrate 4 — Slice 1: Positive Create-and-Verify (Directly-Set State) — Design

**Status:** Design locked (D-115) — **no impl**. The first true *semantic-execution* slice.
**Date:** 2026-06-01
**Decision:** `DECISIONS_LOG.md` D-115.

---

## Framing — the weight is on the world, not the call

S4's first two verticals were thin: metadata-inspection (read an edge, assert `exists`) and the behavioral negative (a create the org should *reject*). This is the first vertical where S4 must **construct a valid operational world** on the live org and **police the S3/S4 boundary** — the create call itself is the easy part. The slice verifies that a requirement's stated field value is *operationally achievable* and *persists* on the current org.

## 1. The governing boundary (k16, TA-locked) — operational validity, never semantic meaning

S4 resolves **operational validity** against the live org but **never changes the recipe's semantic meaning**. This is enforced **structurally, not by discipline** — three structural facts make it impossible for S4 to drift the semantics:

- **The recipe carries the semantic field-value** (the claim's value, S3-set) **+ the target object.** S4 receives them; it does not choose them.
- **S4's writable set = (the object's required fields) − (the semantic fields).** S4 fills **only** that *operational padding* with valid filler (validity checked against the live org). The **semantic field-under-test is recipe-set and never enters S4's writable set** → S4 *structurally cannot choose the value under test*. The set difference is the guardrail; there is no code path by which S4 writes the field it is supposed to be verifying.
- **Grounding compares observed vs. the claim's targets verbatim** (carried, not recomputed) → S4 **cannot reinterpret or soften** the verification either. The assertion target is the claim's `expected_value`, threaded through; S4 evaluates equality, it does not decide what "correct" means.

So both halves of the boundary — what gets *set* and what counts as *verified* — are closed by construction, not by a reviewer trusting S4 to behave.

## 2. Value-sourcing (the resolved seam)

The value-claim's `expected_value` is **requirement-sourced** — carried from synthesis, the value the requirement actually states. `_author_positive` threads that one value into **both** the `CreateStep` (`field = V`) and the `AssertStep` (`field == V`): set it, then verify it persisted.

**S3 never fabricates a value.** If the requirement states no value, **no value-claim grounds** — it stays `EMISSION_DEFERRED`. No representative values, no invented values, no type-default guessing. (Contrast the negative, which *derives* its violating value via D-107; the positive is *given* one or it does not emit.)

## 3. Scope fence — directly-set-state only

- **IN:** create a record, read it back, ground the observed *directly-set* value vs. the claim.
- **OUT (deferred):**
  - automation effects, branch-sensitive flows, async observation, entanglement detection (**k8**);
  - prerequisite-parent construction — **no required lookups**; padding is scalars / simple-picklist only;
  - complex / async teardown;
  - multi-step composition (**k15**).

The fence keeps the slice to the one thing it proves: a directly-set field value, set and observed on one record.

## 4. What it verifies

- The requirement's **stated field value is operationally achievable + persists** on the current org — which catches **VR / FLS / type conflicts** at execution time (a value that synthesis stated but the org won't accept surfaces here, not in production).
- It proves the **positive execution spine** — construct-world → create-expect-success → observe → ground — that **automation-effect positives reuse later** (those add the firing/observation layers on top of this spine).

## 5. The build — two-sided

**(A) S3 (emission).** A `GroundedPositive` (object + field + the requirement-sourced value) grounded on S1 (object exists + field `BELONGS_TO` it). `_author_positive` emits a **value-claim** + a data recipe of `CreateStep` (**no `expect_rejection`**) → `ReadStep` → `AssertStep(equals)`. `EMITTABLE += ("data_behavior", "value-claim")`; `author_emission` dispatch on `GroundedPositive`; governance stashes it (so PROCEED routes here instead of `emission_deferred`). The S2 model needs no change — `ValueClaimBody`, `CreateStep`-without-rejection, `ReadStep`, `AssertStep` + the `equals` predicate + `StateDescriptor` all already exist.

**(B) S4 (execution).**
1. **Construct the operational world** — read S1 requiredness for the target object, compute the padding set (required − semantic), fill it with valid filler.
2. **Create-expect-success** — issue the create; the grounded outcome expects success (the mirror of the behavioral negative's expect-reject).
3. **Observe as a distinct phase** — read-back is its **own phase**, *async-ready*: **no immediate-consistency assumption is baked into finalization or grounding** (so async/eventually-consistent observation slots in later without reworking the spine).
4. **Ground `field == V`** — compare the observed value to the claim's carried target, verbatim.
5. **Structured-trace evidence** — the create + observe + ground steps captured as the S4 evidence record.
6. **Teardown framed as execution-isolation (k14)** — cleanup is an isolation concern (leave the org as found), not part of the semantic verdict.

## 6. What it closes

**generate → run.** For the first time a *positive data recipe* flows the whole chain — requirement synthesis → S3 emission → live S4 execution → grounded verification — end to end. S4 executes a genuinely S3-emitted positive recipe, not a hand-built one.

---

## Status

Design locked (**D-115**). **No impl.** The build is two-sided (S3 emission + S4 execution) and gated behind the structural boundary of §1; HOLD before any build.
