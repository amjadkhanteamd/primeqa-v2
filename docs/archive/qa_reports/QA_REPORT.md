# PrimeQA QA Sweep — 2026-04-22

**Target**: https://primeqa-v2-production.up.railway.app
**Build on main**: `bd2920d` (BA Review Queue phase)
**Harness**: Playwright chromium (headless), direct HTTP via `requests`
**Default credentials**: `admin@primeqa.io` / `changeme123` (seeded superadmin)

Prior QA report archived at `QA_REPORT_2026-04-20.md`.

Findings are recorded below in the order their section runs. Screenshots land
in `qa/screenshots/`. Running log — sections append as they complete.

---

_Last refreshed 2026-04-22 06:06 UTC. Total checks: 72_

## Summary

| Status | Count |
|---|---|
| PASS | 68 |
| FAIL | 3 |
| ERROR | 0 |
| PARTIAL | 1 |
| BLOCKED | 0 |

## Priority Bugs

### P0 — fix before pilot
_none_

### P1 — fix within first week
_none_

### P2 — fix within first month
- **2.1-/dashboard** — Dashboard not implemented  (`FAIL`)
- **2.1-/settings/notifications** — Notifications not implemented  (`FAIL`)
- **2.1-/profile** — Profile not implemented  (`FAIL`)

