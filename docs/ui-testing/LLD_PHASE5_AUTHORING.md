# LLD Phase 5 — Criterion catalogue + customer rule authoring

Status: DESIGN (this commit); implementation follows on its own GO.
Branch: `phase-5-authoring` (from main @3800bd5).
Derives from: SDLC plan v2 §8 phase 5, ACC-03, R3, **FND-05** (the
closed conditional ceiling) and **F8** (the predicate-vocabulary fork
the TA must rule on). Absorbs Phase 4's two residuals: the
engine-census denominator and the rule-derived WCAG level.

> **⚠ PREREQUISITE — a production defect gates this phase.** The
> authenticated consume path is broken on main (`da8b907`, merged
> `3ba0c9f`): `_consume_authenticated` does not accept the `run_set`
> argument its caller passes, so every `vault`/`totp_env` job raises
> `TypeError` and walls to `failed_permanent`. Guest scans are
> unaffected and P-1 predates it. **Phase 5's custom rules target
> authenticated portal surfaces, and the D-466 run-set pin has never
> executed on the authenticated path — so this must be fixed, covered by
> a test, and re-verified (D-468) before any Phase 5 capture work.**
> Detail and the one-line fix: reported alongside this LLD.

---

# PART 1 — THE CRITERION CATALOGUE (the prerequisite)

## a. Ratified criterion lists — source and review path

**The problem restated.** Phase 4 correctly refused to have the
implementer generate criterion numbers from memory, and settled for an
engine-census denominator that is an explicit LOWER BOUND
(`denominator_complete: false`). Phase 5 needs the real list, because an
authored rule must map to a criterion.

**Decision, with the lean defended: the criterion list is an ARTIFACT,
ingested under exactly the discipline already used for the engine.**

`s5_artifacts` was built generically and already carries what this
needs — `kind`, `name`, `version`, `sha256`, `repo_path`, `source_url`,
`retrieved_at`, `byte_size` — and today holds one row
(`engine | axe-core | 4.13.0 | c24f097b… | primeqa/browser_worker/vendor/axe.min.js`).
Criterion catalogues become rows of `kind='criterion_catalogue'`:

| name | version | content |
|---|---|---|
| `wcag` | `2.0` | success criteria, level A/AA/AAA |
| `wcag` | `2.1` | as published |
| `wcag` | `2.2` | as published (4.1.1 removed) |
| `en301549` | `V3.2.1` | clause 9.x table + the WCAG SC each clause binds |
| `section508` | `2017-Refresh` | the incorporation statement (E205.4 → WCAG 2.0 A+AA) |

**Why this is not "inventing criterion numbers".** The distinction is
provenance, not confidence:

- the file is **obtained by a human from the published normative
  source**, committed to the repo, and **hash-pinned** with its
  `source_url` and `retrieved_at` recorded — the same act as vendoring
  axe;
- **Plimsol generates nothing.** The ingester parses a pinned document
  into rows and fails loudly on anything it cannot parse; it never
  supplies a missing number, title or level;
- a **human ratifies the ingested catalogue** through the set lifecycle
  (§d) with a real actor and a content hash, exactly as the map sets
  were ratified;
- the ingest is **reproducible**: same artifact + same ingester → same
  rows, checkable by hash.

Phase 4 refused *generation from memory*. Phase 5 does *ingestion from a
pinned source with human ratification*. If the artifact cannot be
obtained for a standard, that standard keeps its engine-census
denominator and keeps saying so — the honest fallback stays.

