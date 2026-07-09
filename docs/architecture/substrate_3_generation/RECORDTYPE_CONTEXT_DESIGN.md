# RecordType as a first-class context constraint — design (HELD for review)

Status: **DESIGN — HOLD for review before implementation.** Extends
`CONSTRAINT_IR_SPIKE.md`. Builds on **D-348** (RecordType derivation
*infrastructure*, already shipped on `phase-4-substrate-3-req315-quality`) and
**D-343** (the entailment VR selector). This note designs the *selection chain*
D-348 explicitly did **not** build; nothing here is implemented.

## The gap (from D-348)

D-348 unblocked the **derivation** half of a record-type-gated rule:
`verified_negative._satisfy_recordtype` (`verified_negative.py:368`) resolves
`RecordType.DeveloperName = "X"` to a concrete `RecordTypeId` off the
`__record_types__` rail, and `derive(VR08)` produces a firing payload. But VR08
is still **not selected**, empirically confirmed (regen: 0 RecordType
negatives). The reason is that RecordType is invisible to every side of the IR
*except* derivation:

| Layer | Ordinary field predicate | RecordType today |
|---|---|---|
| Claim taxonomy / LLM | proposes `Discount > 0.25` | no way to say "Enterprise deal" |
| Grounding | resolves the field | no path from "Enterprise deals" → a record-type context |
| Entailment (`vr_conflict._fires`) | resolves the leaf | dotted ref → `_MISSING` → **unknown** (`vr_conflict.py:190-193`) |
| Satisfaction (`_satisfy`) | resolves the leaf | **already handled** (D-348) |

So VR08's distinguishing conjunct — `RecordType.DeveloperName = "PLS_BM_Enterprise"` —
is unexpressible upstream and evaluates to *unknown* in the entailment selector,
so `entails_firing` (`vr_conflict.py:96`) can never return TRUE for VR08. A claim
that instead conditions on the ordinary field `Deal_Type = Enterprise`
field-overlaps **VR10** (2 fields) over VR08 (1) and the D-295 field-overlap tie
picks VR10. The fix is to make **RecordType a first-class *context constraint*** so
it becomes expressible (taxonomy), groundable ("Enterprise deals"), and
*entailment-evaluable* — at which point VR08 is necessarily-fired and uniquely
selected, and the test designer can build a contextual boundary pair.

VR08 (the worked example): `RecordType.DeveloperName = "PLS_BM_Enterprise" && Discount > 0.25`.

---

## 1. RecordType as a first-class CONTEXT constraint in the Constraint IR

Add **one** constraint kind to the IR (`CONSTRAINT_IR_SPIKE.md` §"The IR"):

| Constraint kind | predicate | payload | example |
|---|---|---|---|
| **context** (new) | `record_type` | subject, DeveloperName | `RecordType = PLS_BM_Enterprise` |

It is deliberately **not** an ordinary equality predicate on a field, because it
behaves differently on all three boundaries the IR crosses:

- **Value domain** — the payload is a record *classification* (a DeveloperName),
  resolved against S1's RecordType entities, not a literal a user types.
- **Transport domain** — it is realized on the wire as `RecordTypeId` (an 18-char
  Id), the D-346 DeveloperName→Id boundary D-348 already implements.
- **Both IR operations** — it participates in entailment (§3) and satisfaction
  (§4) via the *RecordType leaf*, never a bare `_GroundedCondition` field lookup.

Shape: the `ConstraintSet` for a subject gains an optional single `context`
slot (`record_type`, 0..1 per subject — a record has exactly one RecordType).
Concretely this is a new `_GroundedCondition` predicate value (`"context_record_type"`)
carried alongside the D-343 field constraints, or a parallel `ContextConstraint`
on the set — an implementation choice to settle at build time; the design
commitment is that it is *one first-class constraint*, enumerated into the
entailment worlds and passed to the satisfaction/emission path, not a second
representation.

Bounded on purpose: **only RecordType.** Owner, page layout, sharing, and other
record-context dimensions stay out — RecordType is the single on-record
classification the recipe can realize (D-348's pre-scan discipline: it is the
*one* cross-object ref the record itself owns).

