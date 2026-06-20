"""Fail-closed secret resolution + boot gate (F-3) and the F-8 decrypt-fail-closed.

Both directions are proven for the boot gate: it PASSES with real values and
RAISES on unset / empty / the public dev default — because a validator that
*accepts* the default is the dangerous false-negative (it would let a forgeable
JWT secret into prod). The symmetric guard is also pinned: an unset OPTIONAL
secret (WEBHOOK_SECRET) must NOT block boot (else it bricks a prod that runs fine
with it unset today).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from primeqa.core.secrets import (
    _JWT_DEV_DEFAULT,
    SecretConfigError,
    get_credential_encryption_key,
    get_jwt_secret,
    get_webhook_secret,
    validate_boot_secrets,
)

pytestmark = pytest.mark.unit


def _prod(mp):
    mp.setenv("FLASK_ENV", "production")


def _nonprod(mp):
    mp.delenv("FLASK_ENV", raising=False)


# ----------------------------------------------------------------------
# JWT_SECRET — mandatory, fail-closed in prod
# ----------------------------------------------------------------------
class TestGetJwtSecret:
    def test_prod_real_value_returned(self, monkeypatch):
        _prod(monkeypatch); monkeypatch.setenv("JWT_SECRET", "a" * 64)
        assert get_jwt_secret() == "a" * 64

    def test_prod_unset_raises(self, monkeypatch):
        _prod(monkeypatch); monkeypatch.delenv("JWT_SECRET", raising=False)
        with pytest.raises(SecretConfigError):
            get_jwt_secret()

    def test_prod_empty_raises(self, monkeypatch):
        _prod(monkeypatch); monkeypatch.setenv("JWT_SECRET", "")
        with pytest.raises(SecretConfigError):
            get_jwt_secret()

    def test_prod_dev_default_raises(self, monkeypatch):
        # THE dangerous false-negative: the public default must be rejected in prod.
        _prod(monkeypatch); monkeypatch.setenv("JWT_SECRET", _JWT_DEV_DEFAULT)
        with pytest.raises(SecretConfigError):
            get_jwt_secret()

    def test_nonprod_unset_falls_back_to_dev_default(self, monkeypatch):
        _nonprod(monkeypatch); monkeypatch.delenv("JWT_SECRET", raising=False)
        assert get_jwt_secret() == _JWT_DEV_DEFAULT          # local/test works

    def test_nonprod_real_value_returned(self, monkeypatch):
        _nonprod(monkeypatch); monkeypatch.setenv("JWT_SECRET", "local")
        assert get_jwt_secret() == "local"


# ----------------------------------------------------------------------
# CREDENTIAL_ENCRYPTION_KEY — mandatory in prod
# ----------------------------------------------------------------------
class TestGetCredentialEncryptionKey:
    def test_prod_set_returned(self, monkeypatch):
        _prod(monkeypatch); monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "k" * 64)
        assert get_credential_encryption_key() == "k" * 64

    def test_prod_unset_raises(self, monkeypatch):
        _prod(monkeypatch); monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEY", raising=False)
        with pytest.raises(SecretConfigError):
            get_credential_encryption_key()

    def test_nonprod_unset_returns_empty(self, monkeypatch):
        _nonprod(monkeypatch); monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEY", raising=False)
        assert get_credential_encryption_key() == ""


# ----------------------------------------------------------------------
# WEBHOOK_SECRET — OPTIONAL, never raises (consumer fails closed)
# ----------------------------------------------------------------------
class TestGetWebhookSecret:
    def test_optional_unset_returns_empty_never_raises(self, monkeypatch):
        _prod(monkeypatch); monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
        assert get_webhook_secret() == ""                    # no raise even in prod

    def test_set_returned(self, monkeypatch):
        monkeypatch.setenv("WEBHOOK_SECRET", "wh")
        assert get_webhook_secret() == "wh"


# ----------------------------------------------------------------------
# validate_boot_secrets — the boot gate, BOTH directions
# ----------------------------------------------------------------------
class TestValidateBootSecrets:
    def test_passes_with_both_mandatory_real(self, monkeypatch):
        # Direction 1: PASSES with real values (WEBHOOK unset is fine — optional).
        _prod(monkeypatch)
        monkeypatch.setenv("JWT_SECRET", "j" * 64)
        monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "c" * 64)
        monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
        validate_boot_secrets()                              # no raise

    def test_raises_when_jwt_unset(self, monkeypatch):
        _prod(monkeypatch)
        monkeypatch.delenv("JWT_SECRET", raising=False)
        monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "c" * 64)
        with pytest.raises(SecretConfigError):
            validate_boot_secrets()

    def test_raises_when_jwt_is_dev_default(self, monkeypatch):
        # Direction 2 + the false-negative guard (accepting the default is the bug).
        _prod(monkeypatch)
        monkeypatch.setenv("JWT_SECRET", _JWT_DEV_DEFAULT)
        monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "c" * 64)
        with pytest.raises(SecretConfigError):
            validate_boot_secrets()

    def test_raises_when_cred_key_unset(self, monkeypatch):
        _prod(monkeypatch)
        monkeypatch.setenv("JWT_SECRET", "j" * 64)
        monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEY", raising=False)
        with pytest.raises(SecretConfigError):
            validate_boot_secrets()

    def test_webhook_unset_does_not_block_boot(self, monkeypatch):
        # The load-bearing false-POSITIVE guard: an unset OPTIONAL secret must NOT
        # brick a prod that runs fine with it unset today (WEBHOOK_SECRET is
        # currently unset in prod — proven by the Tier-0 check).
        _prod(monkeypatch)
        monkeypatch.setenv("JWT_SECRET", "j" * 64)
        monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "c" * 64)
        monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
        validate_boot_secrets()                              # no raise

    def test_noop_outside_production(self, monkeypatch):
        # Local/test: even with everything unset, the gate is a no-op.
        _nonprod(monkeypatch)
        monkeypatch.delenv("JWT_SECRET", raising=False)
        monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEY", raising=False)
        validate_boot_secrets()                              # no raise


# ----------------------------------------------------------------------
# F-8 — credential decrypt fails CLOSED (typed error, never forwards ciphertext)
# ----------------------------------------------------------------------
class TestCredentialDecryptFailClosed:
    def _repo_with_config(self, config, ctype="salesforce"):
        from primeqa.core.repository import ConnectionRepository
        repo = ConnectionRepository(MagicMock())
        conn = SimpleNamespace(
            id="c1", tenant_id=1, connection_type=ctype, name="n",
            config=config, status="active", created_by=None, created_at=None)
        repo.get_connection = lambda cid, tid=None: conn    # stub the row read
        return repo

    def test_bad_ciphertext_raises_typed_error(self, monkeypatch):
        from primeqa.core.crypto import CredentialDecryptError
        monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "k" * 64)
        repo = self._repo_with_config({"client_secret": "not-a-valid-fernet-token"})
        with pytest.raises(CredentialDecryptError):
            repo.get_connection_decrypted("c1", 1)

    def test_ciphertext_never_returned_on_failure(self, monkeypatch):
        # F-8 property: on failure we RAISE — the undecryptable value is never
        # placed in the returned dict, so it can never be POSTed to SF as the secret.
        from primeqa.core.crypto import CredentialDecryptError
        monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "k" * 64)
        repo = self._repo_with_config({"client_secret": "garbage-ciphertext"})
        with pytest.raises(CredentialDecryptError):
            repo.get_connection_decrypted("c1", 1)

    def test_happy_path_decrypts(self, monkeypatch):
        from primeqa.core.crypto import encrypt
        monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "k" * 64)
        ct = encrypt("supersecret")
        repo = self._repo_with_config({"client_secret": ct})
        out = repo.get_connection_decrypted("c1", 1)
        assert out["config"]["client_secret"] == "supersecret"
