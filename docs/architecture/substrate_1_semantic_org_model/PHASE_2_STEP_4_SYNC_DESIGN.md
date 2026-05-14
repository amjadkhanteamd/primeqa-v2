# Phase 2 Step 4 — Sync Engine Design

**Status:** Locked design (2026-05-12). Implementation contract for
the sync-layer work on `phase-2-substrate-1-sync`.

**Inputs:** PHASE_2_PLAN.md §§3-4 (sync architecture);
PHASE_2_PLAN_corrections.md §§1-6 (Salesforce constraint family);
DECISIONS_LOG.md D-035 through D-048.

**Predecessor:** `primeqa/integrations/sf_client.py` — 11 fetch
methods covering all 11 entity types Phase 2 needs from Salesforce,
shipped on `main` via PR #1.

---

## 0. Architectural transition

This document marks a deliberate shift in the architectural
character of PrimeQA's substrate work.

Substrate-1's fetch-method layer was built under a "correct by
construction" discipline: each method's design grounded in
live-test-verified Salesforce behavior, with explicit transport
boundaries and the four-category framework (§5 of corrections-log)
governing how methods are shaped. Decisions there optimized for
schema discipline, transparent transport, and platform-fact
accuracy. Iteration was tight: live-probe, implement, commit.

Sync engine work belongs to a different class of system. Its
correctness comes from distributed-workflow reasoning, not from
schema fidelity to an external source. Key concerns shift:

- **Observability** — sync_run rows surface progress to operators
  and downstream consumers; without good progress signals, debugging
  is opaque.
- **Recoverability** — failures mid-sync are operationally
  expected (network drops, AI provider rate limits, sandbox
  transient errors). The system must resume cleanly from partial
  state, not require manual intervention.
- **Eventual consistency** — AI enrichment (embeddings, summaries)
  is decoupled from structural sync. Downstream consumers
  (substrate-2 test generation) must adapt to incomplete enrichment
  state rather than block on it.
- **Operational resilience** — long-running transactions, lock
  retention, vacuum pressure, retry complexity, and blast-radius
  management become first-order concerns.

Decisions from this point forward weigh these operational concerns
heavily. Where substrate-1 chose the strongest correctness guarantee
available, sync-layer work consciously chooses the right consistency
boundary — which is often weaker than maximally strict, but more
operationally healthy.

This framing is permanent. Future Phase 2+ contractors picking up
sync/enrichment work should understand that the reasoning style
here is different from substrate-1's, and design from operational
soundness outward rather than from schema correctness inward.

---

## 1. Scope of this document

This document specifies:

- Sync run state machine (phase tracking, resumability,
  completion criteria)
- Staged transactional boundaries (per-phase atomicity)
- Entity ordering (hybrid hardcoded + FK-assertion)
- Hash-based change detection (skip rules and freshness signals)
- Async enrichment architecture (queue, worker, feature readiness)
- Multi-entity batching (chunk sizes, ordering preservation)
- Deferred / out-of-scope decisions

It does NOT specify:

- Specific function signatures or module structure of the sync
  engine code (deferred to implementation cycles)
- Database schema additions for queue tables (deferred to
  implementation; this document specifies their semantics, not
  their DDL)
- Tenant-vs-shared schema considerations (see Phase 2 plan §1
  and migration history; assumed established)
- Substrate-2 test generation logic (out of scope)

---

## 2. Sync run state machine

Each sync invocation creates a `sync_runs` row tracking the
operation end-to-end. The row's lifecycle:

```
pending → in_progress (structural) → structural_complete → in_progress (enrichment) → enrichment_complete → done
```

Plus terminal-failure states reachable from any in-progress state:
`failed_structural`, `failed_enrichment`, `failed_partial`.

### Phase tracking

The `sync_runs` row carries a `last_completed_phase` column
identifying the most recent entity-type phase that committed
successfully. Phases align with the entity ordering (see §4).

When a sync_run resumes after failure, it reads
`last_completed_phase` and starts the next phase. Phases already
committed are NOT re-fetched or re-processed.

### Completion criteria

- **structural_complete**: all 11 entity-type phases have committed
  per the hardcoded ENTITY_ORDER. AI primitives may be partially
  or fully unpopulated; that's the enrichment layer's concern.
- **enrichment_complete**: every entity row with eligible
  AI primitives populated (embedding NOT NULL, summary NOT NULL
  where applicable). This may complete asynchronously after
  structural_complete; the structural phase does not wait.
- **done**: both above satisfied.

### Per-org coordination

A given `connected_org` has at most one in-progress sync_run at a
time. Concurrent sync attempts for the same org are rejected at
the API layer (separate concern; out of scope here).