---

## 2. Grounding "Enterprise deals" → the context constraint

Requirement semantics such as *"Enterprise deals are subject to stricter discount
controls"* name a **subset of records by classification**, not a field value.
The grounding path:

1. **Propose (LLM).** `tools.py` gains a condition shape for record context —
   `{context: {record_type: "Enterprise"}}` — parallel to the existing field
   conditions. The LLM emits it when the requirement scopes behaviour to a named
   kind of record ("Enterprise deals", "Partner accounts", "Renewal opportunities").
2. **Resolve (governance, certainty-bounded).** Governance resolves the proposed
   name against the neighborhood's RecordType entities — the **same source**
   `_grounding_field_metadata` already reads to build the `__record_types__` rail
   for D-348 derivation (reused, not duplicated). Match on **label OR
   DeveloperName**; on a unique match → pin the context constraint to that
   DeveloperName (e.g. `PLS_BM_Enterprise`). On **no match or an ambiguous match →
   REFUSE** (the D-293 ground-or-refuse floor — never guess a classification the
   org does not expose). This is the authoring-side twin of D-348's derivation-side
   `_Undecidable("RecordType 'X' not in the org")`.

The grounded context constraint then rides the claim exactly like a field
constraint: into the selector (§3), into `derive`/`_satisfy` (already consumes
the rail), and into the emitter (§4).

---

## 3. Entailment evaluates `RecordType.DeveloperName` predicates

Today `vr_conflict._lookup` (`vr_conflict.py:188-193`) returns `_MISSING` for any
dotted ref, so a VR conjunct on `RecordType.DeveloperName` is always *unknown* and
`entails_firing` can never confirm VR08. Extend the entailment leaf resolver to
consult the **context constraint**, mirroring the derivation side:

- `_constraint_states` (D-343, `governance_core`) enumerates the claim's pinned
  worlds. Add the context constraint as a pinned leaf: `RecordType.DeveloperName =
  <grounded DeveloperName>` is a **known** value in every enumerated world (a
  record has one RecordType — no Cartesian blow-up).
- `_fires`/`_lookup` (`vr_conflict.py`) resolve the `RecordType.DeveloperName`
  dotted path from that pinned context instead of returning `_MISSING`. **Only**
  this path is resolved from context; every other dotted ref stays `_MISSING`
  (unknown) — the D-348 `_pre_scan` boundary mirrored on the entailment side, so
  no other cross-object ref silently becomes "known".

Result on VR08 with the claim `{context: RecordType=Enterprise, Discount > 0.25}`:
`RecordType.DeveloperName = "PLS_BM_Enterprise"` → **TRUE** (context pins it) AND
`Discount > 0.25` → **TRUE (necessarily**, via D-343's threshold entailment
`0.25 ≥ 0.25`... — note: the claim constraint is `Discount > 0.25`, which entails
the VR conjunct `Discount > 0.25` iff every claim-satisfying value trips it; the
predicate-entailment already handles `>C` vs `>C`). VR08 → **necessarily fires**.
VR10 (a `Deal_Type`/`ISCHANGED`-gated rule, a different subject condition) → not
necessarily fired by this constraint set → **UNKNOWN/FALSE**. Exactly one
candidate is TRUE → `_break_tie_by_entailment` **selects VR08 uniquely**,
dissolving the field-overlap tie that picked VR10. No new selector rule — the
existing entailment path now simply *has* the RecordType leaf it was missing.

---

## 4. The test designer: a contextual boundary pair

