# LLD — Phase 2.3: per-tenant UI inspection job queue

Status: DESIGN (this commit); implementation follows on GO.
Scope anchor: UI programme HLD/SAD/TAD v1.1, step 2.3.
Branch: `phase-ui-s2-spike`.

## Design stance

The queue MIRRORS the `ai_enrichment_queue` consumer idiom in
`primeqa/worker.py` (claim via `FOR UPDATE SKIP LOCKED`, five-value status
vocabulary, attempts + max cap, reap-to-pending, tenant iteration via
`_discover_tenant_schemas` + `_set_tenant_context`). Every deliberate
deviation is recorded below with its reason. Reference points:

- claim idiom: `primeqa/worker.py:227-268` (`_claim_batch`)
- reap idiom: `primeqa/worker.py:213-224` (`_reap_stalled`)
- failure classification: `primeqa/worker.py:279-298` (`_mark_failed`)
- status vocabulary + timing CHECK:
  `alembic/versions/tenant/20260512_0010_phase2_sync_phase_and_enrichment_queue.py:100-136`
- tenant context: `primeqa/worker.py:148-210`

Naming contract (carried from 2.1): everything the axe engine emits is an
ENGINE OBSERVATION. The word "verdict" appears nowhere in this feature.

## Table: `s4_ui_inspection_jobs` (tenant schemas, alembic)

```sql
CREATE TABLE s4_ui_inspection_jobs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    payload       JSONB NOT NULL,
    status        VARCHAR(20) NOT NULL DEFAULT 'pending',
    attempts      INT NOT NULL DEFAULT 0,
    reaps         SMALLINT NOT NULL DEFAULT 0,
    claimed_by    TEXT,
    claimed_at    TIMESTAMPTZ,
    heartbeat_at  TIMESTAMPTZ,
    enqueued_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at    TIMESTAMPTZ,
    completed_at  TIMESTAMPTZ,
    error_text    TEXT,
    CONSTRAINT s4_ui_inspection_jobs_status_known CHECK (
        status IN ('pending', 'in_progress', 'succeeded',
                   'failed_retryable', 'failed_permanent')
    ),
    CONSTRAINT s4_ui_inspection_jobs_status_implies_timing CHECK (
        (status = 'pending'
            AND started_at IS NULL AND completed_at IS NULL
            AND claimed_by IS NULL AND claimed_at IS NULL
            AND heartbeat_at IS NULL)
        OR (status = 'in_progress'
            AND started_at IS NOT NULL AND completed_at IS NULL
            AND claimed_by IS NOT NULL AND claimed_at IS NOT NULL
            AND heartbeat_at IS NOT NULL)
        OR (status IN ('succeeded', 'failed_retryable', 'failed_permanent')
            AND started_at IS NOT NULL AND completed_at IS NOT NULL)
    )
);
CREATE INDEX s4_ui_inspection_jobs_pickup_idx
    ON s4_ui_inspection_jobs (status, enqueued_at);
CREATE INDEX s4_ui_inspection_jobs_stalled_idx
    ON s4_ui_inspection_jobs (status, heartbeat_at)
    WHERE status = 'in_progress';
```

- `payload` — spike shape: `{"surfaces": [{"key": <surface_key>, "url": <url>}]}`.
  No payload-shape CHECK at spike grade; 2.4's manifest linkage replaces the
  free-form payload, so a shape constraint now would be dead weight.
- `status` — the exact enrichment vocabulary
  (`pending / in_progress / succeeded / failed_retryable / failed_permanent`).
  Mirror, no deltas.
- `attempts` + `UI_INSPECTION_MAX_ATTEMPTS = 5` — mirrors
  `ENRICHMENT_MAX_ATTEMPTS = 5` (`primeqa/worker.py:87`).
- `reaps` — **delta vs enrichment**: counts lease deaths detected by the
  reaper. Deaths stay visible without being charged as attempts (see
  Attempts accounting).
