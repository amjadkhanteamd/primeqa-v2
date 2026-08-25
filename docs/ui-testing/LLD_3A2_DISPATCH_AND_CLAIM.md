# LLD 3A-2 — Explicit Dispatch Modes (D6) + conformance-claim Registration (DE-02)

Status: DESIGN (this commit); implementation follows on its own GO.
Derives from: HLD DE-02/DE-06-adjacent, SAD A3 (explicit mutation flag) +
A10 (worker boundary), TAD v1.2; binding decisions D2 (Plimsol rule ids are
identity; standards/engines are maps), D-459 (no autocommit_block), D-461
(manifests pin catalogue releases). Branch: `phase-3a-substrate`.

## a. D6 — the explicit read-only property at `_authorize_dispatch`

**Today** (`primeqa/execution_engine/run.py:143`):
`read_only_inspection = recipe.recipe_kind == _METADATA_RECIPE_KIND` — the
kind-NAME inference whose own docstring (`run.py:129-133`) carries the
standing warning that a future metadata-write kind silently breaks it.
SAD A3 prohibits inference from kind names. This slice discharges that
docstring demand.

**Mechanism.** A declared table at the chokepoint module
(`primeqa/execution_engine/modes.py`, imported by `run.py`):

```python
class RecipeMode: READ_ONLY / MUTATING     # enum
RECIPE_MODES: dict[str, RecipeMode]        # EVERY registered kind, no default
```