A given `connected_org` may have many historical sync_runs. Each
preserves its own state for audit and observability.

---

## 3. Staged transactional boundaries

**Decision:** One database transaction per entity-type phase, not
one transaction per sync_run.

### Rationale

A single 5-10 minute transaction across the entire sync_run is
operationally hazardous: vacuum pressure, lock retention,
retry complexity, blast radius from any late-stage failure, and
observability are all compromised. The single-transaction model
overpays for its correctness guarantee.

The correctness guarantee actually required by D-036 is:
*downstream phases never observe incomplete upstream
dependencies.* Staged transactional boundaries provide this
guarantee without paying the operational cost.

### Mechanics

Each phase begins a fresh transaction, writes all entities of
its type (per the batching rules in §7), and commits before the
next phase starts. If a phase fails, only that phase rolls back;
all prior phases remain committed.

After commit of phase N, the sync_run row's `last_completed_phase`
advances to N. If the sync process dies between phase N's commit
and the sync_run row update, the next phase's first action is to
re-read sync_run state and verify phase N's data is present (via
checksum or row-count probe); the sync_run row update is
idempotent-safe.

### Failure modes

- **Phase fails mid-transaction**: rollback affects only this
  phase. sync_run marks `failed_structural` with the failing
  phase recorded. Re-running picks up from `last_completed_phase`.
- **Phase commits but sync_run update fails**: next sync_run for
  this org detects the inconsistency (phase data present but
  `last_completed_phase` doesn't reflect it) and reconciles
  before proceeding.
- **AI enrichment fails**: structural state is preserved;
  enrichment can retry independently. sync_run marks
  `failed_enrichment` if enrichment cannot complete within
  retry policy.

### Constraint on phase ordering

The strict ordering of phases (§4) means downstream phases assume
upstream phases are present. A Field write assumes its parent
Object row exists; a ValidationRule write assumes its parent
Field rows exist; etc. If a sync resumes mid-sequence, the resumed
phase can trust that all prior phases are committed.

---

## 4. Entity ordering

**Decision:** Hybrid — hardcoded ENTITY_ORDER tuple as the
operational source of truth, plus a sync-startup assertion that
topologically sorting the actual schema's foreign-key declarations
produces a compatible order.

### Hardcoded order

```python
ENTITY_ORDER = (
    "Object",
    "PicklistValueSet",   # canonical name covering both GlobalValueSet and StandardValueSet
    "PicklistValue",
    "Field",
    "RecordType",
    "Layout",
    "ValidationRule",
    "Profile",
    "PermissionSet",
    "User",
    "Flow",
)
```

Per D-037. This order is operationally clear and explicit; reading
sync code immediately reveals what's happening.

> **Correction (2026-05-14, corrections-log §20):** ENTITY_ORDER
> originally listed a 12th entry, `FlowDefinition`, between `User`
> and `Flow`. FlowDefinition is NOT a materialized entity — SPEC.md
> §9 lists exactly 10 Tier-1 entity types and Flow is the
> flow-related one. Substrate-1's bitemporal supersession provides
> Flow's versioning natively (each version deployment supersedes
> the prior Flow record). `fetch_flow_definitions()` remains as a
> Tooling fetcher supplying fetch-time parent context to the Flow
> phase; it is not itself a phase. ENTITY_ORDER is now 11 entries.

### FK-topological-sort assertion

At sync_run startup, before any phase begins, the sync engine
introspects the database schema to extract foreign-key declarations
between entity tables. It topologically sorts the FK graph and
asserts that the hardcoded ENTITY_ORDER is a valid topological
order (i.e., for every FK from A to B, A appears later than B in
the hardcoded order).

If the assertion fails, sync_run aborts before any data is written,
with a clear error message identifying the FK that violates the
ordering. This catches schema drift loudly rather than producing
subtle FK-violation errors mid-sync.

### What "valid topological order" allows

The hardcoded order may be stricter than the FK graph requires
(i.e., it may impose ordering between entity types that have no
FK relationship). That's intentional — the hardcoded order encodes
semantic intent beyond what FKs alone capture. The assertion only
catches the reverse case (hardcoded order violates an FK
dependency).

---

## 5. Hash-based change detection

**Decision:** Hash comparison gates AI regeneration, not structural
write. Every sync updates `last_synced_at` on every entity;
structural fields update only when the hash changed; AI primitives
regenerate only when the hash changed.

### Mechanics

Per D-035, every entity has a `hash_normalized` column storing
SHA-256 of the normalized representation. During sync, for each
fetched entity:

1. Normalize the fetched payload (per `normalization.py`, shipped
   on main).
2. Compute hash.
3. Compare to stored `hash_normalized`:
   - If hash matches: structural fields NOT rewritten;
     `last_synced_at` IS updated; AI primitives NOT regenerated.
   - If hash differs OR entity is new: structural fields rewritten;
     `last_synced_at` updated; entity becomes eligible for AI
     primitive regeneration (see §6).

### Freshness signals preserved

Even unchanged entities have `last_synced_at` bumped on every sync.
This preserves the operator-facing answer to "was this entity
confirmed present at the last sync time?" — distinct from "did
this entity's content change since last sync?"

### Behavior on resumed syncs

Hash comparison happens per entity within a phase. A resumed sync
may re-hash entities from completed phases if those phases need
re-verification (per §3's idempotency reconciliation), but
typically resume skips re-fetching completed phases entirely.

---

## 6. Async enrichment architecture

**Decision:** AI primitive generation is decoupled from structural
sync. Structural sync completes and commits per §3; AI enrichment
runs as a separate process, hydrating entities progressively after
structural data is in place.

### Rationale

AI primitives (embeddings via OpenAI text-embedding-3-small per
D-043; summaries via Anthropic Claude Haiku 4.5 per D-044) are
derived enrichment, not foundational state. The semantic model
exists for attribution, explanation, and retrieval; structural
attribution works without enrichment, retrieval and explanation
benefit from but do not require enrichment.

Coupling AI generation into the sync_run wall-clock makes sync feel
heavy from day one. A 10-30 minute sync to onboard a large customer
org is operationally hostile. Decoupling enables the org to become
usable immediately for deterministic operations; semantic features
improve progressively as enrichment completes.

The architectural shift this represents:

```
Sync engine extracts truth.
Enrichment engine derives intelligence.
```

These are different responsibilities and benefit from being
separate processes with separate failure modes, retry policies, and
observability.

### Queue model

A `ai_enrichment_queue` table (semantics; DDL deferred to
implementation):

- One row per entity needing AI primitive generation
- Columns: `entity_type`, `entity_id`, `primitive_type`
  (embedding | summary), `enqueued_at`, `attempts`, `status`
  (pending | in_progress | succeeded | failed_retryable |
  failed_permanent), `error_text`
- Indexed by status + enqueued_at for worker pickup
- A worker process polls for pending rows and processes them

When a structural sync writes a new entity or updates an existing
one (hash differed), it enqueues rows for that entity's eligible
AI primitives. The structural sync transaction commits before the
worker picks up enqueued rows; the queue insert happens within the
same transaction as the entity update, ensuring atomicity between
structural write and enrichment-pending state.

### Worker process

Initially: a single worker process, simple poll loop, no
concurrency between workers. Per §5 of this document and the
substrate-1 batching philosophy, defer concurrency until measured
necessary.

Retry policy: failed_retryable rows are retried with exponential
backoff up to a configurable retry limit (default 5). After limit
reached, status becomes failed_permanent and an operator alert
fires.

Failure modes:

- AI provider rate limit: status remains failed_retryable; worker
  backs off, retries with longer delay
- AI provider unavailable: same as rate limit; transient failures
  are retryable
- Invalid input (e.g., entity row deleted between enqueue and
  worker pickup): status becomes failed_permanent silently (not an
  error condition, just stale work)
- Malformed AI response (e.g., schema validation fails): status
  becomes failed_retryable for limited retries, then
  failed_permanent with operator alert

### Feature readiness signaling

A `connected_orgs.ai_enrichment_status` column exposes the
aggregate state:

- `none` — no enrichment has been attempted (org just created,
  initial structural sync may still be running)
- `structural_only` — structural sync complete; enrichment queue
  has pending rows
- `partial` — some entities enriched, some still pending
- `complete` — all eligible entities have AI primitives populated

Downstream consumers (substrate-2 test generation, retrieval
features) read this column and adapt:

- `structural_only` → deterministic features fully functional;
  semantic features fall back to keyword/structural alternatives or
  display a "Generating insights..." indicator
- `partial` → per-entity check: use semantic features for enriched
  entities, fall back for unenriched
- `complete` → full feature surface

This is the substitute for "block sync until enrichment is done."
It puts the adaptation burden in the right place: the consumer of
enrichment knows whether enrichment is available, rather than the
producer of enrichment forcing everyone to wait.

### Resumability

The queue model is naturally resumable: pending rows survive worker
restarts; in_progress rows that are abandoned (worker crash) are
reaped by a periodic janitor that returns them to pending if
`started_at` exceeds a timeout.

### Observability

Sync_run rows aggregate enrichment progress (count pending /
in_progress / succeeded / failed_permanent for that sync_run's
contributions to the queue). Operators see enrichment health
per-org and per-sync_run.

---

## 7. Multi-entity batching

**Decision:** Batched inserts per entity type. Within a phase,
collect all entities of that type and INSERT them in chunks of 500.
No concurrency between phases or within a phase.

### Rationale

Per-row INSERT overhead is significant at 7000+ entities; batched
inserts (500-row chunks) reduce round-trip and parsing cost
dramatically without introducing concurrency complexity. The sync
engine's bottleneck is Salesforce fetch latency and AI generation,
not Postgres insert throughput; concurrency in the database write
layer would optimize a non-bottleneck.

### Chunk size

500 rows per INSERT is a conservative default well within
Postgres's parameter limits (Postgres allows up to 65,535
parameters per query; 500 rows × ~20 columns = 10,000 parameters,
leaving headroom). Chunk size is a configuration knob, not a
constant; tune if measurements suggest a different optimum.