- Lease fields (`claimed_by`, `claimed_at`, `heartbeat_at`) — **delta vs
  enrichment** (which has no lease identity, only `started_at`).
  `claimed_by` is `"<hostname>:<pid>"` — diagnostic, not authoritative;
  the row lock at claim time is the mutual exclusion, `claimed_by` just
  makes a dead lease attributable in the kill test and in ops.
- Timing CHECK — extends the enrichment `status_implies_timing` CHECK with
  the lease-field arms so a reaped row provably drops its lease
  (pending ⇒ all three lease fields NULL). Terminal states keep no
  lease-field constraint: the finalising UPDATE clears them, but the CHECK
  stays permissive there to keep the reap-at-max path simple.

### Reaper rule — heartbeat-based (design delta, reasoned)

A job is stalled iff:

```
status = 'in_progress' AND heartbeat_at < NOW() - HEARTBEAT_STALL_S
```

`HEARTBEAT_STALL_S = 120` (default; module constant).

**Why heartbeat, not elapsed time:** the enrichment reaper declares a row
dead when `started_at` is older than `STALL_THRESHOLD_MINUTES = 10`
(`primeqa/worker.py:213-224`) — fine for enrichment items whose unit of
work is seconds. A browser batch legitimately runs long: N surfaces ×
(navigate + stabilise up to `max_wait_s=30` + axe run) can exceed any
sane elapsed-time bound while being perfectly healthy. The consumer
therefore proves liveness by touching `heartbeat_at` before every surface;
only a heartbeat older than 120 s (≈ 4× the worst single-surface
stabilise budget) marks the worker dead. Elapsed time since `started_at`
is deliberately not consulted.

Reap action (single UPDATE, mirrors the enrichment shape plus the deltas):

```sql
UPDATE s4_ui_inspection_jobs
SET reaps        = reaps + 1,
    status       = CASE WHEN attempts >= :max
                        THEN 'failed_permanent' ELSE 'pending' END,
    completed_at = CASE WHEN attempts >= :max THEN NOW() END,
    error_text   = CASE WHEN attempts >= :max
                        THEN 'reaped at max attempts (stale heartbeat)' END,
    started_at   = CASE WHEN attempts >= :max THEN started_at END,
    claimed_by   = NULL, claimed_at = NULL, heartbeat_at = NULL
WHERE status = 'in_progress'
  AND heartbeat_at < NOW() - make_interval(secs => :stall_s)
```

(When the job returns to `pending`, `started_at` is also cleared to satisfy
the timing CHECK — same as the enrichment reaper's `started_at = NULL`.
The `CASE` arms without `ELSE` yield NULL on the pending path.)

Two deltas vs the enrichment reaper, both deliberate:

1. **The reaper increments `reaps`, never `attempts`.** The enrichment
   reaper returns rows silently; here the death is recorded, but it is not
   charged as an attempt — worker death is frequently an infrastructure
   event (deploy SIGKILL, node OOM) and must not cost the job double.
   Poison batches remain bounded regardless, because every death cycle
   re-claims and the claim charges the attempt.
2. **The reaper caps.** If `attempts` has reached
   `UI_INSPECTION_MAX_ATTEMPTS` when the lease dies, the job parks as
   `failed_permanent` directly (error_text notes the stale heartbeat).
   The enrichment queue caps only in `_mark_failed`, which a dead worker
   never reaches — a poison-pill batch that kills its worker every time
   would cycle forever there. Browser batches are exactly the workload
   where a poison page can hard-kill the process (OOM), so the reaper is
   the place the cap must live.

### Attempts accounting

**Claim-only charging: `attempts` = number of execution starts.** It
increments at claim (mirror of `_claim_batch`) and nowhere else; `reaps`
separately counts detected lease deaths. Arm-B trace: claim #1 → attempts=1;
SIGKILL + reap → attempts=1, reaps=1, status pending; claim #2 →
attempts=2; completion → `succeeded` with attempts=2, reaps=1.

## Table: `s4_ui_inspection_results` (tenant schemas, spike-grade)

