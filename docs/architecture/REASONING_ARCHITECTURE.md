# Reasoning Architecture V1

**Status:** V1, extracted 2026-07-13 from the Validation Rule Benchmark program
(D-342…D-361), B0/B0.1 Grounded Recovery (D-362, D-364), and the Flow
Behaviour arc Slices 1–2 (D-363, D-365). This document describes the reusable
reasoning system those programs produced. The benchmarks are *examples* of the
architecture — evidence and calibration, never its definition. Nothing here is
benchmark-specific; every mechanism operates on any Salesforce org's metadata.

The one-sentence system: **an LLM proposes test intents in business language;
a deterministic substrate grounds every reference and behaviour against the
org's own synced metadata, synthesizes the mechanics, and either emits a
verifiable claim with provenance or refuses with a named, layered reason.**
The division of responsibility is the load-bearing idea: the model owns
*meaning* (what behaviour the requirement names), the substrate owns
*mechanics* (whether the org verifiably has it, and how to prove it).

---

## 1. Semantic Grounding

**Purpose.** No claim exists unless the org's metadata verifiably supports it
— "ground or refuse" is the system's constitution.

**Abstraction.** A *grounding* is a typed, version-pinned binding from an
intent to S1 entities plus the mechanism that realizes the asserted behaviour
(a constraint that fires, a flow that writes, a formula that computes, a grant
that permits). Admissibility (does supporting structure exist?) is separated
from binding (which structure?) and from emission (can we author a test for
it?) — three gates, three failure vocabularies.

**Current implementation.** `generation/governance_core.py`:
`AdmissibilityEngine` (Layer-1 existence over the scoped neighborhood),
`resolve_intent` (per-archetype binding), `finalize_outcome` (authoring).
Reads S1 through `SemanticOrgModel` at the request's pinned `s1_version_seq`,
org-scoped (D-286).

**Known limits.** Layer-1 only — a constraint's *existence* admits; formula
semantics enter later, per capability. Single-hop neighborhoods (no
`traverse`).

**Extension points.** New mechanisms enter as new *binding* rails (approval →
enumeration; calc-field → `is_calculated`; flow → effect/transform match), not
as new admissibility logic.

## 2. Entity Resolution

**Purpose.** Business language must land on exact org API names without the
model being taught any org's naming convention.

**Abstraction.** A two-sided contract. *Subject objects* are validated only:
the model proposes the actual API name inferred from the requirement's own
wording; a wrong-but-real name resolves silently, so specificity is the
model's duty (prompt v29 states exactly this — the prompt must never promise a
capability the substrate lacks). *Fields* are resolved deterministically:
exact → unique-bare → unique-suffix → unique-label, never guessing
(`_resolve_subject_field_name`), with the subject's real vocabulary taught
back on a miss.

**Current implementation.** governance_core (resolver + vocabulary lines);
prompt fragment `data_behavior.md` (the capability-accurate naming contract);
frozen prompt registry with hash-guarded versions.

**Known limits.** Suffix/label rules are per-subject (correct); no cross-object
field search by design. Object-label inference lives in the model, assisted
only after a *failed* resolution.

**Extension points.** RecordType/PermissionSet name resolution can reuse the
same unique-match ladder if a consumer ever needs it.

## 3. Grounded Recovery (B0/B0.1)

**Purpose.** A wrong reference should recover in one hop instead of decaying
into model self-refusal — without the substrate ever choosing for the model.

**Abstraction.** On a *failed* resolution, offer a small, deterministic,
metadata-only candidate set (token+trigram similarity, requirement-context
affinity, exact-label bonus for multi-word verbatim labels), admission gated
by similarity alone so context terms reorder but never expose new entities.
The model re-proposes; its choice re-enters normal validation. Provenance of
every offer is persisted. **The boundary (D-362):** recovery is lexical, so
only lexical-identity entities are recoverable (Object, Field, RecordType,
ValidationRule, PermissionSet, CustomMetadata). Behavioural entities (Flow,
ApprovalProcess, Apex, invocable actions) are *never* candidate-recoverable —
behavioural entities require behavioural verification; supplying an automation
name would let name-trust binding attach an automation whose effect was never
verified (live-observed wrong-attribution).

**Current implementation.** `generation/recovery.py` (pure engine) +
`AdmissibilityEngine.recover_reference` (pooled reads, allowlist) + Layer-A
and resolve-stage feedback wiring.

**Known limits.** Single-token labels earn no phrase bonus (the common-noun
ambiguity class, D-364); generic bare guesses against org-wide pools rank
honestly but unhelpfully — the requirement-context terms are the counterweight.

**Extension points.** Per-entity-type ranking weights; recovery for
RecordType/CustomMetadata once S1 models them richly.

