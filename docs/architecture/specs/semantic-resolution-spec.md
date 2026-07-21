# Semantic Resolution — Shadow Verifier + Wrong-but-Real Veto (D-376)

**Status:** First arc — Slice 1 (shadow) implemented; Slice 2 (veto) designed,
telemeter-gated; Slice 3 (subject vocabulary offers) design-sketched, gated on
shadow evidence.

## Problem

The S3 generation LLM must emit the subject object's exact API name
(`target_subject_hint.object`), which it cannot know. Two failure classes
(D-362, D-363, D-364, D-374, D-375):

1. **Lexical miss** (`Order__c` → nothing): recoverable — the B0 offer
   machinery progressively closed this class (D-375 live: 15 effect-endpoint
   misses → 13 recovered).
2. **Wrong-but-real** (`Order` for `PLS_FB_Order__c`): the guess exact-matches
   a *different real object* and resolves **silently**. D-363: this class is
   "invisible to lexical recovery BY DESIGN" — recovery fires only on misses,
   and the v28 prompt experiment showed prose-level naming guidance can
   *regress* object convergence (4/4 → 1/3). No shipped mechanism can see it.

The evidence-backed strategy (2026-07-21 architecture review): the intent's
OWN structural evidence discriminates — the field mentions the model already
emits (`Priority__c`, `Status__c`, staged values) bind on the org's real
object and not on the wrong-but-real one. And the proven interaction pattern
is **offer → select → verify** (the B0 record), not blind naming: extend that
pattern to subjects rather than inventing a new pipeline.

## The package: `primeqa/resolution/`

Cross-cutting (peer of `intelligence/`/`shared/`); consumes S1 only through
`SemanticOrgModel` behind the `KnowledgeSource` protocol (single S1-backed
implementation today — the seam is the future multi-source point). Substrates
import it; it imports no substrate. It carries its own pure similarity engine
(mirrors `generation/recovery.py` in spirit; deliberately not imported —
wrong dependency direction; consolidation deferred).

### Contracts (generation-agnostic)

- **`BusinessGraph`** — nodes (`entity` / `attribute` / `state` / `actor`,
  each a business *term* + verbatim excerpt; never a resolved identifier) +
  edges (`attribute_of` / `state_of` / `related_to` / `effect_on`).
  `validate()` gates well-formedness; resolution refuses malformed graphs.
- **`ResolvedGraph`** — per-node `Binding` (`{entity_type, sf_api_name,
  entity_id, matched_via, structural_coverage, candidates}`) pinned to
  `(s1_version_seq, connected_org_id)`. Grades are discrete and
  evidence-derived, never probabilities:
  - `BOUND_UNIQUE` — strictly dominant winner with structural support (≥1
    bound sub-mention) or an exact api/label match;
  - `BOUND_WEAK` — dominant on lexical evidence alone (advisory only);
  - `AMBIGUOUS` — ≥2 non-dominated candidates; **ties are never picked** —
    the ranked candidates + features are the disclosure payload;
  - `UNRESOLVED` — nothing admitted.

### Algorithm

- **Phase A (recall), per node:** exact api-name; exact label under a STRICT
  normalization (case/underscore/whitespace-insensitive, SF suffixes NOT
  stripped — an API-shaped guess like `Order__c` must never count as an exact
  match of the business label `Order`); token-Dice + trigram-Dice similarity
  (≥ 0.35) with requirement-context overlap as a feature. Field/state
  sub-mentions use the unique-match ladders (the `_resolve_subject_field_name`
  4 rules: exact qualified → unique bare → unique `_`-suffix → unique label;
  0-or->1 → `None`, never a guess).
- **Phase B (precision), joint:** each entity candidate is scored by how much
  of the node's OWN sub-graph it structurally binds (attribute mentions via
  BELONGS-TO inventory, state mentions via picklist values). Ranking is
  **lexicographic dominance** — `(structural coverage, exact api/label,
  context affinity, lexical)` — never a weighted sum (the D-364 ranking-bug
  class). A winner exists only when its key strictly exceeds the runner-up's.
- **Symbol table:** bulk in-memory hydration per `s1_version_seq` (the
  `metadata_bridge/s1_reader` D-189 pattern, 5 bulk queries; ~5,900
  entities/org). Field→object relationships come from
  `field_details.references_object_entity_id`, not the derived edge.

**The joint machinery is a VERIFIER/GATE, not a decider:** it vets a subject
the pipeline already named, and (Slice 3) vets a menu selection. It never
silently substitutes an entity for the named one.

## Slice 1 — shadow observation (implemented; zero behavior change)

`primeqa/generation/shadow_resolution.py` reconstructs a BusinessGraph
deterministically from each data-behavior intent's existing v29 hints (subject
name as a business term; `field_name` / `effect_field` / trigger + condition
fields; string staged values as states; `effect_object` as a second entity).
`automation_name` is never reconstructed (behavioural identity is never
lexically resolved — D-362).