Extend the D-300/D-328 boundary set (`verified_negative.derive_boundary_set`,
`verified_negative.py:157`) to a **three-member CONTEXTUAL set** when the
ConstraintSet carries a context constraint **and** the org exposes an alternative
RecordType. Using VR08 (threshold `Discount > 0.25`, context `RecordType =
Enterprise`; the org's other record type call it `Standard`):

| # | Fixture | Expected | Proves |
|---|---|---|---|
| **1. in-context, at boundary** | `RecordTypeId=Enterprise, Discount=0.25` | **accepted** (strict `>`; 0.25 is just-inside) | the threshold's lower edge |
| **2. in-context, above boundary** | `RecordTypeId=Enterprise, Discount=0.26` | **rejected + ATTRIBUTED to VR08** | the rule fires above the edge, *for the right reason* |
| **3. out-of-context control, above boundary** | `RecordTypeId=Standard, Discount=0.26` | **accepted** | the rule is **context-scoped** — the *same value* rejected in-context is accepted out-of-context |

The three probes together prove **both axes independently**:

- **Threshold axis** — member 1 (accept) vs member 2 (reject) at the *same*
  RecordType isolates the discount boundary.
- **Context axis** — member 2 (reject) vs member 3 (accept) at the *same discount
  value* isolates the record-type gate. Without member 3, a passing member-2
  rejection could be any Enterprise-only side effect; member 3 is the control that
  attributes the rejection specifically to the Enterprise×threshold conjunction.

Reuse:
- Members 1 & 2 are the existing D-328 gated boundary set — the context constraint
  is simply an additional gate staged TRUE in both (via D-348 `_satisfy_recordtype(want_true=True)` → `RecordTypeId=Enterprise`).
- Member 3 reuses **D-348 `_satisfy_recordtype(want_true=False)`** (`verified_negative.py:390`),
  which already picks an alternative RecordType off the rail — the "Standard" arm
  falls out of code that ships today.
- Member 2's attribution rides the **D-345** evidence contract (OUTCOME→ATTRIBUTED)
  + D-297 `error_message_pattern` = VR08's message.
- Transport (D-346): `RecordTypeId` is already an Id; `Discount` 0.25/0.26 →
  25/26 via the percent scale.

Refuse honestly: if the org exposes **no** alternative RecordType, drop member 3
and emit the plain two-member D-300 set (threshold-only) rather than fabricate a
control — the control arm is only meaningful against a real alternative
classification.

---

## Composition & what this reuses vs. adds

- **Reuses:** D-348 `_satisfy_recordtype` + `__record_types__` rail (both arms) ·
  `_grounding_field_metadata`'s RecordType read (grounding source) · D-343
  `_constraint_states`/`_break_tie_by_entailment`/`entails_firing` (extend the
  leaf resolver only) · D-300/D-328 `derive_boundary_set` (extend to 3 members) ·
  D-346 transport · D-345 attribution · D-293 ground-or-refuse floor.
- **Adds:** the `context`/`record_type` constraint kind (§1) · its LLM shape +
  certainty-bounded resolver (§2) · the entailment context-leaf resolver (§3) ·
  the three-member contextual boundary emitter (§4).

## Bounded (what this is NOT)

- **Only RecordType** context — arbitrary related-record context stays unknown.
- **One record_type per subject** — multi-record-type gating is out.
- The control arm **requires a real alternative RecordType**; else 2-member fallback.
- Not a general record-context / page-layout / field-availability model — the
  single on-record classification the recipe can realize.
- Selection stays a **bounded entailment** over the fixed predicate grammar +
  this one context leaf; no open constraint solving is introduced.

## Acceptance (when built, against live env-59)

1. **Selection** — req-315 regen pins **VR08 (not VR10)** on the Enterprise-discount
   claim; the entailment tie-break resolves it (unit: the necessarily/possibly
   trichotomy extended with the RecordType leaf).
2. **Emission** — a three-member contextual boundary set on VR08
   (Enterprise@0.25 accept / Enterprise@0.26 reject-attributed / Standard@0.26 accept),
   the third arm present iff the org has a second RecordType.
3. **Live** — run the set on env-59: members 1 & 3 create-succeed; member 2 is
   rejected with VR08's message (attributed, not merely "rejected"). Scorecard:
   correctly-exercised controls **4/10 → 5/10**, VR08 attributed and context-isolated.

## Deferred (separate work, not this design)

- **VR10** (ISCHANGED / approval-arc) — the other remaining req-315 control.
- Record-type-dependent field availability / picklist-value-set scoping / page
  layout — the broader "context" surface this note deliberately narrows away from.
