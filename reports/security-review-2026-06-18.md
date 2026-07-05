# PrimeQA / Plimsol — Adversarial Security & Quality Review

**Date:** 2026-06-18
**Type:** Full read-only adversarial security + quality review (backend + UI)
**Method:** Threat-model-driven static analysis. Six parallel review passes
(auth/authz/BOLA, execution-safety/SF-transport/secrets, LLM/generation,
injection/info-disclosure/SCA, UI/UX, dead-code), synthesized; serious findings
re-verified against the code directly.
**Discipline:** Read-only. No code changed, nothing committed, no live exploit /
prod-mutation / live-LLM / Salesforce calls. Findings needing a live run to
confirm are tagged `[needs-runtime-confirmation]` with the exact confirming run.

---

## Plain-English summary (read this first)

I threat-modelled the app, inventoried the whole surface, then ran six
adversarial review passes and verified the serious findings against the code
myself.

**The good news:** the post-2026-04 hardening is real and holds. Login derives
the tenant server-side, JWTs pin their algorithm, CSRF is correctly applied, the
schema-per-tenant isolation is solid, the LLM never gets to author raw
object/field names or SOQL (so a malicious Jira ticket can't directly steer a
destructive run), and the read-only/write executor split is type-enforced.

**The bad news — three things to fix before a pilot:**

1. **Any tenant admin can make themselves "superadmin" (god-mode) with one API
   call.** The user-update path checks *which* fields you can set but never
   checks the *value* of the role. **Verified.**
