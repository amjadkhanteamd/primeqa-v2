# RecordType as a first-class context constraint — design (HELD for review)

Status: **DESIGN — HOLD for review before implementation.** Extends
`CONSTRAINT_IR_SPIKE.md`. Builds on **D-348** (RecordType derivation
*infrastructure*, already shipped on `phase-4-substrate-3-req315-quality`) and
**D-343** (the entailment VR selector). This note designs the *selection chain*
D-348 explicitly did **not** build; nothing here is implemented.

> **Superseded — read Amendments A then B (end of file) first.** AK reviewed §1–§4
> (2026-07-09), approved §1–§3 with two added constraints, and asked for §4 to be
> redesigned into composable primitives. **Amendment A** captured that plus the code
> corrections a grounding workflow surfaced. **Amendment B** then records AK's two
> decisions (ship DeveloperName grounding; do *not* pick a claim shape to make VR08
> selectable) and the architectural discovery that supersedes A's central claim:
> **control relevance (nomination) and formula entailment (proof) are two different
> operations.** A requirement that under-specifies the threshold ("stricter discount
> controls", not ">25%") cannot be handled by entailment as a *selector* — relevance
> nominates the control, formula analysis reads the boundary, entailment only proves
> firing when the requirement's own predicates suffice. **Amendment B is the current
> design of record**; A's A2/A3/A6 entailment-as-disambiguator framing is corrected by
> it. §1–§4 above are the initial sketch.

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

---

# Amendment A — AK review (2026-07-09): grounding constraints, §4 redesign, and the entailment hand-off (HELD)

Status: **DESIGN OF RECORD — HOLD for implementation.** Supersedes §1–§4 above.
Records AK's disposition (approve §1–§3 with two added constraints; redesign §4 into
composable primitives) and the corrections a grounding workflow surfaced — every claim
below is anchored to a real symbol read this pass. **Nothing here is implemented.**

## A0 — The one thing that changed: §2/§3/§4 are one mechanism

The sketch treated grounding (§2), entailment (§3), and the boundary test (§4) as three
independent steps. They are not. The **honest disambiguator** AK asked for — *"reason
from the acceptance criterion, not the word 'Enterprise'"* — has a concrete, code-grounded
form: **necessarily-firing entailment, fed the boundary's firing value.** A hypothesis is
kept iff the VR it grounds to *necessarily fires* under the claim's pins **plus** the
prohibition's firing value (25.01% / 26%). For req-315 that uniquely elects VR08 over
VR10 — not because "Enterprise" reads as a record type, but because VR08 (a discount rule)
necessarily fires at `RecordType=Enterprise ∧ Discount>0.25`, while VR10 (an approval rule)
is only *possibly*-firing (Stage/Deal_Value unpinnable, `ISCHANGED` is an org-state
function → Kleene-`None`) and so loses. The firing value that makes VR08's threshold
conjunct `True` is **owned by §4's BoundaryPair** and **consumed by §3's entailment**.
That hand-off is the spine of the whole design; the rest is plumbing.

### Reality corrections the grounding forced (each reshapes a section)

1. **The LLM is handed zero org metadata** — only requirement text + `{s1_version…}`
   (`runtime.py:142-146`). Grounding is verify-*after*-propose. So the sketch's "the LLM
   emits `{context:{record_type:"Enterprise"}}`" is **construction bias** — handing the
   model a `record_type` slot for the ambiguous word tells it how to read it. Disambiguation
   must live substrate-side (§A2).
2. **Competing hypotheses cannot be carried forward today** — per-intent grounding is
   single-path and *refuses* on >1 candidate (`resolve_intent`, `governance_core.py:1396`);
   the existing `ambiguous-reference` RefusalKind is entity name-collision only, not
   field-vs-context. So "retain competing hypotheses" is net-new; the shippable floor is
   **PICK-ONE-or-REFUSE** (§A2, HOLD-1).
3. **§3 has TWO blockers, the sketch named one** — the dotted-ref `_lookup` (`vr_conflict.py:190`)
   *and* the unpinned threshold value (`_constraint_states` pins only `equals/is_null/in_set`;
   no `>`-literal predicate). Closing only the first leaves `entails_firing(VR08) = (True AND
   None) = None` (§A3).
4. **No recipe- or fixture-level dedup exists** — a recipe row binds to exactly one
   `claim_test_id` (`persistence.py:167`); dedup is claim-identity-grained only. "Three tests
   from one shared fixture" is expressible **only within a single claim bundle**, member-grained
   — not across claims (§A4).
