# Flow-Behaviour Grounding Arc (B1) — Plan

**Status: PLAN — awaiting GO. Nothing implemented.**
Driver: FB-V1 Wave-1 FL01 baseline (2026-07-11): generation over req-320
refused 16/16 ACs (`insufficient_grounding` / `ungrounded_after_reprompt`),
0 claims, zero fabrication. Predicted gap B1 confirmed. This plan turns that
baseline into the smallest correct arc that makes **FL01 correctly
exercised** (the Wave-1 gate), with FL03 as a stretch.

---

## 1. Root cause, verified in code (not the Wave-0 guess)

The Wave-0 prediction ("no flow-element grounding source") was right in
effect but wrong in shape. There IS a flow-behaviour grounding source — it
is just two shapes wide:

- `primeqa/semantic/entity_attributes.py::flow_effects` (D-318) parses the
  Flow entity's stored `attributes.Metadata` **on read** into:
  same-record `recordUpdates[].inputAssignments` (bare field + literal
  value) and cross-object `recordCreates[].object`. Nothing else.
- `primeqa/generation/governance_core.py::_flows_producing_effect` (D-318)
  binds an automation-effect claim to the Flow that *verifiably produces*
  the declared effect using exactly that parse; `_NEGATIVE_LAYER1_DIM` maps
  `automation-effect-claim → (EDGE_FLOW, "Flow")`; D-320 binds approvals by
  enumeration; D-301 (active-only) already enforced.

FL01–FL03, FL06, FL08 are **before-save flows**: their effect mechanism is
`assignments[]` writing `$Record.<field>`, guarded by `decisions[]`. None of
that is visible to `flow_effects`, so `_flows_producing_effect` returns
nothing, grounding fails, and S3 (correctly) refuses. The raw material is
already in S1 — the sync stores the **full flow graph** per version in
`entities.attributes.Metadata` (verified for all 16 fixture flows;
`flow_details.parsed_logic` has no writer anywhere and stays unused) — so
this arc needs **no fetch changes, no sync changes, no migration**.

What already exists downstream and must NOT be rebuilt:
`GroundedAutomationEffect` + `_author_automation_effect` (D-210.1) authors
claims whose recipes stage a create and assert **the org set field=value**
(expected ≠ staged — the FL01-sized sliver of B2 is built); D-299
`trigger_fields` arm entry gates; D-306 `update_trigger_fields`
(same-record law); `expected_absence` rides the claim kind (the suppression
arm exists); evidence tier for the kind is ATTRIBUTED; 78 such claims run
live today.

## 2. Arc statement

**Extend deterministic flow-behaviour extraction from the two-shape
`flow_effects` glance to a bounded Flow-behaviour IR, and let the existing
automation-effect pathway consume it — so that a before-save, decision-
guarded `$Record` transformation grounds, emits fire + suppression arms,
executes, and scores on the frozen benchmark.**