**Table.** `s5_criteria` (public, platform-global — WCAG/EN/508 are
platform facts): `set_id` FK, `standard`, `standard_version`,
`criterion` (the standard's own numbering), `title`, `level`,
`ordinal`, `binds_wcag_sc` (nullable — EN 9.1.1.1 → 1.1.1; 508 → the
incorporated SC), `source_ref`. UNIQUE `(set_id, criterion)`.

## b. Per-criterion LEVEL replaces the rule-derived level

Phase 4 residual #2: migration 063 propagated each axe RULE's level tag
to every criterion of that rule, so `scrollable-region-focusable`
records 2.1.3 as **A** where WCAG makes it **AAA**, and such criteria
slip the A+AA scope gate. With the catalogue, **level is read from
`s5_criteria`, never from the map row**. The map's `level` column
becomes derived/display-only and is backfilled from the catalogue in the
same migration; a mismatch between a map's stored level and the
catalogue's is a **loud ingest-time report**, not a silent overwrite —
the diff is the reviewer's evidence that the seed was imprecise.

## c. `denominator_complete` becomes TRUE

`standard_view`'s denominator becomes the ratified criterion list for
`(standard, standard_version)`, so:

- **"N of M criteria" becomes sayable.** M is the standard's true bound
  scope, not what the engine happens to know.
- **NOT_COVERED gains its true meaning**: a criterion *in the bound
  scope* with *no bound rule* — including criteria no engine addresses,
  which is precisely the set a customer most needs to see, because those
  are the ones only a human or a custom rule can cover.
- The honesty header changes from
  `denominator_provenance: engine_tag_census, denominator_complete: false`
  to `denominator_provenance: ratified_catalogue`,
  `denominator_complete: true`, plus `catalogue_artifact_sha256` and
  `catalogue_set_id` — so a report names the exact criterion list it
  counted against, as it already names the engine and the map set.
- **Fallback stays honest**: a standard without a ratified catalogue
  keeps `false` and the census. The flag is per standard, never global.

## d. Lifecycle — no new machinery

Catalogues ride the Phase 4 set lifecycle. Concretely, the existing
`s5_standard_map_sets` **widens in concept from "map set" to "standard
set"**: one ratified object per `(standard, standard_version)` carrying
**both** the criterion catalogue and the rule→criterion maps, under one
`content_hash` and one real-actor ratification.
`DRAFT → REVIEW → APPROVED → ACTIVE → RETIRED`, single-ACTIVE per
standard, authoring frozen from REVIEW — all already built and proven.
The table keeps its name for migration continuity; the LLD records the
widened meaning. The content hash extends to cover the criterion rows,
so ratifying a set ratifies the denominator and the projection together.

---

# PART 2 — CUSTOM RULE AUTHORING

## e. THE PREDICATE VOCABULARY AND ITS CEILING — **the F8 fork**

> **TA QUESTION (F8, verbatim):** *"how much expressive power is enough,
> before this becomes the general-purpose logic language the product
> philosophy rejects?"*
> **My lean: eleven predicate forms, ZERO composition operators, one
> applicability gate, five fact families — and a stop rule that gives
> every rejected request a destination.** Rejected alternatives in §e.5.
> **This section is the one a short TA check should cover before
> implementation.**

### e.1 The rule shape

A custom rule is exactly: **one selector · one predicate · one
applicability gate · one population declaration.** No connectives
anywhere. Splitting a guideline into several rules costs table rows;
connectives cost the ceiling. Rows are cheap.

### e.2 Selector grammar — closed, and closed for a *ratified* reason

**Raw CSS selectors are refused, and not merely on taste.** DE-13 fixes
the element fingerprint as "rule + role + accessible name + ancestor
chain + owning component (**never CSS path**)", and the RTM records
"no CSS-path storage". A CSS-string selector slot would put CSS paths
into custom-rule rows and into every stored verdict basis — breaking an
existing signed requirement.

It is also a logic language in costume: `:not()` is negation, `:has()`
existential quantification, `:nth-child(an+b)` counting plus arithmetic,
`[class*=…]` substring matching, `a > b ~ c` a relational join. Banning
`NOT` in the predicate slot while admitting `:not()` in the selector
slot would be theatre.

**Permitted selector terms** — AND-joined, flat, at most 4. This is a
conjunctive filter over ONE node's own captured facts, not a logic
operator over predicates:

| term | why it is safe |
|---|---|
| `role_is(<role>)` | the fingerprint's own role vocabulary — one definition of "role" in the system |
| `component_is(<custom-element-tag>)` | already captured (any tag containing `-`) |
| `owned_by_bundle(<S1 bundle ref>)` | processor-side resolution already exists (`classify_ownership`, `bundle_developer_name`) |
| `has_attribute(<name from a closed allowlist>)` | bounded; presence only, no values, no patterns |
| `within(<role \| landmark \| component term>)` | **one hop only** — without it "must be inside an approved component" is inexpressible and the feature is unusable on a real page |
| `heading_level_is(n)` | needs heading level added to the census (today `roleOf` flattens H1–H6 to `heading`) |

Refused in the selector slot: any CSS string, any pseudo-class,
`nth-*`, attribute *value* matching, substring matching, multi-hop
ancestry, sibling combinators.

### e.3 Fact families — five

| family | today | ruling |
|---|---|---|
| DOM structure + attributes | computed in-page then **discarded** (the fingerprint keeps only a sha + capped summary) | **ADMIT** — retain the tree instead of dropping it; cheapest family |
| component identity | partial (custom-element tag; bundle resolution built) | **ADMIT** as *custom-element tag + S1-resolved owning bundle*. **Class tokens refused** — classes carry scoping hashes, are unbounded, and "classes are never read" is a stated invariant; bundle resolution already delivers the benefit |
| computed style | **absent by explicit design** — "Text content, ids, classes and styles are never read" | **ADMIT, narrowed**: the element's OWN resolved value for a **manifest-pinned closed property list**. No cascade origin, no compositing, no `:focus-*` state |
| token sets | not a family | **RECLASSIFY** — it is the *value domain* of the membership predicates; versioned, pinned, size-capped |
| **layout geometry** | absent | **ADMIT as the fifth family** (`getBoundingClientRect` w/h/x/y) |

**Why geometry is admitted** despite the instinct to cut every style
family: it is witnessed by a captured number; it needs no operator
beyond `at_least`/`at_most`; it introduces no unpinned second engine
(the layout engine is the browser's own, already pinned by
`playwright_version` + `worker_image_digest`, and the viewport is
already part of surface identity); and it is the **only** route to a
customer standard *stricter* than the public one — `PLM-A11Y-064` binds
axe `target-size`, whose 24px threshold lives inside the engine and
cannot be remapped, so a customer whose standard is 44px has no path
without it.

**Dropped from the briefed proposal: "focus ring matches token."** It is
the one member that cannot be observed without either mutating the page
or re-implementing the cascade: `:focus-visible` styles do not resolve
on an unfocused element. It becomes either Mode B (parked) or a later
`CSS.forcePseudoState` capture — recorded as a named non-goal now rather
than shipped as a rule that quietly tests the unfocused state.

### e.4 The permitted eleven predicate forms

| form | operand | one-line justification |
|---|---|---|
| `member_of(token_set)` | style value, geometry value, component tag | the workhorse; truth witnessed by a captured value |
| `not_member_of(token_set)` | same | the **only** NOT in the language — witnessed by a value, not by an absence |
| `equals(literal)` / `not_equals(literal)` | attribute value, role, component tag | boolean-arity pair |
| `present` / `absent` | attribute, role | tests the node's OWN attribute, never the page's contents |
| `at_least(n)` / `at_most(n)` | geometry only | numeric comparison against a literal; nothing else numeric is admitted |
| `count_at_least(n)` / `count_at_most(n)` / `count_equals(n)` | the **match-set cardinality**, surface-scoped | a census attests a count *positively*; the only route to "exactly one h1". A rule FORM, not a combinator |
| `idref_resolves_to_role(<role>)` | one attribute from the allowlist (`aria-describedby`, `aria-labelledby`, `aria-controls`) | **one hop, no transitive closure**; unlocks the most-written custom accessibility pattern (error wiring) |

`idref_resolves_to_role` is the marginal admission: it reads a second
element, but only that element's ROLE, never a value, depth-capped at
one. **If the TA wants a smaller v1, this is the form to cut.**

### e.5 Composition operators — **none**, and why

| operator | ruling | reason |
|---|---|---|
| implicit `FORALL` over the match set | **required** (with a mandatory population declaration) | it is the rule's meaning, not a chosen operator |
| `AND` between predicates | **REFUSE** | one rule covering two obligations yields one FAIL that cannot say which half failed, and one row where two obligations exist. Two rules give two verdicts, two attestations, each independently NOT_DETERMINED-able — strictly better evidence, which Phase 4's denominator work just paid for |
| `OR` | **REFUSE** | subsumed by widening the token set; buys nothing, permanently doubles the reviewer's burden |
| `NOT` as a combinator over an existential | **REFUSE** | true when the probe merely failed — a wrong-green machine, a direct D-466 collision |
| nested quantifiers, implication, variable binding, named fragments | **REFUSE** | first-order logic; fails the one-sentence test immediately |
| arithmetic, colour-distance tolerance, regex/substring, cross-element value comparison, cross-run comparison, customer-set severity | **REFUSE** | each is a second evaluator or a second engine |

**The UX objection to refusing `AND` has a non-operator answer.** A
customer thinks in guidelines, not rules. The authoring ledger carries a
`guideline_thread_id` (§f), so a report **groups by guideline** while
**verdicts stay per-rule**: one guideline, three rules, one grouped
panel, three independently attested verdicts. The convenience is
delivered by the reporting layer, never by the grammar.

### e.6 Applicability — exactly one gate

> **APPLICABILITY** = the surface-metadata facts already permitted
> (persona, viewport, path, release membership) **plus at most one** gate
> of the form `surface_contains(<selector term>)` or
> `surface_lacks(<selector term>)` — presence or absence of a match set,
> with no predicate attached, no value read, no count compared, no
> chaining, no else-branch.

Three reasons this is not a crack in the ceiling. **(a)** It adds no
power the engine does not already exercise: every axe rule selects its
own applicable nodes and reports `inapplicable` when it matches none —
which is why `inapplicable_ids` exists in the observation and
`rule_inapplicable` is a first-class NOT_DETERMINED reason. **(b)**
FND-05 forbids *sequenced* conditional logic — branch, act, branch
again. A single non-chaining scope gate is not that: **conditionality
lives in applicability and never in the predicate; if you want an ELSE,
you want a second rule with the complementary gate.** **(c)** A *value*
test in applicability is refused precisely because
`applies when colour ∉ palette` plus an always-false predicate is a
negated rule with the negation hidden in the scope.

### e.7 The ceiling defence and the STOP RULE

**Why this is not a general-purpose logic language:** it has no
variables, no binding, no nesting, no user-defined abstraction, no
control flow, and no arithmetic beyond comparing one captured number to
one literal. Every rule is one sentence a reviewer can read aloud. The
grammar is finite and enumerable — a reviewer can hold all eleven forms
in their head, which is the actual test of reviewability.

**The stop rule for future requests** (four tests; a request must pass
ALL FOUR):

1. **WITNESS** — is the truth of the predicate attested by a value the
   census positively captured? (If it can only be true because something
   was *absent* or a probe *failed*, refuse — that is the D-466 wrong-
   green class.)
2. **NO NEW EVALUATOR** — can it be decided by comparing captured values
   to literals or set members, with no expression evaluation?
3. **ONE SENTENCE** — can the rule be read aloud as one sentence with no
   "and then", "otherwise", or "for each … where"?
4. **NO SECOND ENGINE** — does it avoid introducing an unpinned
   computation (colour-distance maths, layout re-implementation, regex)
   whose version would have to become a manifest pin?

**Every refusal gets a destination**, which is what keeps the ceiling
from being merely obstructive: *widen the data* (a new captured fact or
token set), *split the rule* (two rules, grouped by guideline), or
*file it against the public catalogue* (it is a standards matter, not a
custom one). Extending the vocabulary itself remains, per FND-05, "a
logged decision against principle P1, not a feature request."

## f. AUTHORING PATH, and refusal as a feature

```
prose guideline
   -> LLM drafts candidate rule(s) STRICTLY in the vocabulary (schema-constrained)
   -> the draft is validated against the grammar BEFORE a human sees it
   -> human reviews, edits, approves  ->  DRAFT rule enters the s5 lifecycle
   -> ... -> ACTIVE, versioned, immutable
```

**The LLM never decides a verdict and never authors an ACTIVE rule.** It
proposes; the grammar validator constrains; a human ratifies. A draft
that does not validate never reaches the reviewer as a rule — it becomes
a refusal.

**Refusal is a feature, and it is the most valuable output of this
phase for a customer**: it tells them which of their own standards are
untestable. The refusal record (`cust_authoring_ledger`, tenant schema):
`guideline_thread_id`, the prose verbatim, `outcome`
(`drafted` / `refused`), `refusal_class`, `refusal_reason` in the
customer's terms, `nearest_expressible` (the closest rule the vocabulary
CAN express, offered explicitly as a partial), `actor`, timestamps.