5. **The RecordType human label is not persisted queryably in S1** — `record_type_details`
   has no label column, `RecordTypeAttributes` no label field; the `__record_types__` rail
   carries `{DeveloperName: sf_id}` only. AK's label benchmark is not testable as *label*
   similarity without new S1 capture (§A2, HOLD-2).
6. **`derive_boundary_set` is 2-member and integer-only** — `_is_threshold` requires an
   integer literal (`verified_negative.py:224`), so VR08's `Discount > 0.25` is unrecognized
   and the set refuses today (§A4, prerequisite P1). The sketch's warning about a "hard-coded
   3-member set" guards against something that does not exist.
7. **The `RecordTypeId` already lives only at the transport boundary** — `_satisfy_recordtype`
   emits `{RecordTypeId: rid}` and `transport_payload` treats it as an Id; the semantic IR
   carries no RecordType at all. So AK's §1 "keep the Id out of the IR" is a *conservation*
   rule that matches today's boundary, not a new wall to build (§A1).

## A1 — §1 IR: three-part identity via a net-new `_GroundedContext` (APPROVED + constraint)

`_GroundedCondition` is a frozen 4-slot dataclass whose `field` is an `_Endpoint` filled
*only* from a BELONGS_TO Field lookup (`governance_core.py:186`) — RecordType is not a Field
and refuses that lookup. So RecordType context rides a **parallel struct**, not an overloaded
condition:

```
@dataclass(frozen=True)
class _GroundedContext:        # emission.py, sibling of _GroundedCondition
    subject: _Endpoint         # the Object the record belongs to
    requested: str             # "Enterprise"          — provenance / traceability
    developer_name: str        # "PLS_BM_Enterprise"   — stable SEMANTIC identity
    requirement_excerpt: str   # the AC span that grounded it
    # NO record_type_id. The 012… Id never appears here.
```

The three-part identity AK asked for, and where each part is allowed to exist:

| Part | Value | Lives in | Carrier | Never in |
|---|---|---|---|---|
| `requested` | `"Enterprise"` | semantic IR (provenance) | `_GroundedContext.requested` / `.requirement_excerpt` | — |
| `developer_name` | `"PLS_BM_Enterprise"` | semantic Constraint IR | `_GroundedContext.developer_name`; pinned as the `RecordType.DeveloperName` leaf world in `_constraint_states` | transport payload keys |
| `record_type_id` | `"012…"` | execution/transport boundary **only** | minted **late** by `_satisfy_recordtype` (`verified_negative.py:389`) off the `__record_types__` rail → `{RecordTypeId: rid}` → `transport_payload` (D-346) | the IR / `grounded_conds` / `_constraint_states` / `_GroundedContext` |

**Conservation rule (this is what "keep the Id out of the IR" means operationally):** the
`__record_types__` rail is read at exactly one site — `_satisfy_recordtype`, at
derivation/emission, *downstream* of selection. Nothing upstream (`_constraint_states`,
`vr_conflict._lookup`, `_GroundedContext`) may import `_RECORD_TYPES_KEY`. Selection and
entailment reason on the DeveloperName string; the Id first exists at emission.

## A2 — §2 grounding + ambiguity: substrate-side, entailment-driven, honest (APPROVED + 2 constraints, reshaped)

