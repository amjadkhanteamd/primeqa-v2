"""Alembic environment for Substrate 1 schema-per-tenant migrations.

Two migration modes:

  shared     — runs migrations on the `shared` schema (control plane:
               tenants, users, billing, system_metadata).
               Invoked: `alembic -x mode=shared upgrade head`

  tenant     — runs migrations on a specific tenant schema (`tenant_<id>`).
               Requires -x tenant_id=<int>.
               Invoked: `alembic -x mode=tenant -x tenant_id=42 upgrade head`

  all_tenants — iterates every active tenant in shared.tenants and runs
                migrations on each. Sequential mode. Used for production
                rollouts.
                Invoked: `alembic -x mode=all_tenants upgrade head`

Each schema has its own `alembic_version` table (via version_table_schema),
so the `shared` schema and `tenant_<id>` schemas track migration state
independently. This is required by D-015: tenant schemas are isolated, so
their migration state is too.

Migration files live in two directories under alembic/versions/:
  shared/     — migrations that apply only to the shared schema
  tenant/     — migrations that apply to every tenant schema
"""

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool, text

from alembic import context

# Alembic Config object
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# DATABASE_URL must be set. We don't fall back to a default — fail loud.
db_url = os.environ.get("DATABASE_URL")
if not db_url:
    raise RuntimeError(
        "DATABASE_URL not set. Alembic env requires it explicitly. "
        "Use: DATABASE_URL=... alembic upgrade head"
    )
config.set_main_option("sqlalchemy.url", db_url)

# Read -x args
x_args = context.get_x_argument(as_dictionary=True)
mode = x_args.get("mode")
if mode not in ("shared", "tenant", "all_tenants"):
    raise RuntimeError(
        "Alembic invocation requires -x mode=<shared|tenant|all_tenants>. "
        "See alembic/env.py for usage."
    )


def get_target_schemas():
    """Return list of schemas to migrate based on mode."""
    if mode == "shared":
        return ["shared"]

    if mode == "tenant":
        tenant_id = x_args.get("tenant_id")
        if not tenant_id:
            raise RuntimeError("mode=tenant requires -x tenant_id=<int>")
        try:
            tid = int(tenant_id)
        except ValueError:
            raise RuntimeError(f"tenant_id must be int, got: {tenant_id!r}")
        return [f"tenant_{tid}"]

    if mode == "all_tenants":
        # Query shared.tenants for active tenant ids.
        connectable = engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )
        with connectable.connect() as conn:
            rows = conn.execute(text(
                "SELECT id FROM shared.tenants WHERE deleted_at IS NULL ORDER BY id"
            )).fetchall()
        if not rows:
            raise RuntimeError(
                "mode=all_tenants but no active tenants found in shared.tenants. "
                "Provision at least one tenant first."
            )
        return [f"tenant_{row[0]}" for row in rows]

    raise RuntimeError(f"unreachable mode: {mode!r}")


def run_migrations_for_schema(schema_name: str, connectable) -> None:
    """Run migrations against a single schema.

    Sets search_path to the target schema, sets app.tenant_id GUC for tenant
    schemas (so CHECK constraints don't fire during DDL when defaults are
    evaluated), uses version_table_schema so each schema tracks its own
    migration state.
    """
    is_tenant_schema = schema_name.startswith("tenant_")
    tenant_id = None
    if is_tenant_schema:
        try:
            tenant_id = int(schema_name.removeprefix("tenant_"))
        except ValueError:
            raise RuntimeError(f"malformed tenant schema name: {schema_name!r}")

    with connectable.connect() as connection:
        with connection.begin():
            # search_path puts the target schema first, so unqualified
            # table names resolve there.
            connection.execute(text(f'SET LOCAL search_path TO "{schema_name}", public'))

            # Tenant schemas: set the GUC so CHECK constraints
            # tenant_id = current_setting('app.tenant_id')::INT pass during
            # any data-touching DDL (rare in DDL but possible if a migration
            # backfills).
            if tenant_id is not None:
                connection.execute(
                    text("SET LOCAL app.tenant_id = :tid"),
                    {"tid": str(tenant_id)},
                )

            context.configure(
                connection=connection,
                target_metadata=None,
                version_table_schema=schema_name,
                # Subdirectory routing: tenant migrations live in versions/tenant,
                # shared migrations in versions/shared. Alembic walks both by
                # default; we use a branch label per directory to disambiguate.
                version_table="alembic_version",
                include_schemas=False,
                # Compare server_default to catch drift; opt out of type
                # comparisons because we use raw SQL ops (op.execute(...)) for
                # most things and don't want autogenerate noise.
                compare_server_default=False,
            )

            context.run_migrations()


def run_migrations_online() -> None:
    """Connect, then iterate the resolved schema list."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    schemas = get_target_schemas()
    print(f"alembic env: mode={mode}, schemas={schemas}")

    for schema in schemas:
        print(f"  -> migrating {schema}")
        run_migrations_for_schema(schema, connectable)


# Offline mode (alembic upgrade --sql) is not supported in this configuration.
# Offline-mode SQL generation can't resolve tenant schema lists ahead of time,
# and we don't need it for the dev or production workflow. Refuse explicitly.
if context.is_offline_mode():
    raise RuntimeError(
        "Offline mode (--sql) is not supported. The schema-per-tenant resolver "
        "requires a live connection to enumerate active tenants."
    )
else:
    run_migrations_online()
