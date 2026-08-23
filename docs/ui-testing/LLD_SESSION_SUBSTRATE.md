# LLD — Session substrate spike: TOTP portal login, batch session reuse, arm G

Status: DESIGN (this commit); implementation follows on GO.
Scope anchor: UI programme HLD/SAD/TAD v1.1 — session substrate (arm G +
the first authenticated Salesforce scan).
Branch: `phase-ui-s2-spike`. Builds on 2.3 (queue) + 2.4 (manifests).

Naming contract unchanged: the worker records observations; login
outcomes are run-level error CLASSES, not page results. "verdict" appears
nowhere.

## HARD SECURITY RULES (verbatim from the brief; binding)

- Secrets live in env at run time only. Never in manifest payloads, DB
  rows, logs, evidence JSON, test files, or any committed file. Manifests
  carry `{"auth_mode": "totp_env"}` — a descriptor, not a value.
- Logging redaction: the login module never logs the password, the seed,
  or a generated code. Log events by name (LOGIN_SUBMITTED,
  MFA_SUBMITTED).
- Generated TOTP codes are computed at login time from the seed (pyotp,
  pinned in requirements-browser.txt) and used once.

Corollaries applied here:
- Env names: `PORTAL_USERNAME`, `PORTAL_PASSWORD`, `PORTAL_TOTP_SEED`.
  Read by `consume.py` only, at login time, held in-memory in a
  `Credentials` dataclass whose `__repr__` is redacted, passed to
  `session.login` by argument, never stored on the job, the result, the
  manifest, or any log record.
- The username is treated like a secret for persistence purposes: it
  does NOT appear in manifest payloads, job rows, result rows, evidence
  JSON, or logs (default per the brief — this LLD does not allow it).
- The portal base URL necessarily appears in manifest surface URLs (it
  is the scan target); it carries no credential.
- Test files use obviously fake values (e.g. `user@example.invalid`,
  `not-a-real-password`, a throwaway base32 seed) and assert the
  redaction contract against them.

## Login flow (`primeqa/browser_worker/session.py`)

`login(context, base_url, start_path, creds) -> LoginOutcome`

1. Open a page in the (fresh) browser context; navigate to
   `base_url + start_path` (an authenticated URL); wait for load +
   networkidle.
2. EXPECT the login form. Inventory the page (see Detection) and
   classify:
   - LOGIN form recognized → fill username + password, click the submit
     control, log `LOGIN_SUBMITTED`.
   - anything else → fail `LOGIN_PAGE_NOT_RECOGNIZED`.
3. After submit, wait for load + networkidle; inventory + classify again:
   - MFA form recognized → if `PORTAL_TOTP_SEED` is unset → fail
     `MFA_REQUIRED_NOT_CONFIGURED`; else compute `pyotp.TOTP(seed).now()`
     at this instant, fill the code field, click the verify control, log
     `MFA_SUBMITTED`; the code is used once and discarded.
   - LOGIN form still recognized → fail `BAD_CREDENTIAL` (the portal
     re-presented the credential form).
   - neither → proceed to step 5 (the portal did not challenge for MFA;
     logged as the event `MFA_NOT_PRESENTED` — an observation, not a
     failure: the session is what it is; MFA enforcement is a site
     policy, not a scanner fault).
4. After the MFA submit, wait for load + networkidle; inventory +
   classify:
   - MFA form still recognized → fail `MFA_FAILED`.
   - LOGIN form recognized → fail `MFA_FAILED` (the portal bounced to
     the credential form after a failed code).
   - neither → proceed to step 5.
5. Landed check: the current URL's path starts with `start_path` and
   neither form is recognized → `LoginOutcome(ok=True, events=[...])`.
   Otherwise → fail `LOGIN_PAGE_NOT_RECOGNIZED` (we are somewhere we did
   not expect and refuse to guess).

Every step runs under the job's `max_wait_s` budget. A timeout at any
step is reported as `LOGIN_PAGE_NOT_RECOGNIZED` with detail
`timeout@<step>` — never as `BAD_CREDENTIAL` or `MFA_FAILED`: we did not
SEE a recognizable outcome, and we do not infer credential quality from
silence.

### Detection is explicit (k16 spirit — refuse ambiguity)

The page is inventoried ONCE per classification by a single in-page
script that lists VISIBLE form controls with their attribute-borne
identity (`type`, `id`, `name`, `placeholder`, `aria-label`, associated
label text, button text/value) and tags each candidate with a temporary
`data-plq-<n>` marker so the follow-up fill/click targets exactly that
element. Classification is pure Python over the inventory:

- **LOGIN form** recognized iff EXACTLY ONE visible `input[type=password]`
  AND EXACTLY ONE visible text-like input (`text` / `email` / untyped)
  whose identity matches `/user ?name|email|login/i` AND EXACTLY ONE
  visible button/submit whose text or value matches `/log ?in|sign ?in/i`.
- **MFA form** recognized iff NO visible password input AND EXACTLY ONE
  visible text/tel/number input whose identity matches
  `/verif|code|^tc$|otp/i` AND EXACTLY ONE visible button/submit whose
  text or value matches `/verify|continue|submit|next/i`.
- Anything else — zero candidates, more than one candidate, both forms
  at once — is NOT recognized. No selector guessing, no retries with
  alternative heuristics, no "try the first password field". The run
  fails `LOGIN_PAGE_NOT_RECOGNIZED`, and the HOLD reports the page's
  control inventory (attribute names only — never values) so the
  contract can be refined deliberately.