**The construction-bias fix.** The LLM proposes only what it can see from text — a discount
threshold (the requirement's actual subject) and an *"Enterprise" classification token* on the
subject, **with no field-vs-context commitment and no `record_type` slot offered**. Because the
model sees no metadata, it cannot be data-biased; the only bias risk is the tool schema, so the
schema must not name RecordType for this word.

**Two hypotheses, formed substrate-side** at `resolve_intent` (`governance_core.py:1361-1461`,
the only existing chokepoint) when the token resolves against the scoped neighborhood to *both*
a picklist field value (`Deal_Type__c = Enterprise`, via the `_bind_picklist_values` certainty
bar) *and* a RecordType DeveloperName off the rail:

- `CS_field`  = `{ Deal_Type__c = Enterprise (grounded field condition), discount threshold on the recipe boundary }`
- `CS_context` = `{ _GroundedContext(RecordType = PLS_BM_Enterprise), discount threshold on the recipe boundary }`

**The honest disambiguator — necessarily-firing entailment (not the label, not an ad-hoc
"effect-type match").** Run each candidate through the selector fed the boundary firing value
(the §A3 hand-off): keep the hypothesis whose grounded VR **necessarily fires**. `CS_context`
→ VR08 necessarily fires (`RecordType=Enterprise` pinned ∧ `Discount=26%` pinned) → **kept**.
`CS_field` → VR10 only *possibly* fires (`Stage`/`Deal_Value` unpinned; `ISCHANGED` → org-state
`None`) → Kleene-`UNKNOWN` → **not selected**. This is the code-grounded form of "reason from
the discount acceptance criterion": VR08 *is* the discount rule, and its necessary firing is the
evidence — the string "Enterprise" is never the tiebreak. *(Which selector path carries this —
pass-1 field-overlap cardinality in the RecordType-replaces-Deal_Type shape vs the
`_break_tie_by_entailment` tie branch in the both-fields shape — is the first thing the
implementation must pin down; see §A3 scope note.)*

**Decision rule** (the `_best_aligned_vr` refuse-on-non-unique discipline, reused):

| Condition | Outcome |
|---|---|
| exactly ONE hypothesis's VR necessarily fires & is admissible | **PICK-ONE** (req-315: `CS_context`→VR08); log the rationale fragment |
| both necessarily fire & neither dominates | **REFUSE** (retain-competing is deferred — HOLD-1) |
| zero necessarily fire, or both indistinguishable | **REFUSE** |

**Refusal path:** a **new** `RefusalKind` `CLASSIFICATION_MECHANISM_AMBIGUOUS`, distinct from
`ambiguous-reference`; rides D-302 partial-refusal stashing (batch continues) and the single
D-247/D-340 coverage re-prompt — which asks the LLM to disambiguate *from the requirement text
only* (does the AC say "record type" / "deal type"?), never by offering a mechanism slot.

**Provenance (§2 added constraint):** the struct carries `requested` + `requirement_excerpt`
(what "Enterprise" grounded to); a fragment on the runtime provenance spine (`runtime.py:298/344`)
records *why* — `{requirement_excerpt, token, hypotheses_formed:[field,context], selected, discarded,
rationale:"VR08 necessarily fires; VR10 only possibly (approval rule)"}`. The struct answers
"what"; the spine answers "why not the other hypothesis" — the auditable core of the honesty
mechanism.

**Label constraint (§2 added constraint) — NOT deliverable as stated (HOLD-2).** AK's benchmark
("Label: Enterprise / DeveloperName: PLS_BM_Enterprise"; two semantically similar labels) needs a
queryable RecordType label, which S1 does not persist. Two options, lean **(ii)** with **(i)**
flagged as the prerequisite for AK's full intent:
- **(i)** add S1 RecordType label capture (`record_type_details.label` or `RecordTypeAttributes.label`)
  — a real S1/sync change, out of scope for this S3 amendment; required for genuine *label* grounding.
- **(ii)** resolve the token against **DeveloperName only**, certainty-bounded exactly like
  `_bind_picklist_values` (unique substring/word match; refuse on 0/≥2). Disambiguates req-315
  correctly (`PLS_BM_Enterprise` vs `PLS_BM_Standard` are distinguishable) but re-frames "not fuzzy
  guessing" from *label-similarity* to *devname-uniqueness*. **This is a real scope reduction — AK's
  call.**

Note the requirement TEXT for req-315 lives only in the DB, not the repo; the gold-standard doc
*pre-disambiguates* VR08 to "record type" and is confidential-to-evaluation, so it must **not** be
used as the grounding input — the substrate must disambiguate from the requirement alone.

## A3 — §3 entailment: two blockers + the BoundaryPair hand-off (APPROVED, sharpened)

For entailment to elect VR08, **both** blockers must close — the sketch named only the first:

- **Blocker 1 (leaf) — the dotted RecordType ref.** `vr_conflict._lookup` (`vr_conflict.py:188-193`)
  returns `_MISSING` for any dotted ref. Special-case exactly `RecordType.DeveloperName` (guarded by
  the `_is_recordtype_ref` discipline) to resolve against the pinned `_GroundedContext`; every other
  dotted ref stays `_MISSING`.
- **Blocker 2 (threshold value) — NEW, and the §4↔§3 hand-off.** `entails_firing(VR08) =
  RecordType-leaf ∧ Discount>0.25`. The discount conjunct is never pinned — `_constraint_states`
  enumerates only `equals/is_null/in_set` and the predicate taxonomy has no `>`-literal, so the
  firing value rides the recipe boundary and stays `None` → `(True AND None) = None`. **The firing
  value (26%) is owned by §4's BoundaryPair and must be pinned into the entailment world.** Make this
  interface explicit; without it the tie does not dissolve.

**New attach point:** `_constraint_states` must also enumerate the `_GroundedContext` and pin
`RecordType.DeveloperName = developer_name` — it iterates only `grounded_conds` today, so this is a
second explicit change point beside the `_lookup` special-case.

**The four §3 cases validate the LEAF RESOLVER unit only** (whole-VR entailment additionally needs
the Blocker-2 pin; do not let the table read as "VR08 necessarily fires"):

| Case | Claim pins | Leaf | Result |
|---|---|---|---|
| **(E, E)** | `_GroundedContext(PLS_BM_Enterprise)` | resolves → `= "PLS_BM_Enterprise"` | leaf **TRUE** |
| **(S, E)** | `_GroundedContext(PLS_BM_Standard)` (the `_satisfy_recordtype(want_true=False)` arm) | `Standard = "Enterprise"` | leaf **FALSE** |
| **(unspecified, E)** | no `_GroundedContext` | `_lookup` → `_MISSING` | leaf **UNKNOWN** |
| **(ambiguous, E)** | competing H1/H2 unresolved | — | **REFUSE before entailment** (§A2 RefusalKind) |

**Scope note (load-bearing).** In the claim shape where RecordType context *replaces* Deal_Type,
VR08 wins by field-overlap **cardinality at pass-1** (`_best_aligned_vr`) and the entailment tie
branch is never entered. Entailment is load-bearing for VR08 selection **only** in the *both-fields*
tie shape — exactly where Blocker 2 defeats it today. The implementation must decide which shape the
substrate emits and route accordingly.

## A4 — §4 redesign: BoundaryPair + ContextDifferential composable primitives (the concern, resolved)

Two named primitives, both emitting `BoundaryMember`s into a **single claim's** member set:

**`BoundaryPair`** (generalises D-300/D-328) — proves a *threshold* by holding context constant and
varying the value across the boundary. Inputs: `threshold_conjunct` (VR08 `Discount > 0.25`),
`held_context` (the RecordType binding + the VR's other gate conjuncts staged-true in *both* members
— D-328 already stages gates; this makes them first-class *context*), `attribution` (VR08 +
error-message pattern, reject arm only). Outputs: firing member (26%, `expect_reject=True`,
attributed) + just-inside member (25%, `expect_reject=False`). **It is `derive_boundary_set`
extended** — but blocked today by `_is_threshold`'s integer-only guard (prerequisite P1).

**`ContextDifferential`** (net-new) — proves a *context gate* by holding the whole business scenario
constant and varying exactly one context classification. Inputs: `held_scenario` (the firing payload
minus the one context binding), `dimension` (`RECORD`), `treatment`/`control` bindings. Outputs:
treatment member (`held_scenario ⊕ RecordType=Enterprise`, reject, attributed — **byte-identical to
BoundaryPair's firing member**) + control member (`held_scenario ⊕ RecordType=Standard`, accept).
Reuses D-348 `_satisfy_recordtype(want_true=False)` for the Standard arm directly.

**VR08 composition → one claim, 4 nominal members → 3 physical tests / 1 shared fixture:**

| primitive | member | payload | verdict | attribution |
|---|---|---|---|---|
| BoundaryPair | just-inside | RT=Enterprise, Discount=25% | accept | — |
| BoundaryPair | **firing** | **RT=Enterprise, Discount=26%** | **reject** | **VR08** |
| ContextDifferential | **treatment** | **RT=Enterprise, Discount=26%** | **reject** | **VR08** |
| ContextDifferential | control | RT=Standard, Discount=26% | accept | — |

The two bold rows are the same physical recipe; member-dedup collapses them → 3 tests. BoundaryPair's
firing↔just-inside pair isolates the **threshold**; treatment↔control isolates the **context**.

**Single-dimension guarantee (the orthogonality trap).** Both record types expose
`Deal_Type__c=Enterprise`, so RecordType and Deal_Type are independent axes. `ContextDifferential`
mints both arms from **one frozen `held_scenario` base** and mutates **exactly the `RecordTypeId`
key** — `Deal_Type__c` (and every other field) is copied byte-identically into both arms, so it
cannot co-vary. The reject↔accept delta is attributable to the record-context flip alone.

**Dedup — within-bundle, member-grained, no new physical layer.** Because both primitives feed **one
claim bundle**, the composition author concatenates their members and collapses by a canonical
recipe-payload key (`payload ∪ context_bindings ∪ {expect_reject, attribution_ref}`, keep-first)
before `write_recipe`. This *generalises* the existing `author_boundary_recipes` skip
(`emission.py:1049` already elides the firing member because "it IS the primary reject recipe"). To
guarantee the shared key is provably identical, `ContextDifferential` derives its treatment arm **by
reference** from BoundaryPair's firing member rather than reconstructing it (else the confound in A0
#4 — BoundaryPair's firing payload has Deal_Type absent, ContextDifferential's held_scenario includes
it — yields different keys and `{E,26%}` is authored twice). **Hard invariant: both primitives author
into ONE claim bundle.** Two bundles → same-hash drops the control arm wholesale, or different-hash
double-executes. Cross-claim recipe sharing needs a content-addressed layer that does not exist and is
**out of scope** (P6); single-claim composition suffices for VR08.

**Attribution (D-345) — reject arm only.** The shared `{E,26%}` reject carries the ATTRIBUTED tier via
`error_message_pattern` = VR08's message; the two accept arms are OUTCOME tier (the save succeeds,
nothing to attribute). The proof of the context gate *is* this asymmetry: the identical attributed
reject becomes a clean accept when only RecordType flips to Standard.

