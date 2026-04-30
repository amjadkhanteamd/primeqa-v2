# PHASE_2_PLAN.md — Substrate 1, Phase 2

**Project:** PrimeQA v2, Substrate 1 (Semantic Org Model)
**Phase:** 2 — Sync engine + AI retrieval primitives + materialized view
**Branch:** `phase-2-substrate-1` (to be created from `main` at the latest commit)
**Status:** Draft for final user review before lock
**Date:** 2026-04-30
**Foundation:** Derives from `docs/product/PRIMEQA_PRODUCT_DEFINITION.md` v1.0

---

## 1. Purpose

Per the product definition document, Substrate 1 is the semantic org model — the bitemporal entity graph that serves as authoritative source of truth about a connected Salesforce org's metadata. Phase 1 shipped the schema and derivation primitives. Nothing populates them yet.

Phase 2 builds three things:

1. **The sync path** that pulls Salesforce metadata into the model. This is the populating mechanism Phase 1 deliberately did not include.

2. **The AI retrieval primitives** that downstream substrates depend on — embeddings on every entity for semantic search, and lightweight LLM-generated plain-English summaries on validation rules and flows for natural-language discovery and explanation.

3. **The materialized view** that consumers read from.

Phase 2 is foundation work for both Substrate 2 (test generation retrieves entities relevant to JIRA tickets via embedding similarity) and Substrate 4 (attribution maps Salesforce errors to validation rules and flows by exact-text and semantic similarity). Without Phase 2's AI primitives, the later substrates have nothing to retrieve against.

Phase 2 does not deliver customer-visible value on its own. It is infrastructure. The customer-visible value moment — the Priya scene — lands at the end of Phase 5. The substrate-first approach (per product doc §6.2) commits to building Phase 2 properly even though no customer sees it directly.

## 2. Architectural premises

The premises below are the design discipline Phase 2 holds throughout. Many carry over from the prior Phase 2 design discussion; several are new additions that came out of the AI-first / product-doc work.

### 2.1 Generation/execution split holds (D-029)

Per product doc §4.1: the normative model serves test generation. Per-org metadata access for execution is a separate concern, deferred to a later phase. Phase 2 builds only the generation-side substrate.

### 2.2 Sync is per-(org, run); model is shared across orgs (D-030)

