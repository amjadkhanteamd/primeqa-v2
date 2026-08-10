"""D-438: s1_drift_review_watermarks — the per-org drift-review cursor.

Drift EVENTS are derived (a pure function of S1's bitemporal history — S1 is
the persistence; re-running the detectors reproduces every event at any
time). The only state that cannot be derived is REVIEW state: which events a
human has already seen. That is this table — one row per org, nothing else.

Semantics (D-438):
  * **Never-reviewed is the ABSENCE of the row** — distinguishable by
    construction from a row with ``last_reviewed_seq = 0`` (a human
    explicitly reviewed at seq 0). The column is NOT NULL precisely so no
    third, ambiguous state exists.
  * The row is written ONLY by the explicit CLI review command
    (``scripts/report_metadata_drift.py --ack``) — never by the post-sync
    hook, never automatically.

MIGRATE-FIRST (D-285): apply before any reader/writer deploys.
"""
from alembic import op

revision = '20260810_0010'
down_revision = '20260801_0010'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE TABLE IF NOT EXISTS s1_drift_review_watermarks ("
        " connected_org_id UUID NOT NULL,"
        " last_reviewed_seq INTEGER NOT NULL,"
        " reviewed_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
        " reviewed_by TEXT,"
        " CONSTRAINT pk_s1_drift_review_watermarks"
        "  PRIMARY KEY (connected_org_id)"
        ")"
    )


def downgrade():
    op.execute("DROP TABLE IF EXISTS s1_drift_review_watermarks")