2. **A tenant admin can edit users in *other* tenants** (disable, rename, or —
   chained with #1 — promote them). The API user-update path forgot the tenant
   check the web page has. **Verified.**
3. **If `JWT_SECRET` is ever missing on a deploy, the app silently signs logins
   with a public, in-the-code password** ("dev-secret-change-me") — anyone could
   forge a god-mode token for any tenant. The encryption key fails loudly when
   missing; the login secret does not. **The defect is verified; whether it's
   live depends on the Railway config.**

Below: the threat model, the surface inventory, the full severity-ranked
findings, an explicit statement of what was and was **not** reviewed, and a
triage/re-test plan. No code was changed — this is a detection pass.

---

## Step 1 — Threat model (STRIDE-driven)

**Assets:** (A1) customer Salesforce data; (A2) stored SF/Jira/LLM credentials &
OAuth tokens; (A3) the live Salesforce org S4 can *mutate*; (A4) per-tenant data
isolation; (A5) LLM spend/budget.

**Adversaries:** (D1) a malicious tenant / a low-privilege user escalating; (D2)
a malicious requirement/Jira author (untrusted text → S3 prompt); (D3) hostile
LLM output (an emitted recipe S4 executes); (D4) a malicious/curious insider;
(D5) a network attacker.

**Trust boundaries & the STRIDE pressure that drove probing:**

| Boundary | Spoofing | Tampering | Repudiation | Info-disclosure | DoS | Elevation |
|---|---|---|---|---|---|---|
| browser → API | JWT forge (**F-3**) | role/tenant in update (**F-1/F-2**) | activity_log present | evidence to viewer (**F-14**), errors (**F-16/17**) | bulk coercion (**F-15**), login throttling (gap) | **F-1** admin→superadmin |
| tenant → tenant | login tenant derivation ✓ | cross-tenant group/user writes (**F-2/F-6**) | — | cross-tenant reads (mostly closed ✓) | — | **F-2** |
| untrusted requirement → S3 prompt | — | prompt injection (defended ✓) | — | PII redaction (✓ best-effort) | cost-DoS (**F-11**) | excessive agency (defended ✓) |
| PrimeQA → live SF org | OAuth token resolution | hostile recipe → mutation (defended ✓); env-gate fail-open (**F-4**); enqueue bypass (**F-5**) | run evidence durable ✓ | SF error grading (✓), credential swallow (**F-8**) | unbounded SF query (bounded ✓) | prod-run without Admin (**F-5**) |

---

## Step 2 — Surface inventory (coverage map)

**Backend** — 271 Python files / ~65k LOC. Substrate S1–S8 (`semantic`+`sync`,
`test_representation`, `generation`, `execution_engine`, `knowledge`,
`interpretation`, `conversation`, `evolution`). Cross-cutting: `core`
(auth/authz/crypto/csrf/repository/service), `intelligence` (+ `llm/` gateway,
`substrate_decision`), `release`, `runs`, `integrations` (`sf_client`),
`metadata_bridge`, `shared`, `system_validation`, `vector`. Legacy (gutted):
`metadata`, `execution`, `test_management`. **176 route decorators** (views.py
108, core 26, test_management 18, release 14, intelligence 7, execution 1).
Authz decorators in use: `role_required` 62, `require_role` 41, `require_auth`
23, `require_tier` 9, `require_tier_api` 8. Data layer: Postgres + pgvector,
public migrations 001–057 + per-tenant alembic; bitemporal S1 store. Workers:
`worker.py`, `scheduler.py`, queues `s1_sync_jobs` / `s4_execution_jobs`.

**Frontend** — 56 Jinja templates + 10 JS modules (Tailwind CDN, HTMX, SSE).
Surfaces: auth/login, requirements (+ Jira import picker), claims
review/approval, S3 generation console, S4 run list + evidence detail, release
dashboard/decision, environment/connection/group management, settings (users,
LLM usage, agent), `/ask` conversation, setup wizard.

*What was reviewed vs not is stated explicitly in the Scope-Coverage section.*

---

## Executive summary — finding counts

| Severity | Count | Findings |
|---|---|---|
| **Critical** | 3 | F-1 admin→superadmin escalation, F-2 cross-tenant user write, F-3 JWT secret insecure default (latent) |
| **High** | 4 | F-4 execution-gate fail-open, F-5 prod-run enqueue bypass, F-6 cross-tenant group BOLA, F-7 broken `/api/jira/search` (correctness) |
| **Medium** | 6 | F-8 credential decrypt swallow, F-9 cookie missing `Secure`, F-10 stateless revocation window, F-11 LLM cost-DoS, F-12 secret-field autocomplete, F-13 Jira-picker DOM XSS |
| **Low** | 9 | F-14 viewer evidence read, F-15 bulk id coercion, F-16 unauth internal metrics, F-17 `/health` DB-error leak, F-18 native dialogs, F-19 toast partial-escape, F-20 stale-S1 grounding, F-21 field-override validation, F-22 SOQL escape inconsistency |
| **Info / UX** | 3 | F-23 hardcoded button colors, F-24 SSO/magic-link absent vs brief, F-25 dependency reproducibility |
| **Dead code** | inventory | see Section D |

### Top pilot-critical risks (the 3–5 that matter most before pilot)

1. **F-1 — Admin self-promotes to superadmin** (`core/service.py:110`). One
   PATCH = god-mode. *Verified.*
2. **F-2 — Cross-tenant user takeover** (`core/service.py:110` +
   `repository.py:35`). Admin in tenant A edits/disables/promotes a user in
   tenant B. *Verified.* (F-1 + F-2 chain to full multi-tenant compromise.)
3. **F-3 — JWT secret falls back to a public default with no production guard**
   (`core/auth.py:16` et al). Latent total auth bypass. *Defect verified;
   exploitability `[needs-runtime-confirmation]`.*
4. **F-5 — Production org can be mutated through a secondary enqueue path** (CI
   webhook / repair-agent apply) that skips the Admin-on-prod gate. *Missing
   check verified.*
5. **F-4 — The single execution-safety gate fails OPEN** if it can't read the
   env policy row (`execution_engine/run.py:96`). A `disabled`/`read_only`/prod
   lock silently becomes "allow." *Verified.*

---

## Backend — security & correctness findings

### F-1 — Admin → Superadmin privilege escalation (unvalidated role value on user update) — CRITICAL
- **Severity:** Critical — CVSS 3.1 `AV:N/AC:L/PR:H/UI:N/S:C/C:H/I:H/A:H` = **9.1**
- **Location:** `primeqa/core/service.py:110-112` (`AuthService.update_user`); reachable via `primeqa/core/routes.py:153-159` (`PATCH /api/auth/users/<id>`) **and** `primeqa/views.py:1136-1148` (`POST /users/<id>/edit`).
- **What's wrong:** `update_user` builds `allowed = {"role","is_active","full_name"}` and filters by *key* but never validates the *value*. `create_user` correctly rejects roles outside `("admin","tester","ba","viewer")` (`routes.py:127`), but the update path has no equivalent check, and migration 017 widened the DB CHECK to allow `'superadmin'` — so there is no backstop. Both the API route and the web POST route call straight through.
- **Adversarial trigger:** A tenant `admin` (passes `@require_role("admin")`) sends `PATCH /api/auth/users/<own_id>` with `{"role":"superadmin"}` → god-mode (cost visibility, agent-autonomy config, raw LLM prompts, pre-flight override, cross-tenant ops, exemption from the 20-user cap). *Static-confirmed; the confirming live run is that PATCH against a non-prod instance — not run.*
- **Root cause / class:** Validation asymmetry between create and update; trusting a whitelisted *key* without validating its *value* (mass-assignment-adjacent). A privilege grant is treated as ordinary profile data. **Class:** any service mutation that filters keys but not values.
- **Framework:** OWASP API3:2023 (Broken Object Property Level Authorization) / API1; CWE-269, CWE-639.
- **Proposed fix (class):** In `AuthService.update_user` (the single chokepoint both routes share) reject any role outside the allowlist **and** refuse promotion to a tier ≥ the caller's own (an admin must not mint a superadmin) — pass the caller's tier in. Reuse the exact allowlist `create_user` uses. Add a regression test.
- **Evidence:** `[verified-in-code]`

### F-2 — Cross-tenant user modification (`update_user` not tenant-scoped) — CRITICAL
- **Severity:** Critical — CVSS 3.1 `AV:N/AC:L/PR:H/UI:N/S:C/C:L/I:H/A:H` = **8.5**
- **Location:** `primeqa/core/repository.py:35-36` (`get_user_by_id` filters on `User.id` only), `primeqa/core/service.py:110-118` (no `tenant_id` param), `primeqa/core/routes.py:153-159` (API route omits the tenant check). The web GET form (`views.py:1130`) *does* check `edit_user.tenant_id != request.user["tenant_id"]`; the API PATCH and the web POST (`views.py:1148`) do not.
- **What's wrong:** The target user is resolved by `user_id` alone; nothing scopes it to the caller's tenant.
- **Adversarial trigger:** Admin in tenant A sends `PATCH /api/auth/users/<id-in-tenant-B>` → disable (`{"is_active":false}`), rename, or (chained with F-1) promote a user in another tenant. Breaks the core tenant-isolation boundary. *(Note `users_toggle_active` at `views.py:1173` correctly checks tenant — proof this is an inconsistency, not a design.)*
- **Root cause / class:** BOLA — object reference resolved without scoping to the caller's tenant; the web path enforces it, the API path is the outlier. **Class:** every `get_*_by_id` that feeds a mutation without a tenant filter.
- **Framework:** OWASP API1:2023 (BOLA); CWE-639, CWE-863.
- **Proposed fix (class):** Make `get_user_by_id(user_id, tenant_id)` and every by-id lookup feeding a mutation require and filter on `tenant_id` (404 on mismatch); audit all `get_*_by_id` for the same gap. Best done at the repository layer so no route can forget.
- **Evidence:** `[verified-in-code]`

### F-3 — JWT/SECRET_KEY falls back to a public hard-coded default, fails open — CRITICAL (latent)
- **Severity:** Critical if the var is unset on any deploy — CVSS 3.1 `AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H` = **10.0**; the *defect* (insecure default + no guard) is verified regardless.
- **Location:** `primeqa/core/auth.py:16`, `primeqa/views.py:25`, `primeqa/core/service.py:21`, `primeqa/app.py:41` — all resolve `os.getenv("JWT_SECRET", "dev-secret-change-me")`. No startup assertion anywhere (`app.py:39-58` only toggles `debug` off `FLASK_ENV`).
- **What's wrong:** If `JWT_SECRET` is unset/empty, the app **signs and verifies** JWTs with the in-repo string `dev-secret-change-me`. Contrast `core/crypto.py:40`, which `raise RuntimeError` when `CREDENTIAL_ENCRYPTION_KEY` is missing — the encryption key fails loud; the auth secret fails open. This is the exact inverse of the fail-loud principle, on the most security-critical secret.
- **Adversarial trigger:** On a deploy missing `JWT_SECRET`, an attacker forges `{sub, tenant_id, role:"superadmin"}` signed with the known constant; `require_auth` accepts it (`auth.py:38`, HS256 pinned so no `alg=none` confusion) → full auth bypass, any tenant, god-mode.
- **Root cause / class:** Insecure default usable in production; security secret with a code-shipped fallback resolved at 4 divergent sites. **Class:** missing-config fail-open on secrets.
- **Framework:** OWASP API2:2023 (Broken Authentication); CWE-798, CWE-1188, CWE-453.
- **Proposed fix (class):** One `get_jwt_secret()` resolver that `raise`s when `FLASK_ENV=="production"` and the value is unset/empty/equal to the default; call it from a boot-time validator in `create_app()` *and* the worker/scheduler entrypoints, and from all four sites (incl. `app.py:41`). Centralize all secret resolution (`JWT_SECRET`, `CREDENTIAL_ENCRYPTION_KEY`, `WEBHOOK_SECRET`) behind the same fail-closed module.
- **Evidence:** `[verified-in-code]` for the default + absent guard; `[needs-runtime-confirmation]` that the var is actually unset on any deployed env — confirm with the Railway env list (do not log the value).

### F-4 — Execution-safety gate fails OPEN when the env policy row is unreadable — HIGH
- **Severity:** High — CVSS 3.1 `AV:N/AC:H/PR:H/UI:N/S:C/C:N/I:H/A:N` = **5.8** (operational fail-open on a safety control)
- **Location:** `primeqa/execution_engine/run.py:83-104` (`_resolve_env_gate` → `return (None, None)` on *any* exception or missing row), consumed at `:246-249` and `:474`; `_authorize_dispatch` (`:107-151`) treats `execution_policy=None` and `is_production=None` as "no restriction."
- **What's wrong:** A `(None, None)` result means **both** the resource-policy rule (`disabled`/`read_only`) and the production-role rule become no-ops and the run proceeds. The `except Exception` is intended only for substrate-only test sessions, but it's unconditional: a transient DB error, a `search_path` misconfig, or ORM drift while reading the `environments` row during a *real* production run silently disables the only run-time enforcement that a `disabled` env "rejects all runs."
- **Adversarial trigger:** Not directly attacker-triggerable, but any condition that makes the `Environment` SELECT raise converts a "this org is locked down" guarantee into a mutating run. The `test_disabled_rejects_all` invariant is bypassed by a swallowed read error.
- **Root cause / class:** Same swallowed-exception class as F-8 — a *security-decision input* degrades to a permissive value. A gate that can't read its policy must **deny**, not allow. **Class:** silent-failure → fail-open on the authorization path (see also `run.py:340-341`).
- **Framework:** OWASP API5; CWE-636 (Not Failing Securely), CWE-703.
- **Proposed fix (class):** Distinguish "no env row in a test schema" (narrow, typed exception → degrade) from "couldn't read the env" (production → raise / deny dispatch). Never let a bare `except Exception` feeding an authz/grading decision return a permissive default.
- **Evidence:** `[verified-in-code]`; `[needs-runtime-confirmation]` that production sessions always carry `public` on the search_path (the comment asserts it; the except defeats the assertion if ever false).

### F-5 — Production org mutation via secondary enqueue paths that skip the Admin-on-prod gate — HIGH
- **Severity:** High — CVSS 3.1 `AV:N/AC:L/PR:H/UI:N/S:C/C:N/I:H/A:N` = **6.5** (8.2 for the webhook variant if a `full`-policy prod env exists)
- **Location:** (a) **CI webhook** `primeqa/release/routes.py:387-465` → `intelligence/s4_execution_console.py` enqueue — validates only that `environment_id` belongs to the release's tenant, never checks `is_production`. (b) **Repair-agent apply** `primeqa/intelligence/repair_agent.py:461-481` (`_apply`) calls `enqueue_s4_execution(...)` with no env-policy/production check and no `caller_tier`. The worker then runs as system (`consumer.py:89-91`, `caller_tier=None`), so `_authorize_dispatch`'s production-role rule (`run.py:147`) is skipped *by design* for system callers — meaning **every enqueue boundary must make the production decision itself**, and these two don't. The interactive path (`views.py:3135`) and `auto_apply_proposals` (`repair_agent.py:555`) *do* gate — proof the others are gaps.
- **What's wrong:** The production safety invariant is enforced at *some* enqueue routes, not at the shared chokepoint. A holder of the single global `WEBHOOK_SECRET`, or an admin approving a repair proposal whose stored `environment_id` points at prod, dispatches a mutating data-recipe against the live org with no Admin-on-prod confirmation.
- **Adversarial trigger:** Sign `POST /api/webhooks/ci-trigger` with `{release_id, environment_id=<that tenant's prod env>, commit_sha}`; HMAC passes; mutating recipes enqueue against prod. *Gating precondition:* the prod env must have `execution_policy='full'` (the default) — `[needs-runtime-confirmation]`. The resource-policy rule (`disabled`/`read_only`) **does** still fire at run time (async resolves `env_gate` from a live session), so the exposure is the `is_production` axis specifically.
- **Root cause / class:** Per-entry-point authorization instead of chokepoint authorization. **Class:** a security gate placed at the route layer, then a second route reaches the chokepoint without it.
- **Framework:** OWASP API5:2023 (Broken Function Level Authorization); CWE-862, CWE-1390.
- **Proposed fix (class):** Move the production decision into `enqueue_s4_execution` (or one `authorize_enqueue(tenant_id, environment_id, caller_tier)` helper) so every door — interactive, webhook, repair-agent, scheduled — inherits it; require an explicit caller tier and **fail closed** when unspecified. For the webhook specifically, reject `is_production` (or require a signed `confirm_production`) and consider per-tenant webhook secrets.
- **Evidence:** `[verified-in-code]` for the missing checks; `[needs-runtime-confirmation]` for live mutation given a `full`-policy prod env.

### F-6 — Cross-tenant group member / environment linking (BOLA) — HIGH
- **Severity:** High — CVSS 3.1 `AV:N/AC:L/PR:H/UI:N/S:C/C:H/I:H/A:N` = **7.6**
- **Location:** `primeqa/core/service.py:474-490` (`GroupService.add_member` / `add_environment`); repo `repository.py:443-453` & `470-481`; routes `core/routes.py:409-451`, `views.py:1658-1700`.
- **What's wrong:** Both methods verify the *group's* tenant via `get_group(group_id, tenant_id)` but pass the raw `user_id` / `environment_id` to the repo without checking those targets are in the same tenant. The join tables (`group_members`, `group_environments`) carry no tenant column, so the cross-tenant link persists. `ReleaseService.add_requirement` (`service.py:103-121`) re-queries scoped to tenant — proof the guard exists elsewhere and was omitted here.
- **Adversarial trigger:** Admin in tenant A `POST /api/groups/<A-group>/environments {"environment_id": <B-env>}` → `get_environments` (`repository.py:494`) joins `Environment` with no tenant filter and returns tenant B's env (name, type, `sf_instance_url`) onto the group page. Linking a foreign `user_id` widens that user's env-run access via `EnvironmentRepository.list_environments` and leaks their email/role.
- **Root cause / class:** Parent tenant-scoped, child id trusted from the request body; structural enabler is the tenant-less join tables. **Class:** identical to F-2 (BOLA on a referenced child id).
- **Framework:** OWASP API1:2023; CWE-639, CWE-863.
- **Proposed fix (class):** Re-query `User`/`Environment` scoped to `tenant_id` before linking (raise on mismatch); add a `tenant_id` column + CHECK to the join tables so isolation is enforced at the DB, not per-call.
- **Evidence:** `[verified-in-code]`

### F-7 — `/api/jira/search` calls undefined helpers — broken live feature — HIGH (correctness)
- **Severity:** High (functional bug; security impact Low) — `(UX/correctness, no CVSS)` + CWE-209 sub-note
- **Location:** `primeqa/execution/routes.py:65,70` call `_jira_client(...)` and `_jira_client_for_env(...)` — **neither is defined or imported anywhere** (grep for `def _jira_client*` → empty; file is 84 lines with no other defs).
- **What's wrong:** Any request supplying a valid `conn_id`/`env_id` with `q ≥ 2` chars hits a `NameError`, caught by the global 500 handler. The route is wired to live UI (`templates/requirements/list.html:203` `hx-get="/api/jira/search"`, `requirements_jira_picker.js`), so the requirements "Import from Jira" search is broken at runtime — it only "works" on the early-return (no id / short query) paths. *(Side note: CLAUDE.md's claim that `execution/routes.py` "survives (release-status etc.)" is stale — this file's only route is `/api/jira/search`; release-status lives in `release/routes.py`.)*
- **Adversarial trigger / impact:** User opens the Jira import picker, types a query → silent failure. Interacts with F-13: the *search* path is dead, so the picker's only live input is the *paste* box.
- **Root cause / class:** Missing helpers (likely lost in the v1 cutover); no smoke test on the route. **Class:** routes referencing symbols that were deleted/never-landed, masked by the catch-all 500 handler.
- **Framework:** CWE-457 (use of undefined), CWE-209 (`_render(error=f"Jira search failed: {e}")` at `:80` echoes raw exception text to the client).
- **Proposed fix:** Restore `_jira_client`/`_jira_client_for_env` or repoint the template/JS at the live Jira search in `primeqa/runs/`. HOLD for a decision — don't delete the route while the template calls it. Add a route smoke test. Stop echoing `{e}` to the client.
- **Evidence:** `[verified-in-code]`

### F-8 — Credential decrypt failure swallowed → ciphertext forwarded as the OAuth secret (the reference case, confirmed) — MEDIUM
- **Severity:** Medium — CVSS 3.1 `AV:N/AC:H/PR:H/UI:N/S:U/C:L/I:L/A:L` = **3.9** (primarily an operability/fail-loud defect)
- **Location:** `primeqa/core/repository.py:362-365` (`try: config[field]=decrypt(...) except Exception: pass`); consumers `execution_engine/credentials.py:40-49`, `sync/credentials.py:59-69`, `metadata/worker_runner.py` (`_oauth_token`).
- **What's wrong:** On a missing/wrong/rotated `CREDENTIAL_ENCRYPTION_KEY`, `decrypt()` raises (it's fail-loud), but this `except: pass` leaves the **Fernet ciphertext** in the field. `_oauth_token` then POSTs the ciphertext as `client_secret`/`password` → a generic SF `400/401` ("returned no access_token") instead of "decryption failed — check CREDENTIAL_ENCRYPTION_KEY." A botched key rotation looks like "SF creds expired," hindering incident response; the ciphertext is also transmitted to SF's token endpoint.
- **Adversarial trigger:** Operational, not attacker-driven: any key mismatch silently degrades every SF connection to an opaque auth failure.
- **Root cause / class:** The canonical swallowed-exception-on-secret-resolution case; the only place that converts a key error into garbage-forwarded-as-secret. **Class:** same as F-4 (silent fallback to a wrong value).
- **Framework:** CWE-703, CWE-755; OWASP API8.
- **Proposed fix (class):** Remove the `except: pass`; let `decrypt` propagate and have the resolver raise `CredentialResolutionError("decryption failed for field X")`. Never forward an undecryptable field. Sweep the whole `except Exception: pass|return None|return {}` class (F-4, `semantic/derivation.py:511`, `metadata/service.py:802`) for any that feed a decision or a secret.
- **Evidence:** `[verified-in-code]`

