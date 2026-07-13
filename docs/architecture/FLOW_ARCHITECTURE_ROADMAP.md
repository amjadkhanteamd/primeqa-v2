# Flow Architecture Roadmap — FL03–FL15 as capability families

**Status:** Phases 1–4 of the 2026-07-13 autonomous mission (design before
implementation). Companion to `REASONING_ARCHITECTURE.md` (the concept layer)
and `DEBT_REGISTER.md`. Objective: the SMALLEST architecture that naturally
supports every remaining flow — capabilities are built once and shared;
benchmark flows are exercised as side effects, never implemented directly.

Sources: the 16-flow fixture corpus (each flow's stored graph + the IR's named
demotion reasons), the live refusal texts on req-320, and the shipped rails
inventory (D-210/227/299/304/306/307/318/320, D-342…D-365).

---

## Phase 1 — Dependency analysis (per flow)

Legend for "needs": SEM = semantic/IR capability, WIT = witness, EVI =
evidence/recipe, ATT = attribution, EXE = execution (S4), BEN = benchmark-side.
"—" = already shipped. Reuse column names the shipped rail.

| Flow | Mechanism (from its stored graph) | SEM | WIT | EVI | ATT | EXE | Reuses |
|---|---|---|---|---|---|---|---|
| FL03 tier bands | before-save, 1 decision (3 ordered rules + default), 4 literal assignments | multi-rule ordered guards (negation-context) | band-interval values | N-arm differential | per-arm producer (exists per-behaviour) | — | IR walk, literal effects, D-360 arm discipline |
| FL04 confirm→task | after-save Update (filter Status=Confirmed, requiresChanged), recordCreate + due-date formula | after-save admission; EqualTo filter consumption | — (trigger literal from filter) | — (D-210 cross-object exists) | — (D-318 glance already sees the create) | — | cross-object shape, D-299 trigger staging |
| FL05 cancel→tasks | after-save Update, Get Records (open tasks) + set recordUpdate | after-save; Get-Records premise representation | premise records (sibling tasks) | **set/count assertions (B2 core)** | set-producer | multi-record staging (D-205 chains exist) | parent-stamp N-create rails |
| FL06 duplicate flag | before-save Create, Get Records (same ref, open) + guarded literal assignment | Get-Records premise representation | **premise sibling record** + ref-equality pair | same-record literal (exists) | — | sibling staging | FL01-class emission |
| FL07 line rollup | triggers on Order_Line; loop + parent recordUpdate (totals/count) | child-trigger subject mapping | line-set values | **aggregate assertions (B2)** | **child→parent attribution** | multi-line staging | parent-stamp correlate |
| FL08 SLA stamp | before-save Update (Status=Submitted, requiresChanged), formula TODAY()+5 | date-arithmetic transform grammar; update-phase transform | — (canonical = RelativeDate) | RelativeDate expected (temporal protocol exists) | transform producer (exists) | — | temporal.py, D-306 update phase |
| FL09 reopen guard | after-save Update, $Record__Prior guard, literal write + audit create | after-save; **prior-state guards** (TransitionState vocabulary) | from/to state pair (D-222 exists) | multi-effect bundle | — | — | transition rails |
| FL10 stale escalation | scheduled path +2d | — | — | — | — | **scheduled observation — GATED** | (manual sentinel drill) |
| FL11 async enrichment | AsyncAfterCommit path, audit create | async-path admission | — | **bounded-eventual read** (retry-until-timeout) | — | polling read step | cross-object shape |
| FL12 orchestrator | after-save, lookups + subflow (SF01) + literal write | **subflow inlining** (IR composition) | as per composed body | set assertions (from FL05) | composed-path producer | — | FL05 capabilities |
| FL13 ledger + fault | after-save, recordCreates ×2 + fault connector | **fault-path representation** (guarded alternative) | **failure-premise witness** (violate target's required fields — from field metadata) | dual-arm (success create / fault create) | per-arm producer | — | cross-object shape, D-294-class derivation |
| FL14 approval submit | after-save (filters ≥100k ×2), actionCall submit | actionCall recognition (submit class); numeric filter consumption | boundary values (D-346 exists) | ProcessInstance existence (approval rails exist) | D-320 enumeration (exists) | — | approval arc (D-308/320) |
| FL15 email | emailSimple action | — | — | **none possible — evidence limit** | — | — | honest-refusal exemplar (by benchmark design) |

## Phase 2 — Architectural families

Clustering by *capability*, not by flow:

- **A. Ordered Decisions** — FL03. Pure composition (IR guard chains ×
  interval witnesses × N-arm differentials).
- **B. After-save Effects** — FL04, FL05, FL09, FL11, FL12, FL13, FL14 all
  *require* after-save admission before anything else about them matters.
  Key insight: S4 already reads post-commit, so after-save changes **nothing
  in evidence** — it is an IR-grounding admission plus witness-side care
  (after-save writes are visible to read-backs exactly like before-save ones;
  they differ for VR interplay, which only the transform witness consumes).
- **C. Prior-State Guards** — FL09 (+ any `$Record__Prior` flow). The
  TransitionState two-phase world (D-356 era) re-used as flow-guard
  vocabulary; staging via the existing create-then-update recipes.
- **D. Cross-Record State** — FL05 (set updates), FL06 (premise sibling),
  FL07 (aggregation + child-trigger). The genuinely NEW evidence class (B2
  core): multi-record premises + set/count/aggregate assertions.
- **E. Temporal Transforms** — FL08. Date arithmetic in the transform grammar
  with `RelativeDate` as the canonical (the temporal protocol is the witness).
- **F. Composition** — FL12 (subflow inlining), FL13 (fault paths +
  failure-premise witnesses).
- **G. Deferred Observation** — FL11 (async: bounded-eventual read —
  feasible), FL10 (scheduled +2 days: **gated**, see below).
- **H. Approval Interaction** — FL14 (actionCall recognition; the approval
  evidence rails already exist).
- **I. Evidence Limits** — FL15: the control that *proves* the system knows
  what it cannot observe. No build; its permanent honest refusal is the
  passing result.

## Phase 3 — Family designs

### A. Ordered Decisions (FL03)
- **Problem/limitation:** `multi_outcome_decision` demotes; DecisionBranch-
  Coverage handles only declarative Boolean rules.
- **Minimal capability:** IR walks N ordered rules + default; rule *k*'s
  effective guard = own conditions ∧ ¬(rules 1…k−1); each rule's target path
  walked independently to its literal effect; default is the all-negated arm.
- **IR change:** decision branch in the walk fans out to per-rule behaviours
  with `guard_chain` (positive conds + `negated` prior-rule conds); bounded
  by rules ≤ 6, conditions per rule ≤ 4, single-level (no nested decisions).
- **Witness change:** interval witnesses — for numeric first-match ladders,
  a value strictly interior to each band derived from adjacent thresholds
  (top band: threshold + unit; interior: midpoint-by-scale; default band:
  below the lowest). Composes D-346 boundary discipline. Extract the witness
  entry-point module while adding this (closes DEBT E2).
- **Evidence change:** one fire arm per band (create staging Amount in-band,
  assert Tier literal), attribution per arm via the band's own producer
  behaviour. Boundary edges optional second wave.
- **Regression risk:** FL01/FL02 walk unchanged (single-rule path preserved
  verbatim); corpus pins all other flows' reasons.
- **Generalisation:** any first-match ladder (pricing tiers, risk bands, SLA
  classes) on numeric or equality guards.
- **Honest limits:** non-numeric ladders get equality witnesses only; mixed
  operators inside one rule refuse; overlapping-band detection = refuse on
  ambiguity (first-match makes earlier rule win — model it, don't guess).
- **Exit gate:** corpus (FL03 grounded, N behaviours, others unchanged) +
  live req-320: tier AC exercised with per-band identity-stable claims.

### B. After-save Effects
- **Problem:** `after_save_not_in_grammar` demotes 9 flows before anything
  else is even inspected.
- **Minimal capability:** lift the flow-level demotion; `save_phase` becomes
  a grounding *property* consumed by witnesses (only before-save transforms
  affect VR evaluation — already the case) and by future B3 composition.
  Effects keep per-element grammar (creates/updates enter via family D).
- **IR change:** remove the `after_save` demotion; record phase on each
  behaviour; before-save-only remains a *constraint of the transform witness*,
  not of grounding.
- **Witness/evidence:** none — post-commit reads already observe after-save
  writes.
- **Regression risk:** flows that today demote ONLY for after-save would
  begin grounding — corpus says none do (each has additional element-grammar
  reasons), so behaviour is corpus-stable; tests must pin that.
- **Honest limits:** rollbacks/faults still demote until family F.
- **Exit gate:** corpus byte-stable except the named reason disappearing from
  reason *lists* (state changes only where another reason never existed —
  none today).

### C. Prior-State Guards (FL09)
- **Minimal capability:** `_fb_guard_condition` admits
  `$Record__Prior.<Field>` refs, marking the guard `phase: prior`; grounding
  requires the update-shape recipe (create initial → update → observe), which
  D-306/D-222 already author. Witness: the from/to pair must make the prior
  guard true (e.g. prior Status=Fulfilled, next Status=Cancelled).
- **Honest limits:** prior guards on the *same* field as the effect refuse in
  v1 (write-read circularity).
- **Exit gate:** FL09 grounds its Reopened literal write (audit-log create
  waits for family D/F); live update-shape claim.

### D. Cross-Record State (B2 core; FL05/06/07)
- **Minimal capability:** three additions — (1) IR: Get-Records nodes become
  typed `premise` markers (object, filter conditions) instead of opaque; (2)
  EVI: multi-record premise staging (N-create the sibling/line records — the
  D-205 chain rails) + NEW assertion kinds `count_equals` / `each_matches`
  over a correlated query; (3) ATT: child-trigger flows attribute to the
  parent-claim subject via the trigger-object mapping (FL07).
- **Honest limits:** loops ground only as set-semantics (no per-iteration
  state); premise filters limited to equality/IsNull shapes.
- **Exit gate:** FL06 first (smallest: one sibling + literal flag), then
  FL05 (set update), then FL07 (aggregate).
- **This family is the largest single build; it should be its own session.**

### E. Temporal Transforms (FL08)
- **Minimal capability:** transform grammar admits `TODAY() + <int>` (and
  the field-source variant) producing canonical `RelativeDate(RUN_DATE, n)`;
  emission emits the symbolic value (temporal protocol); update-phase
  transform authoring (the create-then-update shape with the transform staged
  on the update). Requires C1's EqualTo filter consumption (Status=Submitted).
- **Honest limits:** arithmetic beyond ±int days refuses; cross-field date
  math refuses.
- **Exit gate:** FL08 grounded; claim carries RelativeDate; live run PASSES
  via S4 materialisation.

### F. Composition (FL12/13)
- **Minimal capability:** subflow inlining = parse the referenced flow's own
  stored graph and splice (depth 1, cycle-guarded); fault connectors = a
  guarded alternative path whose premise witness VIOLATES the fault-causing
  constraint (derive from the target object's required-field metadata — the
  D-294 class inverted).
- **Honest limits:** depth-1 subflows; fault causes limited to derivable
  required-field violations.
- **Exit gate:** FL13's dual arms (ledger created / fault logged +
  confirmation stands); FL12 after family D.

### G. Deferred Observation (FL10/11)
- **FL11 minimal capability:** bounded-eventual read — a read step with
  retry-until (n attempts, m seconds) for AsyncAfterCommit effects. S4 step
  grammar extension; recipe honesty (the claim names the bounded window).
- **FL10: GATED.** Scheduled +2-day paths need an execution model that spans
  days (scheduler-owned re-observation, run continuation, or org time
  manipulation). That is a **product/execution-model decision** (alternatives:
  (a) S4 deferred-observation jobs — new queue semantics; (b) benchmark-org
  clock manipulation — rejected, modifies benchmark trust; (c) permanent
  documented refusal like FL15 — cheapest, honest). Recommendation: (a) as a
  designed-not-built note; decide with AK. Estimated: M–L (scheduler + S6
  correlation).
- **Exit gate (FL11):** live async claim green within the bounded window.

### H. Approval Interaction (FL14)
- **Minimal capability:** IR recognizes `actionCalls` of the submit-for-
  approval class as a typed behaviour; binding via the existing D-320
  enumeration; evidence via the existing approval rails. Numeric entry
  filters (≥100k ×2) consume via C1 + D-346 boundary witnesses.
- **Exit gate:** FL14 fire arm (large order → pending approval) + boundary
  control (99,999.99 → no approval).

### I. Evidence Limits (FL15)
No build. The permanent, named refusal ("no data-observable effect: email
delivery is outside the evidence model") IS the correct terminal state; add
one IR reason refinement only if the generic action reason proves confusing.

## Phase 4 — Optimised implementation order

Duplication-minimising sequence (each item unlocks everything below it that
shares its machinery; nothing is built twice):

```
C1  IR guard-chain generalisation        → A, C, E, H          ✅ DONE  e4008d8
C2  after-save admission                 → B family precondition ✅ DONE af00e30
C3  interval witnesses + N-arm emission  → FL03 EXERCISED (live) ✅ DONE 27a998d
    C3b value-less N-arm enumeration     → FL03 tier ACs live    ✅ DONE bcd80c2 (+fix 0b5de72)
C4  temporal transform + update-phase    → FL08 EXERCISED (live) ✅ DONE f17ce54
C5  prior-state guards                   → FL09 EXERCISED (live) ✅ DONE 490af98
C6  actionCall/approval recognition (IR) → FL14 IR (REQ-B-gated) ✅ DONE b2933e8
    B0.2 field-miss recovery offers      → convergence fix       ✅ DONE 4fbf81d
────────────────────────────────────────────────────────────
C7  cross-record premises + set/count    → FL06, FL05, FL07   (own session — new EVIDENCE class)
C8  composition (subflow, faults)        → FL12, FL13         (own session)
C9  bounded-eventual read                → FL11               (own session — S4 touch)
GATED: FL10 (execution-model decision) · BY DESIGN: FL15 (no build)
```

Rationale: C1+C2 are pure-IR, offline-verifiable, and are prerequisites for
five families; C3 completes the first family end-to-end (proving the pattern
again live); C4–C6 are small, independent, and each exercises one flow with
existing evidence rails. C7–C9 introduce new *evidence/execution* classes and
deserve fresh sessions with their own gates.

**Wave 2 shipped (2026-07-13, after C1–C6):** CP2 branch identity
(`<decision>:<rule>` provenance, non-identity-bearing); CP3 boundary
witnesses (`witnesses.boundary_witnesses` + edge-staged fire claims behind
`enable_bva_boundaries`; suppression = the mutual-exclusivity law); CP4
formula guards (bare `$Record` passthrough grounds; every other formula
refuses as `formula_guard_not_deterministic:<FN>`); CP6 cross-record
premises (`flow_cross_record_premises` — typed Get-Records representation
with `$Record`-correlation markers; NOT producers; C7 plugs in here).
FL05/FL07/FL12 premises captured; FL06's NotEqualTo filter is the honest
out-of-bounds pin.

**C1–C6 shipped (the prior session).** All "existing-evidence-rails" families
(A/B/C/E/H-IR) are grounded and, where a loaded requirement exercises them
(FL03/FL08/FL09 via req-320), live-proven. FL14's IR grounds but its
emission + live gate wait on REQ-B (the large-order-approval requirement),
which is not loaded — loading a benchmark requirement is out of scope under
the frozen-benchmark constraint. C7/C8/C9 remain: each introduces a
genuinely new evidence or execution class (correlated multi-record
assertions; subflow inlining + fault paths; bounded-eventual reads) — the
mission's own "own session" designation and stop-condition #3 ("a
fundamentally new reasoning problem"). FL10 is the human-judgement gate
(stop-condition #2).

### Architectural discoveries (C1–C6)

1. **Per-path conservatism has two axes, not one.** C1 split the demotion
   rule by *sibling exclusivity* (decision arms are mutually exclusive at run
   time, so one arm's out-of-grammar element does not demote another). C5
   then split it again by *subject-safety* (an element that provably cannot
   rewrite the triggering record's own fields — recordCreates, recordLookups,
   variable-only assignments — demotes only itself). C6 exposed that the
   subject-safe refinement was inconsistently applied: soft reasons leaked
   into the caller's global demote on *unconditional* paths (decision arms
   were already correct). The unified rule: **hard reasons demote the path;
   soft reasons demote only their own behaviour, on every path shape.**

2. **"The org defines the value" is a recurring class, not a one-off.** FL02
   (canonical form), FL08 (relative-date offset), and FL03 (classification
   arm) all share the shape: the requirement names the *field and mechanism*
   but not the *value*, so a value-ful intent is uninventable and the
   substrate must derive it. C3b generalised FL02's value-less grounding into
   N-arm enumeration; the field-miss refusal was reworded to stop demanding a
   value the model would have to fabricate.

3. **Transitions are one shape with two sources.** C4 (temporal) and C5
   (prior-state) both author a create→update transition where the substrate
   derives both states. They differ only in what defines the "before" state
   (C4: a picklist alternative to the entry filter; C5: the `$Record__Prior`
   guard) — and share the picklist-alternative witness (`witnesses.
   picklist_alternative`) and the create/update merge rail.

4. **A submit-for-approval is a new effect class, cleanly bounded.** C6 showed
   the actionCall grammar can admit exactly one action type (submit) as a
   typed behaviour without opening the door to arbitrary action recognition —
   every other actionCall stays honest-refused.
