# Wave 1 (Security) — Implementation Report

> Root-cause security guard fixes from `PLIMSOL_FIX_PLAN.md`. Branch `wave-1-security`
> (from `wave-0-test-infra`). One finding per commit. Written incrementally.
> **Not merged.** This report is uncommitted.

| | |
|---|---|
| Branch | `wave-1-security` ← `wave-0-test-infra` @ `ce74fa4` |
| Scope | SEC-1, SEC-2, SEC-3+5, SEC-4, SEC-7, SEC-8+P1, SEC-9, TEST-1+CSRF |
| Rule | root-cause only; re-verified against current code; no assertion weakening; fail-loud |

---

## Per-finding log

### SEC-1 — admin-gate + mask `GET /api/connections/<id>` ✅ `84e1450`
- **Was:** `routes.py:322-330` `@require_auth def get_connection(...): conn = svc.get_connection(...)` → `svc.get_connection` (413) returns `get_connection_decrypted()` verbatim. Any authenticated role (default `viewer`) read plaintext `client_secret`/`password`/`api_token`/`api_key`. The `list/create/update` routes use the redacted `_conn_dict` (no config); this single-get was the outlier.
- **Change:** (1) route decorator `@require_auth` → `@require_role("admin")` (matches `delete_connection` at 336, `create`/`test`). (2) New `ConnectionService.get_connection_display()` — fetches via the non-decrypted, tenant-scoped `conn_repo.get_connection()` and returns `_conn_dict(conn)` (no config). (3) Route calls `get_connection_display`. `get_connection_decrypted()` left intact for the server-side web/sync/execution callers (`views.py:1498/1549/1623`).
- **Root-cause check:** re-verified line numbers (322/323 route, 335/336 sibling admin gate, 490 `_conn_dict` staticmethod, `repo.get_connection` tenant-scoped). No frontend fetches the single API endpoint (grep clean), so the response-shape change breaks no consumer. MECHANICAL confirmed.
- **Proof:** `tests/integration/test_connection_authz.py::test_get_connection_is_admin_gated_and_secret_masked` — viewer/ba/tester → 403; admin → 200 with no `config` key and the secret marker absent from the body. **PASS.** Regression: `test_tenant_isolation` 5/5 PASS.

### SEC-2 — tenant-scope `add_member`/`add_environment` ✅ `cf9b6b6`
- **Was:** `service.py:549/561` — the group is tenant-checked but the client-supplied `user_id`/`environment_id` is written straight through; `group_members`/`group_environments` have no tenant column.
- **Change:** before the repo write, verify the linked `User`/`Environment` belongs to the caller's tenant (`self.group_repo.db.query(...).filter(id==, tenant_id==).first()`), raise `ValueError("... not found")` otherwise — the exact `release/service.py:114 add_requirement` pattern.
- **Root-cause check:** re-verified `GroupRepository.db` exists, `User.tenant_id`/`Environment.tenant_id` exist. `remove_member`/`remove_environment` left as-is (see OBSERVED — lower risk, out of the named scope). MECHANICAL confirmed.
- **Proof:** `tests/integration/test_tenant_isolation.py::test_add_member_environment_tenant_scoped` — foreign-tenant `user_id`/`environment_id` rejected (no join row); same-tenant links succeed. **PASS.** Suite now 6/6.