Phase 2 supports syncing from any registered Salesforce org into the canonical normative model. Initial seed sync (typically from the customer's recommended base org during onboarding) populates the model. Subsequent syncs from other registered orgs (developer sandboxes, UAT, etc.) update the model in place. The model is one canonical picture; per-entity provenance (`last_synced_from_org_id`) tracks which org each entity was most recently sourced from.

This is single-release by design. Multi-release / multi-version metadata support is **explicitly deferred** (D-041) until a customer drives the requirements.

### 2.3 Bitemporal supersession, no deletion

Sync never hard-deletes from `entities`. Entities that disappear from a sync source get their `valid_to_seq` set; the row persists. Reappearance produces a new entity version. This mirrors Phase 1's lifecycle handling and makes "missing for N consecutive syncs" detection unnecessary.

### 2.4 AI for translation, not invention (D-046; cross-references product rule 7)

This is the foundational discipline of the AI primitives in Phase 2. Structural facts about the org — what objects exist, what fields they have, what types those fields are, what relationships connect them — come from Salesforce's describe and tooling APIs, parsed deterministically, written through Pydantic-validated boundaries. The LLM does not get to invent a field, change a type, or alter a value.

The LLM's role in Phase 2 is bounded:

- Generating plain-English summaries of validation rule formulas and flow logic (where the source content is natural-language-shaped, encoded in a DSL).
- These summaries serve discoverability and explanation. They are not structural claims.

If the LLM fails or produces low-quality output, the sync still completes correctly — structural facts are intact, only the summary may be missing or incomplete. The architectural posture is graceful fallback (product rule 5) over confident wrongness.

### 2.5 Normalization before hashing (D-035)

Hash-based diffing requires per-entity-type `normalize_*` functions producing canonical, stable representations. Without normalization, sorting differences and serialization variance produce phantom changes on every sync. This is a hard requirement of the hashing approach.

### 2.6 Embeddings on every entity for semantic retrieval (D-043)

Every entity in the model carries a vector embedding generated from a deterministic semantic text representation of its structured data. Embeddings enable Substrate 2 (test generation: "find entities relevant to this JIRA ticket") and Substrate 4 (attribution: "find the validation rule semantically matching this error message").

Embedding generation is a post-sync step. Given a fixed input, the same embedding model produces the same output — there is no LLM creativity in the loop. The embedding model is captured per-row (`embedding_model` column) for forward-compatibility when better models emerge or pricing shifts.

### 2.7 Plain-English summaries on validation rules and flows (D-044, D-045)

Two entity types contain natural-language semantics encoded in non-English content: validation rules (formula text like `IsClosed && Amount <= 0`) and flows (decision logic structure). For these, an LLM-generated plain-English summary is stored alongside the source content.

The summary is bounded (target ~100 tokens), grounded in the source content, and serves two purposes: it makes these entities discoverable from natural-language requirements (Substrate 2 use case), and it provides the QA-readable explanation when the entity is matched at attribution time (Substrate 4 use case).

The summary is not the source of truth. The underlying formula text or flow definition remains the truth. The summary is a discovery and explanation aid.

Summaries are stored as columns on the relevant detail tables (`validation_rule_details.plain_english_summary`, `flow_details.plain_english_summary`), not as a separate `entity_interpretations` table. The schema stays simple. We deliberately resist over-engineering: no confidence scores on summaries, no multiple interpretation types per entity, no rich structured semantic extraction. One column with the summary, plus three metadata columns (model, generated_at, prompt_version) for forward-compatibility.

### 2.8 Cost-bounded re-summarization (D-047)

Embeddings and summaries are regenerated only when an entity's hash changes (i.e., when the source data has actually changed). This is the cost discipline.

Realistic cost figures:

- Initial seed sync of a 50K-entity org: ~$30-50 in LLM cost (one-time)
- Subsequent delta syncs (only changed entities): ~$1-5 each
- Embedding generation: <$1 per sync regardless of scale (text-embedding-3-small is essentially free at this volume)

The `prompt_version` field on summary metadata lets us roll out improved prompts. Re-summarization across all entities (when prompts change meaningfully) is a separate manual operation, not part of normal sync.

### 2.9 OAuth tokens stored plaintext in Phase 2; encryption is Phase 5 (D-034)

We are pre-production. Encryption-at-rest is real Phase 5 hardening work. Doing it in Phase 2 is premature. The decision is explicit: **no production org may be connected until Phase 5 ships.** The model is exercised against developer sandboxes only during Phases 2-4. Code paths handling OAuth tokens carry `# TODO Phase 5: encrypt at rest` comments at storage boundaries.

### 2.10 Sync is on-demand, not continuous (D-033)

User-triggered sync only. No cron, no streaming API, no polling. A future phase may add scheduled-fallback syncs once the on-demand path is solid.

### 2.11 Sync atomicity is all-or-nothing (D-036)

A sync run either commits all entity supersessions + edge derivations + embeddings + summaries + matview refresh together, or rolls back entirely. Partial-commit modes are not supported.

A sync run is structured as a small number of carefully sequenced transactions: structural writes in one transaction (entities + detail rows + edges + derivations), AI primitive writes in a second transaction (embeddings + summaries), matview refresh as a third operation. If structural writes succeed but AI primitives fail, the model is still consistent — the entities exist, just without retrieval enrichment. A subsequent sync attempt will fill in the missing AI primitives.

This is a small softening of strict atomicity for the AI-primitive layer specifically, justified because AI primitives are decorative-not-structural and can be filled in retroactively.

### 2.12 Strict entity-type ordering during sync (D-037)

Sync writes entities in dependency order so each entity's parents exist before it does:

```
Object
  → PicklistValueSet
    → PicklistValue
    → Field            (FK to Object, optional FK to PicklistValueSet)
    → RecordType       (FK to Object)
    → Layout           (FK to Object)
    → ValidationRule   (FK to Object)
  → Profile
  → PermissionSet
  → User               (FK to Profile)
  → Flow               (optional FK to Object via triggers_on)
```

`derivation.supersede_and_derive` is called per entity after its detail row is written. Hot reference table rows (`validation_rule_field_refs`, `record_type_picklist_value_grants`) are written between the parent entity's detail row and its `supersede_and_derive` call.

Embeddings and summaries are generated after the structural writes for each entity type are complete (post-pass per type), not interleaved with structural writes.

### 2.13 Multi-release support is deferred (D-041)

The model represents whichever release was most recently synced. A free-form `release_label` column on `connected_orgs` lets customers tag their topology for visibility, but Phase 2 does not consume this label for any logic.

## 3. Schema additions

### 3.1 `pgcrypto` and `pgvector` extensions (step 1A)

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- for gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS vector;     -- for embedding storage and similarity search
```

Both extensions are available on Railway's Postgres. Verify availability at the start of step 1A; if either is unavailable, that's a Railway configuration question to resolve before proceeding.

### 3.2 `tenant_1.connected_orgs`

Per-tenant table capturing which Salesforce orgs this tenant has connected.

```sql
CREATE TABLE connected_orgs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_type VARCHAR(20) NOT NULL,    -- 'production' | 'sandbox' | 'scratch' | 'developer'
    sf_org_id VARCHAR(18),
    sf_instance_url VARCHAR(255) NOT NULL,
    label VARCHAR(255) NOT NULL,
    release_label VARCHAR(100),
    oauth_access_token TEXT,           -- TODO Phase 5: encrypt at rest
    oauth_refresh_token TEXT,          -- TODO Phase 5: encrypt at rest
    oauth_token_expires_at TIMESTAMPTZ,
    last_sync_completed_at TIMESTAMPTZ,
    last_sync_run_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT connected_orgs_org_type_known CHECK (
        org_type IN ('production', 'sandbox', 'scratch', 'developer')
    )
);
```

Any registered org can be the source of any sync run. The customer chooses at sync invocation.

### 3.3 `tenant_1.sync_runs`

Audit log of every sync invocation, success or failure.

```sql
CREATE TABLE sync_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_org_id UUID NOT NULL REFERENCES connected_orgs(id),
    logical_version_seq INT REFERENCES logical_versions(version_seq),
    status VARCHAR(20) NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    entities_inserted INT NOT NULL DEFAULT 0,
    entities_superseded INT NOT NULL DEFAULT 0,
    entities_unchanged INT NOT NULL DEFAULT 0,
    edges_inserted INT NOT NULL DEFAULT 0,
    edges_superseded INT NOT NULL DEFAULT 0,
    embeddings_generated INT NOT NULL DEFAULT 0,
    summaries_generated INT NOT NULL DEFAULT 0,
    summaries_failed INT NOT NULL DEFAULT 0,
    error_message TEXT,
    error_traceback TEXT,
    CONSTRAINT sync_runs_status_known CHECK (
        status IN ('running', 'success', 'partial_success', 'failure')
    ),
    CONSTRAINT sync_runs_completion_implies_terminal CHECK (
        (status = 'running' AND completed_at IS NULL)
        OR (status IN ('success', 'partial_success', 'failure') AND completed_at IS NOT NULL)
    )
);
```

Note the addition of `partial_success` status: structural writes succeeded but some AI primitives failed (e.g., LLM rate-limited, embedding API down). `summaries_failed` counts summary generation failures within a run; non-zero means the run produced incomplete AI primitives but the structural model is still consistent.

### 3.4 `entities` column additions

Six new columns on the existing `entities` table.

```sql
ALTER TABLE entities
    ADD COLUMN entity_origin VARCHAR(20) NOT NULL DEFAULT 'sync';