### Ordering preservation

Within a phase, all entities are of one type, so ordering
constraints don't apply at the row level. Across phases, ordering
is preserved by §4's hardcoded ENTITY_ORDER. The batching layer
doesn't disturb either guarantee.

### Interaction with phase transactions

Each batched INSERT executes within the phase's transaction (§3).
A phase may issue many batched INSERTs (e.g., 2000 fields fetched
→ 4 INSERTs of 500 rows each); all must succeed for the phase to
commit. Any single INSERT failure rolls back the phase transaction.

---

## 8. Open questions / deferred decisions

The following are explicitly out of scope for this design document
and deferred to implementation cycles or later architectural
reviews:

### Sync trigger mechanism

How sync_runs are initiated (user-triggered via UI, scheduled,
event-driven from Salesforce change feeds) is product surface, not
sync-engine surface. Out of scope.

### Delta sync vs full sync

This document specifies full-sync semantics (every entity
re-fetched from Salesforce, hash compared for change detection).
Delta sync (fetch only changed entities, e.g., via Salesforce's
`LastModifiedDate` filter) is a future optimization layered on top
of this design, not a foundational concern. Hash-gating already
provides most of the benefit; true delta-fetch is a later
refinement.

### Multi-tenancy enrichment quotas

The enrichment queue is per-tenant. Whether tenants share AI
provider rate-limit budgets or have isolated quotas is a
multi-tenancy concern deferred until tenant management is
operationally formalized.

### AI primitive feature flags

Whether to support customer-org-level toggles for AI features
(e.g., a customer wanting structural-only PrimeQA without paying
for embedding generation) is product surface. The architecture
supports this trivially — disabled AI primitives just don't
enqueue — but the UX and billing implications are out of scope.

### Concurrent sync_runs across orgs

Multiple orgs can sync concurrently (different sync_run rows, no
contention). What database connection pooling, worker pool sizing,
and resource limits this implies is operational deployment concern,
not architectural.

### Schema migrations for the queue

The `ai_enrichment_queue` table and the
`connected_orgs.ai_enrichment_status` column DDL is deferred to the
first implementation cycle.

---

## 9. Implementation sequencing

This document does not commit to a specific implementation
sequence, but a reasonable order:

1. Schema migrations for `ai_enrichment_queue` table and
   `connected_orgs.ai_enrichment_status` column
2. Sync engine skeleton: sync_runs lifecycle, phase orchestration,
   FK assertion at startup, per-phase transactions
3. Per-entity-type phase implementations (one cycle each or batched
   as pairs): Object → PicklistValueSet → ... → Flow
4. Enrichment queue worker: poll loop, AI primitive generation,
   retry logic, observability
5. Feature readiness signaling end-to-end (sync engine writes
   `ai_enrichment_status`; downstream readers adapt)
6. Integration testing against the dev sandbox: full sync
   end-to-end, observed sync_run lifecycle, observed enrichment
   progression

Each step lands as one or more commits with the same live-test
discipline that worked for substrate-1 fetch methods (now applied
to sync engine correctness via integration testing rather than live
API probes).

---

## 10. Acknowledgment of scope

This document is a design contract, not implementation. The sync
engine code that follows will reference back to specific sections
of this document (e.g., "per §3, phase X commits independently of
phase Y"); deviations from the design require amending this
document, not silently re-architecting in code.

If during implementation an architectural decision in this document
proves wrong, the right path is: STOP, raise the concern, amend
this document with a documented rationale, then proceed. Same
discipline as the corrections-log for substrate-1.
