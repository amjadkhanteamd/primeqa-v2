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
   `base_url + start_path` (an authenticated URL) with the amended
   navigation policy (`domcontentloaded` + bounded retry — see
   *Stabilisation amendment*), then apply the structural-quiet settle.
2. EXPECT the login form. Inventory the page (see Detection) and
   classify:
   - LOGIN form recognized → fill username + password, click the submit
     control, log `LOGIN_SUBMITTED`.
   - anything else → fail `LOGIN_PAGE_NOT_RECOGNIZED`.
3. After submit, apply the structural-quiet settle; inventory + classify again:
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
4. After the MFA submit, apply the structural-quiet settle; inventory +
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

## Stabilisation amendment (2026-08-23 — supersedes the 2.1/2.4 stabilise policy)

The 2.1 stabilise policy (`networkidle` + 500ms quiet on **all** DOM
mutations) was validated on static fixtures. The first live Salesforce
Experience Cloud (Aura/LWC) page falsified it — a genuine spike outcome,
diagnosed 2026-08-23:

- `goto(wait_until="load")` timed out at 45s; `domcontentloaded` succeeded
  once and timed out twice across three tries (guest→login client-side
  redirect + SPA bootstrap over persistent connections).
- longest mutation-free gap over a 12s window: **0ms** — Aura mutates the
  DOM perpetually (live regions, timers, re-renders), so an all-mutation
  500ms quiet gate can never fire on the target application class.

This section is the authoritative stabilisation contract for the browser
worker; it supersedes the 2.1 `_DOM_QUIET_JS`/networkidle description and
the 2.4 LLD's `wait_for_load_state("networkidle")` mention (cross-ref note
added there).

### 1. Navigation: `domcontentloaded` + bounded retry

Navigate with `goto(wait_until="domcontentloaded")`, not `"load"`.
Rationale (recorded): Experience Cloud holds persistent/streaming
connections, so the `load` event may never fire. On a navigation timeout,
retry with a **fresh navigation** up to **2** more times (max 3 total
attempts); exhausting them is the honest failure (scan → `NOT_REACHED`;
login → `LOGIN_PAGE_NOT_RECOGNIZED` detail `timeout@<step>`). Retry
applies to **navigation only** — never to credential submission.

### 2. Structural-quiet gate (replaces all-mutation quiet)

The primary settle gate is a `MutationObserver` counting **structural**
changes only:

