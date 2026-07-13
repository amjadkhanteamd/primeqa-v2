# Wave 2 — Decision Logic & Cross-Record Foundations (design)

**Date:** 2026-07-13 · Branch `phase-7-substrate-3-b01-label-affinity`.
Production-capability wave; benchmark-independent throughout.

## Checkpoint 1 — what exists vs what Wave 2 adds

Already shipped on this branch (D-366/D-367, live-proven): ordered
first-match decisions with negation-context + default arm + per-path
conservatism (C1); interval fire-witnesses + value-less N-arm emission
(C3/C3b); entry-condition reasoning for Create / Update / CreateAndUpdate /
updated-to-meet / IsNull / EqualTo / IsChanged / numeric filters (C2/C4/C5).
Wave 2 therefore builds exactly four structural additions:

### A. Deterministic branch identity (CP2 gap)
Behaviours today carry guard CONTENT but not branch PROVENANCE. Add a
`branch` slot to every fan-out behaviour: `"<decisionName>:<ruleName>"`
(`"<decisionName>:default"` for the default connector), `None` off-decision.
Pure metadata: never identity-bearing at the claim layer (guards already
hash the semantics; two orgs with renamed rules but identical logic must
keep identical claim identities), but it gives attribution narration and
witness naming a stable, deterministic handle.

### B. Boundary + suppression witnesses for ladders (CP3 gap)
The fire witness (strictly interior) exists. Add to `witnesses.py`:
- `boundary_witnesses(constraints, scale)` → for each ORIGINAL threshold in
  an arm's interval, the two adjacent quantized values with their side
  (`at`/`below`); deterministic; composes the D-346 edge discipline.
- Suppression semantics are documented, not new machinery: ladder arms are
  mutually exclusive, so **arm X's fire witness IS every other arm's
  suppression witness** (the same create observing Tier=X refutes Y⋅Z...).
  The N-arm set already emits all arms; no additional claims needed. The
  self-default class (FL01) keeps its existing value-claim suppression arms.
- Emission: boundary arms for ladder claims ride the EXISTING
  `enable_bva_boundaries` flag (generic, org-independent): for a value-ful
  arm claim, one extra grounded stash per adjacent threshold asserting the
  arm that side of the edge fires. Deterministic identities via the staged
  boundary value in semantic_conditions.

### C. Formula guards — bounded deterministic subset (CP4)
Decision conditions whose `leftValueReference` is `{!formulaName}` are
today `unparseable_guard_condition` (undifferentiated). Wave 2:
1. **Named refusals**: an out-of-grammar formula guard demotes as
   `formula_guard_not_deterministic:<FN>` (the offending function), never
   the generic reason — explicit and honest.
2. **Deterministic subset admitted**: a formula that is a bare
   `{!$Record.Field}` passthrough grounds as if the condition referenced
   the field directly (the only semantics-preserving translation that needs
   no inversion). Transform-chain formulas in guards stay refused-by-name
   (comparing `UPPER(f)` to a literal is witnessable but inversion is
   ambiguous — deferred until a real construct demands it; honest limit).

### D. Cross-record premise abstraction (CP6) — NOT full Get Records
Get-Records nodes stay non-grounding, but stop being opaque: a bounded
recordLookups element becomes a typed **premise** in the IR —
`{"kind": "cross_record_premise", "object", "filters" (IsNull/EqualTo
literals only), "single" (getFirstRecordOnly)}` — carried per-path like
behaviours, with out-of-grammar lookups keeping the existing subject-safe
demotion. New projection `flow_cross_record_premises()` for the future C7
evidence engine to plug into; `_field_capability_summary` is unchanged
(premises are not producers). No witnesses, no evidence, no admission
change — representation only, so C7 becomes an evidence-layer slice.

## Non-goals (explicit)
Full Get Records semantics, count/each_matches assertions, child→parent
attribution (C7); formula inversion; any benchmark-specific handling.

## Verification plan per checkpoint
Green suite + corpus byte-diff (only intended IR changes) + deterministic
replay regression (CP9) against the Convergence-V1 baseline. Live
benchmark generation is quota-blocked until 2026-08-01 — CP7 verifies
FL01–FL03 deterministically (corpus + crafted-intent resolution against
live S1) and FL04/FL05 as: premises now REPRESENTED + honest refusals
carrying the named reasons; the live rerun command is documented.