Refusal classes, each naming what is missing:
`not_observable` (e.g. a click handler attached via `addEventListener`
is not in the DOM), `needs_interaction` (Mode B), `needs_capability_not_captured`,
`needs_prohibited_operator`, `belongs_to_public_catalogue`,
`ambiguous_guideline`.

**A refusal is never a dead end**: it is surfaced in the coverage report
beside NOT_COVERED criteria, so "what we cannot test for you" is a
first-class, reviewable list rather than silence.

**One honest limit to state to customers up front.** Computed style is
*post-resolution*: a component that correctly consumes a design token
and one that hardcodes the same hex are **byte-identical in the
observation**. So `member_of(palette)` is expressible, but *"must
consume the token, never the literal"* is **refused**
(`needs_capability_not_captured`) — deciding it needs the winning
cascade declaration, a far heavier capture with a far weaker determinism
story. Most Salesforce design-system teams want the token rule, so this
refusal will be common and must be said plainly rather than papered over
with a value check that looks equivalent and is not.

## g. TENANCY

- Custom rules live in the **tenant schema** (the AK directive: nothing
  per-tenant outside the tenant): `cust_rules`, `cust_rule_versions`,
  `cust_predicates`, `cust_token_sets`, `cust_authoring_ledger`.
- **Namespace `PLM-CUST-nnnn`** — disjoint from `PLM-A11Y-nnn` (3A-1),
  so a public rule id can never be shadowed and a claim's rule id alone
  tells you which store resolves it.
  **⚠ Decide the digit count NOW:** the public CHECK is
  `rule_id ~ '^PLM-[A-Z0-9]+-[0-9]{3}$'` — inheriting that shape caps a
  tenant at **999** custom rules, and widening a CHECK after ids are
  minted is a migration nobody wants. **Lean: four digits.**
