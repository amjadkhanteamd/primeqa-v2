"""Productionisation — the portal-persona vault (LLD_PRODUCTIONISATION §a).

One tenant-schema table: per-tenant portal credentials as ciphertext
under PORTAL_FERNET_KEY (the second key, TAD §3 — provisioned ONLY to
the browser-worker service; the web tier holds no portal-crypto at
all). The USERNAME is ciphertext too (the session substrate treats it
as a secret). CHECKs make the laws structural:

  - auth_mode is the three STORABLE modes only — 'UNSUPPORTED' is
    refused at the service AND impossible at the CHECK (an unsupported
    MFA posture is a fact to report, never a credential to store);
  - the TOTP seed exists exactly when auth_mode = 'TOTP_PROVISIONED'.

Plain DDL in env.py's transaction (D-459: no autocommit_block).
"""
from alembic import op

revision = "20260826_0020"
down_revision = "20260826_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE portal_personas (
            id                   UUID PRIMARY KEY,
            persona_key          TEXT NOT NULL UNIQUE,
            site                 TEXT NOT NULL,
            username_ciphertext  TEXT NOT NULL,
            password_ciphertext  TEXT NOT NULL,
            totp_seed_ciphertext TEXT,
            auth_mode            TEXT NOT NULL
                CONSTRAINT portal_personas_auth_mode_known
                CHECK (auth_mode IN
                       ('NONE','TOTP_PROVISIONED','EXEMPT')),
            active               BOOLEAN NOT NULL DEFAULT TRUE,
            registered_by        INTEGER NOT NULL,
            registered_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            rotated_by           INTEGER,
            rotated_at           TIMESTAMPTZ,
            notes                TEXT NOT NULL DEFAULT '',
            CONSTRAINT portal_personas_totp_seed_iff_provisioned
                CHECK ((auth_mode = 'TOTP_PROVISIONED')
                       = (totp_seed_ciphertext IS NOT NULL))
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS portal_personas")
