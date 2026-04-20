"""Re-encrypt every stored credential under a new CREDENTIAL_ENCRYPTION_KEY.

Use this AFTER:
  1. Generating a new 64-hex key (e.g. `python -c "import secrets; print(secrets.token_hex(32))"`).
  2. Setting CREDENTIAL_ENCRYPTION_KEY=<new> on every Railway service.
  3. Setting CREDENTIAL_ENCRYPTION_KEY_OLD=<old> on every Railway service.
  4. Waiting for the redeploy to finish so the fallback-read path is live.

Run from your laptop (uses local proxy DATABASE_URL from .env):

    OLD_KEY=<old 64-hex> NEW_KEY=<new 64-hex> \\
      ./venv/bin/python scripts/rotate_credential_encryption_key.py

Or in dry-run mode to preview what will change without writing:

    OLD_KEY=<...> NEW_KEY=<...> DRY_RUN=1 \\
      ./venv/bin/python scripts/rotate_credential_encryption_key.py

Tables / columns handled:
  - connections.config (JSONB)   : per-type sensitive fields via
                                   ConnectionRepository._sensitive_fields
  - environment_credentials      : client_id / client_secret / access_token /
                                   refresh_token

After this completes, remove CREDENTIAL_ENCRYPTION_KEY_OLD from Railway.
The app continues with only the new key.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The rotation intentionally does NOT rely on os.environ["CREDENTIAL_ENCRYPTION_KEY"]
# because that would be the NEW key after step 2. We pass OLD and NEW
# explicitly via env vars of different names, so the crypto functions
# never guess.
OLD_KEY = os.environ.get("OLD_KEY", "").strip()
NEW_KEY = os.environ.get("NEW_KEY", "").strip()
DRY_RUN = bool(os.environ.get("DRY_RUN"))

if not OLD_KEY or not NEW_KEY:
    print("ERROR: set OLD_KEY and NEW_KEY env vars.")
    print("       OLD_KEY = the 64-hex key that encrypted existing rows")
    print("       NEW_KEY = the 64-hex key to re-encrypt under")
    sys.exit(2)

if OLD_KEY == NEW_KEY:
    print("ERROR: OLD_KEY and NEW_KEY are identical \u2014 nothing to do.")
    sys.exit(2)

import primeqa.db as db_mod
db_mod.init_db(os.environ["DATABASE_URL"])
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from primeqa.core.crypto import decrypt_with, encrypt_with
from primeqa.core.models import Connection, EnvironmentCredential
from primeqa.core.repository import ConnectionRepository


def _rewrap(raw: str) -> str:
    """Decrypt with OLD, re-encrypt with NEW. Pure function."""
    plaintext = decrypt_with(OLD_KEY, raw)
    return encrypt_with(NEW_KEY, plaintext)


def rotate_connections(sess) -> int:
    """Re-encrypt connections.config.<sensitive fields>."""
    rewritten = 0
    conns = sess.query(Connection).all()
    for c in conns:
        cfg = dict(c.config or {})
        sensitive = ConnectionRepository._sensitive_fields(c.connection_type)
        touched = False
        for f in sensitive:
            if f in cfg and cfg[f]:
                try:
                    cfg[f] = _rewrap(str(cfg[f]))
                    touched = True
                except Exception as e:
                    # Most likely: field already in plaintext (not "gAAAAA" prefix)
                    # or encrypted by yet another key. Skip with warning.
                    print(f"  SKIP conn #{c.id} field={f}: {type(e).__name__}: {e}")
        if touched:
            c.config = cfg
            flag_modified(c, "config")
            rewritten += 1
            print(f"  connection #{c.id} ({c.connection_type} '{c.name}'): "
                  f"re-encrypted {', '.join(f for f in sensitive if f in cfg)}")
    return rewritten


def rotate_environment_credentials(sess) -> int:
    """Re-encrypt environment_credentials.* — four fields per row."""
    rewritten = 0
    rows = sess.query(EnvironmentCredential).all()
    for r in rows:
        touched = False
        for attr in ("client_id", "client_secret", "access_token", "refresh_token"):
            val = getattr(r, attr, None)
            if not val:
                continue
            try:
                setattr(r, attr, _rewrap(str(val)))
                touched = True
            except Exception as e:
                print(f"  SKIP env_cred #{r.id} field={attr}: {type(e).__name__}: {e}")
        if touched:
            rewritten += 1
            print(f"  environment_credential #{r.id} (env={r.environment_id}): "
                  "re-encrypted 4 fields")
    return rewritten


def main():
    Session = sessionmaker(bind=db_mod.engine)
    sess = Session()
    try:
        print(f"\n--- Rotating credential encryption key ---")
        print(f"OLD_KEY (...{OLD_KEY[-6:]})  \u2192  NEW_KEY (...{NEW_KEY[-6:]})")
        print(f"Mode: {'DRY RUN (no writes)' if DRY_RUN else 'LIVE (will commit)'}\n")

        c = rotate_connections(sess)
        e = rotate_environment_credentials(sess)

        if DRY_RUN:
            sess.rollback()
            print(f"\nDRY RUN complete. Would have re-encrypted:")
        else:
            sess.commit()
            print(f"\nCommitted. Re-encrypted:")
        print(f"  connections: {c}")
        print(f"  environment_credentials: {e}")
        print()
        if not DRY_RUN:
            print("NEXT: remove CREDENTIAL_ENCRYPTION_KEY_OLD from Railway for")
            print("all services. Run a fresh generate + connection test from")
            print("the app afterward to confirm the primary key path works.")
    finally:
        sess.close()


if __name__ == "__main__":
    main()