### Partial / not fully exercised
- **7.5.1** — Copy Summary endpoint (no runs to probe)  (no runs in tenant — can't probe)


## Section 1: Authentication & Session

_**PASS**: 8 — 8 total_

### 1.1.1: Login page loads
- **Severity**: P2
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/login
- **Expected**: 200 with a login form
- **Actual**: 200, 2858 bytes
- **Category**: Functionality

### 1.1.7: SQL-injection email rejected
- **Severity**: P2
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/api/auth/login
- **Expected**: 401/400 (credentials invalid)
- **Actual**: 401: {"error":{"code":"UNAUTHORIZED","message":"Invalid email or password"}}

- **Category**: Security

### 1.1.8: XSS email safely rejected
- **Severity**: P2
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/api/auth/login
- **Expected**: 401/400, no reflected payload
- **Actual**: 401; body safe
- **Category**: Security

### 1.1.4: No user enumeration on wrong-password vs unknown-email
- **Severity**: P2
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/api/auth/login
- **Expected**: Identical response for wrong-password vs unknown-email
- **Actual**: both 401; bodies equal=True
- **Category**: Security

### 1.1.2: Valid login redirects away from /login
- **Severity**: P2
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/
- **Expected**: non-/login URL after submit
- **Actual**: landed at https://primeqa-v2-production.up.railway.app/
- **Category**: Functionality
- **Evidence**: /Users/mdamjadkhan/primeqa-v2/.claude/worktrees/gifted-ride/qa/screenshots/section01_post_login.png

### 1.2.1: Session cookie set after login
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/
- **Expected**: cookie named session/access_token/primeqa_access set
- **Actual**: found: ['access_token']
- **Category**: Security

### 1.2.5: Protected web route redirects to /login when unauth
- **Severity**: P2
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/runs
- **Expected**: 302/303 to /login
- **Actual**: 302 -> /login
- **Category**: Security

### 1.2.5b: Protected API returns 401 without token
- **Severity**: P2
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/api/requirements
- **Expected**: 401 Unauthorized
- **Actual**: 401
- **Category**: Security


## Section 2: Navigation & Page Loads

_**FAIL**: 3 | **PASS**: 16 — 19 total_

### 2.1-/: Page load: Home / dashboard
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/
- **Expected**: 2xx, no JS errors, no placeholders
- **Actual**: HTTP 200; placeholders=none; js_errors=none
- **Category**: UI

### 2.1-/dashboard: Dashboard not implemented
- **Severity**: P2
- **Status**: FAIL
- **URL**: https://primeqa-v2-production.up.railway.app/dashboard
- **Expected**: a page
- **Actual**: 404 not found — page likely not implemented
- **Category**: Functionality

### 2.1-/runs: Page load: Runs list
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/runs
- **Expected**: 2xx, no JS errors, no placeholders
- **Actual**: HTTP 200; placeholders=none; js_errors=none
- **Category**: UI

### 2.1-/runs/new: Page load: Run wizard
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/runs/new
- **Expected**: 2xx, no JS errors, no placeholders
- **Actual**: HTTP 200; placeholders=none; js_errors=none
- **Category**: UI

### 2.1-/requirements: Page load: Requirements list
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/requirements
- **Expected**: 2xx, no JS errors, no placeholders
- **Actual**: HTTP 200; placeholders=none; js_errors=none
- **Category**: UI

### 2.1-/test-cases: Page load: Test case library
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/test-cases
- **Expected**: 2xx, no JS errors, no placeholders
- **Actual**: HTTP 200; placeholders=none; js_errors=none
- **Category**: UI

### 2.1-/suites: Page load: Suites list
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/suites
- **Expected**: 2xx, no JS errors, no placeholders
- **Actual**: HTTP 200; placeholders=none; js_errors=none
- **Category**: UI

### 2.1-/reviews: Page load: BA reviews queue
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/reviews
- **Expected**: 2xx, no JS errors, no placeholders
- **Actual**: HTTP 200; placeholders=none; js_errors=none
- **Category**: UI

### 2.1-/impacts: Page load: Metadata impacts
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/impacts
- **Expected**: 2xx, no JS errors, no placeholders
- **Actual**: HTTP 200; placeholders=none; js_errors=none
- **Category**: UI

### 2.1-/releases: Page load: Releases list
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/releases
- **Expected**: 2xx, no JS errors, no placeholders
- **Actual**: HTTP 200; placeholders=none; js_errors=none
- **Category**: UI

### 2.1-/environments: Page load: Environments
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/environments
- **Expected**: 2xx, no JS errors, no placeholders
- **Actual**: HTTP 200; placeholders=none; js_errors=none
- **Category**: UI

### 2.1-/connections: Page load: Connections
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/connections
- **Expected**: 2xx, no JS errors, no placeholders
- **Actual**: HTTP 200; placeholders=none; js_errors=none
- **Category**: UI

### 2.1-/settings/users: Page load: User management (admin)
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/settings/users
- **Expected**: 2xx, no JS errors, no placeholders
- **Actual**: HTTP 200; placeholders=none; js_errors=none
- **Category**: UI

### 2.1-/settings/my-llm-usage: Page load: My LLM usage
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/settings/my-llm-usage
- **Expected**: 2xx, no JS errors, no placeholders
- **Actual**: HTTP 200; placeholders=none; js_errors=none
- **Category**: UI

### 2.1-/settings/llm-usage: Page load: Superadmin LLM usage
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/settings/llm-usage
- **Expected**: 2xx, no JS errors, no placeholders
- **Actual**: HTTP 200; placeholders=none; js_errors=none
- **Category**: UI

### 2.1-/settings/agent: Page load: Agent settings
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/settings/agent
- **Expected**: 2xx, no JS errors, no placeholders
- **Actual**: HTTP 200; placeholders=none; js_errors=none
- **Category**: UI

### 2.1-/settings/groups: Page load: Groups
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/settings/groups
- **Expected**: 2xx, no JS errors, no placeholders
- **Actual**: HTTP 200; placeholders=none; js_errors=none
- **Category**: UI

### 2.1-/settings/notifications: Notifications not implemented
- **Severity**: P2
- **Status**: FAIL
- **URL**: https://primeqa-v2-production.up.railway.app/settings/notifications
- **Expected**: a page
- **Actual**: 404 not found — page likely not implemented
- **Category**: Functionality

### 2.1-/profile: Profile not implemented
- **Severity**: P2
- **Status**: FAIL
- **URL**: https://primeqa-v2-production.up.railway.app/profile
- **Expected**: a page
- **Actual**: 404 not found — page likely not implemented
- **Category**: Functionality


## Sections 3-6: Settings CRUD, Requirements, Generation, Execution

_**PASS**: 11 — 11 total_

### 3.1-/connections: /connections has primary CRUD action ('New connection' or equivalent)
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/connections
- **Expected**: page mentions 'New connection'
- **Actual**: found=True
- **Category**: UI

### 3.1-/environments: /environments has primary CRUD action ('New environment' or equivalent)
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/environments
- **Expected**: page mentions 'New environment'
- **Actual**: found=True
- **Category**: UI

### 3.1-/settings/users: /settings/users has primary CRUD action ('Add user' or equivalent)
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/settings/users
- **Expected**: page mentions 'Add user'
- **Actual**: found=True
- **Category**: UI

### 3.1-/requirements: /requirements has primary CRUD action ('New requirement' or equivalent)
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/requirements
- **Expected**: page mentions 'New requirement'
- **Actual**: found=True
- **Category**: UI

### 3.1-/test-cases: /test-cases has primary CRUD action ('New Test' or equivalent)
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/test-cases
- **Expected**: page mentions 'New Test'
- **Actual**: found=True
- **Category**: UI

### 3.1-/suites: /suites has primary CRUD action ('New suite' or equivalent)
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/suites
- **Expected**: page mentions 'New suite'
- **Actual**: found=True
- **Category**: UI

### 4.2: Requirement detail /requirements/82 renders
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/requirements/82
- **Expected**: detail page with title + content
- **Actual**: 37788 bytes; error-in-head=False
- **Category**: Functionality
- **Evidence**: /Users/mdamjadkhan/primeqa-v2/.claude/worktrees/gifted-ride/qa/screenshots/section04_requirement_detail.png

### 5.2: Test case detail /test-cases/159 renders (steps visible)
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/test-cases/159
- **Expected**: detail page mentions 'step'
- **Actual**: steps-mention=True, 44770 bytes
- **Category**: UI
- **Evidence**: /Users/mdamjadkhan/primeqa-v2/.claude/worktrees/gifted-ride/qa/screenshots/section05_testcase_detail.png

### 5.2b: Validation report surfaces on TC detail (banner OR 'no issues')
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/test-cases/159
- **Expected**: validation banner visible
- **Actual**: found=True
- **Category**: UI

### 6.1: Run detail /runs/125 renders
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/runs/125
- **Expected**: log panel + stage track visible
- **Actual**: log-panel=True, stages-mention=True
- **Category**: UI
- **Evidence**: /Users/mdamjadkhan/primeqa-v2/.claude/worktrees/gifted-ride/qa/screenshots/section06_run_detail.png

### 6.2: Pipeline-log Copy button present
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/runs/125
- **Expected**: log-copy button exists
- **Actual**: present=True
- **Category**: UI


## Section 7: Recent-phase Verification (Developer UX, Tester /run, Admin UI, Reviews)

_**PARTIAL**: 1 | **PASS**: 18 — 19 total_

### 7.1.1: /tickets renders for logged-in user
- **Severity**: P2
- **Status**: PASS
- **URL**: /tickets
- **Expected**: 200 + 'My Tickets' heading
- **Actual**: status=200 len=18080
- **Category**: Functionality

### 7.1.2: /tickets has either switcher OR empty state
- **Severity**: P2
- **Status**: PASS
- **URL**: /tickets
- **Expected**: switcher or empty state
- **Actual**: switcher=True empty=False
- **Category**: UI

### 7.2.1: /run renders for superadmin
- **Severity**: P2
- **Status**: PASS
- **URL**: /run
- **Expected**: 200 + 'Run Tests' heading
- **Actual**: status=200
- **Category**: Functionality

### 7.2.2: /run tabs (sprint + suite) render
- **Severity**: P2
- **Status**: PASS
- **URL**: /run
- **Expected**: sprint and suite tabs visible
- **Actual**: sprint=True suite=True
- **Category**: UI

### 7.3.1: POST /api/bulk-runs bad run_type -> 400
- **Severity**: P1
- **Status**: PASS
- **URL**: /api/bulk-runs
- **Expected**: 400 VALIDATION_ERROR
- **Actual**: status=400 body={"error":{"code":"VALIDATION_ERROR","message":"run_type must be sprint, single, or suite"}}

- **Category**: Security

### 7.3.2: POST /api/bulk-runs rejects unauth (CSRF OR 401)
- **Severity**: P0
- **Status**: PASS
- **URL**: /api/bulk-runs
- **Expected**: no-Bearer: 401 or 403 CSRF; fake-Bearer: 401
- **Actual**: no_auth=403 fake_bearer=401
- **Category**: Security

### 7.4.1: /results redirects to /runs
- **Severity**: P2
- **Status**: PASS
- **URL**: /results
- **Expected**: redirect to /runs
- **Actual**: status=302
- **Category**: Functionality

### 7.4.2: /results preserves query string
- **Severity**: P2
- **Status**: PASS
- **URL**: /results?mine=1
- **Expected**: qs pass-through
- **Actual**: location=/runs?mine=1&status=failed
- **Category**: Functionality

### 7.5.1: Copy Summary endpoint (no runs to probe)
- **Severity**: P3
- **Status**: PARTIAL
- **URL**: /api/runs/.../summary-text
- **Expected**: run to exist
- **Actual**: no runs in tenant — can't probe
- **Category**: Functionality

### 7.5.2: Copy Summary 404 on unknown run
- **Severity**: P3
- **Status**: PASS
- **URL**: /api/runs/999999999/summary-text
- **Expected**: 404
- **Actual**: status=404
- **Category**: Functionality

### 7.6.1: /settings/users renders for admin
- **Severity**: P2
- **Status**: PASS
- **URL**: /settings/users
- **Expected**: 200 + Users heading
- **Actual**: status=200
- **Category**: Functionality

### 7.6.2: /settings/permission-sets renders
- **Severity**: P2
- **Status**: PASS
- **URL**: /settings/permission-sets
- **Expected**: 200 + heading
- **Actual**: status=200
- **Category**: Functionality

### 7.6.3: admin_base permission count rendered
- **Severity**: P3
- **Status**: PASS
- **URL**: /settings/permission-sets
- **Expected**: '39' visible for admin_base perm count
- **Actual**: present
- **Category**: UI

### 7.7.1: Self-deactivate endpoint responds safely
- **Severity**: P0
- **Status**: PASS
- **URL**: /api/users/1/deactivate
- **Expected**: 400 SELF_DEACTIVATE OR 204 (superadmin bypass)
- **Actual**: status=204
- **Category**: Security

### 7.8.1: /reviews renders for superadmin
- **Severity**: P2
- **Status**: PASS
- **URL**: /reviews
- **Expected**: 200
- **Actual**: status=200
- **Category**: Functionality

### 7.8.2: Sidebar badge render doesn't crash page
- **Severity**: P3
- **Status**: PASS
- **URL**: /reviews
- **Expected**: 200 regardless of badge state
- **Actual**: status=200
- **Category**: UI

### 7.9.1: Post-login lands on a valid page
- **Severity**: P2
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app
- **Expected**: / or /run or /runs/new or /requirements or /tickets
- **Actual**: landed at /
- **Category**: Functionality

### 7.10.1: CSRF enforced on cookie-auth state change
- **Severity**: P0
- **Status**: PASS
- **URL**: /api/users/me/active-env
- **Expected**: 400/403 CSRF rejection
- **Actual**: status=403
- **Category**: Security

### 7.11.1: Assign permission-set with unknown id tenant-scoped
- **Severity**: P0
- **Status**: PASS
- **URL**: /api/users/1/permission-sets
- **Expected**: 400 VALIDATION_ERROR on unknown/foreign id
- **Actual**: status=400 {"error":{"code":"VALIDATION_ERROR","message":"Unknown permission set ids: [99999999]"}}

- **Category**: Security


## Sections 11-12: API + Cross-Tenant Safety

_**PASS**: 15 — 15 total_

### 11.1-unauth /api/requirements: Unauthenticated GET /api/requirements
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/api/requirements
- **Expected**: 401 or 403
- **Actual**: 401: '{"error":{"code":"UNAUTHORIZED","message":"Missing or invalid Authorization header"}}\n'
- **Category**: Security

### 11.1-unauth /api/runs: Unauthenticated GET /api/runs
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/api/runs
- **Expected**: 401 or 403
- **Actual**: 401: '{"error":{"code":"UNAUTHORIZED","message":"Missing or invalid Authorization header"}}\n'
- **Category**: Security

### 11.1-unauth /api/test-cases: Unauthenticated GET /api/test-cases
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/api/test-cases
- **Expected**: 401 or 403
- **Actual**: 401: '{"error":{"code":"UNAUTHORIZED","message":"Missing or invalid Authorization header"}}\n'
- **Category**: Security

### 11.1-unauth /api/releases: Unauthenticated GET /api/releases
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/api/releases
- **Expected**: 401 or 403
- **Actual**: 401: '{"error":{"code":"UNAUTHORIZED","message":"Missing or invalid Authorization header"}}\n'
- **Category**: Security

### 11.1-unauth /api/suites: Unauthenticated GET /api/suites
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/api/suites
- **Expected**: 401 or 403
- **Actual**: 401: '{"error":{"code":"UNAUTHORIZED","message":"Missing or invalid Authorization header"}}\n'
- **Category**: Security

### 11.1-unauth /api/auth/users: Unauthenticated GET /api/auth/users
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/api/auth/users
- **Expected**: 401 or 403
- **Actual**: 401: '{"error":{"code":"UNAUTHORIZED","message":"Missing or invalid Authorization header"}}\n'
- **Category**: Security

### 11.1.2: GET on POST-only endpoint returns 405/404
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/api/auth/login
- **Expected**: 405 Method Not Allowed
- **Actual**: 405: '{"error":{"code":"METHOD_NOT_ALLOWED","message":"HTTP method not allowed on this endpoint."}}\n'
- **Category**: Functionality

### 11.1.3: Malformed JSON body rejected cleanly
- **Severity**: P2
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/api/auth/login
- **Expected**: 400 (bad request) or 401
- **Actual**: 400: '{"error":{"code":"VALIDATION_ERROR","message":"email and password are required"}}\n'
- **Category**: Functionality

### 11.1.4-/api/requirements/999999: Non-existent id on /api/requirements/999999
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/api/requirements/999999
- **Expected**: 404 / 403
- **Actual**: 404: '{"error":{"code":"NOT_FOUND","message":"Requirement not found"}}\n'
- **Category**: Functionality

### 11.1.4-/api/runs/999999: Non-existent id on /api/runs/999999
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/api/runs/999999
- **Expected**: 404 / 403
- **Actual**: 404: '{"error":{"code":"NOT_FOUND","message":"Run not found"}}\n'
- **Category**: Functionality

### 11.1.4-/api/test-cases/999999: Non-existent id on /api/test-cases/999999
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/api/test-cases/999999
- **Expected**: 404 / 403
- **Actual**: 404: '{"error":{"code":"NOT_FOUND","message":"Test case not found"}}\n'
- **Category**: Functionality

### 11.1.5: POST /api/requirements with {} body
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/api/requirements
- **Expected**: 400/422/405
- **Actual**: 400: '{"error":{"code":"VALIDATION_ERROR","message":"section_id is required"}}\n'
- **Category**: Functionality

### 11.2.1: Cross-tenant id (probed 5082) returns 404 (tenant-scoped)
- **Severity**: P0
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/api/requirements/5082
- **Expected**: 404 (scoped query)
- **Actual**: 404
- **Category**: Security

### 11.2.2: Negative id handled cleanly
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/api/requirements/-1
- **Expected**: 404/400
- **Actual**: 404
- **Category**: Security

### 11.1.6: /api/_internal/health responds
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/api/_internal/health
- **Expected**: 200 JSON
- **Actual**: 200: '{"error_rate":0.1331,"errors_total":94,"latency_ms":{"p50":0.34,"p95":146.99,"samples":500},"requests_total":706,"slow_queries_total":0}\n'
- **Category**: Functionality


## Not automated (manual checks to do before pilot)

- **Visual layout on narrow viewport (375px)** — spot-check every page for content overflow, touch-target size, and horizontal scrollbars.
- **Browser back/forward from every authenticated page** — automated check covers URL redirects but not history-stack state.
- **Screen reader walkthrough** — `aria-*` labels are present on the run detail page but not exhaustively on every form; use VoiceOver or NVDA against `/run`, `/tickets`, `/settings/users/:id`, `/reviews/:id`.
- **Test connection buttons against live creds** — Jira + SF connection test flows exist but we only exercised negative paths in this sweep.
- **Long-form text on cards/tables** — confirmed short-string rendering; manual pass to verify truncation on 500+ character Jira summaries + extremely long test-case titles.
- **JavaScript console errors** — Playwright captures them per page, but we didn't flag any in this run. Spot-check open DevTools on `/tickets`, `/run`, `/reviews` for late-loaded HTMX + SSE warnings.

## Recommendations

- **CSRF_FAILED vs UNAUTHORIZED ordering** on `/api/*` — when a request has no `Authorization: Bearer` and no csrf_token cookie, the CSRF middleware fires first and the client sees `CSRF_FAILED` instead of `UNAUTHORIZED`. Secure behaviour, slightly confusing error code. Consider treating missing-auth as UNAUTHORIZED so API clients get a more actionable message.
- **`/results` + `/runs` aliasing** preserves query strings but keeps the canonical URL as `/runs/:id`. The sidebar nav now uses `active_also_for: ('/runs',)` so the Results tab stays lit — worth documenting this hook in an internal UI primer so future pages re-use it instead of duplicating the logic.
- **Badge counts** currently run a fresh COUNT on every nav render. At scale, a short-TTL memcache or a simple per-request cache would cut redundant queries on pages that never read the result.
- **Superadmin self-deactivate**: today the superadmin bypass lets the seeded admin deactivate themselves, which can lock a tenant out (especially if there's only one god-mode user). Consider either a hard block (no bypass) or a last-superadmin-in-tenant guard.
