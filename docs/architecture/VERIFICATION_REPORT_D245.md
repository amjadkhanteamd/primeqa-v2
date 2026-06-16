# D-245 Verification Report — Authorization Model: Role Ladder × Environment Scope

Branch: `authz-role-ladder` · Decision: `DECISIONS_LOG.md` D-245 (design + REALIZED).
This report is the final-phase evidence for the redesign. Every claim below was run
against the code this build, not asserted.

## 1. What shipped

The additive **permission-set** authorization model was **deleted** and replaced by two
deliberately-separate axes:

1. **Role ladder** — `primeqa/core/authz.py`: `Tier` `Viewer(1) < Member(2) < Admin(3) <
   Superadmin(4)`. Stored DB `role` values + CHECK constraint unchanged; `rank(role)` maps each to
   a tier (`ba` and `tester` both → `Member`). One `>=` comparison answers "is this caller
   allowed?".
2. **Environment access scope** — Groups, via `EnvironmentRepository.list_environments` /
   `is_environment_accessible` (admin + superadmin see all).

Single enforcement path: `authorize(subject, min_tier, resource=None) -> (allow, reason)`, wrapped
by `require_tier` (web) / `require_tier_api` (API). Legacy `role_required` / `require_role` are now
thin wrappers over `authorize(floor_tier(roles))`. Three distinct, never-conflated errors:
`AuthorizationError` (role tier) · `PolicyError` (env `execution_policy`) · `NotExecutableError`
(substrate executability).

## 2. Phase ledger (all committed on `authz-role-ladder`, author AK, zero Co-Authored-By)

| Phase | What | Commit(s) |
|---|---|---|
| 0 | branch + D-245 decision + green baseline + `scripts/authz_inventory.py` + BEFORE inventory | `3c2bb54`, `7de4da6` |
| 1 | role ladder + `authorize()` (additive) + 92-case oracle | `885399f`, `2d675a3` |
| 2 | replacement role gates (double-gated, OUTER decorator, BEFORE deletion) | `ea685b6`, `1776924` |
| 3 | environment-scope chokepoint (`is_environment_accessible`) | `682d984`, `b8c9970` |
| 4 | production/policy dispatch gate + `ReadOnlyClient` Protocol + reaper guard | `a075522`, `3d44a10`, `d3ebb8e`, `eac1117` |
| 5 | delete the permission-set layer + migration 057 + test-corpus migration | `af94fb8`, `4f2bfc2` |
| 6 | role-lists → floor-tier wrappers + inline S3/S4 checks → decorators | `55c2ecf` + Phase-7 commit |
| 7 | role-UI relabel + docs + this report | (Phase-7 commit) |

## 3. Verification evidence

**App boots:** `python -c "import primeqa.app"` → OK.

**Unit suite (the per-phase interlock):** `python -m pytest tests/unit/` → **2771 passed**. Includes
`tests/unit/test_authz_ladder.py` (ladder + `authorize` + `floor_tier`, 106 cases),
`test_authz_decorators.py`, `test_authz_env_scope.py`, `test_authz_dispatch_gate.py`.

**Inventory regression oracle** (`scripts/authz_inventory.py`, BEFORE = `docs/authz_inventory_before.md`):

```
BEFORE 177 routes → AFTER 174 routes
VANISHED (3): api_assign_permission_sets, api_revoke_permission_set, settings_permission_sets
ADDED (0)
DOWNGRADED to (none): 0      ← no route dropped to login-only (the security invariant)
UPGRADED (none → gated): 2   ← api_s3_generation_enqueue, api_s4_execution_enqueue → MEMBER
```

The authorization surface is strictly improved: exactly the 3 permission-set-admin routes were
removed, nothing was downgraded, and two previously inline-gated job APIs are now decorator-gated at
Member.

**No dangling references to the deleted layer in production code:** grep for `PermissionSet`,
`UserPermissionSet`, `require_permission`, `require_page_permission`, `check_environment_policy`,
`get_effective_permissions`, `_resolve_effective_permissions`, `effective_permissions`,
`BASE_PERMISSION_SETS` across `primeqa/` returns only the surviving `_role_capabilities` shim,
`SharedDashboardLink` / `NotificationPreference`, and explanatory comments.

**Migration 057** drops `user_permission_sets` + `permission_sets` only (idempotent, CASCADE);
every other migration-039 column (env run-policy, `is_production`, `release_status`,
`shared_dashboard_links`, `notification_preferences`) is preserved.

## 4. Behavior changes (intended)

1. **BA-widening** — `ba` maps to `Member` (= `tester`), so any route whose old list named `tester`
   but not `ba` now admits `ba` (claim run/deprecate/quarantine; requirement/release/milestone edit;
   S3/S4 enqueue). This is the single intended behavior change called out in the design.
2. **Create-user default = Viewer (least privilege)** — the role dropdown now shows Viewer/Member/
   Admin; the old form defaulted to the first option (Admin). New users created without an explicit
   pick now land on Viewer instead of Admin.
3. **`finalize_decision` override restored to Admin** — a Phase-5 fallout (`g.effective_permissions`
   went dead → override silently narrowed to superadmin-only) was fixed; any Admin can override a
   NO-GO again, matching pre-Phase-5 intent.

## 5. Caveats requiring a real run

The 5 rewritten files under `tests/` are **Railway integration tests** (live DB + JWT minting),
which the build sandbox cannot execute. They were import/compile-checked and their pure-unit
portions verified directly, but **must be run on a real environment** to confirm green:

```
python tests/test_run_tests_page.py
python tests/test_developer_experience.py
python tests/test_results_page.py
python tests/test_tenant_isolation.py
python tests/test_dynamic_ui.py
```

## 6. Known residual (pre-existing, not introduced by D-245)

`AuthService.list_groups` scopes "see all" on `role == "admin"`, excluding superadmin from the
god-mode all-groups view. The ladder makes the fix trivial (`rank(role) >= Tier.ADMIN`); deferred
here to keep the change minimal. Flagged for a follow-up.

## 7. Not done by design (out of scope for D-245)

- The DB CHECK constraint on `users.role` and the stored role values are unchanged (5 values).
- Superadmin remains creatable only out-of-band (the form never mints superadmin — pre-existing).

## 8. Pre-merge adversarial review (6 dimensions) + fixes

A multi-agent adversarial review of the whole branch diff ran before merge. It caught **one genuine
security gap the green test suites missed**: the production-tier dispatch gate was enforced on the
synchronous run path but bypassed on the async/queued S4 path (the worker runs as system with no
`caller_tier`, so the production-role rule was inert; a Member could enqueue a mutating data-recipe
against production via `/api/s4-execution-jobs`). **Fixed** by deciding the production authorization
at the enqueue boundary (`api_s4_execution_enqueue` rejects a non-Admin enqueue against an
`is_production` env) + adding the env-scope (`is_environment_accessible`) check the route had skipped.
Also fixed: a `revoke_shared_links` UI/API tier mismatch, a misleading test docstring, and the dropped
cross-tenant env-scope coverage (re-homed as a real-DB assertion). Two consequences of the 5→4 tier
collapse (share/approve audience shift; `ba`→`tester` edit normalization) were surfaced for sign-off.
Full record: `DECISIONS_LOG` D-245 PRE-MERGE ADVERSARIAL REVIEW + FIXES.
