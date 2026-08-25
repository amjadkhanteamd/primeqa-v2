# LLD 3A-3 — Surface Inventory, Deterministic Enumeration, claim_set Approval

Status: DESIGN (this commit); implementation follows on its own GO.
Derives from: HLD DE-03 (surface inventory input; entity is 3A-5) + DE-05
(enumeration + set approval), signed decisions D2/D3/F10, D-460 (worker
boundary), D-461 (manifest pin), D-281 (recorded membership law), and the
3A-2 identity decision (frozen five-field natural key,
IDENTITY_HASH_VERSION v1). Branch: `phase-3a-substrate`.

## a. Surface inventory — DECIDED: tenant-schema declared-inventory tables NOW

**Lean, defended:** a declared inventory lands now as tenant-schema tables;
the S1 `Surface` entity (3A-5) attaches later by FK — the exact mirror of
the 3A-2 identity decision. Claims already hash the frozen natural key, so
the inventory's job is to be the DECLARED, versioned universe those keys
enumerate from; an S1 entity would add sync-lifecycle machinery this slice
does not need, and waiting for 3A-5 would serialise the programme for no
identity gain (the natural key is already stable; the entity is
operational linkage — LLD 3A-2 §b consequence 4).

Tables (tenant schemas, alembic; names unclaimed by any substrate prefix,
deliberately — the S1 re-homing at 3A-5 attaches to them without rename):

**`ui_surface_inventories`** — the version anchor:
`inventory_version INT PK` (1..n per tenant), `notes`, `created_by INT`
(real user id), `created_at`. Versions are IMMUTABLE: there is no update
path; a change to the declared universe is a NEW version.

**`ui_surface_inventory_members`** — the recorded membership:
PK `(inventory_version, surface_key)` where `surface_key` is the FROZEN v1
canonical string; plus the five v1 identity fields stored EXPLICITLY
(`site`, `path`, `persona_scope`, `record_context_ref`, `viewport`) so the
row is self-describing and queryable; plus operational metadata
(`display_name`, `notes`, `auth_required BOOL`) that is identity-EXCLUDED;
plus `surface_entity_ref` (nullable, EMPTY until 3A-5 — the identity-
excluded FK slot). A CHECK pins `surface_key = ` the canonical composition
of the five fields is enforced in the SERVICE (the canonical function is
Python; the DB stores both forms, the service is the only writer and a
drift test recomputes).

**Immutability rule (D-281 law, verbatim application):** a version's
membership is RECORDED at creation — one insert transaction writes the
anchor + every member — and never recomputed, amended, or pruned.
**Detection later compares within ONE inventory version only**: a surface
present in V7 and absent in V8 is an INVENTORY CHANGE (a declared act),
never a detection result; cross-version comparison is a diff of declared
universes, not a regression signal.

## b. Enumeration — the deterministic cross product (this IS deterministic-before-LLM)

`enumerate_claims(session, *, catalogue_release_id, inventory_version,
persona_scope, actor)` — S3-homed, ZERO LLM involvement:

```
members(release)              # s5 recorded membership (rule_id × version)
  × members(inventory_version) filtered persona_scope
  → per (rule × surface):  conformance-claim (DRAFT)
  → per surface:           its claims' ui-inspection recipes (DRAFT)
```

- **Inputs are PINS**: the catalogue release id (D-461's pin object) and
  the inventory version — never "current ACTIVE rules" or "the inventory".
- **Recipe shape**: one `ui-inspection` recipe PER CLAIM (S2-shaped:
  recipes belong to claims). The obvious optimisation — one scan per
  surface serving many claims — is an EXECUTION concern: the manifest
  builder (3A-4) batches by surface; the representation stays per-claim.
- **Refusal cases (enumerated, all fail-loud):**
  1. *Surface outside inventory* — enumeration draws ONLY from recorded
     members; an explicitly-requested surface key absent from the version
     → refuse naming the key (no silent skip).
  2. *Unpinned release* — a nonexistent `catalogue_release_id` → refuse.
  3. *Retired rule* — a release member whose rule's CURRENT state is no
     longer ACTIVE at that recorded version → refuse with "stale release
     — cut a new catalogue release" (enumerating history is not allowed;
     releases pin INTERPRETATION, new enumeration requires a current
     release).
  4. *Empty cross product* (no members at the persona scope) → refuse,
     never an empty success.
- **Idempotence:** identity = the frozen natural key × rule id, so
  re-enumeration of the same (release, inventory version, persona) yields
  byte-identical identity hashes; the writer checks `compute_identity_hash`
  against existing claims and NO-OPs on match (the D-339 finalize-dedup
  posture applied at the enumeration door). Re-run result: `{created: 0,
  existing: N}` — a transcriptable no-op.
- Claims carry `semantic_conditions = SemanticConditionsBody()` (empty,
  deterministic) and archetype `ui`.

## c. Pre-manifest applicability — computed at enumeration, never by the worker

Per (rule × surface), from rule capability × surface metadata:

| capability | applicability | executable |
|---|---|---|
| AUTO | APPLICABLE | yes |
| AUTO_WITH_ACTION | APPLICABLE | **NOT_EXECUTABLE until Mode B** — enumerated, visible, never silently dropped |
| HUMAN_WITH_CANDIDATE | HUMAN_REVIEW | engine candidate feeds review (3A-4) |
| HUMAN_ONLY | HUMAN_REVIEW | no |
| (surface metadata contradiction, e.g. viewport-semantic rule × viewport-less surface) | NOT_APPLICABLE | no |