Non-goals (explicitly out; each is a later arc measured against the same
frozen benchmark): after-save side-effect evidence beyond what D-210 already
does (B2 proper: FL04/FL05/FL07 set predicates, cross-object counts),
order-of-execution composition (B3/FL02's VR interplay), Get-Records
data-dependence (FL06), formula-valued assignments (FL02 UPPER/TRIM, FL08
date arithmetic — IR marks them `unresolved`, refusal stays honest),
loops/subflows/async/faults, S8 drift over flow versions, persisting the IR
into `parsed_logic`.

## 3. Design decisions (forks + leans)

1. **Where the IR lives** — (A) on-read accessor beside `flow_effects` in
   `semantic/entity_attributes.py`, schema-versioned dataclasses, pure +
   import-free, S1-owned; or (B) persisted `flow_details.parsed_logic` at
   sync (migration + backfill + reparse discipline). **Lean A**: follows the
   D-318 idiom exactly, zero migration, bitemporality inherited from
   `attributes`, and nothing yet needs SQL-queryable IR. Revisit persistence
   when S8 drift or cross-flow queries demand it.
2. **Guard semantics** — decisions are **first-match ordered**; a rule's
   guard is its own conditions AND the negation-context of prior rules; the
   default outcome is the conjunction of all rules' negations. The IR
   records ordered guards; consumers get sound narrowing via the existing
   Kleene/contradiction machinery (D-360) rather than a new evaluator.
3. **Binding stays effect-first** — `_flows_producing_effect` grows to
   consume IR effects (before-save SetRecordField joins the same-record
   set); the LLM still never names flows (`<UNKNOWN>` sentinel path
   unchanged).
4. **FL01's fire arm uses the existing padding-only-create shape** with the
   guard field **staged-absent** (the D-338 "staged absence beats fillers"
   refinement is the enforcement mechanism); the suppression arm uses
   `expected_absence`-style non-effect assertion with the guard unsatisfied
   (Priority staged `High`, read back `High`).
5. **Prompt change is one narrow paragraph** (division-of-responsibility
   instance #6, v27→v28): "a requirement stating a record *shows* a value
   after save that the user did not enter names an automation effect — name
   the behaviour (field, when, shown value if stated); the substrate binds
   the automation and supplies mechanics." No flow vocabulary enters the
   prompt.

## 4. Slices

**Slice 0 — parity snapshot (no product change).**
Freeze the 16 fixture flows' stored `Metadata` JSON (from tenant_1 entities)
into test fixtures. Unit corpus for the parser; also documents today's
`flow_effects` blind spot as failing-then-passing tests.

**Slice 1 — the bounded IR (S1 accessor).**
`flow_behaviour(attributes) -> FlowBehaviour` beside `flow_effects`:
- trigger context: object, save phase (`RecordBeforeSave`/`RecordAfterSave`),
  record-trigger type, entry filters (typed), `filterLogic`,
  `doesRequireRecordChangedToMeetCriteria`, scheduled/async path markers,
  `triggerOrder`;
- bounded graph walk from `start.connector`: decisions (ordered rules →
  guard contexts), `$Record` assignments (`SetRecordField(field,
  Literal|Unresolved)`), pass-through of the two existing D-318 shapes;
- anything else (loops, Get Records, subflows, actions, faults) → typed
  `opaque` markers; cycles/overflow → `parse_status=partial|opaque` with
  reasons. Never raises (D-203.1 idiom).
Gate: parser unit suite green over all 16 real flow graphs; FL01 IR =
one guarded effect `[Priority IsNull] → SetRecordField(Priority, "Standard")`
on Create/before-save; FL03 IR = 4 ordered band guards.

**Slice 2 — resolver + grounding consume the IR.**
`_flows_producing_effect` (and its callers' hints) matches same-record
effects from IR (guarded, before-save included), typed-tolerant values as
today; grounding surfaces the guard + trigger context to the emission stash.
Gate: offline generation suite — a synthetic FL01-shaped proposal grounds
and binds `PLS_FB_FL01_Default_Priority`; unparseable-flow proposals still
refuse (no regression on the 78 live claims' paths — full gen suite green).

**Slice 3 — guarded-effect emission: fire + suppression arms.**
Extend `GroundedAutomationEffect` authoring so a guarded same-record effect
emits (a) fire arm: guard-satisfying create (staged absence for IsNull
guards, protected against padding), read-back asserting the flow-set value;
(b) suppression arm: guard-unsatisfied create asserting the staged value
persisted (attribution-by-differential, per the FB-V1 gold standard).
Gate: emission unit tests; claim/recipe snapshot review (HOLD-and-show).

**Slice 4 — prompt v28 (one paragraph, above).**
Gate: eval harness (`tests/test_eval_harness.py`) — no regression on the
existing prompt corpus; new FL01-shaped case proposes the effect claim.

**Slice 5 — the exit gate (live, on the frozen benchmark).**
Deprecate-then-regenerate req-320 (D-353 mechanism), read
`claims_written`, approve, run on env-59, score per
`benchmark/flows/v1/GOLD_STANDARD.md`:
- **FL01 correctly exercised** — fire arm TRANSFORM (posted blank, read
  `Standard`) + suppression arm (posted `High`, read `High`), attributed to
  the flow by the differential; zero false tests; every other AC still an
  honest refusal (no new fabrication anywhere).
- Scoreboard moves 0/15 → **1/15**; record apparent vs trustworthy vs
  correctly-exercised.
**Stretch (same slices, no new machinery if Slice 1–3 are right):** FL03
bands through the DecisionBranchCoverage generalisation — if the ordered-
guard IR feeds D-360's shape recognizer cleanly, score it; if not, record
why and stop (that finding shapes the next arc).

## 5. Risks

- **Guard→fixture interaction**: padding/fixture completion must treat guard
  fields as protected dimensions (staged-absence vs the D-354 filler rules);
  wrong interaction = false suppression arms. Mitigation: Slice-3 unit tests
  pin the staged-absence payloads byte-exactly.
- **Prompt drift**: v28's paragraph could perturb VRB-V1 behaviour. VRB-V1
  is frozen and rerunnable — rerun its generation pass offline (eval
  harness) before the live gate.
- **Dedup masking**: req-320 refusals wrote no claims, so no deprecation
  needed on first regen; but repeat regens during the arc must follow
  claims_written-id reads (never time-window queries).
- **Concurrent main movement**: the D-361 control-telemetry read-model is
  live on outcomes; it must remain read-only of this arc (promotion boundary
  respected — nothing here reads control telemetry).

## 6. Estimate

Slices 0–2 one focused session; 3–4 one session (emission + prompt + eval);
Slice 5 half a session live. Branch per convention:
substrate work → `phase-N-substrate-M` feature branch off main
(S1 accessor + S3 generation touch; suggest `phase-7-substrate-3-flow-grounding`),
HOLD-and-show before commits, merge on the live gate.