**Generalisation — design-direction only; only RECORD ships today.** The primitive is dimension-
agnostic in principle:

```
ContextDifferential(held_scenario, dimension, treatment, control, attribution)
    -> [ Member(held_scenario ⊕ treatment, reject, attributed),
         Member(held_scenario ⊕ control,   accept) ]
ContextBinding = (dimension, resolver, resolved_value, lands_on)   # ⊕ = exactly one mutation on the frozen base
```

But only **RECORD** is reachable now (its resolver is D-348 `_satisfy_recordtype`; its binding lands
on the record payload). The other dimensions each need three things that do not exist:

| Dimension | Differential | Missing today |
|---|---|---|
| USER | permission (run-as with/without permset), profile (FLS visible/hidden) | run-as binder + a `lands_on=run-as` member slot + S4 run-as/FLS execution |
| EXECUTION | currency, channel (UI vs API) | currency/channel binder + `lands_on` slot + S4 multi-channel execution |
| TRANSITION | prior-state / `ISCHANGED` differential | `PRIORVALUE`/`ISCHANGED` staging, which `_pre_scan` explicitly **refuses** as org-state |

`BoundaryMember.payload` is a record-field dict only — it has no `lands_on` tag — so anything past the
record dimension needs that new member slot plus a per-dimension resolver plus S4 execution support.
Present the dimension-agnostic signature as the *target shape*, not as buildable today.

