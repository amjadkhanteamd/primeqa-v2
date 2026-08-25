# Technical Architecture Document — v1.2 (signed design of record)
**Gate 2: FULL APPROVAL (TA, 2026-08-24)** — conditions P-1 (production
authenticated path before first customer-authenticated production run) and
P-2 (stable egress before any allowlisting-dependent customer) attach to
productionisation, not the substrate build. D-1 correction applied below.
**Status:** for TA review with HLD; Phase 2 spike validates the marked estimates. · 2026-08-21
Items marked **VERIFY** are unconfirmed against provider docs/plan and must be checked in Phase 2 — no build decision rests on them yet.

## 1. Service topology (Railway)

**Grounded 2026-08-21 by direct repo read (HEAD 2c4932d):** the Procfile already defines THREE processes — `web` (gunicorn), `worker` (`python -m primeqa.worker`), `scheduler`. The browser worker is a FOURTH process, in its own Railway service because it needs a Chromium image.

| Service | Exists | Runs | Notes |
|---|---|---|---|
| web (Flask/gunicorn) | yes | API, UI, substrate logic | no portal-credential key |
| worker | yes | per-tenant queue consumers (s3_generation_jobs, s4_execution_jobs, s1_sync_jobs, ai_enrichment_queue), heartbeats, SIGTERM/died_reason lifecycle | established idiom the browser worker copies |
| scheduler | yes | cadence enqueuers (e.g. s1_sync_enqueuer_tick) | the future home of scheduled UI runs |
| postgres (+pgvector) | yes | canonical store; **schema-per-tenant** (`tenant_N` + `public`) | tenancy is search_path-based, NOT tenant_id columns |
| browser-worker (dashboard-configured — railway.toml carries no per-service commands, verified) | NEW | Playwright + Chromium image; consumes the UI job queue; ephemeral browser context per batch | holds PORTAL_FERNET_KEY (a NEW second key — see §3) |

**Queue design corrected to the repo's established idiom** (`ai_enrichment_queue` / `s4_execution_jobs` pattern, verified in `primeqa/worker.py`): the UI job queue is a **per-tenant-schema table** (`s4_ui_inspection_jobs` in each `tenant_N`), consumed by a tick that iterates `_discover_tenant_schemas` and sets `search_path` + `app.tenant_id` per tenant — exactly as every existing consumer does. Claim via `FOR UPDATE SKIP LOCKED`; stall-reap returns stuck rows to pending (the existing `_reap_stalled` idiom = the lease); `attempts` + max-attempts cap = the retry policy. **One extension is genuinely new:** per-job `heartbeat_at` — enrichment jobs are seconds long and a fixed stall threshold suffices, but a browser batch runs minutes, so the reaper must distinguish "long-running and alive" from "dead" by job heartbeat, not elapsed time alone. Idempotency key within the tenant schema: (manifest_id × surface × attempt) — the schema IS the tenant boundary, so tenant_id in the key is redundant; isolation is structural. Evidence and results write idempotent upserts on that key; the result processor finalizes per the evidence lifecycle below. **Evidence lifecycle (DB + object storage are not one transaction):** CAPTURED → UPLOADED → VERIFIED (checksum, object key, content type, byte size recorded) → REFERENCED; a result is not COMPLETE until required evidence is VERIFIED; upload failure holds the result incomplete, never falsely complete. Expired lease → reclaim; retry re-executes the same manifest; duplicates impossible by key. Deterministic, observable, no new broker. Migration trigger to a dedicated broker: sustained queue depth or >1 worker instance contention — logged TAD change.

## 2. Images and pinning
- browser-worker Dockerfile: playwright + chromium pinned by exact version; image digest recorded in Environment.
- axe-core: vendored as an S5 artifact (exact version + sha), injected from the artifact store. Never npm/CDN at run time.

