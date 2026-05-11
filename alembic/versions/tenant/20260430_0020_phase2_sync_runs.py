"""Phase 2 tenant schema: sync_runs (per Phase 2 plan §3.3)

Second Phase 2 migration. Two operations:

  1. Creates the per-tenant sync_runs table — audit log of every sync
     invocation (success or failure). Per D-036 / D-048: status enum
     includes 'partial_success' for runs where structural sync
     committed but AI primitives failed (graceful fallback).

  2. Adds the deferred FK from connected_orgs.last_sync_run_id to
     sync_runs.id. This was deferred from 1B because sync_runs did
     not yet exist.

Schema notes:
  - id UUID PK with gen_random_uuid() default (pgcrypto in tenant
    schema per Phase 0 bootstrap).
  - source_org_id UUID NOT NULL FK to connected_orgs(id). Every sync
    run must point at a registered source org.
  - logical_version_seq BIGINT (not INT — plan §3.3 had a typo;
    logical_versions.version_seq is BIGINT, and all existing references
    to it across entities/edges/change_log are BIGINT). Nullable
    because the row is inserted with status='running' BEFORE a logical
    version is allocated, then backfilled on success.
  - status VARCHAR(20) NOT NULL with CHECK enum
    {running, success, partial_success, failure}.
  - started_at TIMESTAMPTZ NOT NULL DEFAULT NOW().
  - completed_at TIMESTAMPTZ — nullable while status='running'.
  - 8 counter columns (entities_inserted, entities_superseded,
    entities_unchanged, edges_inserted, edges_superseded,
    embeddings_generated, summaries_generated, summaries_failed) —
    all INT NOT NULL DEFAULT 0. summaries_failed > 0 with
    status='partial_success' is the canonical D-048 shape.
  - error_message / error_traceback TEXT, nullable. Populated on
    status='failure' or 'partial_success'.

Two CHECK constraints:
  - sync_runs_status_known: enum on status
  - sync_runs_completion_implies_terminal: status='running' iff
    completed_at IS NULL; terminal statuses iff completed_at IS NOT NULL

Deferred FK from 1B:
  ALTER TABLE connected_orgs ADD CONSTRAINT
  connected_orgs_last_sync_run_id_fkey FOREIGN KEY (last_sync_run_id)
  REFERENCES sync_runs(id).

Revision ID: 20260430_0020
Revises: 20260430_0010
Create Date: 2026-04-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260430_0020"
down_revision: Union[str, Sequence[str], None] = "20260430_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE sync_runs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_org_id UUID NOT NULL REFERENCES connected_orgs(id),
            logical_version_seq BIGINT REFERENCES logical_versions(version_seq),
            status VARCHAR(20) NOT NULL,
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMPTZ,
            entities_inserted INT NOT NULL DEFAULT 0,
            entities_superseded INT NOT NULL DEFAULT 0,
            entities_unchanged INT NOT NULL DEFAULT 0,
            edges_inserted INT NOT NULL DEFAULT 0,
            edges_superseded INT NOT NULL DEFAULT 0,
            embeddings_generated INT NOT NULL DEFAULT 0,
            summaries_generated INT NOT NULL DEFAULT 0,
            summaries_failed INT NOT NULL DEFAULT 0,
            error_message TEXT,
            error_traceback TEXT,
            CONSTRAINT sync_runs_status_known CHECK (
                status IN ('running', 'success', 'partial_success', 'failure')
            ),
            CONSTRAINT sync_runs_completion_implies_terminal CHECK (
                (status = 'running' AND completed_at IS NULL)
                OR (status IN ('success', 'partial_success', 'failure')
                    AND completed_at IS NOT NULL)
            )
        )
    """)

    op.execute("""
        ALTER TABLE connected_orgs
            ADD CONSTRAINT connected_orgs_last_sync_run_id_fkey
            FOREIGN KEY (last_sync_run_id) REFERENCES sync_runs(id)
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE connected_orgs
            DROP CONSTRAINT connected_orgs_last_sync_run_id_fkey
    """)
    op.execute("DROP TABLE sync_runs")
