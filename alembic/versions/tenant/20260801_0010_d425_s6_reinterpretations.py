"""D-425.1: s6_reinterpretations — append-only re-reads of stored interpretations.

Interpretations are a record of what was concluded from what was KNOWN AT THE
TIME. A re-read with newer code (or against a newer S1) therefore lands in
THIS table, alongside — never an UPDATE of ``s6_interpretations`` (whose PK
``run_id`` makes a second row per run impossible, and whose consumers must
keep reading originals only). Same append-only principle as DECISIONS_LOG.

Render contract: "as originally concluded" reads ``s6_interpretations``;
"as re-read on <date>" reads this table. ``run_id`` is the 1:1 reference to
the original row. ``verdict`` is CARRIED from the original (a re-read
re-attributes the cause; it never re-judges the outcome). ``code_version``
names the commit that produced the re-read; ``envelope_reconstructed`` is
True when the D-424 assert envelope was reconstructed retroactively from the
stored read evidence (pre-D-424 runs) rather than read from the evidence
itself.

MIGRATE-FIRST (D-285): apply before any writer runs.
"""
from alembic import op

revision = '20260801_0010'
down_revision = '20260729_0010'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE TABLE IF NOT EXISTS s6_reinterpretations ("
        " run_id UUID NOT NULL,"
        " reinterpreted_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
        " code_version TEXT NOT NULL,"
        " verdict TEXT NOT NULL,"
        " cause_kind TEXT,"
        " vr_name TEXT,"
        " cause_detail TEXT,"
        " envelope_reconstructed BOOLEAN NOT NULL DEFAULT FALSE,"
        " CONSTRAINT pk_s6_reinterpretations"
        "  PRIMARY KEY (run_id, reinterpreted_at)"
        ")"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_s6_reinterpretations_cause_kind "
        "ON s6_reinterpretations (cause_kind)"
    )


def downgrade():
    op.execute("DROP TABLE IF EXISTS s6_reinterpretations")
