"""per-org Slice 3a: UNIQUE the connected_orgs.environment_id resolution (D-257).

The per-org resolution seam (``get_connected_org_for_environment``) reads
``SELECT id FROM connected_orgs WHERE environment_id = :eid`` and assumes exactly
one row. Today that 1:1 cardinality is enforced only by app convention
(``ensure_connected_org_for_environment`` SELECT-then-INSERT) on top of a
**non-unique** index (``idx_connected_orgs_environment_id``, migration
20260604_0010). This adds the DB guarantee: a partial UNIQUE index so a second
connected_org for the same environment_id can never be inserted.

PRE-CHECK (fail loud): before building the UNIQUE index, this asserts no tenant
already has >1 connected_org per environment_id — a duplicate would otherwise make
``CREATE UNIQUE INDEX`` crash opaquely mid-build. If a duplicate exists the
migration raises with the offending rows named, so the operator resolves it first.

Additive + deploy-safe: only adds a UNIQUE index (the existing non-unique
``idx_connected_orgs_environment_id`` is left in place — now redundant, a future
cleanup may drop it). Idempotent (``IF NOT EXISTS``). Per-tenant (tenant branch).
Apply with: ``alembic -x mode=all_tenants upgrade 20260623_0010``.

Revision ID: 20260623_0010
Revises: 20260622_0020
Create Date: 2026-06-23
"""
from alembic import op
from sqlalchemy import text


revision = "20260623_0010"
down_revision = "20260622_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PRE-CHECK: fail loud on any duplicate environment_id (per tenant schema).
    dups = op.get_bind().execute(text(
        "SELECT environment_id, count(*) AS c FROM connected_orgs "
        "WHERE environment_id IS NOT NULL "
        "GROUP BY environment_id HAVING count(*) > 1 "
        "ORDER BY environment_id"
    )).fetchall()
    if dups:
        detail = ", ".join(f"environment_id={r[0]} ({r[1]} rows)" for r in dups)
        raise RuntimeError(
            "per-org Slice 3a: connected_orgs has duplicate environment_id rows "
            f"[{detail}] — resolve (collapse to one connected_org per environment) "
            "before adding uq_connected_orgs_environment_id"
        )

    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_connected_orgs_environment_id "
        "ON connected_orgs(environment_id) WHERE environment_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_connected_orgs_environment_id")