- **Joining the catalogue at enumeration and projection.** Enumeration
  reads a catalogue release's recorded membership; a tenant's release
  becomes the **union** of the platform release's members and the
  tenant's ACTIVE custom rules, recorded as membership at cut time
  (D-281 unchanged). Identity is safe by namespace disjointness; the
  claim's `plimsol_rule_id` resolves to the public registry for
  `PLM-A11Y-*` and the tenant store for `PLM-CUST-*`.
- **What is a custom rule's "standard"? Lean: a customer profile is its
  own standard-like set.** A tenant may declare
  `CUSTOM:<profile-name>` as a standard whose "criteria" are the
  customer's own guideline headings, ratified through the same set
  lifecycle in the TENANT schema. Rejected alternative — custom rules
  carry no standard map and render in a separate view — because it would
  fork the reporting surface, lose the coverage split for exactly the
  rules the customer cares most about, and prevent a customer guideline
  from ALSO mapping to a WCAG criterion when it genuinely does (a
  contrast rule stricter than AA still bears on 1.4.3). The profile set
  is disjoint from the platform standards by name, so no cross-tenant
  collision is possible.

## h. EXECUTION — the worker CAPTURES, the processor EVALUATES

**Decision: a processor-side evaluator over an enriched observation.
The worker gains a CAPTURE family and no judgment whatsoever.**