## 4. Behaviour Representation (Flow Behaviour IR)

**Purpose.** Automations are procedural graphs, not formulas; grounding them
needs a representation that says *exactly which behaviours are understood* and
refuses the rest by name.

**Abstraction.** A bounded walk of the stored flow graph into (trigger
context, guard, effect) units, each `grounded | unsupported | opaque` with
named reasons, under the **conservatism rule**: any out-of-grammar element on
the path demotes every behaviour on it — partial understanding never grounds.
Effects are literal writes (`set_record_field`) or bounded transforms
(`set_record_field_transform`: UPPER/LOWER/TRIM nests over one `$Record`
field). Entry filters in the bounded IsNull shape are consumed as guards.

**Current implementation.** `semantic/entity_attributes.py::flow_behaviour`
(IR v2) + binder projections (`flow_grounded_same_record_effects`,
`flow_grounded_transforms`) + `apply_transform_chain`. On-read, pure,
version-safe; nothing persisted.

**Known limits.** Before-save immediate paths only; single-rule decisions;
literal guard values; no lookups/loops/actions/subflows/faults; transforms are
string-functional only.

**Extension points.** Multi-rule ordered decisions (FL03's ask) extend the
guard model with negation-context chains; after-save effects extend the
trigger model; each new element type is one new walk case with its own
demotion reason until grounded.

## 5. Constraint Representation

**Purpose.** Declarative rules (validation rules) need machine-readable
semantics to derive tests *from the org's own logic*.

**Abstraction.** Formula ASTs (`semantic/formula`: comparisons, field refs,
literals, recognized function calls) evaluated tri-state (Kleene) so unknowns
stay unknown; constraint *firing* is a decidable predicate over staged worlds.
Rules bind to claims by field-overlap alignment (a claim grounds on *its own*
rule, D-295), and staged states are checked against every governing rule
before emission (D-337) so generation refuses what the org would bounce.

**Current implementation.** the formula package; `vr_conflict.py` (staged
worlds, Kleene); `verified_negative.py` (AST-driven derivation);
`_ground_rejection_conditions` (typed clause grammar incl. cross-field
`exceeds`).

**Known limits.** The parser's recognized-function set is deliberately small;
unparsed formulas fall to caveated paths, never guessed.

**Extension points.** Each newly recognized function widens derivation,
conflict-checking, and witness synthesis simultaneously — one AST, three
consumers.

## 6. Witness Synthesis

**Purpose.** Tests need concrete values with *provable* relationships to the
constraint or behaviour under test — certainty, not plausibility.

**Abstraction.** A family of deterministic generators, each with an explicit
undecidability boundary: violating payloads from rule ASTs (`derive`, D-107);
minimally-violating and boundary-honest values (D-346/D-352); non-match probes
and — bounded — full-match synthesis for format regexes
(`regex_matching_value`, literal+digit-class grammar, D-344/D-365); transform
witnesses as (canonical, raw) pairs where the canonical must be a **fixed
point** of the transform chain and the raw must provably normalize to it while
differing from it. Values are typed through the field's own metadata; temporal
values stay symbolic (see §10).

**Current implementation.** `verified_negative.py`, `decision_branch.py`
(_satisfy composition), governance's `_synthesize_transform_witness`.

**Known limits.** Regex synthesis excludes alternation/groups/letter classes;
"no governing rule" transforms use a documented default seed; matching-value
synthesis is not wired into the negative-derivation engine (deliberate — see
debt register).

**Extension points.** Widening the regex grammar; per-type canonical seeds;
cross-field consistent witnesses.

## 7. Differential Evidence

**Purpose.** A single green result can be green for the wrong reason; paired
arms isolate *which mechanism* produced the outcome.

**Abstraction.** Every strong claim is a **differential**: two executions that
differ in exactly one dimension, with opposite expectations. Instances: the
boundary pair (violating vs boundary-accepted value); the context differential
(same value, alternative RecordType, expected accepted — one structural
mutation, derived by reference from the firing arm); fire vs suppression arms
for guarded automations; the transform pair implicit in staged-raw ≠ expected.
The single-dimension guarantee is structural, not narrative.

**Current implementation.** emission's BoundaryPair/ContextDifferential
authoring (Amendment B), decision_branch's isolated-firing + necessity
controls, the FL01 suppression shape, FL02's staged-raw/expected split.

**Known limits.** Differentials exist per capability rather than as one
declared combinator; the RECORD dimension covers RecordType only.

**Extension points.** A general "differential" descriptor (dimension +
mutation + expectation flip) is the natural refactor once a third dimension
appears (see debt register — research, not debt).

## 8. Effect Attribution

**Purpose.** A claim that credits the wrong automation is worse than no claim
— attribution is the claim's value.

**Abstraction.** Effect-first binding: an automation is named by the substrate
only when its parsed metadata *verifiably produces* the claimed effect
(literal write, transform, record creation). The model never names
automations; requirement-named automations must still resolve on the subject;
enumeration binds approvals (metadata-less, D-320); a calculated observed
field re-binds to the formula engine (D-304); **no verifiable producer → no
bind** — the provisional first-encountered fallback refuses on every shape
(the cross-object `flows[0]` floor, D-362 evidence). Ambiguity (>1 producer)
refuses by count.

**Current implementation.** `_flows_producing_effect`,
`_flows_producing_transform`, `_approval_binding`, the SUB-3 floors, the
calc-field coherence gates.

**Known limits.** Attribution is single-automation; stacked flows writing the
same field refuse rather than compose (correct until order-of-execution
composition lands, B3).

**Extension points.** Order-of-execution composition; after-save effect
attribution via the IR once its grammar extends.

## 9. Transition Reasoning

**Purpose.** Update-time semantics (`ISCHANGED`, `PRIORVALUE`, state moves)
need a two-phase world, not a flat payload.

**Abstraction.** `TransitionState(prior, next)` makes org-state functions
ordinary decidable predicates; single-phase evaluators return unknown for them
*by construction*, so transition reasoning is additive. Claims carry
from-state/to-state; recipes stage create-then-update; causality gates demand
a producer for create-scoped to-states (approvals can never be one — they fire
on submission, not creation; D-362 evidence).

**Current implementation.** `transition.py` (evaluate_transition,
derive_transition), the D-222 staged transition shape, D-306's update-observe
phase, the no-op-change guard.

**Known limits.** Two phases only (no multi-step journeys — the D-310/D-312
continuous-walk blueprint remains banked); before/after-save flow phases are
represented but only before-save grounds.

**Extension points.** Journey execution (multi-transition accumulated state);
flow-transition interaction once after-save grounds.

## 10. Temporal Reasoning

**Purpose.** Date logic must be testable without freezing calendar literals
into artifacts that rot.

**Abstraction.** Three layers kept separate: semantic constraint
(`Start < TODAY()`), test-design value (`RelativeDate(RUN_DATE, -1)` — the
persisted, symbolic layer), transport value (materialized once at the
execution boundary). Regeneration or re-run re-anchors automatically;
determinism is preserved because the *symbol* is the identity, not the date.

**Current implementation.** `test_representation/temporal.py` (VR06 arc,
D-359-era) + S4 materialization.

**Known limits.** Offsets around a single reference point (RUN_DATE); no
cross-field date arithmetic synthesis (FL08's `TODAY()+5` stamp remains
unsupported in the flow grammar).

**Extension points.** Date-arithmetic transforms in the IR; relative ranges;
scheduled-path reasoning (FL10-class) if async observation ever enters scope.

## 11. Decision Branch Coverage

**Purpose.** Compound logic (`A AND (B OR C) AND D`) hides untested branches
behind a single green.

**Abstraction.** Bounded Boolean decomposition: one isolated firing witness
per branch *group* (same-field branches unify), all non-target branches held
provably false; one necessity control per gate (falsified minimally at its
boundary) plus the all-branches-false control — each accepted arm proving a
gate is necessary, not incidental. Composes the witness engine (§6) and
differential evidence (§7) rather than owning new value logic.

**Current implementation.** `decision_branch.py` (VR03 arc, D-360).

**Known limits.** Exactly one top-level disjunction; declarative rules only.
**This is FL03's pressure point**: ordered first-match flow decisions are the
*procedural* sibling — N exclusive bands where order, not Boolean structure,
determines the winner — and need band-interval witnesses (per-band values
*between* thresholds) plus the IR's multi-rule guard chains. The concepts
compose; neither currently reaches it (see Capability Map).

**Extension points.** First-match ordered decomposition (FL03); shared
"branch group" abstraction between declarative and procedural deciders.

## 12. Honest Refusal

**Purpose.** The system's credibility is its refusals: every incapability is
stated, named, and diagnosable — never silently papered over.

**Abstraction.** Refusal is a first-class outcome with a taxonomy
(`refusal_kind` × dismissal reasons × named details), produced at the gate
that owns the failure. Fail-closed is the default posture everywhere a wrong
green is possible (placeholder values, unverifiable producers, opaque
governing rules, non-fixed-point witnesses, unreadable formulas). The
coverage enforcer (D-247/D-340) closes the loop per acceptance criterion:
covered means *grounded*, refusals are recorded per-AC, and one recovery hop
re-asks with the substrate's feedback.

**Current implementation.** RefusalRouter + per-gate details; the coverage
map; `coverage.py`'s deterministic floor against model under-declaration.

**Known limits.** AC-level coverage is structurally blind to control-level
loss — measured (not yet corrected) by §13's lifecycle telemetry.

**Extension points.** Control-oriented recovery (Phase 1+ of D-361) is
deliberately deferred until benchmark evidence justifies promotion.

## 13. Provenance

**Purpose.** Every recorded reason must say *who* concluded it and *where* —
a model explanation must never read as a substrate fact.

**Abstraction.** Two orthogonal dimensions on every refusal payload and
coverage row: origin (`substrate | model`) × layer (`resolution | grounding |
admissibility | execution`), surfaced as UI badges. Beside it: the control
lifecycle read-model (EXPECTED → NOMINATED → SELECTED → EMITTED → EXECUTED →
ATTRIBUTED) — read-only by decision (D-361), with an explicit promotion
boundary no generation-path code may cross; recovery offers persisted with
source; claim identity fingerprinting (SHA-256 over identity-bearing layers
only) keeping semantics and operations separate (§6.3).

**Current implementation.** D-362 provenance tags end-to-end;
`control_coverage.py` + its SELECT-only report; identity_hash +
canonicalization.

**Known limits.** Pre-D-362 rows carry no tags (read as substrate — they
were); lifecycle telemetry measures loss but nothing consumes it yet, by
decision.

**Extension points.** Provenance on S6 interpretation outputs; lifecycle
Phase 1 promotion after the Flow benchmark's evidence.

## 14. Benchmark Philosophy

**Purpose.** Benchmarks measure the architecture; they must never *become*
it.

**Abstraction.** A benchmark is a frozen org fixture + a leak-reviewed
business requirement + an internal gold standard the system never reads.
Requirements state observable behaviour only (no thresholds, no API names, no
mechanism vocabulary); deriving those from metadata *is the capability under
test*. Scoring distinguishes apparent / trustworthy / correctly-exercised.
Product code must contain zero benchmark knowledge (audited per slice: the
FL02 slice adds none); fixtures live in tests as the parser's corpus, which is
the correct direction of dependency. Regressions run the *older* benchmark
after every architecture change (the VR benchmark pair after B0.1).

**Current implementation.** VRB-V1 (10/10) and FB-V1 (FL01+FL02 exercised;
the rest honestly refused) under `benchmark/` + `sandbox_fixtures/`, both
frozen.

**Known limits.** Two benchmarks, one org family; determinism is proven
per-claim, not yet per-full-run-scoreboard.

**Extension points.** Every new capability class earns a benchmark *before*
its architecture lands (the FB-V1 pattern: predict the gap, then build).

---

# Capability Map

```
                        REASONING ARCHITECTURE V1
  Grounding ─ Resolution ─ Recovery ─ IR ─ Constraints ─ Witnesses
  Differentials ─ Attribution ─ Transitions ─ Temporal ─ Branches
  Refusal ─ Provenance
        │
        ├─► SUPPORTED (live-proven, deterministic)
        │     VR benchmark: 10/10 controls (boundary pairs, context
        │       differentials, transition gates, temporal values,
        │       branch coverage, entailment selection)
        │     FB-V1 FL01: literal guarded default (fire arm; IR + binder)
        │     FB-V1 FL02: bounded transform + synthesized witness +
        │       consumed IsNull entry filter (fire arm)
        │     VR01-class format prohibitions (regex derivation)
        │     Convergence: B0/B0.1 recovery, v29 naming contract
        │
        └─► UNSUPPORTED (each named by its refusal today)
              FL03  multi-rule ordered decisions  ◄── THE PRESSURE POINT
              FL04/05/07/09–15  after-save effects, cross-object
                    creates/updates, counts, absence differentials (B2)
              FL06  Get-Records data dependence
              FL08  date-arithmetic transforms (temporal × IR)
              FL10/11  scheduled/async paths
              FL12  subflow composition
              FL02's VR interplay as *stated* order-of-execution
                    composition (B3) — the witness fact covers the
                    fire arm only
              Journeys: multi-transition accumulated state (D-312)
```

**Why FL03 is the next architectural pressure point.** FL03 sits at the
junction of the two halves the architecture built separately: the IR walks
flow graphs but stops at multi-rule decisions (`multi_outcome_decision`), and
DecisionBranchCoverage decomposes compound logic but only declarative Boolean
rules. An ordered first-match band ladder is the *procedural* decision: guard
chains where each rule's context includes the negation of every earlier rule,
requiring (a) IR guard chains with negation-context (grammar extension), (b)
band-interval witnesses — values strictly inside each band, a new witness
class between §6's boundary values, and (c) N-arm differential emission (one
firing witness per band, attribution per arm) — the procedural sibling of
§11's decomposition. It exercises no new *concept*; it forces the existing
concepts to compose. That is exactly what a next slice should do.