ALTER TABLE entities
    ADD CONSTRAINT entities_entity_origin_known CHECK (
        entity_origin IN ('sync', 'requirements', 'manual_curation')
    );

ALTER TABLE entities
    ADD COLUMN last_seed_hash VARCHAR(64);

ALTER TABLE entities
    ADD CONSTRAINT entities_hash_only_for_sync CHECK (
        (entity_origin = 'sync') OR (last_seed_hash IS NULL)
    );

ALTER TABLE entities
    ADD COLUMN last_synced_from_org_id UUID REFERENCES connected_orgs(id);

ALTER TABLE entities
    ADD CONSTRAINT entities_synced_from_only_for_sync CHECK (
        (entity_origin = 'sync') OR (last_synced_from_org_id IS NULL)
    );

-- AI primitive columns
ALTER TABLE entities ADD COLUMN semantic_text TEXT;
ALTER TABLE entities ADD COLUMN embedding VECTOR(1536);
ALTER TABLE entities ADD COLUMN embedding_model VARCHAR(50);
ALTER TABLE entities ADD COLUMN embedding_generated_at TIMESTAMPTZ;

CREATE INDEX entities_embedding_idx
    ON entities USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
```

- `entity_origin` (D-031): forward-compat for `requirements` and `manual_curation` paths. Phase 2 only writes `'sync'`.
- `last_seed_hash` (D-032): SHA-256 hex of normalized entity content. Used for diffing on subsequent runs.
- `last_synced_from_org_id` (D-040): per-entity provenance.
- `semantic_text` (D-046): deterministic natural-language representation of the entity, generated from structured data via templating. Input to the embedding step. Stored for transparency and debugging.
- `embedding` (D-043): 1536-dim vector. Dimension matches OpenAI `text-embedding-3-small`. If we change embedding models later, the dimension may change and a migration is required.
- `embedding_model`: identifier of the model that produced the vector (e.g., `'openai/text-embedding-3-small'`). Forward-compatibility for model swaps.
- `embedding_generated_at`: timestamp for staleness tracking.

The ivfflat index supports cosine similarity search. `lists = 100` is conservative for tenants up to ~100K entities; tunable later.

### 3.5 `validation_rule_details` and `flow_details` summary additions

```sql
ALTER TABLE validation_rule_details ADD COLUMN plain_english_summary TEXT;
ALTER TABLE validation_rule_details ADD COLUMN summary_model VARCHAR(50);
ALTER TABLE validation_rule_details ADD COLUMN summary_prompt_version VARCHAR(20);
ALTER TABLE validation_rule_details ADD COLUMN summary_generated_at TIMESTAMPTZ;

