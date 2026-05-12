"""Phase 2 tenant schema: sync engine phase tracking + AI enrichment queue

Per PHASE_2_STEP_4_SYNC_DESIGN.md §2 (state machine), §3 (staged transactional
boundaries), §6 (async enrichment architecture), and §4 (ENTITY_ORDER).

Four changes, single migration:

  1. CREATE TABLE ai_enrichment_queue — the in-flight work table for the
     async enrichment worker. One row per (entity, primitive_type)
     combination needing AI primitive generation. Sync engine UPSERTs on
     the (entity_type, entity_id, primitive_type) UNIQUE constraint when
     it commits a structural write; worker process polls pending rows.
     Per design doc §6.

  2. ALTER connected_orgs ADD COLUMN ai_enrichment_status — aggregate
     state surfaced to downstream consumers (substrate-2 test generation,
     retrieval features). Enum {none, structural_only, partial,
     complete}. Allows consumers to adapt to partial enrichment rather
     than blocking on it. Per design doc §6 "Feature readiness signaling".

  3. ALTER sync_runs ADD COLUMN last_completed_phase — phase resumability
     after mid-sync failure. Records the most recent entity-type phase
     that committed; next sync_run reads this and resumes from the next
     phase. Per design doc §2 "Phase tracking" and §3 "Mechanics".
     Values match ENTITY_ORDER from design doc §4.

  4. ALTER sync_runs ADD COLUMN phase — current phase of the sync_run,
     orthogonal to status. phase tracks where in the run we are
     (structural | enrichment | done); status tracks success/failure.
     A sync_run can be (phase=enrichment, status=running) — the existing
     status enum was conflating these axes. Separating them keeps the
     status_known + completion_implies_terminal CHECK constraints
     stable from migration 20260430_0020. Per design doc §2 state
     machine and the orthogonality argument settled during architectural
     review.

Schema notes for ai_enrichment_queue:

  - id UUID PK with gen_random_uuid() default (pgcrypto in tenant
    schema per Phase 0 bootstrap).
  - entity_type VARCHAR(50) NOT NULL with CHECK enum matching
    ENTITY_ORDER (12 values).
  - entity_id UUID NOT NULL. NO FK to entities(id) — per design doc §6,
    worker handles missing entities (deleted between enqueue and
    pickup) as failed_permanent. FK would force tight coupling between
    entity lifecycle and queue lifecycle that we don't want.
  - primitive_type VARCHAR(20) NOT NULL with CHECK enum
    {embedding, summary}.
  - status VARCHAR(20) NOT NULL DEFAULT 'pending' with CHECK enum
    {pending, in_progress, succeeded, failed_retryable, failed_permanent}.
  - enqueued_at, started_at, completed_at TIMESTAMPTZ.
    enqueued_at NOT NULL DEFAULT NOW(); started_at + completed_at
    nullable, gated by status via CHECK constraint.
  - attempts INT NOT NULL DEFAULT 0 — retry counter.
  - error_text TEXT — populated on failed_* status.

Four CHECK constraints + one UNIQUE constraint:

  - ai_enrichment_queue_entity_type_known: 12-value enum
  - ai_enrichment_queue_primitive_type_known: 2-value enum
  - ai_enrichment_queue_status_known: 5-value enum
  - ai_enrichment_queue_status_implies_timing: pending → no
    started/completed; in_progress → started, no completed; terminal
    states → both started and completed.
  - ai_enrichment_queue_entity_primitive_unique UNIQUE
    (entity_type, entity_id, primitive_type): at most one queue row
    per (entity, primitive) combination. Sync engine UPSERTs on this
    constraint.

Two indexes:

  - ai_enrichment_queue_pickup_idx ON (status, enqueued_at):
    worker scans pending rows in enqueue order.
  - ai_enrichment_queue_stalled_idx ON (status, started_at)
    WHERE status = 'in_progress':
    janitor scans for in_progress rows abandoned by crashed workers
    (started_at exceeds a timeout).

Revision ID: 20260512_0010
Revises: 20260430_0040
Create Date: 2026-05-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260512_0010"
down_revision: Union[str, Sequence[str], None] = "20260430_0040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. CREATE ai_enrichment_queue
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE ai_enrichment_queue (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            entity_type VARCHAR(50) NOT NULL,
            entity_id UUID NOT NULL,
            primitive_type VARCHAR(20) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            enqueued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            attempts INT NOT NULL DEFAULT 0,
            started_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            error_text TEXT,
            CONSTRAINT ai_enrichment_queue_entity_type_known CHECK (
                entity_type IN (
                    'Object', 'PicklistValueSet', 'PicklistValue',
                    'Field', 'RecordType', 'Layout', 'ValidationRule',
                    'Profile', 'PermissionSet', 'User',
                    'FlowDefinition', 'Flow'
                )
            ),
            CONSTRAINT ai_enrichment_queue_primitive_type_known CHECK (
                primitive_type IN ('embedding', 'summary')
            ),
            CONSTRAINT ai_enrichment_queue_status_known CHECK (
                status IN (
                    'pending', 'in_progress',
                    'succeeded', 'failed_retryable', 'failed_permanent'
                )
            ),
            CONSTRAINT ai_enrichment_queue_status_implies_timing CHECK (
                (status = 'pending'
                    AND started_at IS NULL AND completed_at IS NULL)
                OR (status = 'in_progress'
                    AND started_at IS NOT NULL AND completed_at IS NULL)
                OR (status IN ('succeeded', 'failed_retryable', 'failed_permanent')
                    AND started_at IS NOT NULL AND completed_at IS NOT NULL)
            ),
            CONSTRAINT ai_enrichment_queue_entity_primitive_unique UNIQUE (
                entity_type, entity_id, primitive_type
            )
        )
    """)

    # Worker-pickup index: scan pending rows in enqueue order.
    op.execute("""
        CREATE INDEX ai_enrichment_queue_pickup_idx
            ON ai_enrichment_queue (status, enqueued_at)
    """)

    # Janitor index: scan stalled in_progress rows by started_at age.
    op.execute("""
        CREATE INDEX ai_enrichment_queue_stalled_idx
            ON ai_enrichment_queue (status, started_at)
            WHERE status = 'in_progress'
    """)

    op.execute("""
        COMMENT ON TABLE ai_enrichment_queue IS
            'In-flight AI enrichment work. One row per (entity, '
            'primitive_type). Sync engine UPSERTs on structural write; '
            'worker polls pending rows. Per PHASE_2_STEP_4_SYNC_DESIGN.md §6.'
    """)

    op.execute("""
        COMMENT ON COLUMN ai_enrichment_queue.entity_id IS
            'UUID of the entities.id row this work targets. NO FK '
            'constraint — worker handles missing entities as '
            'failed_permanent per design doc §6.'
    """)

    op.execute("""
        COMMENT ON COLUMN ai_enrichment_queue.attempts IS
            'Retry counter. Incremented on each pickup attempt. '
            'When attempts >= retry limit (default 5), worker '
            'transitions status to failed_permanent and fires an '
            'operator alert.'
    """)

    # ------------------------------------------------------------------
    # 2. ALTER connected_orgs ADD COLUMN ai_enrichment_status
    # ------------------------------------------------------------------
    op.execute("""
        ALTER TABLE connected_orgs
            ADD COLUMN ai_enrichment_status VARCHAR(20)
                NOT NULL DEFAULT 'none'
    """)

    op.execute("""
        ALTER TABLE connected_orgs
            ADD CONSTRAINT connected_orgs_ai_enrichment_status_known
            CHECK (
                ai_enrichment_status IN (
                    'none', 'structural_only', 'partial', 'complete'
                )
            )
    """)

    op.execute("""
        COMMENT ON COLUMN connected_orgs.ai_enrichment_status IS
            'Aggregate AI enrichment state for this org. Updated by '
            'sync engine post-structural and by enrichment worker on '
            'completion. Per design doc §6.'
    """)

    # ------------------------------------------------------------------
    # 3. ALTER sync_runs ADD COLUMN last_completed_phase
    # ------------------------------------------------------------------
    op.execute("""
        ALTER TABLE sync_runs
            ADD COLUMN last_completed_phase VARCHAR(50)
    """)

    op.execute("""
        ALTER TABLE sync_runs
            ADD CONSTRAINT sync_runs_last_completed_phase_known
            CHECK (
                last_completed_phase IS NULL OR
                last_completed_phase IN (
                    'Object', 'PicklistValueSet', 'PicklistValue',
                    'Field', 'RecordType', 'Layout', 'ValidationRule',
                    'Profile', 'PermissionSet', 'User',
                    'FlowDefinition', 'Flow'
                )
            )
    """)

    op.execute("""
        COMMENT ON COLUMN sync_runs.last_completed_phase IS
            'Most recent entity-type phase that committed successfully '
            'in this sync_run. NULL means no phase has completed yet. '
            'Used for resumability per design doc §3. Values match '
            'ENTITY_ORDER from design doc §4.'
    """)

    # ------------------------------------------------------------------
    # 4. ALTER sync_runs ADD COLUMN phase
    # ------------------------------------------------------------------
    op.execute("""
        ALTER TABLE sync_runs
            ADD COLUMN phase VARCHAR(20)
                NOT NULL DEFAULT 'structural'
    """)

    op.execute("""
        ALTER TABLE sync_runs
            ADD CONSTRAINT sync_runs_phase_known
            CHECK (phase IN ('structural', 'enrichment', 'done'))
    """)

    op.execute("""
        COMMENT ON COLUMN sync_runs.phase IS
            'Current phase of this sync_run. Orthogonal to status: '
            'phase tracks where in the run we are; status tracks '
            'success/failure. A sync_run can be (phase=enrichment, '
            'status=running) or (phase=enrichment, status=partial_success). '
            'Per design doc §2 state machine.'
    """)


def downgrade() -> None:
    # Reverse order of upgrade.

    # 4. DROP COLUMN sync_runs.phase
    op.execute("""
        ALTER TABLE sync_runs
            DROP CONSTRAINT sync_runs_phase_known
    """)
    op.execute("ALTER TABLE sync_runs DROP COLUMN phase")

    # 3. DROP COLUMN sync_runs.last_completed_phase
    op.execute("""
        ALTER TABLE sync_runs
            DROP CONSTRAINT sync_runs_last_completed_phase_known
    """)
    op.execute("ALTER TABLE sync_runs DROP COLUMN last_completed_phase")

    # 2. DROP COLUMN connected_orgs.ai_enrichment_status
    op.execute("""
        ALTER TABLE connected_orgs
            DROP CONSTRAINT connected_orgs_ai_enrichment_status_known
    """)
    op.execute("ALTER TABLE connected_orgs DROP COLUMN ai_enrichment_status")

    # 1. DROP TABLE ai_enrichment_queue (cascades indexes)
    op.execute("DROP TABLE ai_enrichment_queue")
