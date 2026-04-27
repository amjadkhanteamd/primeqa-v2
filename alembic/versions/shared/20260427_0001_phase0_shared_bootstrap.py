"""Phase 0 shared schema bootstrap

Creates:
  - `shared` schema
  - shared.tenants (control-plane: id, name, schema_name, deleted_at, ...)
  - shared.provision_tenant_schema(tenant_id) function — creates tenant_<id>
    schema with empty Substrate 1 tables ready for tenant-scoped migrations

Notes:
  - This migration runs against the `shared` schema only (mode=shared).
  - It does NOT create per-tenant schemas. Those are provisioned at tenant
    onboarding via the provision function.
  - It does NOT create `users`, `billing`, `system_metadata` yet — those move
    in from v2's public schema during Phase 4 cutover and get their own
    migration. Phase 0 only needs `tenants` because Alembic's all_tenants
    mode reads from it.

Revision ID: 20260427_0001
Revises:
Create Date: 2026-04-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260427_0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = ("shared",)
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS shared")

    # Control-plane: tenants. Minimum viable shape for Phase 0. Onboarding
    # flows (name validation, billing tier, etc.) extend in later migrations.
    op.execute("""
        CREATE TABLE IF NOT EXISTS shared.tenants (
            id SERIAL PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            schema_name VARCHAR(63) NOT NULL UNIQUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            deleted_at TIMESTAMPTZ,

            -- Schema name must match the conventional pattern. Belt-and-braces:
            -- the provision function generates it, but a stray INSERT shouldn't
            -- be able to break the convention.
            CONSTRAINT tenants_schema_name_pattern
                CHECK (schema_name ~ '^tenant_[0-9]+$'),

            -- The schema_name must match the id. Cross-column invariant.
            CONSTRAINT tenants_schema_name_matches_id
                CHECK (schema_name = 'tenant_' || id::text)
        )
    """)

    op.execute("CREATE INDEX IF NOT EXISTS idx_tenants_active ON shared.tenants(id) WHERE deleted_at IS NULL")

    # Provisioning function. Creates tenant_<id> schema and grants usage.
    # Does NOT create the S1 tables themselves — those arrive via the tenant
    # branch of Alembic migrations once the schema exists.
    #
    # Caller workflow:
    #   1. INSERT INTO shared.tenants (...) RETURNING id
    #   2. SELECT shared.provision_tenant_schema(<id>)
    #   3. alembic -x mode=tenant -x tenant_id=<id> upgrade head
    #
    # The three-step split keeps DDL (CREATE SCHEMA) separate from data
    # writes, which matters for transactional rollback semantics — CREATE
    # SCHEMA in PostgreSQL IS transactional but mixing DDL with row inserts
    # creates surprising lock interactions. Keep them in separate calls.
    op.execute("""
        CREATE OR REPLACE FUNCTION shared.provision_tenant_schema(p_tenant_id INT)
        RETURNS VOID
        LANGUAGE plpgsql
        AS $func$
        DECLARE
            v_schema_name TEXT;
        BEGIN
            -- Verify the tenant row exists. This catches the "you forgot
            -- step 1" case loudly instead of silently creating a schema
            -- with no owning tenant row.
            IF NOT EXISTS (
                SELECT 1 FROM shared.tenants
                WHERE id = p_tenant_id AND deleted_at IS NULL
            ) THEN
                RAISE EXCEPTION
                    'tenant % not found in shared.tenants (or deleted). '
                    'Insert into shared.tenants first.',
                    p_tenant_id;
            END IF;

            v_schema_name := 'tenant_' || p_tenant_id::text;

            -- IF NOT EXISTS makes this idempotent. Safe to call twice
            -- during a botched onboarding.
            EXECUTE format('CREATE SCHEMA IF NOT EXISTS %I', v_schema_name);

            -- gen_random_uuid() requires pgcrypto. Install in tenant schema
            -- so each tenant's UUID generator is local; avoids any
            -- cross-tenant function-resolution surprises.
            EXECUTE format(
                'CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA %I',
                v_schema_name
            );
        END;
        $func$
    """)


def downgrade() -> None:
    # Downgrade is destructive of all tenant schemas. We refuse it. To unwind,
    # do it manually with full awareness of the data loss.
    raise NotImplementedError(
        "Downgrade of the shared schema bootstrap is refused. To unwind, "
        "drop tenant schemas individually, then DROP SCHEMA shared CASCADE, "
        "with full awareness that this destroys all customer data."
    )