### SEC-3 + SEC-5 — Salesforce URL SSRF guard ✅ `5004271`
- **Was:** SEC-3 `create_environment` stored `sf_instance_url` with only a non-empty check, then `EnvironmentService.test_connection` (336) GET-ed `{sf_instance_url}/services/data/...` with the org access-token. SEC-5 `worker_runner._oauth_token` (162-179) POST-ed `client_secret`(+password) to `{login_url}/services/oauth2/token`. No scheme/host validation anywhere.
- **Change:** new **one shared validator** `primeqa/integrations/sf_url.validate_sf_instance_url` — positive-security allowlist (https + `*.salesforce.com`/`*.force.com`), IP-literal hosts rejected, pure + DNS-free (allowlist subsumes private-IP blocking without network I/O). Wired at: `create_environment` (write, raises→400); `EnvironmentService.test_connection` (before the GET, returns failed on reject); `ConnectionService.test_connection` SF branch (before the OAuth POST **and** before the data-API GET); `worker_runner._oauth_token` (before the POST — the shared S1+S4 chokepoint). Fail-loud (raise/failed, never a silent fallback).
- **Design note (recorded, not an escalation):** the plan's "resolve host and reject private IP" is realized as **allowlist + IP-literal reject without runtime DNS** — the allowlist already excludes every non-Salesforce host and IP literal, and avoids DNS I/O / rebinding surface in a validator called on every outbound call. The allowlist itself is named in the plan. I also guarded `ConnectionService.test_connection`'s SF branch (OAuth POST + data GET) — the same SSRF class as SEC-3/5, not separately cited but covered by "before every outbound call"; root-cause discipline (don't fix 336 while 452 leaks `client_secret` identically).
- **Proof:** `tests/unit/integrations/test_sf_url_validation.py` (23 cases — SF/sandbox/lightning hosts pass; `169.254.169.254`/loopback/RFC-1918/IPv6/non-SF/suffix-spoof/non-https rejected) + `tests/integration/test_ssrf_guard.py` (write path rejects SSRF URL before any DB write; accepts a valid SF URL). **PASS.** Full unit suite **3656 green**, no regression.

### SEC-4 — production gate on `POST /releases/<id>/run` ✅ `1c68942`
- **Was:** `views.py:3506` `@role_required("admin","tester")` (floor_tier = Member) → enqueues to `s4_execution_jobs` with no `is_production` check; the queued job runs as system (`caller_tier=None`), so the execution chokepoint's production-role rule is structurally skipped.
- **Change:** after the env is loaded, add the two-part gate the sibling enqueue boundaries (`api_s4_execution_enqueue` / the claim-run page) use — non-Admin blocked on `env.is_production` (`rank < Tier.ADMIN`), and `environment_can_bulk_run(env, confirm_production)` requiring explicit confirm. Added the `confirm_production` checkbox to the run form template.
- **Root-cause check:** re-verified `rank`/`Tier` imported (views.py:18), `is_production` is a real column, `environment_can_bulk_run` semantics. MECHANICAL confirmed.
- **Proof:** `tests/integration/test_release_run_prod_gate.py` — tester(with env access)+prod+confirm → blocked ("requires an Admin"); admin+prod+no-confirm → blocked ("confirmation required"); admin+prod+confirm → passes the gate. **PASS.**

### SEC-7 — production gate on the CI webhook ✅ `4971d77`
- **Was:** `release/routes.py:412` — after the A5 tenant guard, enqueues with no production check; machine caller (global `WEBHOOK_SECRET`), system-run job → chokepoint bypassed.
- **Decision (owner-resolved):** the plan marked SEC-7 NEEDS-DECISION+TA (lean: hard-reject prod). The **owner resolved it to Option C** — require `confirm_production` in the webhook body, fail closed on `is_production` without it. Implemented by **reusing `environment_can_bulk_run`** (same helper as SEC-4 — one resolver, consistent posture, honours `allow_bulk_run`).
- **Proof:** `tests/integration/test_ci_webhook.py::test_ci_webhook_production_gate` — HMAC-signed prod without confirm → 403; with confirm → passes; sandbox → passes. **PASS.**

### SEC-8 + SEC-P1 — JWT sign/verify via the fail-closed chokepoint ✅ `eb95058`
- **Was:** `views.py:25` bound `JWT_SECRET = os.getenv("JWT_SECRET","dev-secret-change-me")` at import and verified every web cookie with it (SEC-8); `core/service.py:28` signed tokens with the same default (SEC-P1) — both bypassing `core.secrets.get_jwt_secret()` (which refuses the dev-default in production).
- **Change:** route both through `get_jwt_secret()` (mirrors the already-migrated `core/auth.py`) — `views.get_current_user` resolves per-request; `service._get_jwt_secret` delegates; the `dev-secret-change-me` defaults deleted. Signer and verifier now resolve the SAME secret and fail closed in prod.
- **Proof:** `tests/unit/core/test_jwt_secret_chokepoint.py` (3) — signer delegates; a chokepoint-signed token verifies via `get_current_user` while a dev-default-forged token does not; `get_jwt_secret` raises in prod on unset/dev-default. **PASS.** Auth invariant suite green (sign→verify round-trip intact).

### SEC-9 — stop echoing upstream error bodies into the redirect URL ✅ `e051cfe`
- **Was:** `service.py` env/connection `test_connection` returned raw `resp.text`/`token_resp.text`/`str(e)` in `detail`; `views.py:1521` placed it verbatim into `redirect('/connections/<id>?message=...')` → history/Referer/proxy logs (and the API-JSON body).
- **Change (root cause = producer):** log the raw body/exception server-side (`logger.warning`), return a generic category message (e.g. `"Salesforce authentication failed (HTTP 401)."`). Consumer hardened: log the view's own exception instead of echoing it; URL-encode the message. The SEC-3 `SalesforceUrlError` message (ours, safe) preserved.
- **Proof:** `tests/unit/core/test_connection_test_no_leak.py` (2) — a marker in the upstream OAuth body / a raised exception does not appear in the returned `detail`; a generic message is returned. **PASS.**

### TEST-1 + CSRF carry-in — webhook coverage + fix stale CSRF test ✅ `3c4c8f4` (test-only)
- **TEST-1:** the CI webhook ingress had zero tests. Added the four security branches to `tests/integration/test_ci_webhook.py::test_ci_webhook_hmac_and_tenant_guard` — unset secret → 503; bad/missing HMAC → 401; A5 cross-tenant env → 404; valid same-tenant request clears HMAC+A5+prod and reaches enqueue (400 "no substrate claims"). **PASS (2/2 in the file).**
- **CSRF carry-in:** the R2 `t_agent_settings_update` POSTed `/settings/agent` with no CSRF token and asserted 200/302 (stale — the route is correct). Fixed the **test**: assert 403 with no token, then send the double-submit token (cookie == field) and assert 200/302. CSRF enforcement unchanged. **R2-3 now PASSES** (was FAIL).
- **Root-cause check:** confirmed the webhook route + CSRF enforcement are correct — this finding is test-only, no `primeqa/` change.

---

## ESCALATED — NEEDS DECISION
**None.** All 8 findings were mechanical as planned and were fixed. Two notes on judgment calls that did **not** rise to an escalation:
- **SEC-3/5** — the plan's "resolve host + reject private IP" was realized as an **allowlist + IP-literal reject without runtime DNS** (the allowlist already excludes non-Salesforce and IP-literal hosts, and avoids DNS I/O / rebinding in a validator called on every outbound call). The allowlist is named in the plan; this is an implementation realization, not a fork. Also guarded `ConnectionService.test_connection`'s SF branch (OAuth POST + data GET) — same SSRF class, "before every outbound call" per the plan; not separately cited.
- **SEC-7** — was NEEDS-DECISION+TA in the plan; the **owner pre-resolved** it (require `confirm_production`), so no escalation was needed — I implemented the owner-approved posture.

## OBSERVED, NOT FIXED (out of Wave-1 scope)
1. **`primeqa.runs.cost` carry-in (Wave-4/DEAD).** `test_r2_superadmin_suite` still fails on R2-5/R2-6 (`ModuleNotFoundError: No module named 'primeqa.runs.cost'`). Explicitly out of Wave-1 scope; **not touched**. (The other Wave-0 carry-in, the CSRF gap, WAS in scope and is fixed — R2-3 now passes.)
2. **`tests/test_environments.py` placeholder URLs.** This *uncollected* root file drives `EnvironmentService.create_environment` with `https://evil.com` / `https://test.com`; the new SEC-3 write-guard would reject those if that file were run (it isn't in `testpaths`, so no collected test breaks). Those tests would need updating when/if that file is wired into pytest — a test-hygiene follow-up, not a Wave-1 change.
3. **SEC-2 `remove_member`/`remove_environment`** carry the same "no tenant column on the join row" shape but are lower-risk (they only *unlink* an already-attached row; once `add_*` is guarded, no foreign link exists to remove). Left out of the named fix scope; a defense-in-depth follow-up.
4. **SEC-9 environments view.** The producer fix (`EnvironmentService.test_connection` now returns a generic `detail`) covers the `/environments/*` test path too; the `environments_test` view consumers already `quote()` their messages. Only the `connections_test` consumer's own-exception handler was additionally hardened (it was the one echoing `Error: {e}` raw).

## Final verification
- **Full unit suite:** **3661 passed, 0 failed** (`pytest tests/unit`). (+28 vs the 3633 Wave-0 baseline: the new sf_url/jwt/no-leak unit tests.)
- **Invariant/auth + Wave-1 security integration suites** (`-m "not sandbox and not live"` active): **14 passed, 1 failed** — `test_tenant_isolation` (6, incl. SEC-2), `test_auth` (1), `test_release_audit` (1), `test_connection_authz` (1, SEC-1), `test_ssrf_guard` (2, SEC-3), `test_release_run_prod_gate` (1, SEC-4), `test_ci_webhook` (2, SEC-7+TEST-1) all **PASS**; the lone failure is `test_r2_superadmin_suite`, red **only** on the `primeqa.runs.cost` carry-in (R2-1..R2-4, R2-7 pass; R2-3 CSRF now passes).
- **Scope:** every changed `primeqa/` file maps to a Wave-1 finding (`core/routes.py`→SEC-1; `core/service.py`→SEC-1/2/3/8/9; `integrations/sf_url.py`→SEC-3/5; `metadata/worker_runner.py`→SEC-5; `release/routes.py`→SEC-7; `templates/releases/detail.html`→SEC-4; `views.py`→SEC-4/8/9). **No Wave-2/3/4 code touched.**
- **Discipline:** one finding per commit, author `AK <amjad.khan@teamd.co.in>`, **zero `Co-Authored-By`**, no `--no-verify`, no force-push, no `__pycache__` staged. No assertion weakened; no silent fallback introduced (SSRF guard + SEC-9 both fail loud / log-then-refuse).

## Commits (branch `wave-1-security`, from `wave-0-test-infra`, NOT merged)
| Finding | Commit |
|---|---|
| SEC-1 | `84e1450` |
| SEC-2 | `cf9b6b6` |
| SEC-3 + SEC-5 | `5004271` |
| SEC-4 | `1c68942` |
| SEC-7 | `4971d77` |
| SEC-8 + SEC-P1 | `eb95058` |
| SEC-9 | `e051cfe` |
| TEST-1 + CSRF | `3c4c8f4` |

## 🛑 HOLD
Wave 1 is complete on `wave-1-security` (8 findings, 8 commits, all proven). **Not merged.** Remaining for your triage: the `primeqa.runs.cost` carry-in (Wave-4/DEAD) still reds `test_r2_superadmin_suite`. Wave-1 branches from the still-unmerged `wave-0-test-infra`, so merging Wave 1 pulls Wave 0 with it.