## A5 — Prerequisites ledger (the honest "not buildable today")

| # | Prerequisite | Why | Scope | Needed for |
|---|---|---|---|---|
| P1 | `_is_threshold` accepts decimal/float literals | VR08's `> 0.25` is rejected → `derive_boundary_set` returns `()` today (the D-300 float-scale deferral) | S3, small | BoundaryPair over VR08 |
| P2 | `_GroundedContext` + `_constraint_states` enumeration + `_lookup` special-case + boundary-value pin | §3 two-blocker close + the §4↔§3 hand-off | S3 | VR08 entailment selection |
| P3 | substrate two-hypothesis expansion + `CLASSIFICATION_MECHANISM_AMBIGUOUS` RefusalKind at `resolve_intent` | honest field-vs-context disambiguation without LLM bias | S3 | §A2 |
| P4 | S1 RecordType **label** capture | AK's label benchmark; else devname-only fallback (ii) | **S1/sync — out of scope**; devname fallback ships without it | §2 label constraint |
| P5 | `lands_on`-tagged member slot + per-dimension resolvers + S4 execution support | ContextDifferential beyond RECORD (permission/profile/currency/channel) | S3 + S4, large | the generalisation |
| P6 | content-addressed recipe/fixture layer | cross-claim fixture sharing | **new layer — out of scope**; NOT needed for VR08 | future reuse only |
| P7 | carry competing interpretations forward | the both-effect-match ambiguity case | S3, net-new | retain-competing (PICK-ONE-or-REFUSE is the floor) |