- **Hook:** `_resolve_one`, immediately after `resolve_subject` — one site
  sees the raw proposed names, all three actual outcomes, and the raw field
  hints. Exception-safe (any raise swallowed + logged; a failed symbol-table
  hydration caches a sentinel and never retries per intent).
- **Persistence:** verdicts ride `attempted_interpretation["shadow_resolution"]`
  on BOTH outcome kinds (`finalize_outcome` + `route_refusal`, the D-361
  idiom). Hash-safe: `explanation_hash` canonicalizes only its four fixed
  keys (test-asserted). **No migration.**
- **Verdict:** `{term, actual{outcome, sf_api_name}, shadow{grade, winner,
  structural_coverage, field_mentions, model_binds, winner_binds}, agreement,
  would_veto, veto_evidence}`. Agreement classes: `agree` / `conflict` /
  `shadow_only` / `model_only` / `neither`.
- **Live-scope note:** single-intent Layer-A subject misses are refused by
  `check_refs_exist` before `resolve_intent` and are not live-observed; the
  replay script (`scripts/shadow_resolution_replay.py`) bypasses Layer A and
  covers that class offline. `scripts/shadow_resolution_report.py` aggregates
  persisted verdicts, including **ambiguity persistence** (same term
  AMBIGUOUS in ≥2 outcomes per org) — the measured input to the deferred
  glossary-pin decision.

### PROMOTION BOUNDARY (the D-361 language)

**Recovery, prompting, and grounding MUST NOT depend on this telemetry.** No
selector, re-prompt loop, prompt builder, or grounding gate may read
`shadow_resolution` or `state.shadow_verdicts` to change what it does — the
verdicts are an observation of the pipeline, never an input to it. The one
sanctioned promotion is Slice 2's flag-gated veto, which reads ONLY
`would_veto` at the subject-resolution site.

## Slice 2 — wrong-but-real veto (designed; telemeter first, then flag-armed)

`would_veto` is deliberately conservative: actual subject **resolved** AND the
shadow winner differs AND winner grade `BOUND_UNIQUE` AND the model's object
binds **zero** of the veto-evidence mentions AND the winner binds **all** of
them (≥1 required), **minus two replay-derived suppressions** (2026-07-21
offline replay over req-320/req-315, which surfaced 9 true positives — all the
`Order` → standard-`Order` flagship class — and 30 cross-object false
positives):

1. **Foreign-qualified mentions are excluded from the veto-evidence set** — a
   mention whose qualifier names another object
   (`PLS_FB_Order_Line__c.Order__c` under subject `PLS_FB_Order__c`)
   self-declares cross-object framing and carries no evidence about the
   subject.
2. **Adjacency suppression** — when the winner is reachable from the actual
   subject via a lookup relationship (`Order_Line` → `Order`), the case is
   cross-object effect framing, not a wrong subject; it is also not silent
   (the field miss downstream carries B0 offers). It stays in the `conflict`
   telemetry class.

When vocabularies overlap (a `Status` mention binding on both objects) the
predicate also stays silent — `conflict` carries it. Further tightening is a
data-driven decision for the telemeter gate, not a default.

Arming (after one reviewed live cycle): migration 062 adds
`tenant_agent_settings.llm_enable_resolution_veto` (D-300 pattern, default
false, not ORM-mapped) → `OperationalContext.enable_resolution_veto` → at
`subject = matches[0]`, flag on + `would_veto` ⇒ refuse via
`RefusalKind.AMBIGUOUS_REFERENCE` (reused — a new enum kind means ALTER TYPE
across every tenant schema; the payload distinguishes) with
`detail_source="substrate"`, `detail_layer="resolution"`, the D-374
field-discriminator disclosure, and the winner in the B0 `candidates` channel
so the model re-proposes (offer → select → verify). Errors in the veto path
fall through to today's behavior. Open governance question (logged, not
decided): whether ARMING bumps `GovernanceContext.refusal_policy_version`.

## Slice 3 — subject vocabulary offers (sketch; gated on shadow evidence)

Remove the blind guess: a retrieval-narrowed **subject vocabulary offer**
(top-K objects by the resolution scorer against the requirement text: label +
API name + 2–3 discriminating custom fields + an explicit none-fits) enters
the initial requirement turn; prompt v30 changes the subject contract from
"exact API name, validate-only" to "choose from the offered vocabulary or
declare none-fits"; the Slice-1 verifier gates the selection (a dominated
choice gets one corrective hop naming the discriminators). Non-negotiable
gates (the v28 precedent): replay baselines must not regress; FB-V1 object
convergence ≥ the v29 baseline; one reviewed live cycle before v30 becomes
CURRENT. Rollback = registry pin to v29.

## Out of scope (this arc)

Free-graph prompt contract (rejected in the 2026-07-21 architecture review);
catalog compiler / alignment map / glossary pins (gated on measured ambiguity
persistence); non-Object subject kinds; config/permission/ui archetype
resolvers; embeddings recall tier (dormant until the deterministic ladder
demonstrably under-recalls).