**Where it is stored — decided, with one deliberate deviation from the
brief's wording, flagged:** the brief says "stored on the claim";
applicability is stored on the **claim_set MEMBER row** (§d), not as a
claim column. Defence: applicability is an ENUMERATION-time judgment
(capability × surface metadata at that catalogue release) — set-scoped by
nature. A claim-level column would be silently overwritten by the next
enumeration, destroying the per-set audit; the member row is D-281-recorded
and each set carries its own applicability snapshot. The claim itself
stays identity-pure. If the TA wants a claim-level cache column as well,
it is an additive migration later.

**D-460 boundary, restated as a build rule:** the worker receives
manifests naming surfaces to scan — applicability never crosses into the
browser plane; a NOT_APPLICABLE or HUMAN_REVIEW pair simply never enters a
manifest, and the worker cannot tell the difference.

## d. claim_set approval (D3/F10) — one human act, real attribution

Tables (tenant schemas):

**`claim_sets`**: `id UUID PK`, `persona_scope`, `inventory_version` FK,
`catalogue_release_id` (the s5 pin), `standard_profile` ('WCAG22'),
`status` CHECK IN ('draft','approved','revoked'), `created_by`/
`created_at`, `approved_by INT NULL` (REAL user id), `approved_at`,
`member_count INT` (recorded at approval — the batch audit anchor).

**`claim_set_members`**: PK `(claim_set_id, test_id)`; `applicability`
CHECK IN ('APPLICABLE','NOT_APPLICABLE','HUMAN_REVIEW'); `executable
BOOL`; `revoked_at`/`revoked_by NULL`.

**The approval act** — one human act approves (persona × inventory version
× release) as a set:
1. Every APPLICABLE+executable member claim promotes DRAFT → approved
   through the coordinator's existing humans-only authority gate.
2. **Explicitly fixing, not inheriting, the actor gap** (the 2026-08-21
   recon finding: `test_provenance.event_actor` is the literal `"human"`
   with no user id and no batch correlation): the promote path gains
   `event_data.user_id` (the REAL approver) and
   `event_data.claim_set_id` (the batch correlation id) on every
   provenance event — `event_actor` stays `"human"` for D-ε-1 authority
   compatibility; the ATTRIBUTION rides event_data. Plus ONE
   `activity_log` write for the act (`s2.claim_set.approve`, real user
   id, member_count + set id in details) — today's bulk-approval route
   writes none (recon: zero `log_activity` calls in views.py).
3. `claim_sets.status` → 'approved', `approved_by`/`approved_at`/
   `member_count` stamped. Membership was recorded at set CREATION
   (enumeration output), so approval approves EXACTLY what was reviewed —
   never reconstructed from parts (manifests will reference
   `claim_set_id` alone, 3A-4).
- **Per-claim inspectability preserved**: the existing paginated review
  surfaces (50/page) render member claims; no UI redesign.
- **Individual revocation without dissolving the set**: revoking one
  member = deprecate the claim (existing path, reason required) + stamp
  the member row `revoked_at/by`. The set stays 'approved'; the member
  row's revocation is the recorded exception; a manifest built from the
  set excludes revoked members AT BUILD TIME (recorded in the manifest,
  3A-4).

## e. What lands where

- **Tenant alembic migration** (chained on `20260825_0010`):
  `ui_surface_inventories`, `ui_surface_inventory_members`, `claim_sets`,
  `claim_set_members` (+ CHECKs above). Plain DDL, D-459-safe.
- **`primeqa/generation/enumeration.py`** (NEW, S3): the deterministic
  generator — inventory reads, S5 release reads (via
  `knowledge/rule_registry`), applicability computation, idempotent claim
  + recipe writes through the coordinator, claim_set + member creation.
- **`primeqa/test_representation/claim_sets.py`** (NEW, S2): set/member
  repository + the approval service (batch promote with attribution,
  revocation, activity_log).
- **`primeqa/test_representation/coordinator.py`**: promote-path
  extension — optional `event_context` (user_id, claim_set_id) folded
  into `event_data`; default None keeps every existing caller
  byte-identical.
- **Inventory service** (in `claim_sets.py` or a sibling
  `surface_inventory.py`): create-version (one transaction, recorded
  membership), read APIs.
- **One route**: claim_set approval endpoint following the existing
  bulk-approval pattern (`views.py:2922` precedent) with the D-245 tier
  gate — plus the activity_log write the old route lacks.
- **Deferred 3A-2 items that land HERE (named):** none of 3A-2's
  registration files reopen; the two 3A-2 forward-wirings that were
  parked — the browser-plane enqueue policy consult and the manifest
  catalogue-pin sourcing — both move to **3A-4** with the manifest
  builder (per §f), NOT this slice. This slice's file count is the five
  new/changed sources above + migration + tests.

## f. Non-goals (3A-3)

- No execution/manifest wiring changes — the manifest gains its
  `claim_set_id` pin + surface batching + catalogue-pin sourcing in 3A-4
  with the result processor.
- No S1 entities (3A-5 attaches `surface_entity_ref`).
- No review-UI redesign — existing paginated surfaces render member
  claims; approval is one new route on the established pattern.
- No scheduler wiring, no worker changes of any kind (D-460).
- No Mode B: AUTO_WITH_ACTION stays enumerated-but-NOT_EXECUTABLE.