## A6 — Revised acceptance + suggested build order (when GO'd)

**End-to-end for VR08 (honest path):** substrate forms `CS_field` + `CS_context` → necessarily-firing
entailment (fed the boundary value) uniquely elects `CS_context`→VR08, VR10 only possibly-fires → §3
(both blockers closed) selects VR08 → §4 emits BoundaryPair + ContextDifferential into one claim → 3
tests / 1 shared fixture → live env-59: `E@25` accept, `E@26` reject-attributed (VR08 message),
`S@26` accept. Scorecard: correctly-exercised controls **4/10 → 5/10**, VR08 attributed *and*
context-isolated.

**Suggested build order — each an independently checkpointed slice (never optimize AC count):**
1. **P1** — `_is_threshold` float support + `BoundaryPair` held_context (recovers the 2-member VR08
   boundary set on its own; checkpoint on a regen).
2. **P2** — `_GroundedContext` + entailment two-blocker close + the boundary-value hand-off (VR08
   becomes *selectable* once a context is present).
3. **P3** — substrate two-hypothesis disambiguation + RefusalKind (the honest grounding of "Enterprise
   deals"; this is where the ambiguity is genuinely tested).
4. **P4(ii)** — devname-only certainty-bounded classification resolver.
5. **§4 ContextDifferential (RECORD)** + within-bundle member-dedup (the third arm; 3 tests / 1 fixture).

**Deferred to explicit later GO:** P4(i) S1 label capture, P5 non-record dimensions, P6 cross-claim
sharing, P7 retain-competing, VR10.

## HOLD

This amendment is the design of record and is **HELD for AK review before any implementation.**
Two items need an explicit AK call before a build starts: the **§2 label scope reduction** (P4 —
devname-uniqueness now, or block on S1 label capture) and **which claim shape** the substrate emits
for RecordType-gated rules (context-replaces-field vs both-fields — it determines whether entailment
or pass-1 cardinality carries selection, §A3).

> **Both items resolved by AK in Amendment B below** — (1) ship DeveloperName grounding, no S1 label;
> (2) the "which claim shape" question was a **false binary** — do not pick a shape to make VR08
> selectable; RecordType is a distinct context hypothesis nominated by *control relevance*, not by
> entailment or field-overlap. Read Amendment B; it supersedes A2/A3/A6's entailment-as-selector framing.

---

# Amendment B — AK decisions (2026-07-09): control relevance ≠ formula entailment (HELD)

Status: **DESIGN OF RECORD — HOLD for implementation.** Supersedes Amendment A's central claim
(§A2/§A3/§A6: "necessarily-firing entailment, fed the boundary value, is the disambiguator/selector").
Records AK's two decisions and the architectural discovery that reframes them. **Nothing implemented.**

## B0 — The discovery: control relevance (nomination) is not formula entailment (proof)

Amendment A tried to make one operation — necessarily-firing entailment fed the boundary value — do
two jobs: *nominate* the control (which VR is this requirement about?) and *prove* it fires. AK's
correction: these are different operations, and conflating them is what made the design feel forced
into a claim-shape binary.

- **Control relevance (nomination).** *Is VR08 a plausible implementation of this requirement?*
  Established by **semantic subject/context alignment**, not by proving the requirement entails the
  VR's full formula. The req-315 requirement — *"Enterprise deals are subject to stricter discount
  controls"* — **never states 25%**, so it cannot entail `Discount > 0.25`. Demanding entailment for
  nomination is category error: the org metadata, not the requirement, supplies the threshold.
- **Test derivation (boundary).** *Given VR08 as the nominated control, what value do we test?*
  Read **25%** off VR08's formula (formula analysis), independent of the requirement's silence on it.
- **Entailment (proof) stays in its lane.** Use `entails_firing` to *prove* firing / disambiguate
  **only when the requirement's own predicates are specific enough** (e.g. a requirement that literally
  says "discount over 25%"). For under-specified requirements it is simply not the operation.

This dissolves the A3 false binary. We do **not** choose `RecordType=Enterprise` over `Deal_Type=Enterprise`
to win field-overlap, and we do **not** force both into one claim. RecordType is a **distinct context
hypothesis** whose relevant control is found by alignment — VR08 is nominated because it *gates on the
Enterprise record type and governs Discount*, full stop.

## B1 — Decision 1: ship DeveloperName grounding, no S1 label (AK: "do not block on S1 label capture")

The grounding contract:

