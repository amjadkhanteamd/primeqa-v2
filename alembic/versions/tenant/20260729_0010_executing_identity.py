"""S4 run-as: persist the EXECUTING IDENTITY on execution runs.

Revision ID: 20260729_0010
Revises: 20260727_0011
Create Date: 2026-07-29

D-419 / D-421. Every run to date executed as the environment's single admin
service identity (D-266 pins it to System Administrator via the shared
credential — the coupling D-415 severed). The moment a second identity can
execute, any verdict that differs by identity — the entire point of run-as —
is unexplainable unless the identity is persisted with the run.

  * ``s4_execution_runs.executing_identity VARCHAR`` (nullable) — the
    Salesforce username the run executed as (the JWT ``sub``; also the S1
    ``User`` entity's ``sf_api_name``). Open text, no CHECK, no FK — the
    same posture as the other logical refs on this table.

**NULL means "not identity-scoped" — it does NOT mean "ran as admin".**
Every existing row is NULL because the ledger never recorded who executed;
backfilling the admin identity would assert something the ledger never
captured (the D-408 NULL-discipline applied to identity). Absence stays
absence: only a run that RESOLVED a run-as identity writes the column, via
the omit-when-None INSERT pattern (the D-275 batch-cols precedent), so the
writer is deploy-order independent.

Additive, nullable, no default, no backfill, no index (no reader filters on
it yet). Idempotent per the migrations-016+ convention.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '20260729_0010'
down_revision = '20260727_0011'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE s4_execution_runs "
        "ADD COLUMN IF NOT EXISTS executing_identity VARCHAR")
    op.execute(
        "COMMENT ON COLUMN s4_execution_runs.executing_identity IS "
        "'D-419/D-421: the Salesforce username this run executed as (JWT sub). "
        "NULL means NOT IDENTITY-SCOPED — it does not mean ran-as-admin; "
        "rows predating run-as (and every non-run-as row since) never "
        "recorded an identity, and absence stays absence.'")


def downgrade():
    op.execute(
        "ALTER TABLE s4_execution_runs DROP COLUMN IF EXISTS executing_identity")