The three options, and why:

| option | ruling |
|---|---|
| **(a) processor-side over an enriched observation** | **CHOSEN.** The worker emits a **census** — a bounded, normalised property bag per semantic node — without ever being told what any rule says. Evaluation, applicability and verdicts stay in S6. D-460 is untouched: that boundary is about *who decides*, not *what is recorded*, and a property bag is an observation exactly as the fingerprint is |
| (b) worker-side predicate pass | **REJECTED.** The worker would have to receive rules and decide their truth — interpretation in the browser, which D-460 rejects by principle. It would also make every rule change a worker concern |
| (c) authoring custom axe rules | **REJECTED.** It would inject customer-authored code into the engine (FND-05 forbids embedded code), break the engine artifact's hash pin, put the customer's logic inside the very component whose determinism the manifest pins, and make a customer rule indistinguishable from a catalogue rule in the observation |

**What must be captured** (all currently absent — the phase's real
cost is here, not in the parser): for each semantic node, its role,
accessible name, heading level, custom-element tag, allowlisted
attribute presence/values, resolved values for a **pinned closed
property list**, and its bounding box. The census is emitted for the
nodes under a pinned scope, with a **node cap** recorded when hit.

**Determinism additions** — the census must be as pinned as the engine:
- the **property allowlist** and the **census schema version** become
  manifest pins beside `engine_run_set`, so two runs agree on what was
  captured, not only on what was found;
