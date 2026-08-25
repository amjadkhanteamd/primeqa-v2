# LLD 3A-1 — Rule Registry + Catalogue Store (S5, DE-01)

Status: DESIGN (this commit); implementation follows on its own GO.
Derives from: the signed Gate 2 set (`docs/ui-testing/design/` —
HLD DE-01, SAD A4/A10, TAD v1.2 §2). Binding decisions: **D2** (test
identity uses Plimsol-owned rule ids; engine ids and standards are
MAPPINGS, never identity — HLD DE-02 + requirements FND-01), the
**rule lifecycle** DRAFT→REVIEW→APPROVED→VERSIONED→ACTIVE→RETIRED with
ACTIVE immutable, **D-461** (manifest invariant — manifests reference
catalogue versions), **D-460 / SAD A10** (registry is
result-processor-side; workers only ever receive the pinned engine
artifact), **D-459** (no autocommit_block in migrations).
Branch: `phase-3a-substrate`.

## 1. The tenancy split — DECIDED: platform-global catalogue, tenant-schema custom rules

**Decision.** The standard catalogue — `s5_rules`, `s5_rule_versions`,
`s5_engine_bindings`, `s5_standard_maps`, `s5_artifacts`, and the
catalogue releases (§2.6) — lives in the **`public` schema,
platform-global**. CUSTOM rules (R3's predicate vocabulary, future) will
live in **tenant schemas** when they arrive; nothing tenant-scoped ships
in 3A-1.

**Defence.**
- *One truth, versioned once.* WCAG 2.2 and the axe rule inventory are
  platform facts, not tenant opinions. The in-repo precedent is exact:
  `llm_models` (migration 061) is deliberately public — "NOT
  tenant-scoped by design: the model catalog is a platform fact". A
  per-tenant copy of WCAG mappings would be N copies of one truth that
  drift.
- *Cross-tenant comparability.* A `PLM-A11Y-001` verdict means the same
  thing for every tenant only if the id resolves to one catalogue row.
  Per-tenant catalogues would make rule ids tenant-relative and destroy
  benchmarkability and support conversations ("your PLM-A11Y-007" vs
  "ours").
- *Schema mechanics.* Tenant sessions run with
  `search_path = tenant_N, public` — catalogue READS work unchanged from
  every tenant context with zero new plumbing; the browser-plane tenant
  boundary (arm I) is unaffected because the catalogue carries no tenant
  data.

**Identity consequences (explicit).**
- Claim identity (DE-02) = `plimsol_rule_id × surface_id`; the rule half
  always resolves against the ONE public catalogue. Identity hashes are
  therefore tenant-portable by construction.
- Future custom rules get a DISJOINT id namespace — `PLM-CUST-nnn` —
  minted and stored in the tenant schema. The namespace split (A11Y vs
  CUST prefix) is the collision guard: a public rule id can never be
  shadowed by a tenant rule id, and a claim's rule id alone tells you
  which store resolves it. (Reserved now; not built in 3A-1.)

**Audit consequences (explicit).**
- Catalogue lifecycle transitions are PLATFORM-admin actions:
  superadmin-gated, written to `activity_log` (public, per the house
  rule "every destructive/admin action writes to activity_log via the
  service layer"). They are NOT tenant events.
- Tenant reproducibility does not depend on catalogue stasis: every run
  pins a catalogue release id in its manifest (§5, D-461), so tenant
  evidence replays against the pinned release even as the global
  catalogue advances. Tenant-visible audit is the pin in the manifest,
  not the platform lifecycle log.

## 2. Tables (public schema, migration 062 — §6)

### 2.1 `s5_rules` — the identity anchor
| column | type | notes |
|---|---|---|
| `rule_id` | VARCHAR(32) PK | the Plimsol id, `PLM-A11Y-nnn`; CHECK `rule_id ~ '^PLM-[A-Z0-9]+-[0-9]{3}$'`; NEVER renumbered |
| `owner` | VARCHAR(16) NOT NULL DEFAULT 'plimsol' | CHECK IN ('plimsol') for 3A-1; 'tenant' reserved for R3 |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |

Descriptive fields live on versions (they may legitimately change
between versions); the rules row is the immutable identity.

### 2.2 `s5_rule_versions` — immutable version rows + the lifecycle
| column | type | notes |
|---|---|---|
| `rule_id` | FK → s5_rules | composite PK (rule_id, version) |
| `version` | INT NOT NULL | 1..n |
| `name` | VARCHAR(200) NOT NULL | |
| `description` | TEXT NOT NULL | |
| `automation_capability` | VARCHAR(24) NOT NULL | CHECK IN ('AUTO','AUTO_WITH_ACTION','HUMAN_WITH_CANDIDATE','HUMAN_ONLY') |
| `human_review_required` | BOOLEAN NOT NULL | |
| `state` | VARCHAR(12) NOT NULL | CHECK IN ('DRAFT','REVIEW','APPROVED','VERSIONED','ACTIVE','RETIRED') |
| `state_changed_at` / `created_at` | TIMESTAMPTZ | |
| `created_by` / `state_changed_by` | INT (user id) | REAL actor attribution from day one (the Gate 2 audit-gap lesson: never the literal "human") |
| `seed_provenance` | JSONB NULL | §3: engine rule ids + derivation note for seeded rows |

**Lifecycle enforcement — DECIDED: service layer owns transitions; the DB
enforces the structural invariants.** Split and justification:
- **DB-enforced** (cannot be violated by any code path):
  - `UNIQUE (rule_id, version)` (the PK);
  - **at most one ACTIVE version per rule**: partial unique index
    `ON s5_rule_versions (rule_id) WHERE state = 'ACTIVE'`;
  - the `state` CHECK vocabulary.
- **Service-enforced** (`primeqa/knowledge/rule_lifecycle.py`, the ONLY
  write path): transition legality
  (DRAFT→REVIEW→APPROVED→VERSIONED→ACTIVE→RETIRED, no skips), ACTIVE
  immutability (a content change on an ACTIVE version is refused — the
  service only offers `new_draft_version()`), actor + activity_log on
  every transition, superadmin gating.
- **Why this split:** transition legality needs actor, authorization and
  audit context, which live in the service layer — the repo's
  established pattern (the S2 coordinator's `promote_claim_to_approved`
  authority gate; "three gates, three errors"). Postgres could encode
  the state machine only via triggers, which this codebase deliberately
  avoids (service-layer writes + repository pattern). The DB keeps the
  two invariants whose violation would corrupt READERS (duplicate
  versions, two ACTIVEs); a service bug can then at worst stall a
  transition, never fork the catalogue. A guard test pins the service as
  the only writer (grep: no other module writes s5_rule_versions).

Activating vN moves the previously-ACTIVE version (if any) to RETIRED in
the same service transaction — the partial unique index makes the swap
atomic-or-refused.

### 2.3 `s5_engine_bindings` — Plimsol rule version → engine rule(s)
| column | type | notes |
|---|---|---|
| `id` | BIGSERIAL PK | |
| `rule_id`, `rule_version` | FK → s5_rule_versions | |
| `engine` | VARCHAR(32) NOT NULL | 'axe-core' in R1 |
| `engine_version` | VARCHAR(32) NOT NULL | '4.13.0' |
| `engine_rule_id` | VARCHAR(128) NOT NULL | e.g. 'image-alt', 'label' |
| | | UNIQUE (rule_id, rule_version, engine, engine_version, engine_rule_id) |

Many-to-many by construction: one Plimsol rule may bind several engine
rules; one engine rule may serve several Plimsol rules. The result
processor resolves observations through this table (engine_rule_id ×
engine_version → Plimsol rule versions); an engine observation with no
binding is UNMAPPED — an honest state, never silently dropped (surfaced
by the read API, §4).

### 2.4 `s5_standard_maps` — rule → criterion per standard (MAPPING, per D2)
| column | type | notes |
|---|---|---|
| `id` | BIGSERIAL PK | |
| `rule_id`, `rule_version` | FK → s5_rule_versions | version-scoped: a mapping correction is a rule-version event |
| `standard` | VARCHAR(24) NOT NULL | CHECK IN ('WCAG22','EN301549','SECTION508') — WCAG22 seeded first; the others are vocabulary-reserved, unseeded |
| `criterion` | VARCHAR(32) NOT NULL | e.g. '1.1.1' |
| `level` | VARCHAR(3) NULL | 'A'/'AA'/'AAA' where the standard has levels |
| | | UNIQUE (rule_id, rule_version, standard, criterion) |

### 2.5 `s5_artifacts` — the pinned engine bundle
| column | type | notes |
|---|---|---|
| `id` | BIGSERIAL PK | |
| `kind` | VARCHAR(24) NOT NULL | 'engine' in R1 |
| `name` | VARCHAR(64) NOT NULL | 'axe-core' |
| `version` | VARCHAR(32) NOT NULL | '4.13.0'; UNIQUE (kind, name, version) |
| `sha256` | CHAR(64) NOT NULL | the integrity pin |
| `repo_path` | VARCHAR(255) NOT NULL | `primeqa/browser_worker/vendor/axe.min.js` |
| `source_url`, `retrieved_at`, `byte_size` | | provenance (from the spike's VERSIONS.md) |

**Bytes location — DECIDED: repo-vendored file referenced by hash; NOT
DB bytea.** Justification against the two governing rules:
- *No-runtime-fetch (SAD A4):* the worker image bakes the vendored file
  at build; the worker fetches nothing at run time — not from a CDN and
  equally not from the DB. A bytea column would create exactly the
  fetch-at-runtime channel A4 prohibits (and hand workers a DB read the
  A10 boundary says they must not have).
- *Manifest invariant (D-461):* integrity comes from HASH EQUALITY, not
  from where bytes rest: the manifest pins (name, version, sha256) from
  this table; the worker asserts sha256(baked file) == manifest pin
  before injecting (§5). The store row is the authority; the repo file
  is the distribution; git + Docker image digest are the provenance
  chain. A gate test asserts store-row sha256 == hash of the repo file,
  permanently (§5).

### 2.6 `s5_catalogue_releases` + `s5_catalogue_release_members` — the D-461 pin target
**This is the one deliberate extension beyond DE-01's five tables —
surfaced as a fork, with my lean built in.** D-461 requires manifests to
reference "catalogue versions", and DE-05's claim_set carries a
"rule-catalogue version". That needs a first-class release object:

| `s5_catalogue_releases` | |
|---|---|
| `id` | BIGSERIAL PK — THE value manifests pin |
| `created_at`, `created_by`, `notes` | |
| `content_hash` | CHAR(64) — sha256 over the ordered member list |

| `s5_catalogue_release_members` | |
|---|---|
| `release_id` FK, `rule_id`, `rule_version` | composite PK |

Membership is RECORDED at release creation (the set of ACTIVE rule
versions at that instant), never recomputed at read time — the D-281
drift-immunity law, verbatim: recomputing "what was active then" from
`state_changed_at` timestamps is reconstruction, and reconstruction
drifts. *Rejected alternative:* pinning the full (rule_id, version) list
inline in every manifest — self-contained but duplicates the list into
every run payload and gives audit no single release object to reason
about; the worker never needs the list anyway (A10: rules are
result-processor-side). If the TA prefers strict five-table DE-01
conformance, the fallback is the inline-list pin; the LLD's lean is the
release pair.

## 3. Seeding strategy (R1)

- **Derivation:** the seed derives from axe-core 4.13.0's own rule
  inventory (`axe.getRules()` of the pinned artifact, executed ONCE at
  seed-authoring time on the vendored file — never at runtime) filtered
  to WCAG-mapped rules, joined to WCAG 2.2 criteria from axe's rule
  tags. Each seeded rule receives a frozen `PLM-A11Y-nnn` id assigned
  deterministically (engine rule ids sorted lexicographically, numbered
  from 001) — ids NEVER renumber afterwards.
- **Reviewable fixture, not runtime import:** the derivation output is a
  checked-in fixture `migrations/seeds/s5_rule_seed_axe4130_wcag22.json`
  (engine rule id, proposed PLM id, name, description,
  automation_capability, WCAG criteria, seed provenance). Migration 062
  ships the DDL; a companion idempotent seed SQL (numbered 063, or 062's
  second half — decided at implementation, both idempotent) INSERTs
  from that reviewed content with `ON CONFLICT DO NOTHING`. The PR diff
  IS the review surface for every seeded rule.
- **Lifecycle honesty:** seeded rows land as version 1 in state
  **ACTIVE** directly. This is a declared bootstrap exception to the
  DRAFT→…→ACTIVE machine, justified: the fixture PR review + the Gate
  2-signed derivation constitute the DRAFT→REVIEW→APPROVED steps, and
  `seed_provenance` records exactly that. Every post-seed change goes
  through the service lifecycle with no exceptions.
- **Bootstrap non-repeatability guard (amended per GO):** the service
  layer REFUSES direct-to-ACTIVE for any rule version not carrying
  bootstrap seed provenance — there is no service path that creates a
  version in any state but DRAFT, and an ACTIVE insert without
  `seed_provenance.bootstrap = true` is refused outright. The seed
  migration is the only path that ever created ACTIVE without the
  transition chain, and it cannot recur (idempotent `ON CONFLICT DO
  NOTHING` + the guard). Tested.
- **Set-review principle (amended per GO):** the seed-set review follows
  the same principle as D3's claim_set approval — set-level human review
  (the fixture PR) with per-item inspectability (the fixture is
  line-reviewable).
- **Capability split:** each seeded rule's `automation_capability` is
  assigned in the fixture (axe-automated rules = 'AUTO';
  axe `incomplete`-prone rules = 'HUMAN_WITH_CANDIDATE' — the spike's
  Class-3 feed finding), which is what makes the SF-14 accessibility
  coverage's "automated vs human-only, honestly split" computable.
- **ACC-05 mapping:** ACC-05 is the R1 automated rule list (requirements
  baseline: "ACC-05/06 automated and interaction rule lists stand"). The
  seed covers ACC-05 as: every ACC-05 automated rule maps to ≥1 seeded
  PLM rule with capability 'AUTO'; the implementation PR includes the
  explicit ACC-05 → PLM-id cross-list as part of the fixture review so
  the coverage claim is inspectable, not asserted.

### 3a. ACC-05/WCAG-2.2 collision ruling + item-level cross-list (2026-08-24)

**Collision + ruling:** the duplicate-id engine rules are excluded from the
WCAG22 seed (criterion 4.1.1 is removed in WCAG 2.2) but remain VALID RULE
ATOMS — EN 301 549 / Section 508 bind WCAG 2.0/2.1 where 4.1.1 persists.
Plan: append `PLM-A11Y-069` (duplicate-id) and `PLM-A11Y-070`
(duplicate-id-active) — never renumber — when the first non-WCAG22 standard
map lands (R2); ACC-05's "duplicate IDs" item then closes fully via those
maps. **Fork B / D2: rules are atoms, standards are maps — this collision is
the model's first live proof** (the atom outlives the standard that dropped
its criterion).

**Item-level cross-list (baseline v3.1's 16 items, recorded in the fixture
provenance with per-item PLM ids):** ground truth against the pinned
inventory is **12 CLOSED + 1 PARTIAL + 3 OPEN** — not the expected 15/16:
- PARTIAL — *duplicate IDs*: `duplicate-id-aria` (4.1.2) IS seeded; the
  generic pair awaits the 069/070 plan above.
- OPEN — *headings*: axe's heading rules are best-practice or experimental
  (`p-as-heading` is wcag131 + experimental), so the seed criterion excludes
  them all. OPEN — *landmarks*: every landmark/region rule is
  best-practice-tagged. OPEN — *status messages*: axe 4.13.0 maps ZERO rules
  to 4.1.3. Closure paths (future lifecycle work, not seed edits): Plimsol
  rules bound to those engine rules under Plimsol's own criterion judgment
  (rules are atoms), or HUMAN_* capability rules where no automation exists.

## 4. Read API + write paths (`primeqa/knowledge/`)

New modules (S5's package; result-processor-side only, per A10):
- `rule_registry.py` — READ:
  - `active_rules_for_profile(session, standard_profile) -> list[RuleRead]`
    — S3 enumeration's feed (DE-05): ACTIVE versions joined through
    `s5_standard_maps` for the profile's standard(s).
  - `rule(session, rule_id, version=None) -> RuleRead` — result
    processor + UI: metadata by Plimsol id (default: ACTIVE version).
  - `bindings_for_engine(session, engine, engine_version) ->
    dict[engine_rule_id, list[(rule_id, version)]]` — the observation→
    rule resolution map; engine rules absent from the dict are UNMAPPED
    and reported as such.
  - `pinned_artifact(session, kind, name) -> ArtifactRead` — manifest
    building reads the pin here.
  - `release(session, release_id)` / `create_release(...)` lives in the
    lifecycle module (a release is a write).
- `rule_lifecycle.py` — the ONLY write path: `create_rule`,
  `new_draft_version`, `submit_review`, `approve`, `mark_versioned`,
  `activate` (atomic predecessor-retire), `retire`, `create_release`.
  All superadmin-gated, all activity_log-audited with real user ids.
- Repository pattern per house style; all queries through the service
  layer; reads are tenant-context-agnostic (public tables — §1).

## 5. Changes in the spike modules

- **The vendored artifact becomes THE store-referenced artifact.**
  `primeqa/browser_worker/vendor/axe.min.js` (sha256
  `c24f097bd2f451d4f933e8bc7d8d539f8672a2ebcb5cc9f9f3eec8ca9470a0c1`,
  already documented in vendor/VERSIONS.md) is row 1 of `s5_artifacts`.
  **Hash equality asserted twice:** (i) a permanent gate test hashes the
  repo file and compares it to the seed fixture's sha256 (pure,
  network-free, no DB); (ii) at scan time the executor asserts the
  manifest's artifact pin equals the store row it was built from
  (implementation slice of the executor change, later in 3A).
- **Manifests gain catalogue pins sourced from the store:** the manifest
  payload `pins` block adds `rule_catalogue_release` (the
  `s5_catalogue_releases.id`) and keeps the artifact triplet (name,
  version, sha256) — now READ FROM `s5_artifacts` at manifest-build time
  instead of hard-coded (the spike's `session_arms.py` hard-codes were
  scaffolding). Workers still receive only the engine artifact + the
  manifest: no registry read from the worker, ever (A10).

## 6. Migration plan

- **Chain position — public numbered SQL, `migrations/062_s5_rule_registry.sql`**
  (next number verified: 061 is the current max; the 053 duplicate is a
  known wart, not a blocker). NOT the tenant alembic branch: these are
  platform-global public tables, and the tenant branch would wrongly
  stamp a copy of the catalogue DDL into every `tenant_N` schema. NOT the
  `shared` alembic branch: that is control-plane (D-015) and this is
  substrate catalogue data.
- **MIGRATE-FIRST (D-285):** 062 (+ seed) applies to production BEFORE
  any reader deploys; verified on a fresh local scratch DB first;
  idempotent per the 016+ convention (`CREATE TABLE IF NOT EXISTS`,
  `CREATE INDEX IF NOT EXISTS`, seed `ON CONFLICT DO NOTHING`).
- **D-459 guard:** numbered SQL migrations run via psql — alembic's
  `autocommit_block` cannot appear by construction; the existing guard
  test continues to police `alembic/versions/**`, and nothing in this
  slice adds an alembic revision.

## 7. Non-goals (3A-1)

- No custom-rule authoring (R3; namespace reserved in §1 only).
- No criterion/standards UI.
- No second engine (the schema's `engine` column is ready; nothing else).
- No worker-side registry access of any kind (A10 — hard boundary).
- No changes to dispatch, queue, evidence, or session modules beyond the
  manifest-pin sourcing named in §5 (which itself lands with the
  manifest-builder slice, not 3A-1).
