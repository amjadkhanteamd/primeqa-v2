"""D-291: one-time env-59 history correction — close twin-guarded source-dangling edges.

Revision ID: 20260629_0010
Revises: 20260627_0020
Create Date: 2026-06-29

D-291 (S1 close-on-change). A read-only diagnostic (env-59, HEAD 72671e5) found
55 **source-dangling current edges** — a current edge (``valid_to_seq IS NULL``)
whose ``source_entity_id`` references an entity-VERSION row that is no longer
current. Root cause: the sync change path re-versioned an entity but never closed
its prior version's outbound edges (only the deletion reconcile closed edges). The
forward fix (close-on-change, endpoints="source") stops the leak going forward;
this migration corrects the accumulated env-59 history.

ONE-TIME, ENV-59-SCOPED (org-coupling INTENTIONAL), TWIN-GUARDED (loss-free by
construction): it closes ONLY a source-dangling edge that HAS a healthy current
twin — a current edge of the same ``edge_type`` whose source resolves to the SAME
source-identity's current version and target to the SAME target-identity (identity
= ``COALESCE(sf_id, entity_type||':'||sf_api_name)`` within the org). The diagnostic
proved all 55 are twin-guarded (blast radius D9 = 0); a no-twin edge (none in
env-59 today) is LEFT UNTOUCHED. The close is **bitemporally correct**: the edge's
``valid_to_seq`` is set to the seq at which ITS source version retired
(``es.valid_to_seq`` — equals the current version's ``valid_from_seq`` in the
2-version case), NOT "now". Idempotent: a re-run matches zero rows (the closed
danglers fail the ``valid_to_seq IS NULL`` guard).

The store-wide sweep of the remaining ~4138 source-danglers (~4193 store-wide minus
env-59's 55) is a TRACKED FOLLOW-UP — D9=0 is proven for env-59 ONLY; bucket first.

FORWARD-ONLY history correction. ``downgrade`` is a documented NO-OP: reopening a
correctly-closed stale-dup edge would re-introduce the danglers; SCD-2 history is
not reversed.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '20260629_0010'
down_revision = '20260627_0020'
branch_labels = None
depends_on = None


_ENV59_ORG = '902850e3-89c0-4d74-9141-66084045f439'
_IDENT = "COALESCE({a}.sf_id, {a}.entity_type || ':' || COALESCE({a}.sf_api_name, '<noapi>'))"


def upgrade():
    op.execute(f"""
        UPDATE edges e
        SET valid_to_seq = es.valid_to_seq          -- close at the seq the source retired
        FROM entities es
        WHERE e.source_entity_id = es.id
          AND e.valid_to_seq IS NULL                -- edge currently open
          AND e.connected_org_id = '{_ENV59_ORG}'   -- env-59 only (org-coupling intentional)
          AND es.valid_to_seq IS NOT NULL           -- source NOT current → dangling
          AND es.valid_to_seq > e.valid_from_seq    -- edges_validity_range CHECK guard
          AND EXISTS (                              -- twin-guard: a HEALTHY current twin exists
              SELECT 1
                FROM edges e2
                JOIN entities es2 ON es2.id = e2.source_entity_id
                JOIN entities et2 ON et2.id = e2.target_entity_id
                JOIN entities et  ON et.id  = e.target_entity_id
               WHERE e2.valid_to_seq IS NULL
                 AND e2.connected_org_id = '{_ENV59_ORG}'
                 AND es2.valid_to_seq IS NULL       -- twin's source IS current (healthy)
                 AND e2.edge_type = e.edge_type
                 AND {_IDENT.format(a='es2')} = {_IDENT.format(a='es')}
                 AND {_IDENT.format(a='et2')} = {_IDENT.format(a='et')}
          )
    """)


def downgrade():
    # Documented NO-OP — D-291 is a forward-only SCD-2 history correction. Reopening
    # a correctly-closed stale-dup edge would re-introduce the source-danglers; the
    # bitemporal close (valid_to_seq = the source's retirement seq) is not reversed.
    pass