```sql
CREATE TABLE s4_ui_inspection_results (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id       UUID NOT NULL
                 REFERENCES s4_ui_inspection_jobs (id) ON DELETE CASCADE,
    surface_key  TEXT NOT NULL,
    attempt      INT NOT NULL,
    observation  JSONB NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT s4_ui_inspection_results_job_surface_unique
        UNIQUE (job_id, surface_key)
);
```

- `observation` — the raw `scan_page()` return dict (engine observations,
  timings, browser/axe versions), or, for a surface that raised,
  `{"status": "ERROR", "error": "<repr>"}`. Either way the row exists —
  a surface-level failure is recorded, not dropped.
- **Finalisation is an UPSERT on `(job_id, surface_key)`:**

```sql
INSERT INTO s4_ui_inspection_results (job_id, surface_key, attempt, observation)
VALUES (:job_id, :surface_key, :attempt, :observation)
ON CONFLICT (job_id, surface_key)
DO UPDATE SET observation = EXCLUDED.observation,
              attempt     = EXCLUDED.attempt,
              created_at  = NOW()
```

  A retried batch re-scans every surface and rewrites completed ones
  idempotently; duplicate rows are impossible **by constraint**, not by
  consumer discipline. The kill test asserts exactly this.
- **2.4 forward note:** manifest linkage extends this table; the unique key
  then becomes `(manifest_id, surface_key, …)` per the HLD. The spike key
  `(job_id, surface_key)` is the degenerate form while a job IS the batch.

## Consumer semantics (`primeqa/browser_worker/consume.py`)

- **Claim**: one `pending`/`failed_retryable` job per tenant per tick —
  the `_claim_batch` SQL shape with `LIMIT 1`, ordered by `enqueued_at`,
  `FOR UPDATE SKIP LOCKED`; flips to `in_progress`, stamps `started_at`,
  `claimed_by`, `claimed_at`, `heartbeat_at`, clears `completed_at`,
  bumps `attempts`.
- **Per surface**: touch `heartbeat_at` (proof of liveness), run
  `spike.scan_page(url)`, `finalize_surface(...)` upsert. A surface-level
  exception writes the `{"status": "ERROR", ...}` observation row and the
  loop continues — **per-surface failure does not abort the batch**.
- **Terminal**: all surfaces attempted → `succeeded` (surface-level
  outcomes live in the result rows; the job status describes the batch
  run, not the observations). A batch-level exception (browser will not
  launch, DB gone) → `failed_retryable` until `attempts >=
  UI_INSPECTION_MAX_ATTEMPTS`, then `failed_permanent` — the `_mark_failed`
  classification verbatim.
- **Tenant context**: exactly the `worker.py` idiom — `SET search_path`
  + `SET app.tenant_id` + the `after_begin` re-apply listener.
- **Invocation**: manual only —
  `python -m primeqa.browser_worker.consume --tenant <id> [--once]`.
  The Railway service CMD stays `sleep infinity` (dormant-first); wiring
  into a polling loop is a later step.

## Explicit non-goals of 2.3

- **No change to `_authorize_dispatch`** (`primeqa/execution_engine/run.py:115-159`).
- **No recipe-kind registration** — `ui-recipe` stays modeled-but-dead.
- **No manifest table** — 2.4.
- No scheduler tick, no enqueue API/UI surface, no `worker.py` edits —
  the queue is dormant until manually driven.

## Verification plan (arm-B kill test)

Runs against a NON-production database only. Enqueue a 4-surface job;
SIGKILL the consumer after ~2 surfaces; verify the dead lease
(`in_progress`, stale `heartbeat_at`, `attempts = 1`, `reaps = 0`, exactly
the completed surfaces in results); `reap_stalled()` → `pending`,
`attempts = 1`, `reaps = 1`; re-consume to completion; assert exactly 4
result rows (UNIQUE key), final status `succeeded`, `attempts = 2`,
`reaps = 1`. Plus the poison-cap test: a job pre-set to `attempts = 5`
with a stale heartbeat is reaped to `failed_permanent`, never `pending`.

Migration ordering: MIGRATE-FIRST (D-285) — the production application of
this revision happens only at branch merge; until then it is applied to
local verification schemas only.
