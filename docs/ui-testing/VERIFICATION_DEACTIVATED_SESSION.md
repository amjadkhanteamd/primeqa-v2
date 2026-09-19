# AUD-038 containment — a deactivated user is out on their next request

Branch `contain-deactivated-session` from `main` @9523b29 (after the audit's
run 2 merged). AK's GO of 2026-09-19: one containment slice, not a fix
programme; four items.

## What was true

`users.is_active` was read at login and at refresh, never on a request.
`POST /api/users/<id>/deactivate` set the column and committed directly,
skipping `AuthService.update_user` — the one place that revokes refresh
tokens on deactivation. On scratch a deactivated user's still-valid session
read pages, read the API and WROTE a requirement for the access token's
30-minute life; the API path left their refresh tokens live (refresh itself
re-checks the flag, so that window was bounded). The seventh self-reporting
control of the audit.

## What changed

| item | where | change |
|---|---|---|
| 1. the flag on every request | `primeqa/core/auth.py::session_is_active` — THE chokepoint | one indexed `SELECT is_active FROM users WHERE id AND tenant_id` per request, memoised on `g`; **fail closed** (a missing row, an inactive row, or a read that fails all refuse). `require_auth` (API) answers `401 ACCOUNT_INACTIVE`; `login_required` (web) redirects to `/login?reason=inactive` and clears the cookie. `require_tier`, `require_tier_api`, `role_required` and `require_role` all sit on these two, so every authenticated route is covered. |
| 2. one deactivation path | `views.py` `api_deactivate_user` / `api_activate_user` → `_set_user_active` → `AuthService.update_user` | the direct `u.is_active = …; db.commit()` DELETED from both; the last-superadmin guard MOVED from the API route's body into the service (`LastSuperadminError`, `UserRepository.count_active_superadmins`) so it holds on every path; the service's revocation failure is LOGGED at error, never silent (it was `except: pass`). The web edit and toggle routes already used the service. |
| 3. proof | `scratchpad/contain/prove.py` on scratch, and `tests/integration/test_deactivated_session.py` | below |
| 4. guard | `tests/unit/test_deactivation_paths.py` (11) | an AST guard: no module assigns a user row's `is_active` outside `core/service.py` + `core/repository.py` and no SQL literal updates it — proven RED on the pre-change `views.py` (naming lines 1392 and 1414) and on a planted rogue module; the chokepoint guard reads the real source (both decorators call `session_is_active`) and the behaviour (a stubbed inactive answer refuses before the view, the cookie is cleared, a failed read refuses); the service guard (revokes on deactivation, revokes nothing on a rename, the last superadmin refused, a second superadmin admits) |

## The proof (scratch, users 14 tester / 15 admin, a planted refresh token each time)

| step | result |
|---|---|
| member 14 active | GET /requirements 200 · GET /api/releases 200 · POST edit changes the row |
| **API deactivate** by admin 15 | 204; `is_active` false; live refresh tokens 1 → **0** |
| the SAME still-valid cookie, next request | GET /requirements **302 /login?reason=inactive, cookie cleared** · GET /api/releases **401 ACCOUNT_INACTIVE** · POST edit refused, row unchanged; refresh with the revoked token 401 |
| API activate | 204; the member is back (200 / 200 / the row changes) |
| **web toggle** by admin 15 | 302; `is_active` false; live refresh tokens 1 → **0**; next request refused the same way |
| the admin throughout | 200 / 200 |
| an active user's rename through the service | full_name changes, **no token revoked** |
| the last active superadmin | scratch has none (its admins are `admin`); the guard is proven in the unit test with a stubbed repository |

The audit's September POST sweep had left the scratch audit admin (15)
deactivated and tenant 1's requirements deleted; both were restored/re-planted
for this proof and the probe requirement removed afterwards.

## Classification

WRITE-FREE: no migration, no DDL, no data write added — the chokepoint adds
one SELECT per authenticated request; the routes now write through the
service they should always have used. Dumpless.

## Suites

| suite | result |
|---|---|
| sweeps first (D-490: dead links, undefined names, the authority table, the deactivation paths, close-2 call sites, 6a routes) | 47 passed |
| unit | **5,212 passed** (11 new in `test_deactivation_paths.py`) |
| DB-real corpus (26 suites + `test_deactivated_session.py`) | **189 passed, 4 skipped, 3 failed** — the three known reds identical on `main` (`test_phase5_authoring::test_a`, `test_report_slice::test_a`, `test_step_5_scope`) |
| harness | **454 passed, 6 failed** — the six calendar-rotted fixtures, identical on `main` |

Two suites needed a note. The route-authority table (D-497) mints tokens for a
user that exists in no database; the chokepoint now refuses such a session
before the tier gate speaks, so that gate stubs the account active — its
subject is the tier. And the audit's run-2 POST sweep had left scratch with
env 4 deactivated, tenant 1's requirements/groups/connections deleted, the
audit admin deactivated, and three checkpoint rows the old remover deleted by
the wrong key (a logical_versions seq instead of the checkpoint's NAME) —
each restored or repaired, and the remover fixed, before the gate read true.