## Run-level error taxonomy (arm G)

| class | meaning | job status |
|---|---|---|
| `BAD_CREDENTIAL` | the portal re-presented the credential form after submit | `failed_permanent` |
| `MFA_FAILED` | the portal rejected the code (MFA form persisted, or bounced to login) | `failed_retryable` (clock skew / one-time glitch is possible; the max-attempts cap still bounds it) |
| `MFA_REQUIRED_NOT_CONFIGURED` | the portal asked for a code and `PORTAL_TOTP_SEED` is unset | `failed_permanent` |
| `LOGIN_PAGE_NOT_RECOGNIZED` | the expected form was not recognized at any step (incl. timeouts) | `failed_permanent` |
| `SESSION_LOST` | mid-batch, a surface navigation landed on a recognized LOGIN/MFA form | `failed_retryable` |
| `CREDENTIAL_NOT_CONFIGURED` (approved by AK, 2026-08-23) | `auth_mode=totp_env` but `PORTAL_USERNAME` / `PORTAL_PASSWORD` unset; refusing before any navigation | `failed_permanent` |

Invariants:
- Any class above ⇒ job `failed_*` with `error_text` = the class name
  (plus a short non-secret detail such as `timeout@mfa`), and **ZERO
  result rows are written** for a login-phase failure. A credential
  failure is never page results.
- `SESSION_LOST` mid-batch fails the job at that surface; result rows
  already written for prior surfaces are KEPT (an idempotent re-run
  redoes them via the 2.3 UPSERT). No result row is written for the
  surface that lost the session.
- The `failed_permanent` classes are permanent because re-running the
  same inputs cannot succeed; `MFA_FAILED` / `SESSION_LOST` may.
- `CREDENTIAL_NOT_CONFIGURED` is kept distinct from `BAD_CREDENTIAL`
  because configuration absence and credential rejection are different
  facts with different fixes; conflating them would store a false
  diagnosis.
- **Timeout posture (accepted spike posture, AK 2026-08-23):** mapping
  login-phase timeouts to `LOGIN_PAGE_NOT_RECOGNIZED` → `failed_permanent`
  is conservative and loud — right for a manually driven spike. When
  scheduled runs exist, a transient network blip should not permanently
  fail a job; revisit the timeout mapping at the scheduling step.

## Session semantics

- **One login per job** (the 2.3 batch boundary). `consume_job` opens
  ONE browser + ONE context for the job, logs in once, and every surface
  of the job is scanned in that authenticated context.
- `spike.scan_page` is unchanged except for accepting a pre-authenticated
  `context`: when given, it opens a new page in that context (no launch,
  no close — the job owns the browser); timings simply omit the
  `launch` phase.
- Before each authenticated surface scan, the consumer runs the
  inventory classifier on the landed page: a recognized LOGIN/MFA form
  means `SESSION_LOST`.
- Guest mode (no `auth` key in the manifest) is byte-for-byte the 2.3/2.4
  path: per-surface `scan_page` launching its own browser.

## Auth in the pipeline

- Manifest payload gains an OPTIONAL `"auth": {"mode": "totp_env"}`
  (descriptor only). `enqueue_for_manifest` copies it into the job
  payload (the job executes the manifest).
- `consume_job`: if `payload.auth.mode == "totp_env"` → read the three
  env vars → `session.login(...)` BEFORE the surface loop → on failure
  `mark_failed(error_text=<class>)` with zero result rows → else scan
  each surface in the authenticated context with the `SESSION_LOST`
  check → `mark_succeeded`.
- `base_url` for login is derived from the first surface URL's scheme +
  host; `start_path` is the first surface's path + query.
- Unknown `auth.mode` values → `LOGIN_PAGE_NOT_RECOGNIZED`? No —
  refused at enqueue time by `enqueue_for_manifest` (`ValueError`):
  the manifest is malformed, no job is created.

## Dependencies

- `pyotp==2.10.0` added to `requirements-browser.txt` (browser-worker
  image only; never the web/worker/scheduler images).

## Verification plan (runs ONLY with AK-supplied env; transcripts
redacted)

a. GUEST scan of the authenticated start page (no `auth` key) → record
   its fingerprint.
b. AUTHENTICATED batch: manifest with `auth.mode=totp_env`, two surfaces
   (the start page + the tabset variant) → exactly ONE login sequence
   for the batch (count `LOGIN_SUBMITTED` events) → both surfaces
   scanned, results + fingerprints written.
   LOCK CHECK: (a)'s fingerprint vs (b)'s start-page fingerprint.
   DIFFERENT → site lock proven. SAME → public access is still ON →
   HOLD immediately and flag (site must be locked before this closes).
   (If the site is unlocked, (b) itself fails `LOGIN_PAGE_NOT_RECOGNIZED`
   at step 2 — no login form appears — which is the same flag by another
   route.)
c. Arm G-1: wrong `PORTAL_PASSWORD` → `BAD_CREDENTIAL`, job failed, zero
   result rows.
d. Arm G-2: wrong `PORTAL_TOTP_SEED` → `MFA_FAILED`, same guarantees.
e. DB hygiene: scan the manifest/job/result rows from these runs for the
   username's local part, the password, the seed — all absent.

## Non-goals

- No vault, no per-tenant credential records, no Railway env changes.
- No dispatch / claim-kind work; no `_authorize_dispatch` touch.
- No scheduler/worker wiring — manual consume only; service CMD stays
  `sleep infinity`.