### F-9 — `access_token` cookie missing the `Secure` flag — MEDIUM
- **Severity:** Medium — CVSS 3.1 `AV:N/AC:H/PR:N/UI:N/S:U/C:H/I:N/A:N` = **5.9**
- **Location:** `primeqa/views.py:130` — `set_cookie("access_token", ..., httponly=True, samesite="Lax", max_age=1800)` with no `secure=`. Contrast `csrf.py:152`, which sets `secure=(FLASK_ENV=="production")`.
- **What's wrong:** The bearer cookie defaults to `Secure=False` and will be sent over plaintext HTTP (downgrade, mixed-content, or an internal non-TLS hop). The `Secure` attribute is applied inconsistently across cookies.
- **Adversarial trigger:** A network attacker (D5) on any non-TLS segment captures and replays the cookie for its 30-minute life.
- **Root cause / class:** Per-call cookie attributes instead of one helper; the auth cookie missed the flag the CSRF cookie has.
- **Framework:** OWASP API8; CWE-614, CWE-1004.
- **Proposed fix (class):** Set `secure=(FLASK_ENV=="production")` on `access_token` (and on logout's `delete_cookie`); route all cookie writes through one helper that applies HttpOnly/Secure/SameSite uniformly.
- **Evidence:** `[verified-in-code]`

### F-10 — Authorization never re-checks active/role state — ≤30-min stale-privilege window — MEDIUM
- **Severity:** Medium — CVSS 3.1 `AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:N` = **7.1**, scoped down to Medium by the ≤30-min bound
- **Location:** `core/auth.py:37-59` and `views.py:37-49` decode the JWT and trust its claims; neither loads the `users` row to confirm `is_active`/current `role`. Revocation only revokes refresh tokens (`service.py:129-137`).
- **What's wrong:** A disabled user or a just-demoted superadmin keeps full access (incl. to customer SF data and run dispatch) until the access token's `exp` (30 min). The code acknowledges this as the JWT trade-off, but a 30-minute live-credential window post-deactivation is a real exposure.
- **Root cause / class:** Stateless authorization with no fast-path revocation on the hot route. **Class:** trusting long-lived self-contained tokens for decisions that change mid-session.
- **Framework:** OWASP API2; CWE-613, CWE-863.
- **Proposed fix:** On sensitive/state-changing routes, re-load `users.is_active`+`role` (one indexed query, cacheable a few seconds) via a single `current_user_state()` gate the decorators consult; and/or shorten the access TTL / add a `token_version`.
- **Evidence:** `[verified-in-code]`

### F-11 — LLM cost-DoS: `BatchBudget` unwired on the live path + rate limits default to NULL — MEDIUM
- **Severity:** Medium — CVSS 3.1 `AV:N/AC:L/PR:L/UI:N/S:U/C:N/I:N/A:L` = **4.3**
- **Location:** `generation/intake.py:69-78` (builds `OperationalContext` with a default empty `BudgetSpec()`); `generation/runtime.py:184-212` (`BatchBudget` with all-None dims → `exceeded_dimension()` always None); `intelligence/llm/limits.py:202-203` (all-NULL caps → `allowed=True`); `limits.py:131-144` (starter defaults), and `load_tenant_config` fails *open* to starter.
- **What's wrong:** The substrate's designed cost-DoS control (`BudgetSpec`: token/time/tool-call) is never populated on the production intake — every dimension is None, so the per-batch budget is a no-op. The remaining guards are per-requirement turn/correction caps (real: 24 turns × 3 corrections) and the gateway rate limits, which default to NULL ("gentle onboarding") and count *calls/spend*, not tokens. A requirement crafted to trip `detect_complexity → high → Opus` (5× cost) and maximize per-turn input tokens, repeated across requirements, multiplies spend with no token ceiling.
- **Adversarial trigger (D2):** Submit many requirements engineered for the high-complexity Opus route with large excerpts. *Cost amplification magnitude `[needs-runtime-confirmation]`.*
- **Root cause / class:** A defense-in-depth control present in design but disabled at the production assembly point; the cross-cutting limit is opt-in and call-count-based. **Class:** OWASP LLM10 unbounded consumption.
- **Framework:** OWASP LLM10; CWE-770.
- **Proposed fix (class):** Set a conservative non-None `BudgetSpec` (token + tool-call + time) in `build_generation_request` so every batch carries a hard ceiling regardless of tier; add a token-spend window to `limits.check`; make `load_tenant_config` fail *closed* to a low cap.
- **Evidence:** `[verified-in-code]` for the unwired budget + NULL defaults; `[needs-runtime-confirmation]` for the dollar amplification.

---

## LLM-specific findings (OWASP LLM Top 10)

**Defenses that hold (verified) — the high-risk LLM concern is well-defended.**
On the live S3 path the LLM never authors object/field names, SOQL, or DML: it
emits *typed proposals* via three tool schemas (`generation/tools.py`,
`additionalProperties:false`, enum-locked, forced `tool_choice`); the substrate
authors every recipe body from S1-resolved entities (`governance_core.py`),
gated by `is_emittable`/`check_refs_exist`. SOQL is built from S1 names
(`translator._soql_literal` escapes `\` then `'`); LLM values land in
`field_values` (JSON POST) or compared `AssertionPredicate.value`, never
interpolated into SOQL. Read/write executor segregation is type-enforced
(`read_only_client.ReadOnlyClient` Protocol; `bridge.py:123-128,216` rejects
non-`metadata_read`/`DeployStep`). The retired v1 free-form
`test_plan_generation` prompt has no live caller. PII redaction (`redact.py`) is
per-tenant-keyed (no cross-tenant cache leak). **So prompt injection (LLM01) →
destructive recipe (LLM08) is structurally bounded — a malicious Jira ticket
cannot make S4 target an arbitrary object/operation.**

### F-20 — Generation grounded against pinned S1, executed against current S1 (staleness TOCTOU) — LOW
- **Severity:** Low — CVSS 3.1 `AV:N/AC:H/PR:L/UI:N/S:U/C:N/I:L/A:N` = **2.6** (largely by-design)
- **Location:** grounding pins `ctx.semantic_context.s1_version_seq` (`governance_core.py:441`) but S4 padding reads `s1.current_version_seq()` (`world.py:159`, `run.py:268`) and re-inspects live org state by design (D-099.3). No inline version-consistency assertion on the run path; S8 (intact/drifted/broken) is the async compensating control.
- **What's wrong / abuse:** A verdict can reflect an org drifted from the grounded model. An adversary able to influence org state or S1 sync timing between generation and execution could get a recipe to assert against a different world than it was grounded in — *manufacturing a pass against stale grounding* without an inline warning.
- **Root cause / class:** Deliberate temporal decoupling without an inline staleness gate. **Class:** TOCTOU (CWE-367); OWASP LLM08 (excessive-agency flavor).
- **Proposed fix:** On the run path, compare the recipe's grounding `version_seq` to `current_version_seq()` and surface a "grounded-against-stale-S1" caveat into the evidence/verdict so the decision engine can down-weight it.
- **Evidence:** `[verified-in-code]` for the decoupling; `[suspected]` for exploitability (mostly by-design — flagged for the decision layer).

### F-21 — Run-time field-overrides reach a live create with no S1 validation — LOW
- **Severity:** Low — CVSS 3.1 `AV:N/AC:L/PR:H/UI:N/S:U/C:N/I:L/A:N` = **2.7**
- **Location:** `views.py:2428-2446` (`_parse_field_overrides`) → `execution_engine/data_executor.py:474-475` (merged override-wins onto the subject create); gated `@role_required("admin","tester","superadmin")`.
- **What's wrong:** Operator `Field=Value` overrides are length-capped but merged with **no S1 pre-flight** (D-235 defers it), so a Member+ user can set arbitrary field values on the live-org create, bypassing the k16 "S4 never sets the value under test" invariant for the override path. Authenticated, role-gated, JSON body (not injection), one auto-cleaned record — hence Low.
- **Root cause / class:** Deferred input validation at a live-mutation boundary (CWE-20); OWASP LLM08.
- **Proposed fix:** Validate override keys against S1 fields for the target object at submit time (reject unknown/system/audit fields), closing the D-235 deferral.
- **Evidence:** `[verified-in-code]`

---

## UI / UX findings

### F-13 — DOM-based XSS in the Jira chip picker (unescaped key) — MEDIUM
- **Severity:** Medium — CVSS 3.1 `AV:N/AC:L/PR:L/UI:R/S:C/C:L/I:L/A:N` = **5.0** (self-XSS today; escalates to stored-XSS once F-7 is fixed)
- **Location:** `static/js/requirements_jira_picker.js:48` — `chip.innerHTML = '<span ...>' + k + '</span>' + ...` with `k` (the Jira key) **unescaped**, while the adjacent `info.summary` *is* `escapeHtml`'d. Source paths: paste handler `:117` (`raw.split(...).forEach(k => add({key:k}))`, **no validation**) and search-result `dataset.key` (`templates/runs/_jira_search_results.html:20`, rendered without `| e`).
- **What's wrong / synthesis:** The escape was simply forgotten on `k`. **Important nuance:** the *search* source is currently dead (F-7 NameErrors before results render), so the **only live vector today is the paste box → self-XSS** (the user pastes markup into their own session — low practical impact). The moment F-7 is fixed, a malicious Jira author's issue key (e.g. `<img src=x onerror=...>`) becomes **stored/reflected XSS** against other users in the tenant who open the picker — so this must be fixed *together with* F-7, not after.
- **Root cause / class:** Inconsistent use of the module's own `escapeHtml` helper; pasted tokens trusted as well-formed. **Class:** every `innerHTML +=` mixing escaped and raw operands.
- **Framework:** OWASP A03:2021; CWE-79, CWE-20.
- **Proposed fix (class):** Wrap `k` in `escapeHtml()` at `:48`; validate pasted/synced keys against `/^[A-Z][A-Z0-9_]+-\d+$/` before `add()`; restore `| e` on `data-key` at `_jira_search_results.html:20`. Audit every `innerHTML` concatenation for unescaped operands.
- **Evidence:** `[verified-in-code]` (sink + both sources + the F-7 interaction confirmed).

### F-12 — Credential form fields lack `autocomplete` hardening — MEDIUM
- **Severity:** Medium — CVSS 3.1 `AV:L/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N` = **6.2** (local / shared-machine)
- **Location:** `templates/connections/new.html:40,45,58,70,72`; `connections/edit.html:29,33,44,50,52`; `auth/login.html:25`; `users/form.html:11`. No `type="password"` field anywhere sets `autocomplete`.
- **What's wrong:** SF password, client secret, Jira API token, LLM/Voyage API keys are collected in `type="password"` inputs with no `autocomplete` control, so browsers/managers may cache org secrets keyed to the Plimsol origin (and cloud-sync them).
- **Adversarial trigger:** Admin enters a Connected-App secret on a shared/managed workstation → later user of the same browser profile retrieves it.
- **Root cause / class:** Missing autofill controls; no shared password-input macro. **Class:** all credential-bearing forms share the omission.
- **Framework:** OWASP A07:2021; CWE-200, CWE-522.
- **Proposed fix (class):** `autocomplete="off"`/`"new-password"` on secret inputs, `"current-password"` on login, via a central `password_input` macro.
- **Evidence:** `[verified-in-code]`

### F-14 — Viewer-tier can read full S4 run evidence + cause attribution — LOW
- **Severity:** Low — CVSS 3.1 `AV:N/AC:L/PR:L/UI:N/S:U/C:L/I:N/A:N` = **4.3**
- **Location:** `views.py:2818-2842` (`s4_run_detail`, `@login_required` only), `:2645` (list), `:2364` (claims detail). Tenant-scoped (no cross-tenant leak), but any tier incl. `viewer` sees full evidence (captured SF record shapes/values) + failure attribution. The repair sub-section *is* admin-gated (`:2838`), suggesting evidence detail warrants ≥ Member.
- **Root cause / class:** Read-detail routes default to the lowest authenticated tier; D-245 requires every endpoint to *declare* a minimum tier. **Class:** `@login_required`-only reads with no deliberate tier decision.
- **Framework:** OWASP API3:2023; CWE-200.
- **Proposed fix:** Decide + apply `@require_tier(MEMBER)` on evidence detail; audit all `@login_required`-only reads with `scripts/authz_inventory.py`.
- **Evidence:** `[verified-in-code]` for gating; `[suspected]` it's a policy violation (needs product confirmation).

### F-16 — Unauthenticated internal metrics endpoint — LOW
- **Severity:** Low — CVSS 3.1 `AV:N/AC:L/PR:N/UI:N/S:U/C:L/I:N/A:N` = **5.3**
- **Location:** `shared/observability.py:106-107` (`/api/_internal/health`, no auth decorator, CSRF-exempt) returns `STATS.snapshot()` — `requests_total`, `errors_total`, `error_rate`, slow-query count, latency p50/p95. Aggregate only (no tenant data), but anonymous.
- **Root cause / class:** "Internal" by naming convention, not enforced by a gate. **Class:** convention-named endpoints lacking an auth/network ACL.
- **Framework:** OWASP API8; CWE-200.
- **Proposed fix:** Gate `/api/_internal/*` behind `require_tier(SUPERADMIN)` or a network allowlist.
- **Evidence:** `[verified-in-code]`

### F-17 — `/health` leaks raw DB error string — LOW
- **Severity:** Low — CVSS 3.1 `AV:N/AC:L/PR:N/UI:N/S:U/C:L/I:N/A:N` = **5.3**
- **Location:** `app.py:164` — `jsonify(status="unhealthy", database=str(e)), 503` returns the raw connection-exception (host/port/driver detail) to any anonymous caller on a DB outage.
- **Root cause / class:** Exception text echoed to the client. **Class:** `str(e)` in a response (see also F-7 `:80`).
- **Framework:** CWE-209; OWASP API8.
- **Proposed fix:** Return a generic "unavailable"; log the detail server-side.
- **Evidence:** `[verified-in-code]`

### F-18 — Native `confirm()`/`alert()`/`prompt()` bypass the component kit (+ raw error in alert) — LOW
- **Severity:** Low (UX, no CVSS) + CWE-209 sub-note
- **Location:** `templates/dashboard_release.html:358,362,368,376,385,386,394,407,412,415`; `settings/user_detail.html:95`. CLAUDE.md mandates `PrimeQA.confirm`/`toast` and forbids native `confirm()`. The release-triage approve/override/share flows use native dialogs; `alert(e.message)`/`alert(msg)` surface raw error text in an unstyled blocking dialog.
- **Root cause / class:** Handlers skipped the kit. **Class:** any new JS reaching for native dialogs.
- **Proposed fix (class):** `confirm()`→`PrimeQA.confirm`, `alert()`→`PrimeQA.toast(...,'error')`, override-reason `prompt()`→`modal_shell`+textarea; use `PrimeQA.showErrorFromResponse` for the structured envelope.
- **Evidence:** `[verified-in-code]`

### F-19 — `toast()` HTML-escape is incomplete (only `<`) — LOW
- **Severity:** Low — CVSS 3.1 `AV:N/AC:H/PR:L/UI:R/S:C/C:L/I:N/A:N` = **2.8** (latent; element-content context blocks tag injection today)
- **Location:** `static/js/toast.js:36-40` — `innerHTML = ... message.replace(/</g,"&lt;") ...`; `>`,`&`,`"`,`'` unescaped.
- **Root cause / class:** Hand-rolled partial escape instead of `textContent`. **Class:** `innerHTML` + partial `.replace()`.
- **Framework:** OWASP A03; CWE-116.
- **Proposed fix:** Build the message node with `textContent` (the pattern already used in `requirements/detail.html:253`).
- **Evidence:** `[verified-in-code]`

### F-22 — SOQL escape inconsistency on a secondary sync path — LOW
- **Severity:** Low — CVSS 3.1 `AV:N/AC:H/PR:H/UI:N/S:U/C:L/I:L/A:N` = **3.1**
- **Location:** `integrations/sf_client.py:831` — `escaped_label = label.replace("'", "\\'")` escapes the quote but **not** the backslash (inconsistent with the canonical `translator._soql_literal`, which escapes `\` then `'`). Input is caller-supplied StandardValueSet labels on an internal sync path (not the primary recipe path).
- **Root cause / class:** Divergent ad-hoc escaping instead of one helper. **Class:** every SOQL-building site not routed through `_soql_literal`.
- **Framework:** CWE-89 (SOQL injection, second-order); OWASP API8.
- **Proposed fix (class):** Route all SOQL literal building through one `_soql_literal`.
- **Evidence:** `[verified-in-code]`

### F-15 — Bulk `requirement_ids` elements not integer-coerced — LOW
- **Severity:** Low — CVSS 3.1 `AV:N/AC:L/PR:L/UI:N/S:U/C:N/I:N/A:L` = **3.1**
- **Location:** `release/routes.py:164` — list shape + `BULK_MAX_ITEMS=100` validated, but elements not coerced to int before `add_requirements_bulk`; `require_bulk_confirm` (`shared/api.py`) shares the gap.
- **What's wrong:** Heterogeneous elements (dicts/lists/strings) reach the service → potential unhandled 500 (DoS-lite). Not SQLi (binds are parameterized).
- **Root cause / class:** List length validated, element type/bounds not. **Class:** all bulk-id endpoints.
- **Proposed fix (class):** A shared `coerce_id_list(raw, max_items)` rejecting non-positive-int elements with 400.
- **Evidence:** `[verified-in-code]`

### F-23 / F-24 / F-25 — Info / UX / SCA
- **F-23 (UX):** 20+ templates hardcode `bg-indigo/red/gray-600` instead of `_buttons.html` macros (`requirements/detail.html:54`, `auth/login.html`, etc.) → theming drift in the dark reskin. *Fix:* use macros; add a CI grep gate. `[verified-in-code]`
- **F-24 (Info/spec):** Google/Microsoft SSO + magic-link (per the brief) are **not implemented** — `auth/login.html` + `views.py:104-133` are email/password only. *(Positive: login has no user-controlled `next`, so no open redirect.)* Either build OIDC/magic-link (validate the IdP `state`/redirect against an allowlist) or correct the brief. `[verified-in-code]`
- **F-25 (Info/SCA):** `requirements.txt` uses `>=` floors with no upper pins and **no lock file**; the installed venv is current (Flask 3.1.3, cryptography 48.0.0, PyJWT 2.12.1, gunicorn 26.0.0, etc. — no vulnerable installed version found), but a fresh resolve could drift to an unaudited version. *Fix:* pin exact versions / commit a lock file. `[verified-in-code]` (local venv); `[needs-runtime-confirmation]` for the deployed Railway image.

---

## Section D — Dead / unused code inventory

**Headline:** the v1 retirement was clean at the *table/SQL* layer — **zero live
SQL queries against any dropped table.** What remains is orphaned v1-shaped
functions, one orphaned sync runner, one broken-if-called route (F-7), a retired
flag column, stale comments, and two unused deps.

**(a) v1 remnants touching dropped tables**
- **`MetadataService.list_pending_impacts`** (`metadata/service.py:1035`) imports `MetadataImpact`, a class that no longer exists, off the dropped `metadata_impacts` table → **ImportError if called**, no callers. *Delete.* `[verified-in-code]`
- **`IntelligenceService` causal-link/explanation methods** (`intelligence/service.py:359,396`) keyed on the dropped `run_test_results`; the advertised `/api/explanations/*` + `causal-links` routes are **not registered**; zero callers. *(The class itself is live for patterns/dependencies/facts.)* *Verify-then-delete.* `[verified-in-code]`
- **Stale docstrings/comments asserting dropped tables are live** — `worker.py:3` ("Polls pipeline_runs"), `intelligence/substrate_dashboard.py:5,17` (refs `pipeline_runs` + a deleted `release/dashboard.py`), `intelligence/routes.py:3,8-9`, `llm/dashboard.py:246`, `llm/feedback_rules.py:33,363`, `test_management/models.py:3-4` + `repository.py:3-4` (stale "tables owned" lists). *Fix comments.* `[verified-in-code]`
- **`core/models.py:44-46`** — `role` column carries a "DEPRECATED — use permission_sets instead" comment, but permission_sets were **dropped (057)** and `role` is now the **live** authz basis. The comment inverts reality (actively dangerous guidance). *Rewrite comment.* `[verified-in-code]`

**(b) orphaned modules/functions**
- **`metadata/worker_runner.py`** — entire v1 sync runner orphaned (`poll_and_run_once`, `_claim_next`, `_run_claimed`, `reap_stalled_jobs` have zero callers); the **only** live export is `_oauth_token` (reused by `sync/credentials.py` + `execution_engine/credentials.py`). *Extract `_oauth_token` to a shared helper, then delete the runner.* `[verified-in-code]`
- **`metadata/service.py` (`MetadataService`, ~1080 lines)** + **`metadata/repository.py` (`MetadataRepository`)** — reachable only via the orphaned runner + `parity.py` + one unused import (below). *Verify-then-delete as a unit (HOLD — large).* `[verified-in-code]` importers / `[suspected]` no CLI path.
- **`metadata_bridge/accessor.py` (`MetadataAccessor`)** + **`metadata_bridge/parity.py`** — zero callers (consumers were the retired v1 generator/validator/linter; the cutover is complete). *Delete or archive.* `[verified-in-code]`
- **`MetaSyncStatus` read** (`views.py:840-850`) — its only writer is the orphaned runner, so it now returns `{}`; the page also renders the live S1 status. *Verify template, then remove.* `[suspected]`

**(c) dead branches/flags**
- **`cutover_read_s1`** (migration 051) — retired (D-158/D-195.3); referenced only in comments, not even declared as a Column, zero attribute reads. *Drop the column in a new migration.* `[verified-in-code]`
- *(Non-findings, confirmed live: `llm_enable_domain_packs` (049), `llm_enable_interpretation_phrasing` (050) — both sides wired.)*

**(d) unused imports** — `test_management/routes.py:22` imports `MetadataRepository`, never used (the last non-dead reference keeping that module alive). *Delete.* `[verified-in-code]` *(No full pyflakes pass run — only spot checks.)*

**(e) commented-out code** — **none found** (a five-pass sweep; the codebase deletes rather than comments). `[verified-in-code]`

**(f) stale docs** — `reports/triage/2026-05-24.md` (audits deleted v1 code, no currency banner — known); `docs/architecture/greenfield_cutover/SPEC.md` (describes pre-cutover world in present tense, references deleted `generation.py`/`validator.py`; its sibling `SEQUENCE.md` got a 2026-06-15 banner, SPEC.md didn't). *Add currency banners.* `[verified-in-code]`

**(g) unused dependencies** — **`apscheduler`** (scheduler is a hand-rolled `croniter` loop; zero imports) and **`numpy`** (zero imports) are dead in `requirements.txt`. *Remove.* `[verified-in-code]`

---

## Scope-coverage statement

**Reviewed:** `core/{auth,authz,crypto,csrf,repository,service,models}.py`; all
176 routes enumerated across `views.py`, `core/`, `release/`, `intelligence/`,
`test_management/`, `execution/`;
`execution_engine/{run,credentials,executor,data_executor,bridge,tooling_client,read_only_client,data_mutation_client,stranded_cleanup,provisioning}.py`;
`generation/{intake,runtime,governance_core,tools}.py`;
`intelligence/llm/{gateway,router,limits,redact,tiers}.py` + `repair_agent.py` +
`s4_execution_console.py`; `integrations/sf_client.py` (error-mapping + escaping
paths); `semantic/connection.py`; `worker.py`/`scheduler.py` (enqueue
boundaries); `shared/{query_builder,observability,api}.py`; all 56 templates +
10 JS modules (sink-level); `requirements.txt` + the local venv freeze;
migrations 017/049–057 (disposition); the dead-code surface across the tree.

**NOT reviewed / deferred (and therefore unverified):**
- **No live runs of anything** — every `[needs-runtime-confirmation]` item is
  unfired. The confirming runs: **F-1/F-2** = a PATCH on a non-prod instance;
  **F-3** = the Railway `JWT_SECRET` env state; **F-5** = whether a `full`-policy
  production env exists + a live webhook enqueue; **F-11** = a cost-amplification
  measurement.
- **SSRF via `sf_instance_url`** — is the org instance URL validated against an
  allowlist before PrimeQA makes requests to it? **Not reviewed — recommend a
  dedicated pass.**
- **Login brute-force / rate-limiting** on `/api/auth/login` — no throttling
  observed but not deeply assessed.
- **Fernet key rotation correctness** beyond noting it fails loud; bcrypt cost /
  password policy.
- **S1 sync engine internals** (`sync/engine.py`, `phases.py`, `materialize.py`,
  `detail_mappers.py`) and the bitemporal write path; **S6 interpretation**
  internals + `substrate_decision.py` (verdict→GO/NO-GO) beyond the run-path
  seam; **S7 `/ask`** beyond the template; the `metadata_bridge` parity harness.
- **CSP / security response headers** — not checked (relevant to mitigating F-13).
- **Substrate `alembic/versions/` per-tenant DDL** and `tests/`/`scripts/` for
  dead helpers / dropped-table refs.
- **No exhaustive `pyflakes` unused-import pass** (spot-checked only); **no SCA
  tool run** against the deployed image.
- **Dynamic dispatch / string-keyed registries** — dead-code findings assume
  static import/call graphs.

---

## Triage & re-test plan

| ID | Severity | Owner | Fix summary | Re-verification |
|---|---|---|---|---|
| F-1 | Critical | Backend/authz | Role-value allowlist + caller-tier ceiling in `AuthService.update_user` | Unit: admin PATCH `role:superadmin` → 403; admin cannot set tier > own. Re-grep create vs update parity. |
| F-2 | Critical | Backend/authz | `tenant_id` on `get_user_by_id` + every by-id mutation lookup | Integration (`tests/test_tenant_isolation.py`): tenant-A admin PATCH tenant-B user → 404. |
| F-3 | Critical (latent) | Platform/ops | Fail-closed `get_jwt_secret()` + boot validator; confirm Railway var set | Boot test: prod env w/o `JWT_SECRET` refuses to start. Confirm prod env var present. |
| F-4 | High | Execution eng. | `_resolve_env_gate` denies on read error in prod (typed test-only degrade) | Unit: simulate env-read exception on a prod session → dispatch denied, not allowed. |
| F-5 | High | Execution eng. | Production decision inside `enqueue_s4_execution`; webhook rejects prod / signed confirm | Tests: webhook + repair-apply against prod env → rejected unless explicit confirm + Admin. |
| F-6 | High | Backend/authz | Tenant re-check on group add_member/add_environment; tenant col on join tables | Integration: cross-tenant link → rejected. |
| F-7 | High (bug) | Backend | Restore `_jira_client*` or repoint UI; stop echoing `{e}` | Route smoke test for `/api/jira/search` with conn_id+q. |
| F-8 | Medium | Backend | Remove `except: pass`; raise `CredentialResolutionError` on decrypt fail | Unit: wrong key → loud error, ciphertext never forwarded. |
| F-9 | Medium | Backend | `secure=` on `access_token` cookie via central helper | Inspect Set-Cookie in prod config. |
| F-10 | Medium | Backend/authz | `current_user_state()` re-check on sensitive routes | Disabled user's cached token → 401 on next sensitive call. |
| F-11 | Medium | Generation | Non-None `BudgetSpec` in intake; token-spend window; fail-closed config | Eval: oversized requirement → budget refusal. |
| F-12 | Medium | Frontend | `autocomplete` on secret inputs via `password_input` macro | Template grep: no secret `type=password` without `autocomplete`. |
| F-13 | Medium | Frontend | `escapeHtml(k)` + paste regex + restore `data-key | e` (fix **with** F-7) | Render a key with markup → inert text. |
| F-14–F-22 | Low | mixed | Per-finding fixes above (fix the class) | Targeted unit/template tests + authz_inventory + SOQL-helper grep. |
| F-23–F-25 | Info/UX | Frontend/ops | Macros + CI grep gate; SSO decision; pin deps / lock file | CI gate; SCA on a pinned image. |
| Dead code | — | Maintainer | Section D actions (F-7 first; HOLD on the large `metadata` deletion) | Grep dropped-table names = 0; route smoke tests; `ruff F401`; `pip-compile`. |

---

## HOLD — nothing changed

This was a detection pass only. No code edits, no commits, no live exploit /
prod-mutation / live-LLM / Salesforce calls. The five pilot-critical items (F-1,
F-2, F-3, F-5, F-4) are the place to start. Each fix should follow the working
agreement (design commit separate from impl commit; HOLD before commit).