ALTER TABLE flow_details ADD COLUMN plain_english_summary TEXT;
ALTER TABLE flow_details ADD COLUMN summary_model VARCHAR(50);
ALTER TABLE flow_details ADD COLUMN summary_prompt_version VARCHAR(20);
ALTER TABLE flow_details ADD COLUMN summary_generated_at TIMESTAMPTZ;
```

Same shape on both tables: the summary text plus three metadata columns for forward-compat (model identifier, prompt version, timestamp). NULL summary means generation failed or hasn't run yet — graceful fallback per D-048.

We also add a separate embedding for the summary text on these two entity types (the entity's own embedding captures the structural picture; a summary embedding captures the semantic-meaning picture, which retrieves differently for natural-language queries):

```sql
ALTER TABLE validation_rule_details ADD COLUMN summary_embedding VECTOR(1536);
CREATE INDEX validation_rule_summary_embedding_idx
    ON validation_rule_details USING ivfflat (summary_embedding vector_cosine_ops);

ALTER TABLE flow_details ADD COLUMN summary_embedding VECTOR(1536);
CREATE INDEX flow_summary_embedding_idx
    ON flow_details USING ivfflat (summary_embedding vector_cosine_ops);
```

The Substrate 4 attribution use case ("find the validation rule semantically matching this error message") is precisely a summary-embedding similarity search.

### 3.6 `mv_active_graph` materialized view (D-039)

Single matview, denormalized projection over the active model state. Now includes the AI primitive columns.

**Includes:**

- All currently-active entities (`entities.valid_to_seq IS NULL`)
- All currently-active edges (`edges.valid_to_seq IS NULL`)
- Each entity's hot detail-table columns
- Each entity's full `attributes` JSONB
- Provenance columns (`entity_origin`, `last_synced_from_org_id`, `last_synced_at`)
- AI primitive columns: `semantic_text`, `embedding`, `embedding_model`
- For validation rules and flows: `plain_english_summary`, `summary_embedding`

**Excludes:**

- Superseded rows
- Bitemporal columns (`valid_from_seq`, `valid_to_seq`)
- Hot reference table rows (accessed via the edges they produce)
- Change log
- Raw OAuth tokens or other secrets

`REFRESH MATERIALIZED VIEW CONCURRENTLY mv_active_graph` runs at the end of each successful sync run. Concurrent refresh requires a unique index, which is defined on `entity_id`.

## 4. Code modules

### 4.1 `primeqa/semantic/normalization.py` (new)

Per-entity-type normalization functions producing canonical dicts suitable for stable hashing. Outputs are deterministic, sorted, and strip Salesforce-internal IDs that change without semantic meaning.

```python
def normalize_object(raw_describe: dict) -> dict: ...
def normalize_field(raw_describe: dict) -> dict: ...
# ... one per entity type (11 total)