- **element** node additions / removals (`childList`, nodeType 1 only —
  text-node churn such as `textContent=` is non-structural, matching the
  fingerprint's element-only `el.children` walk), and
- changes to `role`, `aria-label`, `alt`, `title`, `placeholder`, and the
  tag identity of existing nodes (an `attributes` filter over exactly the
  fingerprint's identity attributes).

The page is settled when **500ms of structural quiet** elapses within the
bounded budget. `attribute`/`characterData` churn outside that set (text
ticking, class/style toggling, data-* updates) **never blocks a scan**.
This aligns the gate with what the fingerprint already measures
(role/name/tag/hierarchy; text/ids/classes/styles excluded), so
non-structural Aura churn no longer starves the scan.

**`networkidle` is REMOVED from the required chain** — it exhibits the
same streaming pathology as `load` (never idle while Aura polls). The
Salesforce loading-indicator absence check is RETAINED and is noted as
already-structural: spinners are elements, so their removal is a
`childList`/structural event the gate already observes.

On budget exhaustion without 500ms structural quiet → `NOT_REACHED`
(unchanged contract: an unstable page is never scanned).

### 3. Determinism criterion

Determinism is proven by **fingerprint equality across runs** (arm A's
method), not by DOM silence. On live pages, a cross-run fingerprint delta
is **DE-18 `NOT_COMPARABLE`** behaviour (the page genuinely differed
structurally between runs), not a stabilisation failure. Arm A's
kill-criterion (a `DIFFERS` on identical inputs) still binds on the
static fixtures; on live SPAs the honest label for structural drift is
`NOT_COMPARABLE`.

### 4. Lock-check method (this check only)

The guest **302-redirect to `/s/login/` with a visible password field** is
accepted as **direct lock proof**, superseding the fingerprint-diff method
for the lock check. (Observed 2026-08-23: guest `/s/` →
`/s/login/?ec=302`, password field present.) The fingerprint diff remains
the **persona-differential** instrument (guest structure vs authenticated
structure) where both personas render a scannable page.

### 6. TOTP seed handling + the uncoded-escape belt (2026-08-23, post-review)

Two adversarial-review rounds hardened the login path against exceptions
that are neither `LoginError` nor `PlaywrightError`:

- **Seed normalisation + `MFA_SEED_INVALID`:** the seed is normalised
  (`[\s-]` stripped, upper-cased) before `pyotp.TOTP(seed).now()`, so a
  valid seed in space/hyphen-grouped display form works. If the seed is not
  valid base32, `pyotp` raises `binascii.Error` (a `ValueError`); this is
  caught and mapped to a NEW **PERMANENT** class `MFA_SEED_INVALID`
  (present but unusable — distinct from `MFA_REQUIRED_NOT_CONFIGURED` =
  unset, and from `MFA_FAILED` = portal rejected a validly-computed code).
  Permanent because a deterministic decode fault cannot be fixed by retry —
  so it never resubmits credentials.
- **Contract catch-all belt:** `login()` ends with `except Exception ->
  LoginError(LOGIN_PAGE_NOT_RECOGNIZED, "error@<step>:<ExcType>")`, after
  the `LoginError` / `PlaywrightTimeoutError` / `PlaywrightError` arms.
  Nothing escapes `login()` uncoded (the docstring contract), so no failure
  can reach the consumer's generic wall and be marked `failed_retryable`
  by default -> no credential resubmission. The detail carries the
  exception TYPE only, never its message (which could echo secret input).
- **Premature-quiet guard:** after a submit, `_stable_classify_after_submit`
  re-inventories until a recognised form appears OR the URL leaves the login
  page, so a transiently-cleared form mid-render is not misread as
  `unknown`; the landed check additionally refuses any URL still under
  `/login` as success.
- **Full-nav settle:** `await_submit_outcome` detects the outcome by URL
  change OR the in-place change flag OR a destroyed context, so a classic
  full-page-navigation login is detected immediately instead of burning the
  settle cap polling a flag that a new document does not carry.

### 5. Credential-attempt discipline

`G-1` (wrong password) and `G-2` (wrong seed) are **single-attempt** runs:
the login module never retries credentials (retry applies to navigation
only, per §1). Verification sequence: **(b) success first, then G-1, then
G-2.** Prerequisite (recorded): AK disables invalid-login lockout on the
test profile before G-1/G-2.

### 7. Credential-rejection failures are PERMANENT (2026-08-23, final review)

The final adversarial round showed a valid-base32 BUT WRONG seed (the
realistic G-2 input) computes a code without error, submits it, is rejected,
and reaches `MFA_FAILED` — which was retryable, so the queue re-claimed the
job and resubmitted username+password+a fresh wrong code up to the
max-attempts cap. That resubmits a known-bad credential to a live auth
endpoint (MFA-lockout risk) and violates §5's single-attempt guarantee,
which was previously enforced only by operator discipline, not by code.

Fix (root cause): every CREDENTIAL-REJECTION class is PERMANENT —
`BAD_CREDENTIAL`, `MFA_FAILED`, `MFA_SEED_INVALID`,
`MFA_REQUIRED_NOT_CONFIGURED`, `CREDENTIAL_NOT_CONFIGURED`,
`LOGIN_PAGE_NOT_RECOGNIZED`. A rejected credential will not be accepted on
retry, so retrying only resubmits it. `MFA_FAILED` moving to permanent
reverses the earlier clock-skew-retry rationale: the rare transient-code
case is better served by a human re-enqueue than by auto-resubmitting wrong
codes. Only `PAGE_NOT_REACHED` (pre-submit — no credential sent) and
`SESSION_LOST` (post-login recovery with CORRECT credentials) remain
retryable. The single-attempt guarantee for G-1/G-2 is now CODE-enforced.

**Ratified (AK/Claude, 2026-08-23):** credential-rejection is permanent by PRINCIPLE — for a rejected credential, retry equals resubmission. Retryability is reserved for the pre-submit navigation classes (`PAGE_NOT_REACHED`) and correct-credential session recovery (`SESSION_LOST`); no class that has submitted a rejected credential is ever retryable.

Consumer fail-safe: `consume_job`'s generic `except Exception` wall now
marks the job PERMANENT (`retryable=False`) and records only the exception
TYPE name — an uncoded/unexpected error is never assumed transient, so it
can never become `failed_retryable`-by-default and resubmit credentials.

### 8. Live-run findings (2026-08-23, a-e proven against the DE portal org)

The a-e verification against the real Salesforce Experience Cloud portal
(the fresh Developer Edition org orgfarm-4399654d2d-dev-ed — NOT env-59,
which has no Experience Cloud licences; that is why the DE org exists)
surfaced three implementation facts the static fixtures could not:

- **CSP blocks `add_script_tag`.** Experience Cloud sends a strict
  `Content-Security-Policy` (`script-src 'self' ...`) that refuses the
  inline `<script>` `page.add_script_tag` appends. The vendored axe engine
  is now injected via `page.evaluate(source)` — CDP main-world execution,
  not subject to the DOM `script-src` CSP (this CSP even allows
  `unsafe-eval`). Still the only source, read from local disk, never
  fetched. (spike.py Phase d.)
- **The login form renders after a JS-load quiet gap.** Aura reaches
  `domcontentloaded`, goes structurally quiet while it loads its JS, THEN
  injects the login form. A single settle can return in that gap (observed:
  initial inventory `inputs=0 buttons=0`). The initial classify now uses the
  same wait-for-a-recognised-form loop as the post-submit path.
- **login->MFA is a deferred navigation (transition race).** After a correct
  password, the login form lingers in the DOM on `/s/login/` for a beat
  before the client navigates to `/_ui/identity/verification/` (the MFA
  page). A first inventory can catch that transient login form and wrongly
  conclude `BAD_CREDENTIAL`. Both submit steps now CONFIRM a `login`/`mfa`
  classification with one more settle before concluding a rejection — a
  transient form resolves to the real next state; a persistent one is the
  true rejection.
- **Navigation is flaky (validated PAGE_NOT_REACHED retry).** Raw
  `domcontentloaded` on this dev org succeeds ~2/3 of the time (8-15s) and
  otherwise exceeds 20s. `PAGE_NOT_REACHED` (retryable, pre-submit) absorbed
  this live: a nav that failed its 3 in-flow attempts was re-claimed and
  succeeded on a later consume, never resubmitting a credential.

Proven a-e (DE org orgfarm-4399654d2d-dev-ed): (a)/(a2) guest determinism (fingerprint
`aecaf4a46fa46481` twice); (b) first authenticated scan — ONE login, both
surfaces REFERENCED; LOCK proven (guest `aecaf4a46fa46481` != authenticated
`41ad9361541974ad`, plus the direct guest 302->/s/login/ redirect);
(G-1) wrong password -> BAD_CREDENTIAL, zero result rows, single attempt;
(G-2) wrong seed -> MFA_FAILED, zero result rows, single attempt;
(e) DB hygiene — no password, seed, or username in any manifest/job/result
row. Open observation: the `?tabset-398be=2` surface renders identically to
the base surface under the current settle (its tab content loads late); a
tab-content-ready wait is future work, and cross-run differences there are
DE-18 NOT_COMPARABLE by design, not a stabilisation fault.

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
| `MFA_FAILED` | the portal rejected the code (MFA form persisted, or bounced to login) | `failed_permanent` (credential rejection — a wrong code must not be resubmitted; reversed from the earlier retryable choice, see §6/§7) |
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
  same inputs cannot succeed AND, for credential rejections, must not
  resubmit. Only `PAGE_NOT_REACHED` (pre-submit) and `SESSION_LOST`
  (correct-credential session recovery) are retryable — neither resubmits
  a WRONG credential.
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
