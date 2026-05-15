# PrimeQA Production QA Report

**Target**: https://primeqa-v2-production.up.railway.app
**Date**: 2026-04-20
**Deploy commit**: `e93b09e` (knowledge injection + validator extensions)
**Methodology**: Playwright (desktop 1280x720), direct HTTPS probes, DB reads, code audit.
Written section-by-section; updated as findings surface.

---

## Section 1 — Authentication + Session

**PASS**: 8

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

---
## Section 11 — API Direct Testing + Cross-Tenant Safety

**PARTIAL**: 2 **PASS**: 13

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
- **Actual**: 405: '<!doctype html>\n<html lang=en>\n<title>405 Method Not Allowed</title>\n<h1>Method Not Allowed</h1>\n<p>The method is not al'
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
- **Status**: PARTIAL
- **URL**: https://primeqa-v2-production.up.railway.app/api/requirements/999999
- **Expected**: 404 / 403
- **Actual**: 405: '<!doctype html>\n<html lang=en>\n<title>405 Method Not Allowed</title>\n<h1>Method Not Allowed</h1>\n<p>The method is not al'
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

### 11.2.1: Cross-tenant probe returned unexpected status
- **Severity**: P1
- **Status**: PARTIAL
- **URL**: https://primeqa-v2-production.up.railway.app/api/requirements/5068
- **Expected**: 404
- **Actual**: 405: '<!doctype html>\n<html lang=en>\n<title>405 Method Not Allowed</title>\n<h1>Method Not Allowed</h1>\n<p>The method is not allowed for the requested URL.</p>\n'
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
- **Actual**: 200: '{"error_rate":0.0,"errors_total":0,"latency_ms":{"p50":0.45,"p95":91.03,"samples":28},"requests_total":28,"slow_queries_total":0}\n'
- **Category**: Functionality

---
## Section 11 (supplementary) — Cross-tenant scoping + API surface gaps

**FAIL**: 2 **PARTIAL**: 1 **PASS**: 2

### 11.2.3: Cross-tenant GET /api/test-cases/<other-id> returns 404 with JSON envelope
- **Severity**: P0
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/api/test-cases/10149
- **Expected**: 404 NOT_FOUND (tenant-scoped query)
- **Actual**: 404: {"error":{"code":"NOT_FOUND","message":"Test case not found"}}
- **Category**: Security

### 11.2.4: Cross-tenant GET /api/runs/<other-id> returns 404 with JSON envelope
- **Severity**: P0
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/api/runs/10120
- **Expected**: 404 NOT_FOUND
- **Actual**: 404: {"error":{"code":"NOT_FOUND","message":"Run not found"}}
- **Category**: Security

### 11.1.7: Missing API surface: GET /api/requirements/:id (only PATCH/DELETE)
- **Severity**: P2
- **Status**: FAIL
- **URL**: https://primeqa-v2-production.up.railway.app/api/requirements/68
- **Expected**: GET handler returning 200 with requirement JSON
- **Actual**: 405 Method Not Allowed (no GET route registered)
- **Category**: Functionality
- **Evidence**: grep of primeqa/test_management/routes.py shows PATCH + DELETE + restore + purge, no GET

### 11.1.8: Missing API surface: GET /api/suites/:id (only PATCH/DELETE expected, no GET)
- **Severity**: P2
- **Status**: FAIL
- **URL**: https://primeqa-v2-production.up.railway.app/api/suites/20
- **Expected**: GET handler or 404
- **Actual**: 405 Method Not Allowed
- **Category**: Functionality