```
requirement term  →  candidate RecordType contexts  →  exact normalized DeveloperName matching
                     → one match → ground · zero → unresolved · many → ambiguous/refuse
```

- **Deterministic only.** Normalization such as `Enterprise → PLS_BM_Enterprise` is permitted **only if
  a namespace/prefix convention is provable from metadata** (e.g. every RecordType DeveloperName on the
  object shares the `PLS_BM_` prefix, so the prefix is a provable object-local convention — not a guess).
  **No fuzzy semantic matching.** Refuse on 0 or ≥2 (the `_bind_picklist_values` discipline).
- **Name the capability accurately:** *RecordType context grounding from stable metadata identity* —
  **not** "natural-language RecordType label grounding." S1 has no queryable label (Amendment A, HOLD-2);
  label capture waits for broader evidence that human-label semantics are needed across RecordTypes /
  picklists / translated labels / UI metadata.
- **Honesty in the IR (AK's added constraint):** the struct must not pretend `"Enterprise"` was resolved
  from a human label. Carry the grounding method:

```
RecordTypeContext(              # the A1 _GroundedContext, field names reconciled to AK's
    requirement_term = "Enterprise",
    developer_name   = "PLS_BM_Enterprise",
    grounding_method = "developer_name_normalization",   # provenance of HOW it resolved
    # record_type_id resolved late at the transport boundary (A1 conservation rule)
)
```

## B2 — Decision 2: RecordType is a distinct context hypothesis; relevance nominates (AK: "don't make overlap accidentally carry the architecture")

**Two independent hypotheses, neither collapsed, forced-conjoined, nor mechanically substituted:**

```
Hypothesis A : Deal_Type__c = Enterprise ∧ Discount > threshold     → aligns toward Deal_Type-gated rules
Hypothesis B : RecordType   = Enterprise ∧ Discount > threshold     → aligns toward VR08
```

They are formed and evaluated **independently**. If both remain plausible from the requirement alone,
Plimsol must **not silently collapse** them (retain-or-refuse; generic retention across the whole
pipeline is *not* in this slice — Amendment A P7).

**Control-relevance nomination (the concrete mechanism).** For a context hypothesis with context
predicate *C* (`RecordType.DeveloperName = <devname>`) and the requirement's subject field(s) *F*
(Discount), a VR is a **relevant control** iff its formula:
1. **gates on the context** — contains the conjunct *C* (the VR is scoped to that record classification), **and**
2. **governs the subject field** — constrains *F*, **and**
3. **effect-subject alignment** — *F* is a *primary* constraint (a top-level threshold on *F*), so the
   VR's controlled effect matches the requirement's subject, rather than *F* appearing incidentally
   (e.g. one disjunct of a different rule's trigger).

Verified against all ten req-315 VRs, for Hypothesis B (`RecordType=Enterprise`, subject = Discount):

| VR | gates on RecordType=Enterprise? | governs Discount? | primary discount effect? | relevant? |
|---|---|---|---|---|
| VR08 | **yes** (`RecordType.DeveloperName="PLS_BM_Enterprise"`) | yes (`> 0.25`) | yes (top-level cap) | **✔ nominated (unique)** |
| VR02 | no (general rule) | yes | — | ✘ (not context-gated) |
| VR03 | no | yes (in a nested OR) | no | ✘ |
| VR10 | no (gates on `Deal_Type__c`, Hyp A) | yes (one disjunct) | no (approval rule) | ✘ |
| VR01/04/05/06/07/09 | no | no | — | ✘ |

**Exactly one VR (VR08) is nominated** — with no threshold pinning and no entailment. VR10 is correctly
excluded from Hypothesis B (it is not RecordType-gated) and its effect-subject is approval-on-transition,
not a discount cap; it belongs to Hypothesis A. The `"Enterprise"` label is never the tiebreak — the
alignment is *context-gate ∧ governs-the-subject-field*.

**Threshold from formula analysis, not the requirement.** Once VR08 is nominated, `25%` is read off its
formula (the existing `derive_boundary_set` / boundary machinery, once it accepts the decimal literal —
Amendment A P1). The requirement's silence on the number is expected and fine.

**Entailment retained (proof role).** `entails_firing` / the A3 leaf resolver stay for the case where a
requirement's *own* predicates are specific enough to prove which VR fires (e.g. "discount over 25% on
Enterprise deals"). It is no longer on the critical path for *nominating* VR08 from the under-specified
req-315 text — which also means the A3 "Blocker 2 / boundary-value hand-off into entailment" is **not
required for the narrow slice** (it returns only if/when entailment is used for proof).

## B3 — The clean path for VR08 (superseding A6's end-to-end)

```
requirement "Enterprise deals have stricter discount controls"
  → context hypothesis  RecordType = Enterprise           (B1 deterministic DeveloperName grounding)
  → control relevance   VR08 governs Discount under the Enterprise record type   (B2 — unique)
  → formula analysis    threshold = 25%                    (read off VR08)
  → test design         BoundaryPair:        Enterprise 25% → accept · Enterprise >25% → reject+attribute
                        ContextDifferential: Enterprise >25% → reject · Standard same value → accept
```

Selection here is **relevance**, not `_best_aligned_vr` field-overlap and not entailment — so the
"which claim shape wins pass-1 cardinality" question (A3) never arises for the RecordType hypothesis.

## B4 — What ships now vs. not (AK's narrow slice)

**Ship (RECORD dimension only):**
1. **RecordType context representation** — `_GroundedContext(dimension=RECORD_TYPE, requirement_term,
   developer_name, grounding_method)`; `record_type_id` minted late at the transport boundary (A1).
2. **DeveloperName-based deterministic grounding** — exact normalized matching, metadata-provable
   normalization only, refuse on 0/≥2 (B1). No S1 label work.
3. **Control-relevance nomination** — the B2 mechanism (context-gate ∧ governs-subject-field ∧
   effect-subject alignment) for a RecordType context hypothesis.
4. **RecordType-aware entailment leaf** — the A3 Blocker-1 `_lookup` special-case (the context leaf can
   satisfy/contradict `RecordType.DeveloperName = "PLS_BM_Enterprise"`), for the *proof* role.
5. **RecordType transport conversion** — already delivered by D-348.
6. **Record context differential** — `ContextDifferential` (RECORD) **only for a claim already grounded
   to RecordType context**; + `BoundaryPair` decimal-threshold support (A1 P1) + within-bundle member-dedup.

**Do NOT ship (explicit, AK):** generic competing-hypothesis retention across the pipeline (A P7); fuzzy
business-field-vs-context arbitration; S1 label capture (A P4-i); user/profile/currency/channel context
dimensions (A P5); generic fixture-member architecture changes beyond the RECORD dimension (A P6); any
prompt manipulation that forces "Enterprise" to mean RecordType (A2 construction-bias rule).

## B5 — Corrected build order (each independently checkpointed; never optimize AC count)

1. **P1** — `_is_threshold` accepts decimal literals + `BoundaryPair` held_context (recovers VR08's
   2-member boundary set on the *Deal_Type* path even before context lands; checkpoint on a regen).
2. **RecordType context representation + DeveloperName grounding** (B1) — form Hypothesis B; struct +
   deterministic resolver + refusal. Checkpoint: req-315 grounds an `_GroundedContext(PLS_BM_Enterprise)`.
3. **Control-relevance nomination** (B2) — nominate VR08 for the RecordType hypothesis without entailment.
   Checkpoint: VR08 is selected for the RecordType-context claim; VR10 is not.
4. **RecordType-aware entailment leaf** (A3 Blocker-1) — the proof-role `_lookup` special-case + the four
   leaf cases.
5. **ContextDifferential (RECORD)** + within-bundle member-dedup — the third arm; 3 tests / 1 fixture.
6. **Live env-59** — `E@25` accept, `E@26` reject-attributed, `S@26` accept; scorecard controls **4/10 → 5/10**.

**One point to confirm before I build (the load-bearing discovery):** the **control-relevance rule** in
B2 — *context-gate ∧ governs-subject-field ∧ effect-subject alignment* — is the net-new selection
operation that replaces entailment-as-disambiguator. It is verified against the ten req-315 VRs above,
but it is *your* architectural discovery and I want your confirmation it matches your intent before I
build the core on it. The open sub-question it leaves: when Hypothesis A and Hypothesis B *both* nominate
an effect-aligned control for the same requirement (not the case for req-315), the slice's behaviour is
**refuse-and-surface** (my lean), since generic retention is out of scope — confirm or override.

## HOLD

Amendment B is the design of record and is **HELD for AK review before implementation.** On GO I build
the B5 order; the one confirm-point is the B2 control-relevance rule + the refuse-and-surface lean for
the both-nominate case.