def hash_normalized(normalized: dict) -> str:
    """SHA-256 of canonical JSON serialization."""
```

### 4.2 `primeqa/semantic/sf_client.py` (new)

Thin wrapper over Salesforce APIs. Hybrid by entity type:

- REST `describe` API: Object, Field, RecordType, Layout, PicklistValueSet/PicklistValue
- Tooling API: Flow, ValidationRule, Profile, PermissionSet, User

OAuth flow with refresh-token rotation. Salesforce API version pinned in `sf_client.py`.

### 4.3 `primeqa/semantic/semantic_text.py` (new)

Per-entity-type `to_semantic_text(entity_dict) -> str` functions. Deterministic templating; no LLM. Output is a structured natural-language representation of the entity suitable for embedding.

Example output for a Field entity:

```
Salesforce Field 'Industry' on Account object.
Type: picklist.
Label: 'Industry'.
Help text: 'The customer's primary industry classification.'
Allowed values managed by global value set 'IndustryValues'.
Required: false.
Custom: false.
```

The template is deterministic and the output is stable across runs given stable input. This is the input to embedding generation.

### 4.4 `primeqa/semantic/embeddings.py` (new)

Embedding generation and storage.

```python
async def generate_embedding(text: str, model: str = "text-embedding-3-small") -> list[float]:
    """Call OpenAI embeddings API, return vector."""

async def embed_entity(entity_id: UUID, semantic_text: str) -> None:
    """Generate embedding for entity's semantic text, write to entities.embedding."""

async def embed_summary(detail_table: str, entity_id: UUID, summary: str) -> None:
    """Generate embedding for a summary, write to {detail_table}.summary_embedding."""
```

Batched API calls (OpenAI supports batch embedding for cost reduction). Failures are logged but do not crash sync; the structural writes are committed regardless.

### 4.5 `primeqa/semantic/summaries.py` (new)

LLM-mediated plain-English summary generation for validation rules and flows.

```python
async def summarize_validation_rule(rule_data: dict) -> Optional[str]:
    """Call Anthropic Haiku 4.5, return plain-English summary or None on failure."""

async def summarize_flow(flow_data: dict) -> Optional[str]:
    """Call Anthropic Haiku 4.5, return plain-English summary or None on failure."""
```

Prompts are version-controlled. Constrained outputs: max ~150 tokens, must be grounded in the input data (the prompt instructs the LLM to refuse if the input is malformed rather than fabricate). Failures return `None`; the calling code handles by writing NULL summary and incrementing the `summaries_failed` counter on the sync run.

Prompt templates committed under `primeqa/prompts/v1/` with version identifiers. Changes to prompts require a version bump and a re-summarization run for affected entities.

### 4.6 `primeqa/semantic/sync.py` (new)

Orchestrator. Public API:

```python
def run_sync(tenant_id: int, source_org_id: UUID) -> dict:
    """Run a sync from the specified source org into the normative model.

    Phase 1: Structural sync (atomic transaction)
      1. Verify source_org_id is registered
      2. Insert sync_runs row with status='running'
      3. Pull metadata from Salesforce in dependency order
      4. For each entity:
         - Normalize, hash
         - Insert/supersede/skip based on hash diff
         - Call derivation.supersede_and_derive
      5. For entities not in the pull: supersede
      6. Commit structural transaction

    Phase 2: AI primitive sync (separate transaction, partial-success allowed)
      7. Generate semantic_text for changed entities
      8. Generate embeddings (batched)
      9. For validation rules and flows: generate summaries via LLM,
         then embed the summaries
      10. Commit AI primitive transaction (or partial-success on failure)

    Phase 3: Materialized view refresh
      11. REFRESH MATERIALIZED VIEW CONCURRENTLY mv_active_graph
      12. Update sync_runs to terminal status with counters
      13. Update connected_orgs.last_sync_*

    On structural-phase exception: rollback, mark sync_runs failure
    On AI-primitive-phase exception: structural commit holds,
      sync_runs marked partial_success, summaries_failed counter populated
    """
