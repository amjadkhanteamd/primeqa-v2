# LLD Productionisation — the Vault, the Armed Worker, and P-1

Status: DESIGN (this commit); implementation follows on its own GO.
Branch: `phase-prod` (from main @2f847045, Phase 7 merged).
Derives from: the signed TAD v1.2 §3 (PORTAL_FERNET_KEY — "a second,
distinct key … provisioned ONLY to the browser-worker service. Web
writes ciphertext it can never decrypt. Decryption is job-scoped;
plaintext never leaves the job process"), SAD §D4/D8 as amended
("the web tier can never read a portal credential; D-416 exception
scope: external test personas only"), the ui-s2.6 role gate (ALREADY
ENFORCED in `core/secrets.py`: role `browser-worker` requires exactly
`{PORTAL_FERNET_KEY}`), the session substrate's credential laws
(env-at-runtime-only, single-attempt permanence, username treated as
secret), D-460/D-461, and D-464 (next TA gate: productionisation,
vault → P-1). This phase closes P-1 and arms the dormant substrate.

## a. The vault — per-tenant portal personas

**Tenancy (the AK directive: nothing per-tenant outside the tenant):**
a TENANT-SCHEMA table. Personas are client data; they live where the
client's claims live.

**`portal_personas`** (tenant schema, one alembic revision):
- `id UUID PK`; `persona_key TEXT NOT NULL UNIQUE` (the name surfaces
  reference — matches `SurfaceNaturalKey.persona_scope`, e.g.
  `customer`);
- `site TEXT NOT NULL` — the portal site this persona authenticates to
  (the surface-site base ref; the login URL derives from it exactly as
  the spike's `_split_start` does);
- `username_ciphertext`, `password_ciphertext`,
  `totp_seed_ciphertext` — Fernet ciphertext under PORTAL_FERNET_KEY;
  the username IS ciphertext too (the session substrate treats it as a
  secret); `totp_seed_ciphertext` nullable exactly when
  `auth_mode != 'TOTP_PROVISIONED'`;
- `auth_mode` CHECK IN (`'NONE'`, `'TOTP_PROVISIONED'`, `'EXEMPT'`) —
  **`UNSUPPORTED` is REFUSED at registration** (the service returns the
  named refusal; the CHECK makes the row structurally impossible — an
  unsupported MFA posture is a fact to report, never a credential to
  store);
- `active BOOL NOT NULL DEFAULT TRUE`; rotation metadata
  (`registered_by INT`, `registered_at`, `rotated_by`, `rotated_at`);
  `notes`.

**The write path — decided, with the lean: a CLI, not a web surface.**
`python -m primeqa.browser_worker.vault register …` executed IN the
browser-worker service environment (`railway run` against that service,
or locally with the key exported transiently by the operator). Why:
the role gate ALREADY excludes PORTAL_FERNET_KEY from the web role —
enforced code, least privilege — and Fernet is symmetric, so any
web-tier encrypt path would hand web the decrypt capability the SAD
forbids. The CLI keeps ALL portal-crypto material out of the web tier
entirely — a strengthening of the TAD sentence ("web writes
ciphertext") to "web writes NOTHING cryptographic", flagged here for
TA visibility. The rejected alternative — an admin surface encrypting
under an asymmetric public key — introduces a second crypto primitive
outside the platform's Fernet idiom and a second custody surface, to
serve a registration act P-1 performs once. Registration is a
tenant-admin act with REAL-ACTOR audit: the CLI requires
`--actor-user-id` (validated against an active admin+ user of the
tenant) and writes `activity_log` (`ui.persona_registered`, real user
id, persona_key + auth_mode in details — never a secret). Web gets
READ-ONLY persona metadata later (list/status/deactivate — metadata
acts, no crypto); not in this phase (§g).

**The read path:** the worker resolves at LOGIN time from the job's
tenant context — the tenant session the queue already opens. The
manifest's auth descriptor evolves from the spike's
`{"mode": "totp_env"}` to **`{"mode": "vault", "persona": "<key>"}`**
— a descriptor, never a value (the standing law). The worker looks up
the ACTIVE persona row by key, decrypts the three ciphertexts with
PORTAL_FERNET_KEY inside the job process, builds the existing
`Credentials` (redacted repr unchanged), and the proven `login()` runs
untouched. Plaintext never in logs / manifests / evidence / DB —
byte-for-byte the session substrate's rules. `totp_env` becomes
DEV-ONLY: refused when `PLIMSOL_SERVICE_ROLE=browser-worker` (the
production worker accepts vault personas only).

## b. Arming the role gate

- Railway browser-worker service env gains
  `PLIMSOL_SERVICE_ROLE=browser-worker`. `validate_boot_secrets` then
  REQUIRES `PORTAL_FERNET_KEY` there — and only there; the three
  legacy services stay untagged on the legacy path, byte-identical
  (the ui-s2.6 tests already pin this).
- **The key:** generated once via
  `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`,
  set in the browser-worker service env ONLY (dashboard env var —
  the same custody class as the service's start command). It never
  enters `.env`, the repo, or any other service.
- **Rotation posture (FND-24 per the TAD):** a
  `vault rotate-key` CLI run in the worker env with OLD and NEW keys
  present transiently — decrypt-under-old / encrypt-under-new per row,
  `rotated_by/rotated_at` stamped, one activity_log event per persona;
  then the env var swaps and the old key is destroyed. Credential
  (not key) rotation is re-registration of the persona (same CLI,
  `rotated_*` stamped).

## c. The real entrypoint — `sleep infinity` dies

`python -m primeqa.browser_worker` becomes the CONSUMER LOOP (the
spike's URL-probe CLI moves behind a `probe` subcommand). Composed
from proven pieces — nothing new is invented:

1. **Fail-closed boot**: `validate_boot_secrets()` under the
   browser-worker role (raises → the service refuses to start), then
   the egress-IP print (P-2 evidence on every boot, observed).
2. **Per-tenant tick over DISCOVERED schemas**: enumerate
   `tenant_%` schemas that actually exist (`information_schema`), NOT
   the tenants table — the stale-tenant lesson (the recorded FIX-PLAN
   item: 15 active rows, 1 schema) applied from birth; a tenant row
   without a schema is skipped loudly-once per process, never a
   traceback per tick.
3. **Per tenant**: `reap_stalled` (heartbeat-based, poison-cap to
   `failed_permanent` — the proven 2.3 reaper), then `claim_one` →
   `consume_job` (the proven chain: lease → heartbeat-per-surface →
   session per the manifest's auth descriptor → scan → evidence
   upload/verify/REFERENCED → finalize UPSERT).
4. **SIGTERM**: the handler sets a stop flag; the loop exits after the
   CURRENT surface finalizes; an in-flight job's lease returns via the
   reaper with claim-only attempts charging (the proven arm-B path) —
   `died_reason` recorded on the exit log line.
5. Idle sleep between ticks (env-tunable, default ~5s); one process,
   sequential jobs — R1 scale is one worker.

The Railway start command flips from `sleep infinity` to
`python -m primeqa.browser_worker` — dashboard-configured (TAD §2
records this), an ops act at arming time.

**The enqueue boundary (D-245 replicated for ui-inspection):** a
service-layer `enqueue_ui_run(subject, claim_set_id, …)` wraps the
3A-4 builder + enqueue: `authorize(subject, Tier.MEMBER)` decides
allowed (403 envelope on deny), the RECIPE_MODES consult stays (D6),
and the act writes `activity_log` (`ui.run_enqueued`, real user id,
claim_set + manifest ids). The browser plane's env-policy posture is
declared honestly: ui-inspection is READ_ONLY by the mode table and
targets portal surfaces, not org DML — the D-245 `execution_policy` /
`is_production` chokepoint governs S4 org dispatch and does not
apply here; the tier gate + mode table + the manifest invariant
(D-461) are the browser plane's three gates. **Manual for P-1: no
scheduler wiring — invoked runs only.** Scheduling is post-P-1, its
own slice.

## d. Audit wiring — the named spike deferral lands

Worker-side security events flow into `public.activity_log` through
the core service layer (reachable from the tenant session via
search_path; the 3A-3 approval write is the precedent):

| event (`action`) | when | details carry |
|---|---|---|
| `ui.tenant_boundary_refused` | the arm-I deny fires (a caller-expressed foreign-tenant key) | the refused prefix shape, job id — never the key material |
| `ui.login_failed` | a permanent login-failure class | the CLASS (`BAD_CREDENTIAL` / `MFA_FAILED` / `MFA_REQUIRED_NOT_CONFIGURED` / `LOGIN_PAGE_NOT_RECOGNIZED` / `CREDENTIAL_NOT_CONFIGURED`), persona_key, job id — never a credential, never a code |
| `ui.persona_registered` / `ui.persona_rotated` / `ui.persona_deactivated` | vault CLI acts | persona_key, auth_mode, real actor |
| `ui.run_enqueued` | the enqueue boundary | claim_set + manifest ids, real actor |

**Actor semantics:** vault + enqueue events carry the REAL user id
(the 3A-3 attribution posture). Worker-emitted events
(boundary-refused, login-failed) are SYSTEM-AS-ACTOR: `user_id` NULL
with `details.actor = "browser-worker"` — if `activity_log.user_id`
proves NOT NULL at implementation, the fix is widening that column
(nullable), never a sentinel fake user. All writes best-effort
(never fail a job over audit plumbing) but logged loudly on failure.
The tenant admin sees these rows in the existing activity feed —
login failures and boundary refusals become visible operational
facts, not worker-log archaeology.

## e. P-1 — the acceptance run, precisely

**The run the spike deliberately deferred:** an authenticated TOTP
scan of the portal DE org (orgfarm-4399654d2d's Experience Cloud
portal) executed END TO END FROM THE RAILWAY BROWSER-WORKER SERVICE —
real image (playwright 1.62.0 / chromium 151, amd64), real pinned
stack (S5-sourced pins, hash-asserted), real queue (claimed by the
service's consumer loop, not a local process), real vault read
(PORTAL_FERNET_KEY decrypt, job-scoped), real evidence to the live R2
bucket.

**Prerequisites, enumerated:**
1. the vault migration applied (MIGRATE-FIRST, prod tenant_1);
2. PORTAL_FERNET_KEY generated + set on the browser-worker service;
   `PLIMSOL_SERVICE_ROLE=browser-worker` set; start command flipped;
3. the portal persona registered in the vault (the existing
   `~/.plimsol/portal.env` credentials, entered via the CLI — an AK
   act, since the values are theirs to provide);
4. the portal surfaces declared (inventory), enumerated against the
   current catalogue release, approved (one act), manifest built with
   `auth: {"mode": "vault", "persona": …}`;
5. R2 evidence env present on the service (already the case since 2.5
   if unchanged; verified, not assumed).

**Transcript requirements (the P-1 closure record):**
- ONE login (TOTP computed from the vault seed, used once), the batch
  scanned on the single session — the arm-G shape, production-placed;
- verdicts processed (the 3A-4 processor over the run);
- evidence REFERENCED on live R2 (keys + checksums + sizes +
  verified_at — the DB-guarded state);
- ZERO secrets in any output: the DB-hygiene scan (no password / seed
  / username substring in any row), log scan on the service output,
  manifest + evidence JSON clean;
- the worker's EGRESS IP recorded from the service boot log — the
  P-2/D9 evidence, OBSERVED not promised (whether it is stable across
  restarts is exactly what D9 needs measured; this run contributes the
  first data point).

P-1 closes on this transcript; the closure entry cites it and takes
the next D-number.

## f. What P-1 closure unlocks + honest remainders

**Pilot-ready checklist** (a real client onboarding still needs):
their org connected (Connected-App credentials — the env-59-class ops
act), their portal personas registered (vault CLI, their values),
their surfaces declared (inventory version), their standard profile
selected (WCAG22 today; the S5 catalogue is profile-keyed), then:
enumerate → approve → run → verdicts → compare.

**Named non-closures:**
- **P-2/D9**: static egress remains a PLAN decision — this phase
  records observed egress IPs; it does not promise stability or
  purchase a static IP.
- **Scheduling** is post-P-1 (§c) — invoked runs only.
- **Mode B** stays parked (AUTO_WITH_ACTION members enumerated,
  NOT executable).
- **AK ops items unchanged**: the stale `SF_REFRESH_TOKEN` in `.env`;
  the portal org as an S1 connected org (bundle sync for portal
  LWCs — the DE-11 CONFIRMED tier for portal scans waits on it).

## g. Non-goals

- No scheduler automation (no cron, no auto-enqueue on approval).
- No client-onboarding surface beyond the persona-registration CLI —
  the web persona list/deactivate view is a later slice.
- No Mode B.
- No report-UI work.
- No S1 portal-org connection (ops, above).
- The worker-interpretation boundary is untouched: this phase arms
  execution; verdicts stay S6-only, and the string-ban/import-ban
  guards must stay green over every worker change it makes.