`_authorize_dispatch` consults `RECIPE_MODES[recipe.recipe_kind]`; a kind
ABSENT from the table is REFUSED (fail closed — `PolicyError`, "kind has no
declared dispatch mode"), never inferred. The `read_only_inspection` local
becomes `mode is READ_ONLY`; the rest of the gate (disabled / read_only /
production ladder, D-245 three-gates) is unchanged.

**The declared table (all six kinds):**

| kind | mode | grounds |
|---|---|---|
| `metadata-recipe` | READ_ONLY | the bridge already enforces `mode == metadata_read` (`bridge.py:71,125-127`); the declaration makes explicit what the inference assumed |
| `data-recipe` | MUTATING | creates/updates/deletes org records by design |
| `ui-recipe` | MUTATING | Mode B inheritance: ordered click/type steps mutate application state |
| `event-subscription-recipe` | READ_ONLY | subscribes/observes platform events; performs no org write (declared; executor still unwired) |
| `callout-intercept-recipe` | MUTATING | conservative: interception alters runtime behaviour even without org writes; unclear ⇒ MUTATING (fail-closed posture) |
| `ui-inspection` (NEW, §c) | READ_ONLY | SAD A3 verbatim: "`ui-inspection` = read-only; any Mode B kind = mutating" |

A unit drift-guard asserts `RECIPE_MODES.keys() == RECIPE_KIND_ENUM` (both
directions) so a future kind cannot register without declaring its mode.

**Enqueue-boundary production-role interaction (D-245 pattern), stated:**
- At DISPATCH: `ui-inspection` is READ_ONLY ⇒ permitted on
  `execution_policy = read_only` envs, and permitted for non-admin callers
  on production (the read-only-inspection bypass, now flag-driven) —
  exactly the metadata-recipe posture.
- At the S4 ENQUEUE boundary: the existing production non-admin hard-reject
  (`views.py:4230-4241`) is UNCHANGED — it guards the `s4_execution_jobs`
  queue and stays as-is.
- `ui-inspection` work reaches execution through the BROWSER plane
  (`s4_ui_inspection_jobs` + manifests, D-461), not `s4_execution_jobs`.
  Its enqueue-time policy evaluation (env policy × role, consulting the
  same `RECIPE_MODES` declaration) is wired in 3A-3 when enumeration
  builds manifests; 3A-2 provides the declaration + the dispatch gate.
  Until then nothing routes a `ui-inspection` recipe into the in-process
  S4 verticals: `executability.py` refuses it with an honest message
  ("browser-plane kind; no in-process S4 vertical executes it").

## b. conformance-claim (DE-02) — identity DECIDED: canonical surface natural-key string NOW

**The question:** the Surface S1 entity does not exist until 3A-5. Does
claim identity hash a canonical surface natural-key string now (entity FK
linked later), or does 3A-5 move before claim registration?

**DECIDED (defended lean): hash the canonical natural-key string now, and
FOREVER.** The natural key IS the semantic identity per FND-01 (a surface =
site × page × persona × record context × viewport-when-semantic); an S1
entity id would be an operational pointer to that identity, not the
identity itself.

- **Body** (`ConformanceClaimBody`, kind `conformance-claim`, v1, archetype
  `ui` — LayoutClaimBody's home):
  `plimsol_rule_id` (shape-checked `PLM-...`, the D2 atom) ×
  `surface` value object `{site, path, persona, record_context?,
  viewport?}` — `viewport` present ONLY where a criterion makes it
  semantic (FND-01b), else absent and excluded from identity.
- **Canonical surface string — exact fields = the D2 surface identity,
  FROZEN as IDENTITY_HASH_VERSION v1 (amended per GO):**
  `site | path | persona-scope | record-context-ref | viewport`, where
  `viewport` participates ONLY where the criterion makes it semantic
  (FND-01b) and is otherwise absent. This five-field composition is the
  v1 identity contract: ANY field change — addition, removal, or
  semantics — is a NEW `IDENTITY_HASH_VERSION`, never an in-place
  re-hash. A field-composition test pins exactly these fields and FAILS
  if one is added or dropped.
- **Canonicalizer** (per-kind, registered `(body_class,
  IDENTITY_HASH_VERSION)` beside `canonicalizers/state_transition.py`):
  emits `{rule_id, surface_key}` where `surface_key` is the canonical
  string over the five D2 fields above under FROZEN v1 normalisation
  rules (host lowercased, path with leading `/` and no trailing `/`,
  absent components as `-`). Precedent: `LayoutClaimBody`'s
  composite two-ref key through the generic walk — here the refs are the
  rule ATOM string and the surface NATURAL KEY string, and we deliberately
  do NOT entity-re-key (the existing canonicalizers re-key external→entity
  because entity identity is the semantic anchor for org metadata; for
  surfaces the natural key is the anchor).
- **Identity-stability consequences, stated:**
  1. When 3A-5 lands `Surface` entities, the natural key REMAINS the
     hashed identity permanently; the entity linkage arrives as an
     operational, identity-EXCLUDED field (`surface_entity_ref`,
     nullable). No corpus identity migration, ever.
  2. The rejected alternative — switching identity to `entity_id` at
     3A-5 — would change every conformance claim's identity hash and
     force a corpus-wide deprecate-then-regen (the migration law);
     rejected as pure cost with no semantic gain.
  3. The normalisation rules are FROZEN at v1: any change is a new
     `IDENTITY_HASH_VERSION` through the existing envelope, never an
     in-place re-hash.
  4. Ordering consequence: 3A-5 need NOT move before claim registration;
     claims registered in 3A-2/3A-3 keep byte-stable identities across
     the entity landing.

## c. Reuse-vs-new — DECIDED: `ui-inspection` is its OWN recipe kind

The modeled-but-dead `ui-recipe` kind is NOT reused. Reasons:
1. **A scan has no steps.** `UIRecipeBody` is an ORDERED step model
   (navigate/click/type/select/wait/capture/assert) with a
   framework field (playwright/selenium/lightning_test_service) and step
   coupling validators — Mode B/R3 inheritance. A conformance scan is
   declarative: surface + engine; forcing it into a step model fabricates
   structure the executor would then have to ignore.
2. **A3 mode split.** `ui-recipe` must stay MUTATING (its Mode B future);
   `ui-inspection` is READ_ONLY by declaration. One kind cannot carry both.
3. **Identity separation.** Reuse would entangle the identity/versioning
   semantics of two different test classes; a later Mode B build would
   inherit conformance baggage or vice versa.

`UiInspectionBody` (v1): `{kind: 'ui-inspection', surface: <the same
natural-key value object as §b>, engine: {name: 'axe-core'}}` — minimal and
declarative. Everything execution-related (artifact pins, catalogue
release, stabilisation, persona/auth mode) lives in the MANIFEST (D-461),
never in the recipe body; the recipe names WHAT, the manifest pins HOW.

## d. The registration path — concrete file list (D-305 precedent + recon registries)

1. `alembic/versions/tenant/<new>`: `ALTER TYPE claim_kind ADD VALUE IF NOT
   EXISTS 'conformance-claim'` + `ALTER TYPE recipe_kind ADD VALUE IF NOT
   EXISTS 'ui-inspection'` — plain `op.execute`, transaction-safe PG12+
   (the 20260702_0010 posture; D-459: no autocommit_block; guard test
   already polices this).
2. `primeqa/test_representation/models/claims/ui/conformance_claim.py` —
   `@register_body("conformance-claim", 1)`.
3. `models/claims/ui/__init__.py` — import / `__all__` / archetype union.
4. `models/claims/__init__.py` — top union member.
5. `primeqa/test_representation/models/recipes/ui_inspection.py` —
   `@register_body("ui-inspection", 1)`.
6. `models/recipes/__init__.py` — union + discriminator docs.
7. `primeqa/test_representation/__init__.py` — re-exports (closing the
   D-305 gap pattern: the top-level export was missed there).
8. `models_db.py` — `CLAIM_KIND_ENUM` + `RECIPE_KIND_ENUM` members.
9. `coordinator.py` — `_VALID_CLAIM_KINDS` + `_VALID_RECIPE_KINDS`.
10. `canonicalizers/conformance.py` + registration in
    `canonicalizers/__init__.py` (§b).
11. `primeqa/generation/tools.py` — **deliberately NOT added**.
    Reframed per GO: enumerated-only IS the platform's
    deterministic-before-LLM principle applied to this kind —
    conformance claims are derived (active rules × surface inventory,
    DE-05), so keeping them out of the LLM vocabulary is the principle
    working, not an exception to it. The EXCEPTION is to the TOOLING
    CONTRACT only: the taxonomy drift-guard (`test_taxonomy_contract`)
    currently asserts models_db == coordinator == tools; it gains a
    documented `ENUMERATED_ONLY` set so the contract reads
    "models_db == coordinator == tools ∪ ENUMERATED_ONLY".
12. `primeqa/generation/evidence_contract.py` — per-kind EvidenceTier row
    (engine-observation evidence class).
13. `primeqa/intelligence/readable_body.py` — kind-keyed renderer.
14. `primeqa/intelligence/claim_presentation.py` — display label
    ("Conformance check" family).
15. `primeqa/execution_engine/modes.py` (NEW) + `run.py` gate rewire +
    `executability.py` honest refusal (§a).
16. Tests: `test_substrate_imports.EXPECTED_REGISTRATIONS` (+2),
    `test_taxonomy_contract` (exception), canonicalizer identity-property
    tests (natural-key stability, viewport-only-when-semantic, frozen
    normalisation), `RECIPE_MODES` drift-guard, dispatch-gate tests
    (declared-mode refusal for undeclared kinds).

NOT touched (deliberate): `interpretation/interpreter.py` — verdict logic
is 3A-4 (the result processor); until then conformance claims exist as
enumerable/registerable drafts whose interpretation honestly does not
exist yet. No S3 emission/governance wiring (enumeration is 3A-3's
deterministic generator, not the LLM pipeline).

## e. Non-goals (3A-2)

- No enumeration generator (3A-3). No result processor / interpreter
  (3A-4). No S1 `Surface` / `LightningComponentBundle` entities (3A-5).
- No worker changes of any kind (A10: the browser worker neither reads the
  registry nor learns about claims).
- No manifest-builder changes (the catalogue-pin sourcing remains 3A-3
  scope with enumeration).
- No UI surfaces.
