"""Fernet symmetric encryption for credential storage.

Uses CREDENTIAL_ENCRYPTION_KEY from environment. The key must be a
32-byte hex string which is derived into a valid Fernet key via SHA-256 + base64.

Dual-key rotation support:
  - Writes (encrypt) ALWAYS use CREDENTIAL_ENCRYPTION_KEY.
  - Reads (decrypt) try CREDENTIAL_ENCRYPTION_KEY first; on
    InvalidToken, fall back to CREDENTIAL_ENCRYPTION_KEY_OLD if set.
  - Rotation runbook:
      1. Generate new key N.  Keep old key O.
      2. Set CREDENTIAL_ENCRYPTION_KEY=N and CREDENTIAL_ENCRYPTION_KEY_OLD=O
         in Railway for every service (web/worker/scheduler).
      3. Deploy. App reads O-encrypted rows via fallback; new writes
         use N.
      4. Run scripts/rotate_credential_encryption_key.py to re-encrypt
         every stored credential under N.
      5. Remove CREDENTIAL_ENCRYPTION_KEY_OLD from Railway.
"""

import base64
import hashlib
import logging
import os

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger(__name__)


def _derive_fernet(raw_key: str) -> Fernet:
    derived = hashlib.sha256(raw_key.encode()).digest()
    fernet_key = base64.urlsafe_b64encode(derived)
    return Fernet(fernet_key)


def _primary_fernet() -> Fernet:
    raw_key = os.getenv("CREDENTIAL_ENCRYPTION_KEY", "")
    if not raw_key:
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY not set")
    return _derive_fernet(raw_key)


def _fallback_fernet():
    """Return a Fernet built from CREDENTIAL_ENCRYPTION_KEY_OLD, or None
    if the env var isn't set. Used only on decrypt() when the primary
    key can't decrypt (rotation window)."""
    raw_key = os.getenv("CREDENTIAL_ENCRYPTION_KEY_OLD", "")
    if not raw_key:
        return None
    return _derive_fernet(raw_key)


def encrypt(plaintext):
    if plaintext is None:
        return None
    return _primary_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(ciphertext):
    if ciphertext is None:
        return None
    token = ciphertext.encode("utf-8")
    try:
        return _primary_fernet().decrypt(token).decode("utf-8")
    except InvalidToken:
        fallback = _fallback_fernet()
        if fallback is None:
            raise
        try:
            plaintext = fallback.decrypt(token).decode("utf-8")
            # Intentional info-level: during rotation the ops dashboard
            # should see this shrinking to zero as the migration script
            # re-encrypts stored rows. Stays silent outside rotation.
            log.info("credential decrypted via fallback key \u2014 rotation pending re-encrypt")
            return plaintext
        except InvalidToken:
            raise


# Convenience for the rotation script so it doesn't reach into privates.
def decrypt_with(raw_key: str, ciphertext: str) -> str:
    """Decrypt a single ciphertext with an explicitly-provided raw key.

    Used by scripts/rotate_credential_encryption_key.py so it can drive
    the re-encryption without depending on env-var ordering. Not called
    by the app at runtime.
    """
    return _derive_fernet(raw_key).decrypt(ciphertext.encode("utf-8")).decode("utf-8")


def encrypt_with(raw_key: str, plaintext: str) -> str:
    """Encrypt a single plaintext with an explicitly-provided raw key.

    Counterpart to decrypt_with() for the rotation script.
    """
    return _derive_fernet(raw_key).encrypt(plaintext.encode("utf-8")).decode("utf-8")
