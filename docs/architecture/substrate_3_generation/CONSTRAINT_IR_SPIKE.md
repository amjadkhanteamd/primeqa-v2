# Constraint IR — design spike (req-315 programme, Phase 2 foundation)

Status: DESIGN (not yet built). Precedes the Phase-2.1 entailment selector and the
Phase-3 fixture solver so both consume ONE representation. D-342 tactical guards ship
independently of this.

## Why one IR

Predicate **selection** ("which VR does this claim exercise?") and **fixture
construction** ("build a record that activates the target rule while satisfying every
other active rule") are the same representation problem seen from two sides. If the
selector invents a witness-value representation and the solver later needs interval
reasoning, we build — and drift between — two constraint models. This spike fixes the
shared IR up front.

Lifecycle it must serve:
```
requirement condition → Constraint IR → target-behaviour selection → target-rule
  activation constraints → sibling-rule constraints → fixture construction →
  transport conversion (D-342 §1.3 boundary) → execution → evidence validation
```

## The IR: constraints, not witnesses

Carry the claim's grounded conditions **as constraints** — do NOT collapse to a single
witness value (brittle; the D-342 percent trap showed why `Discount>0.20 → {Discount:1}`
is wrong). The objects already exist: `_GroundedCondition(field, predicate, value,
compared_to)` (`emission.py`) over the settled predicate taxonomy
(`governance_core.py:162-166`):

| Constraint kind | predicate(s) | payload | example |
|---|---|---|---|
| threshold | `>` `<` `>=` `<=` (via `exceeds`/value) | field, op, literal | `Discount > 0.20` |
| equality | `equals` / `not_equals` | field, value | `Compliance_Approved = false` |
| membership | `in_set` | field, {values} | `Risk_Level ∈ {High, Critical}` |
| nullity | `is_null` / `is_not_null` | field | `Approval_Reason is_null` |
| cross-field | `exceeds` (+`compared_to`) | field, field | `Loan > Property_Value` |
| transition | (D-210 to/from state) | field, from→to | `Stage: * → Approved` |

`ConstraintSet` = an AND of these over one subject Object. It is the LEFT side of both
operations. VR error-condition formulas (RIGHT side) stay their parsed D-107 AST.

## Two operations on the IR

### (A) Entailment — `necessarily_fires(vr_ast, constraint_set) → TRUE | FALSE | UNKNOWN`
Powers Phase-2.1 selection. **Selection requires NECESSARILY fires, never merely
possibly.** Kleene tri-state, **absent field = UNKNOWN** (reuse `vr_conflict._fires`'s
discipline — `evaluate`'s absent=blank would manufacture spurious fires,
`vr_conflict.py:40-47`). Leaf evaluation:
- threshold vs threshold: `Discount>0.20` entails a VR conjunct `Discount>C` iff `0.20 ≥ C`
  (every value satisfying the claim also trips the rule). `>30` entails both `>20` and
  `>25` → **≥2 necessarily fire → ambiguous → refuse** (never arbitrarily pick).
- `>20` vs a VR `>25`: some claim-satisfying values trip it, not all → **not entailed →
  UNKNOWN → refuse** (the possibly/necessarily distinction — the crucial semantic).
- membership/equality/nullity: standard entailment; `Risk ∈ {High,Critical}` entails
  `ISPICKVAL(Risk,"High") || ISPICKVAL(Risk,"Critical")`.
- cross-field: exact-pair match (the existing D-330 filter, promoted onto the IR).
- any operator/shape outside this grammar → **UNKNOWN** (never guess). The fixed grammar
  is the named boundary that keeps this a bounded entailment check, not an open solver.

Selector rule (into `_best_aligned_vr`'s tie branch): exactly one candidate `TRUE` → select;
0 or ≥2 → keep the refuse-on-non-unique floor; compose after the D-330 cross-field filter,
before the D-296 structural fallback.

### (B) Satisfaction search — `minimal_fixture(target_vr, sibling_vrs, metadata) → assignment | UNSAT`
Powers Phase-3. Find a minimal field assignment that makes `target_vr` **activatable**
(for a positive: satisfy all sibling constraints so the target transition is *reached*;
for a negative: trip ONLY the target rule) while every sibling VR evaluates FALSE. This is
`_satisfy` (`verified_negative`) generalised from one formula to the active-constraint SET,
over the SAME grammar as (A). Bounded identically: a sibling outside the grammar → refuse
the fixture (honest "cannot isolate") rather than emit T3-style false-fails. VR08's
RecordType and required-field parents enter here as additional constraints (via the D-342
§1.3 transport boundary for RecordType devname↔Id).

## What this reuses vs adds
- **Reuses:** `_GroundedCondition` (the IR carrier), the predicate taxonomy, `vr_conflict._fires`
  (Kleene leaves), the D-107 parser, the D-330 cross-field pair logic, `_satisfy`.
- **Adds:** a thin `ConstraintSet` view + the interval/set **entailment** on leaves
  (`_fires` today answers "does a concrete state fire?"; (A) answers "does the constraint set
  *entail* firing?") and the multi-formula **satisfaction** extension for (B).

## Acceptance for the spike (before building 2.1)
The IR + operation (A) must reproduce, on the req-315 rules: `deal_value≤0`→VR01 (unique);
`{Deal_Value>1e6, Compliance=false, Risk∈{High,Critical}}`→VR03 (unique); `Discount>30`
vs {VR-A `>20`, VR-B `>25`}→ambiguous-refuse; `Discount>20` vs `>25`→possibly-only→refuse.
Operation (B) must, for VR10, return a fixture that satisfies VR01–VR09 while reaching
Enterprise/Approved/>$2M (the T3 worked case) — or a bounded UNSAT, never a false-fail.

## Deferred (bounded on purpose)
General constraint solving (stay within the fixed predicate grammar); non-anchored regex
and free date arithmetic (Phase-2.3 handles the anchored/relative cases only); the value
model + evidence-attribution abstractions (D-342 first instances) are parallel workstreams
that consume this IR but are not part of the spike.