- **normalisation is specified, not left to the evaluator**: colours to
  a canonical sRGB tuple (`rgb`/`rgba`/`hsl`/`color-mix`/`currentColor`
  resolved), lengths to px with a **declared epsilon** (browsers return
  `13.9993px`; exact match would manufacture reds), fonts to a
  normalised family list;
- **shadow-DOM traversal semantics MUST be declared and recorded.**
  Aura Experience Cloud uses synthetic shadow (internals visible) while
  LWR/native shadow hides them, so the same rule could otherwise return
  different results on two site templates — the worst possible property
  for a conformance rule. The census records the traversal mode
  observed; a rule evaluated under a different mode than its baseline is
  **NOT_COMPARABLE**, not a transition.

**D-466 holds unchanged — PASS still requires positive attestation.** A
custom rule's attestation is the census itself: the rule's verdict is
PASS only when the census attests that the scope was walked, the match
set was non-empty, and every matched node's captured facts satisfy the
predicate. Therefore:

| situation | verdict |
|---|---|
| scope walked, match set non-empty, predicate holds for all | **PASS** |
| any matched node violates | **FAIL** |
| **selector matched nothing** | **NOT_DETERMINED (`no_match_set`)** — never PASS. This is the vacuous-pass class this programme has already been burned by (D-465/D-466); a rule that matched nothing tested nothing |
| a required property absent from the census (not in the allowlist, or the cap was hit) | **NOT_DETERMINED (`fact_not_captured`)** |
| census schema older than the rule requires | **NOT_DETERMINED (`census_unattested`)** |
| traversal mode differs from the rule's declared assumption | **NOT_DETERMINED (`traversal_mode_mismatch`)** |

**Guards against dishonest results** (each maps to a failure the panel
identified): the empty-match-set rule above; a **token-set version pin**
on every rule version, so a drifting design system invalidates the
projection rather than silently changing verdicts; **ownership-scoped
applicability** (a rule matching all platform chrome drowns the customer
and is not theirs to fix), which the processor can already resolve via
`classify_ownership`; and a **conflict check at ratification** that
refuses a custom rule whose predicate contradicts an ACTIVE catalogue
rule on the same criterion.

## i. Lifecycle and approval reuse

Custom rules ride the **s5 rule lifecycle shape** unchanged —
`DRAFT → REVIEW → APPROVED → VERSIONED → ACTIVE → RETIRED`, immutable
when ACTIVE, changes are new versions, real-actor audit into
`activity_log`, and entry into catalogue releases like any rule. Custom
token sets and the customer profile set ride the **standard-set
lifecycle** of Phase 4. **No new lifecycle machinery is introduced by
this phase** — which is the strongest evidence that the substrate was
built right.

# Non-goals

- **No UI** — data layer + CLI only.
- **No Mode B predicates** (and therefore no focus-ring rule, §e.3).
- **No customer-authored ENGINE rules** (option (c) above).
- No scheduling; no cross-tenant sharing of custom rules; no
  customer-set severity; no cross-run predicates.

# What the TA is asked to rule on

**One question:** *is the eleven-form, zero-connective, one-gate
vocabulary in §e the right ceiling — or is it too tight (the `AND` and
`idref` arguments) or too loose (geometry and the `surface_contains`
gate)?* My lean and the rejected alternatives are stated above; the
stop rule in §e.7 is offered as the durable test so this question is
answered once rather than at every future request.
