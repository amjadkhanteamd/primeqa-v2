# Phase 0 Plan — Substrate 1 Foundation

**Status:** Active
**Locked under:** D-024
**Duration:** Week 1 of 12
**Outcome:** Schema-per-tenant infrastructure live, three foundation tables (`logical_versions`, `entities`, `change_log`) creatable in tenant schemas via Alembic, GUC-asserted tenant isolation working end-to-end.

---

## What ships in Phase 0

**Infrastructure:**

- Alembic configured for two-mode migrations: `shared` (control plane) and `tenant` (per-tenant schemas).
- `alembic/env.py` resolves target schemas based on `-x mode=...` argument and sets search_path + GUC before running migrations.
- `primeqa/semantic/connection.py` providing the canonical access path: `get_tenant_connection(tenant_id)`, `admin_run_in_shared_schema()`, `admin_iterate_all_tenants(fn)`, `validate_search_path_takes_effect(tenant_id)`.
- Connection pool checkin hook resets `search_path` and `app.tenant_id` defensively on every connection return.

**Database state at end of Phase 0:**

- `shared` schema exists.
- `shared.tenants` table exists with provisioning constraints.
- `shared.provision_tenant_schema(tenant_id)` PL/pgSQL function exists.
- `shared.alembic_version` exists at revision `20260427_0001`.
- For every active tenant in `shared.tenants`, a `tenant_<id>` schema exists containing:
  - `logical_versions`, `entities`, `change_log` tables per SPEC §6
  - All indexes per SPEC §6
  - All CHECK constraints including `tenant_id = current_setting('app.tenant_id')::INT` assertions
  - `alembic_version` at revision `20260427_0010`

---

## What does NOT ship in Phase 0

Explicitly deferred to Phase 1 or later, per the lock:

- `edges` table (Phase 1)
- 14 Tier 1 edge types (Phase 1)
- 10 detail tables (Phase 1)
- Containment-as-column derivation logic (Phase 1)
- Pydantic validators for entity attributes (Phase 1)
- New sync engine (Phase 2)
- `effective_field_permissions` materialized view (Phase 2)
- `SemanticOrgModel` query class (Phase 3)
- Diff query primitives (Phase 3)
- Generation/validator/linter cutover (Phase 4)
- `meta_*` table drop (Phase 4)
- Pilot onboarding (Phase 5)

---

## Apply order (high level — see chat for step-by-step)

The detailed apply sequence is being driven step-by-step from the design conversation. The phases below are checkpoints, not standalone instructions.

**Step A — Revert D-023 scaffold.** Drop the `change_log` table from `public`, revert the D-023 commit on main.

**Step B — Land Phase 0 artifacts on the `phase-0-substrate-1` branch.** Five files: `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`, the two migration files, and `primeqa/semantic/connection.py`. Plus this PHASE_0_PLAN.md and the D-024 entry in DECISIONS_LOG.md.

**Step C — Apply the shared bootstrap migration.** `alembic -x mode=shared upgrade head`. Creates `shared` schema, `shared.tenants`, `provision_tenant_schema` function.

**Step D — Backfill tenants from public to shared, provision tenant schemas.** Map `public.tenants.status` to `shared.tenants.deleted_at`. Provision `tenant_<id>` schema for every active tenant.

**Step E — Apply the tenant migration to all tenants.** `alembic -x mode=all_tenants upgrade head`. Creates `logical_versions`, `entities`, `change_log` in every tenant schema.

**Step F — Smoke test.** Run `validate_search_path_takes_effect(1)`. Insert a genesis `logical_versions` row. Read it back. If everything passes, Phase 0 is done.

---

## Backfill SQL (used in Step D)

`public.tenants` uses `status='active'|'suspended'`, not `deleted_at`. The backfill maps these:

```sql
-- Backfill from public.tenants to shared.tenants
INSERT INTO shared.tenants (id, name, schema_name, created_at, deleted_at)
SELECT
    id,
    name,
    'tenant_' || id::text,
    COALESCE(created_at, NOW()),
    CASE WHEN status = 'suspended' THEN NOW() ELSE NULL END
FROM public.tenants
ON CONFLICT (id) DO NOTHING;

-- Reset sequence to max(id)+1
SELECT setval(
    pg_get_serial_sequence('shared.tenants', 'id'),
    COALESCE((SELECT MAX(id) FROM shared.tenants), 1),
    true
);

-- Provision schemas for active tenants
DO $BODY$
DECLARE
    tid INT;
BEGIN
    FOR tid IN SELECT id FROM shared.tenants WHERE deleted_at IS NULL ORDER BY id LOOP
        PERFORM shared.provision_tenant_schema(tid);
    END LOOP;
END $BODY$;
```

---

## Rollback procedure

If anything goes wrong before Step F passes:

```sql
-- Drop tenant schemas (data loss risk — only run if tenants are empty or test)
DO $BODY$
DECLARE
    tid INT;
BEGIN
    FOR tid IN SELECT id FROM shared.tenants LOOP
        EXECUTE format('DROP SCHEMA IF EXISTS tenant_%s CASCADE', tid);
    END LOOP;
END $BODY$;

-- Drop shared schema
DROP SCHEMA IF EXISTS shared CASCADE;
```

Then `git revert` the Phase 0 commits and merge back to main. Public schema is untouched throughout — v2 keeps running.

---

## Definition of Done

- [ ] D-023 reverted (commit reverted, change_log table dropped)
- [ ] D-024 in DECISIONS_LOG.md
- [ ] PHASE_0_PLAN.md committed (this file)
- [ ] Phase 0 code artifacts on `phase-0-substrate-1` branch
- [ ] `alembic -x mode=shared upgrade head` ran clean against Railway
- [ ] At least one tenant row backfilled into `shared.tenants`
- [ ] At least one `tenant_<id>` schema provisioned
- [ ] `alembic -x mode=all_tenants upgrade head` ran clean against Railway
- [ ] `validate_search_path_takes_effect(tenant_id)` passes
- [ ] Smoke test inserts and reads back a `logical_versions` row
- [ ] Phase 0 branch reviewed and merged to main

---

## What changes for v2 in Phase 0

**Nothing functional.** v2's `public` schema, `meta_*` tables, all routes, all workers, all generation flows continue to operate exactly as before. Phase 0 only adds; it does not modify.

The only v2 file that changes during Phase 0 is `requirements.txt` (adds Alembic). No imports change. No behaviour changes. The risk surface for v2 regression is approximately zero.

This is intentional: Phase 0 ships infrastructure, not features. Customer impact arrives in Phase 4 cutover.
