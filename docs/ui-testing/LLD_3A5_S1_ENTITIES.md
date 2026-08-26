# LLD 3A-5 — S1 Entities: Surface + LightningComponentBundle

Status: DESIGN (this commit); implementation follows on its own GO.
Derives from: HLD DE-03/DE-11/SF-08, D-308 (the ApprovalProcess
entity-type precedent), D-285 (MIGRATE-FIRST), the 3A-2 identity
decision (natural key IS identity; the entity is operational linkage)
and 3A-3 §a (the reserved `surface_entity_ref` slot). Branch:
`phase-3a-substrate`. This slice CLOSES the substrate phase; the merge
brief (MIGRATE-FIRST 062–064 + tenant chain) follows separately.

## a. Two entity types via the D-308 precedent

The S1 closed lists widen from 13 to 15 — `Surface` and
`LightningComponentBundle` join `entities.entity_type` and every CHECK
that mirrors it. The D-308 recipe, applied verbatim:

- **One tenant migration** (chained on `20260825_0030`): drop +
  re-create the widened CHECKs — `entities` `entity_type`,
  `sync_runs.last_completed_phase`, and `ai_enrichment_queue`'s
  entity-type list (bundle rows are enrichment-eligible later; Surface
  rows are not queued — declared entities carry their own description).
  MIGRATE-FIRST: prod tenants take the migration before any code that
  writes the new types deploys (this branch is unmerged, so the whole
  chain lands together at the merge gate).
- **One sync phase** for `LightningComponentBundle` registered in
  `PHASE_REGISTRY` (`sync/phases.py`), on the ApprovalProcess phase
  shape: full-current-set fetch, SCD-2 close of absent rows, normalize
  → version-hash → upsert. **Surface gets NO sync phase** — it is a
  DECLARED entity (see §b): rows materialize from inventory
  declarations, not from the org, so the sync engine never touches it
  and `last_completed_phase` never carries it (it joins the entities
  CHECK only).

## b. Surface — the inventory rows gain their S1 linkage

`materialize_surface_entities(session, *, inventory_version, actor)`
(S1-side service, called by the inventory service after
`create_inventory_version`; a backfill call covers existing versions):

- One `entities` row per DISTINCT canonical `surface_key` across
  inventory versions (`entity_type='Surface'`, `api_name` = the
  canonical key, display name from the member row). Re-declaring the
  same key in a later inventory version reuses the entity —
  the entity is the continuity object across versions.
- `ui_surface_inventory_members.surface_entity_ref` (the 3A-3 slot)
  is FILLED with the entity id at materialization. Identity-EXCLUDED,
  exactly as reserved.

**What S1 adds beyond the inventory row:** participation in the
bitemporal version machinery (an SCD-2 lifecycle when a surface's
operational description changes), eligibility as an EDGE endpoint
(Surface → renders → LightningComponentBundle is the phase-7 edge this
enables; declared edge specs only, none written in 3A-5), and
addressability by every S1-generic consumer (change_log, affected-tests
queries via `list_tests_affected_by_entity` once claims carry
references — they don't yet, deliberately). **What stays
inventory-side:** declared membership and its D-281 immutability —
which surfaces are IN version N is the inventory's recorded fact; the
entity carries no membership.

**Claim identity UNCHANGED** (the 3A-2 decision, restated as a build
rule): the frozen five-field natural key remains the ONLY hash input at
`IDENTITY_HASH_VERSION` v1; the entity id appears nowhere in any claim
body. A drift guard asserts the conformance-claim canonicalizer's
output fields are exactly `{kind, plimsol_rule_id, surface_key}` before
and after this slice.

## c. LightningComponentBundle — the attribution instrument's data source

Synced from the connected org (SF-08: identity = Salesforce metadata
identity; version = normalised source hash):

- **Sync path:** a new phase in the existing per-category engine
  (after ApprovalProcess in phase order), reading the **Tooling API**:
  `LightningComponentBundle` (DeveloperName, ApiVersion, Description) +
  `LightningComponentResource` (the bundle's source files). `api_name`
  = DeveloperName; the version hash = sha256 over the bundle's
  resources normalised (sorted by FilePath, content newline-normalised)
  — a source edit produces a new S1 version, a no-op resync produces
  none. Detail rows land in a new `lwc_bundle_details` table
  (DeveloperName, api_version, resource manifest + per-file hashes,
  source_hash, target_configs), on the `*_details` pattern.
- **Edge set:** NONE written in 3A-5. The declared future edge is
  `Surface —renders→ LightningComponentBundle` (phase 7, populated
  from scan evidence, not metadata guessing); bundle→Object
  dependency edges are named as a candidate, not designed.
- **What phase-7 detection will read:** the bundle's version history —
  "this component's source hash changed between release R1 and R2" —
  joined to FAIL verdicts whose owning component is that bundle
  (via §d). That is the drift-attribution read; none of it is built
  here.

## d. Ownership upgrade path (DE-11 CONFIRMED tier)

The mapping shape, stated now: a DOM custom-element tag `c-loan-widget`
maps to bundle DeveloperName `loanWidget` by the deterministic LWC
rule — strip the `c-` namespace prefix, kebab→camel. The rule (per the
2026-08-26 ruling): **CONFIRMED requires resolution** — the tag resolves
to a synced bundle row (tag → DeveloperName → entity lookup) — and
**no resolution ⇒ PROBABLE unconditionally**, including when the org
has no bundle rows at all: `c-*` markup we cannot attribute to a known
bundle is client-namespace evidence, never a confirmed identity. The
3A-4 spike-grade behavior (CONFIRMED on the `c-*` marker alone) is
corrected in this slice as a SIGNED-DESIGN CONFORMANCE FIX — it is not
preserved as compatibility. The join is one indexed lookup per FAIL
row — cheap — so 3A-5 implements it. The verdict row gains
`owner_bundle_ref` (nullable), set exactly when the join resolves. Wiring verdicts
to entity references beyond this lookup is the first phase-7-adjacent
task, named.

## e. Scope honesty — what verifies live vs fixture

env-59 is the connected BACKEND org; the portal bundles live on the
Experience Cloud DE org (orgfarm-4399654d2d), which is NOT a connected
org today (it exists for the spike's portal, with portal-USER
credentials only — no API/Connected-App setup).

**Decision, with the lean: DEFER connecting the portal org.**
Connecting it is an ops task (Connected-App + admin credential
provisioning — AK's hands, the same class as the env-59 credential
task), and 3A-5's machinery does not need it to be proven: the sync
phase is org-generic. **Live verification on env-59**: run the bundle
phase against it and record the honest outcome — whatever bundle count
it returns, INCLUDING zero, is a verified live result (the phase ran,
fetched, wrote/or-wrote-nothing, resumed idempotently).
**Fixture verification** covers the semantics env-59 may not exercise:
normalised-hash stability (same source resync → no new version;
one-line edit → new version), the SCD-2 close on a removed bundle, and
the DE-11 CONFIRMED join (planted bundle row + a `c-*` FAIL verdict).
Portal-org connection is recorded as the standing ops item that turns
the CONFIRMED tier live for portal scans — phase-7-adjacent, AK's
call.

## f. Non-goals (3A-5)

- No detection/diff logic, no drift subtraction, no run-vs-run reads
  (phase 7) — the bundle version history is WRITTEN, never yet read.
- No report/listing changes beyond the verdict row's nullable
  `owner_bundle_ref`.
- No worker changes of any kind (the 4244ed1 one-liner closed the
  approved delta; the boundary guards stay green).
- No Surface sync phase, no edges written, no enrichment-queue
  producers for the new types.
- No portal-org connection (ops item, above).
