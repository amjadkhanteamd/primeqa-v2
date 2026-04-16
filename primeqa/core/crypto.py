"""Fernet symmetric encryption for credential storage.

Uses CREDENTIAL_ENCRYPTION_KEY from environment. The key must be a
32-byte hex string which is derived into a valid Fernet key via SHA-256 + base64.
"""

import base64
import hashlib
import os

from cryptography.fernet import Fernet


def _get_fernet():
    raw_key = os.getenv("CREDENTIAL_ENCRYPTION_KEY", "")
    if not raw_key:
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY not set")
    derived = hashlib.sha256(raw_key.encode()).digest()
    fernet_key = base64.urlsafe_b64encode(derived)
    return Fernet(fernet_key)


def encrypt(plaintext):
    if plaintext is None:
        return None
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(ciphertext):
    if ciphertext is None:
        return None
    return _get_fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