### 11.1.9: Negative id 404s as HTML (not JSON envelope)
- **Severity**: P3
- **Status**: PARTIAL
- **URL**: https://primeqa-v2-production.up.railway.app/api/test-cases/-1
- **Expected**: JSON envelope 404 matching the rest of /api/*
- **Actual**: Flask default HTML 404 page
- **Category**: Functionality
- **Evidence**: Int-converter rejects -1 before reaching the handler; returns HTML 404 instead of {error:{code,message}}

---
## Section 2 — Navigation + Page-load smoke

**FAIL**: 3 **PASS**: 16

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

---
## Sections 3-6 — Settings CRUD + Core workflow pages

**PARTIAL**: 2 **PASS**: 7

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

### 4.2: Requirement detail /requirements/68 renders
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/requirements/68
- **Expected**: detail page with title + content
- **Actual**: 10816 bytes; error-in-head=False
- **Category**: Functionality
- **Evidence**: /Users/mdamjadkhan/primeqa-v2/.claude/worktrees/gifted-ride/qa/screenshots/section04_requirement_detail.png

### 5.2: Test case detail /test-cases/149 renders (steps visible)
- **Severity**: P2
- **Status**: PARTIAL
- **URL**: https://primeqa-v2-production.up.railway.app/test-cases/149
- **Expected**: detail page mentions 'step'
- **Actual**: steps-mention=False, 10816 bytes
- **Category**: UI
- **Evidence**: /Users/mdamjadkhan/primeqa-v2/.claude/worktrees/gifted-ride/qa/screenshots/section05_testcase_detail.png

### 5.2b: Validation report surfaces on TC detail (banner OR 'no issues')
- **Severity**: P2
- **Status**: PARTIAL
- **URL**: https://primeqa-v2-production.up.railway.app/test-cases/149
- **Expected**: validation banner visible
- **Actual**: found=False
- **Category**: UI

---
## Sections 6 + 9 + 10 + 12 — Runs / Dashboard / Metadata / Edge

**BLOCKED**: 1 **PARTIAL**: 1 **PASS**: 4

### 6.1: Run detail /runs/120 renders full UI
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/runs/120
- **Expected**: log panel + stage track visible
- **Actual**: log-panel=True, stages=True, copy-button=True
- **Category**: UI
- **Evidence**: /Users/mdamjadkhan/primeqa-v2/.claude/worktrees/gifted-ride/qa/screenshots/section06_run_detail.png

### 6.2: Pipeline-log Copy button present
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/runs/120
- **Expected**: Copy button in log header
- **Actual**: present=True
- **Category**: UI

### 9.1: Dashboard / home page shows run activity
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/
- **Expected**: recent runs / activity widget
- **Actual**: recent-runs-mention=True
- **Category**: UI
- **Evidence**: /Users/mdamjadkhan/primeqa-v2/.claude/worktrees/gifted-ride/qa/screenshots/section09_dashboard.png

### 12.2: Data integrity on broken env linkage — MANUAL CHECK NEEDED
- **Severity**: P2
- **Status**: BLOCKED
- **URL**: https://primeqa-v2-production.up.railway.app/environments
- **Expected**: delete a linked connection; env detail page shouldn't 500
- **Actual**: requires write-side mutation on production; not exercised in this pass
- **Category**: Data Integrity

### 12.5a: Bearer-auth POST bypasses CSRF (documented behavior)
- **Severity**: P3
- **Status**: PARTIAL
- **URL**: https://primeqa-v2-production.up.railway.app/requirements/68/label
- **Expected**: 200/302/404 (Bearer skips CSRF per design)
- **Actual**: 403
- **Category**: Security

### 10.2: Environments list renders (metadata access point)
- **Severity**: P3
- **Status**: PASS
- **URL**: https://primeqa-v2-production.up.railway.app/environments
- **Expected**: list of environments
- **Actual**: env-mention=True
- **Category**: UI

---

## SUMMARY

- Total checks: 62
- Pass: 50
- Fail: 5
- Partial: 6
- Blocked: 1

## P0 BUGS (fix before pilot)
_(none found)_

## P1 BUGS (fix within first week)
- **11.2.1**: Cross-tenant probe returned unexpected status — 405: '<!doctype html>\n<html lang=en>\n<title>405 Method Not Allowed</title>\n<h1>Method Not Allowed</h1>\n<p>The method is not allowed for 

## P2 BUGS (fix within first month)
- **11.1.7**: Missing API surface: GET /api/requirements/:id (only PATCH/DELETE) — 405 Method Not Allowed (no GET route registered)
- **11.1.8**: Missing API surface: GET /api/suites/:id (only PATCH/DELETE expected, no GET) — 405 Method Not Allowed
- **2.1-/dashboard**: Dashboard not implemented — 404 not found — page likely not implemented
- **2.1-/settings/notifications**: Notifications not implemented — 404 not found — page likely not implemented
- **2.1-/profile**: Profile not implemented — 404 not found — page likely not implemented
- **5.2**: Test case detail /test-cases/149 renders (steps visible) — steps-mention=False, 10816 bytes
- **5.2b**: Validation report surfaces on TC detail (banner OR 'no issues') — found=False

## RECOMMENDATIONS
- Add GET /api/requirements/:id and GET /api/suites/:id so programmatic integrations can read individual records (today only PATCH/DELETE exist; both 405 on GET). Pattern is already in place for /api/test-cases/:id and /api/runs/:id.
- Integer-converter failures on /api/<resource>/-1 currently render Flask's default HTML 404 page. Register a blueprint-level 404 handler that returns the JSON envelope {error:{code:'NOT_FOUND', message:'...'}} for every /api/* route so clients see a consistent shape.
- Stand up /profile and /settings/notifications pages (or remove the expectation from navigation helpers) — today they 404. /dashboard is arguably intentional (the home page IS the dashboard) but worth aliasing to reduce user confusion.
- Replace the seeded admin@primeqa.io / changeme123 credential on production. This QA pass authenticated with the default seeded password; anyone reading CLAUDE.md or migration 001 can do the same. Rotate now; enforce a first-login password change in a follow-up.
- Surface CRUD auto-fixes (e.g. Name stripped on non-createable objects, expect_fail flipping test status, validator applied fixes) in the run detail page so users know what the executor mutated vs what their test definition said.