```

The phase split means a sync run that fails on AI primitives still leaves the model in a consistent structural state. Subsequent syncs fill in missing primitives.

## 5. Decisions to capture

To be appended to `docs/architecture/DECISIONS_LOG.md` during Phase 2:

| ID | Title | Summary |
|---|---|---|
| D-029 | Generation/execution split | Cross-references product doc §4.1. |
| D-030 | Sync is per-(org, run); model is shared | Any registered org can be a sync source. |
| D-031 | `entity_origin` column | Forward-compat for requirements + manual curation. |
| D-032 | Hash-based diffing on `last_seed_hash` | Constrained to sync-sourced rows. |
| D-033 | On-demand sync only | No cron/streaming/polling in Phase 2. |
| D-034 | OAuth tokens plaintext in Phase 2 | Encryption is Phase 5; sandboxes only until then. |
| D-035 | Mandatory normalization before hashing | Phantom-change prevention. |
| D-036 | All-or-nothing structural sync atomicity | Softened for AI primitive phase (see D-048). |
| D-037 | Strict entity-type ordering | Object → PVS → PV → Field/RT/Layout/VR → Profile → PS → User → Flow. |
| D-039 | Single `mv_active_graph` matview | Active state, JSONB attributes included, refreshed concurrently. |
| D-040 | Per-entity sync provenance | `last_synced_from_org_id` tracks origin. |
| D-041 | Multi-release deferred | `release_label` is a future-extensibility hook only. |
| D-042 | pgvector for embedding storage | ivfflat indexes for cosine similarity search. |
| D-043 | OpenAI `text-embedding-3-small` for embeddings | 1536-dim, cost-efficient, dominant default. |
| D-044 | Anthropic Claude Haiku 4.5 for plain-English summaries | Cost-bounded, prompt-versioned. |
| D-045 | Summaries as columns on detail tables | Not a separate `entity_interpretations` table. |
| D-046 | AI for translation, not invention | Cross-references product rule 7. |
| D-047 | Re-embed and re-summarize only on hash change | Cost discipline. |
| D-048 | Graceful fallback for AI primitive failures | Structural sync commits, AI primitives mark partial_success. |

## 6. Implementation step plan

| Step | Artifact | Notes |
|---|---|---|
| 1A | Verify `pgcrypto` and `pgvector` available; enable if needed | Railway Postgres extension migration |
| 1B | `connected_orgs` table migration + smoke | Per-tenant, includes `release_label` |
| 1C | `sync_runs` table migration + smoke | Includes AI primitive counters |
| 1D | `entities` ALTER for entity_origin / last_seed_hash / last_synced_from_org_id / semantic_text / embedding / embedding_model / embedding_generated_at | All constraints + ivfflat index |
| 1E | `validation_rule_details` and `flow_details` ALTER for summary columns + summary_embedding + indexes | |
| 1F | Combined commit for 1A-1E | Schema additions for Phase 2 |
| 2A | `normalization.py` + per-type unit tests | Pure functions, table-driven tests |
| 2B | `semantic_text.py` + per-type unit tests | Deterministic templating, tested for stability |
| 2C | `sf_client.py` + integration test against developer sandbox | OAuth flow, REST + Tooling fetches |
| 2D | Combined commit for 2A-2C | Salesforce-side + text-prep machinery |
| 3A | `embeddings.py` + integration test | OpenAI API, batching, failure handling |
| 3B | `summaries.py` + prompt v1 + integration test | Anthropic API, prompt-versioned, fallback |
| 3C | Combined commit for 3A-3B | AI primitive modules |
| 4A | `sync.py` orchestrator (structural phase) | Implements steps 1-6 of run_sync |
| 4B | `sync.py` AI primitive phase | Implements steps 7-10 |
| 4C | Unit tests for sync logic | Hash diff scenarios, partial-success scenarios |
| 4D | Integration test: full sync against developer sandbox | End-to-end with real OAuth + real Salesforce + real LLM |
| 4E | Commit for sync engine | |
| 5A | `mv_active_graph` migration + smoke | Definition, unique index, initial REFRESH |
| 5B | sync.py wired to refresh matview at end of run | |
| 5C | Integration test: sync → query matview → verify shape including AI primitives | |
| 5D | Commit for matview | |
| 6A | Phase 2 close-out: D-029 through D-048 + PHASE_2_SUMMARY.md | |
| 6B | Merge `phase-2-substrate-1` to main | |

Per-step gates apply per the working agreement: schema-inspection prompts before any test asserts production-code-specific names (lesson from Phase 1 step 10).

Realistic timeline estimate: **5-7 days** of focused work, assuming OpenAI and Anthropic API access is set up and a Salesforce developer sandbox is available. The structural sync (steps 2-4) is roughly Phase 2 as originally planned (~3 days). The AI primitive layer (steps 3, 4B) adds ~2 days. The matview and close-out are ~1 day.

## 7. Open questions for final user review

**O-1.** "No production org connection until Phase 5" as a hard rule — confirm acceptable. Constrains Phases 2-4 testing to sandboxes only.

**O-2.** Matview includes the full `attributes` JSONB to support attribute-filter queries without JOINs. Confirm acceptable, or do we want a leaner matview?

**O-3.** Single `mv_active_graph` covering both entities and edges, or separate matviews? Lean: single, refactor only if Phase 3 query patterns demand it.

**O-4.** Salesforce API version pinning. Lean: pin to a current stable version (v60.0 or later as of April 2026), document in `sf_client.py`, plan periodic version bumps as normal dependency-update activity.

**O-5.** Sample developer sandbox availability for steps 2C, 3A-B, 4D. Prerequisite to start hands-on Phase 2 work — confirm one is set up.

**O-6.** OpenAI `text-embedding-3-small` (1536-dim) vs `text-embedding-3-large` (3072-dim). Lean: small, on cost grounds. Quality difference is marginal for Salesforce metadata use cases. Storage cost doubles for large.

**O-7.** Anthropic Claude Haiku 4.5 for summaries — confirm or push toward Sonnet for higher quality. Lean: Haiku. Summaries are short and structural; quality difference unlikely to justify Sonnet's cost premium for this volume.

**O-8.** ivfflat vs hnsw for embedding indexes. Lean: ivfflat for Phase 2 simplicity. hnsw is faster at query time but has more parameters to tune. Switch to hnsw later if query latency demands it.

**O-9.** Prompt version directory location. Lean: `primeqa/prompts/v1/validation_rule_summary.txt`, `primeqa/prompts/v1/flow_summary.txt`, etc. Versioned subdirectories so v2, v3 can coexist.

**O-10.** Phase 2 timeline acceptance: 5-7 days realistic. Confirm or push back. (Original Phase 2 estimate was 2-4 days; AI primitive additions roughly double the scope.)

## 8. Out of scope (explicit)

Phase 2 does not include:

- Per-org persistent metadata caches (the architecture is a single shared model, not per-org)
- Org adapter for execution-time describe (Phase 4 with test execution)
- Requirements-doc ingestion path (Phase 3+; schema accommodates via `entity_origin`)
- Manual curation UI (Phase 3+)
- Multi-tenant orchestration (Phase 4+)
- OAuth encryption (Phase 5)
- Production org connection (Phase 5)
- Cron-based sync fallback (future phase if at all)
- Multi-release / multi-version metadata representation (deferred indefinitely until customer-driven)
- Test generation (Phase 3 — Substrate 2)
- Test execution (Phase 4 — Substrate 3)
- Attribution and explanation (Phase 5 — Substrate 4)
- A separate `entity_interpretations` table (over-engineered for current needs)
- Confidence scoring on summaries (over-engineered; embeddings provide implicit confidence via similarity score)
- Cross-entity context updates triggering re-embedding (only direct hash change triggers re-embedding)

## 9. Approval

Phase 2 work begins on the `phase-2-substrate-1` branch only after:

1. User reviews this plan
2. Open questions O-1 through O-10 are resolved
3. Plan is committed to `docs/architecture/substrate_1_semantic_org_model/PHASE_2_PLAN.md` on the branch as the first commit

After commit, step 1A begins.

---

*End of Phase 2 Plan.*