## 3. Secrets
- Existing (verified): Fernet decryption of customer connection credentials happens in BOTH web and worker today (`get_connection_decrypted` via the v1 store; the worker boot runs `validate_boot_secrets` fail-closed — F-3). The existing key is therefore platform-wide and CANNOT be the portal-credential key.
- New: **`PORTAL_FERNET_KEY` — a second, distinct key** for portal test-user credentials + TOTP seeds, provisioned ONLY to the browser-worker service. Web writes ciphertext it can never decrypt. Decryption is job-scoped; plaintext never leaves the job process, never appears in logs, manifests, or evidence. Rotation/revocation/expiry per FND-24. JWT_SECRET rotation remains a separate owner-timed item.
- **Constraint (CC recon 2026-08-21): `validate_boot_secrets` mandates identical secrets on every service — per-service separation is absent by design. PORTAL_FERNET_KEY therefore requires a role-aware boot gate (each service validates its role's secret set) as a prerequisite change with its own decision. The spike does not need it (no portal credentials in step one).**
- Boot: the browser worker adopts `validate_boot_secrets` fail-closed and the worker lifecycle contract (heartbeat registration, SIGTERM → died_reason, structured lifecycle logs) verbatim from `primeqa/worker.py`.

## 4. Evidence storage
- Authenticated evidence: private storage with signed, expiring URLs; violating-node fragments + screenshots; retention 90-day/3-release (D5).
- **VERIFY:** current Cloudinary plan supports authenticated/private delivery with signed URLs at our volume; else S3-compatible bucket is the fallback — decision recorded before Phase 3A.

## 5. Network
- Worker egress IP must be allowlistable by clients (v3.1 bot-protection path). **VERIFY:** Railway static outbound IP availability on current plan; if unavailable, options: Railway feature/plan change or egress proxy with static IP. This is a customer-facing fact; settle in Phase 2.

## 6. Environments
- Production (Railway) runs the platform. Targets: client sandbox orgs for pre-release runs; production orgs read-only per policy. Per-customer config: site list, personas, auth mode, bot-protection path, standards profile.

## 6a. Tenancy
Per-tenant setup exists only as tenant-scoped DB records; Railway env holds platform secrets only (DB URL, WORKER_FERNET_KEY, platform JWT). Decryption is job-scoped; a decrypted credential never leaves the job process and never appears in logs, manifests, or evidence. Evidence namespaces, browser contexts, and sessions are tenant-isolated. In-app, each tenant sees the platform egress IPs to hand their security team.

## 7. Cost model — first measured numbers (spike 2.1, Railway production, 2026-08-21)
| Metric | example.com | developer.salesforce.com/docs |
|---|---|---|
| total | 1,388 ms | 3,534 ms |
| navigate / stabilise / inject / axe | 58 / 1,007 / 119 / 69 ms | 1,043 / 1,423 / 111 / 883 ms |
| peak RSS | 40 MB | 70 MB |
| chromium (pinned stack VERIFIED) | 151.0.7922.34 (playwright 1.62.0) | same |
Launch amortizes to ~100ms in-container (10.3s on the dev Mac was host noise). Stabilise floor = the 500ms STRUCTURAL-quiet period (D-1 correction per
Gate 2 sign-off: **structural-quiet is the default convergence mechanism;
network idle is NOT a required Salesforce readiness condition** —
falsified on live Aura, see the session-substrate LLD stabilisation
amendment). Extrapolated commercial unit: 1,500 surface executions ≈ 60–90 min sequential, single worker — viable pre-parallelism. Shadow-DOM piercing demonstrated on Salesforce design-system custom elements; axe `incomplete` bucket confirmed as the Class-3 feed.
**Egress IP observed: 208.77.244.156. Stability across redeploys NOT yet verified — the allowlisting claim waits on that check.**

## 7a. Original measurement plan (remaining items)
Per page: load + stabilise + inject + collect ≈ tens of seconds (measure, do not assume). Capture: runtime/page, cost/page, cost/persona, cost/100 pages, worker utilisation, evidence storage/page, feasible concurrency. Plus the commercial unit: **cost per complete customer regression run** (e.g. 500 pages × 3 personas = 1,500 surface executions).
Spike exit (expanded per TA + P0-5): (1) all cost numbers recorded; (2) same manifest re-run → same results; (3) worker killed mid-job → reclaim + retry, zero duplicates; (4) **real Experience Cloud authenticated login from the production architecture** — TOTP path exercised, egress IP confirmed allowlistable (static IP VERIFY resolved), bot-protection path demonstrated; (5) evidence store proven: private, signed URLs, expiry, tenant isolation, deletion, no public indexing (Cloudinary-vs-S3 VERIFY resolved); (6) artifact SHAs (engine, catalogue, image digest) recorded in evidence.

## 8. Failure surfaces
- Worker crash mid-job → attempt log preserved, job retryable, original failure kept (FND-20).
- Queue poison job → max-attempts then ERROR status, never silent drop.
- Credential invalid → run-level ERROR classification (K5), no page results emitted.
