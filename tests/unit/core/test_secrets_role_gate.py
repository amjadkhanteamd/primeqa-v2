"""Role-aware boot gate (ui-s2.6) on primeqa/core/secrets.py.

The four required proofs:
  (a) no PLIMSOL_SERVICE_ROLE  -> behaviour byte-identical to the legacy gate
  (b) unknown role             -> fail closed, naming the valid roles
  (c) browser-worker role      -> PORTAL_FERNET_KEY required; JWT_SECRET and
                                  CREDENTIAL_ENCRYPTION_KEY explicitly NOT required
  (d) each other role's set    -> matches current (legacy) behaviour

All tests force production (the gate is a no-op otherwise). Env is isolated
per-test via monkeypatch.
"""
from __future__ import annotations

import pytest

from primeqa.core.secrets import (
    SecretConfigError,
    VALID_SERVICE_ROLES,
    validate_boot_secrets,
)

pytestmark = pytest.mark.unit

_REAL_JWT = "a-real-jwt-secret-value"
_REAL_CEK = "k" * 64
_REAL_FERNET = "portal-fernet-key-value"


def _prod(mp):
    mp.setenv("FLASK_ENV", "production")


def _clear(mp):
    for v in ("PLIMSOL_SERVICE_ROLE", "JWT_SECRET", "CREDENTIAL_ENCRYPTION_KEY",
              "PORTAL_FERNET_KEY"):
        mp.delenv(v, raising=False)


# (a) legacy regression: unset role == the historical gate, byte for byte
class TestNoRoleIsLegacy:
    def test_unset_role_requires_jwt_and_cek_and_passes_with_both(self, monkeypatch):
        _prod(monkeypatch); _clear(monkeypatch)
        monkeypatch.setenv("JWT_SECRET", _REAL_JWT)
        monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", _REAL_CEK)
        validate_boot_secrets()   # no raise

    def test_unset_role_raises_on_missing_jwt(self, monkeypatch):
        _prod(monkeypatch); _clear(monkeypatch)
        monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", _REAL_CEK)
        with pytest.raises(SecretConfigError):
            validate_boot_secrets()

    def test_unset_role_raises_on_missing_cek(self, monkeypatch):
        _prod(monkeypatch); _clear(monkeypatch)
        monkeypatch.setenv("JWT_SECRET", _REAL_JWT)
        with pytest.raises(SecretConfigError):
            validate_boot_secrets()

    def test_unset_role_ignores_portal_fernet(self, monkeypatch):
        # legacy set does not include PORTAL_FERNET_KEY — its absence is irrelevant
        _prod(monkeypatch); _clear(monkeypatch)
        monkeypatch.setenv("JWT_SECRET", _REAL_JWT)
        monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", _REAL_CEK)
        validate_boot_secrets()   # no raise despite PORTAL_FERNET_KEY unset


# (b) unknown role -> fail closed, naming the valid roles
class TestUnknownRoleFailsClosed:
    @pytest.mark.parametrize("bad", ["web-worker", "browser_worker", "WEB",
                                     "api", "browserworker"])
    def test_unknown_role_raises_naming_valid_roles(self, monkeypatch, bad):
        _prod(monkeypatch); _clear(monkeypatch)
        monkeypatch.setenv("PLIMSOL_SERVICE_ROLE", bad)
        # even with every secret present, an unknown role must refuse
        monkeypatch.setenv("JWT_SECRET", _REAL_JWT)
        monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", _REAL_CEK)
        monkeypatch.setenv("PORTAL_FERNET_KEY", _REAL_FERNET)
        with pytest.raises(SecretConfigError) as ei:
            validate_boot_secrets()
        for role in VALID_SERVICE_ROLES:
            assert role in str(ei.value)


# (c) browser-worker -> PORTAL_FERNET_KEY required; JWT/CEK NOT required
class TestBrowserWorkerRole:
    def test_requires_portal_fernet_key(self, monkeypatch):
        _prod(monkeypatch); _clear(monkeypatch)
        monkeypatch.setenv("PLIMSOL_SERVICE_ROLE", "browser-worker")
        # JWT + CEK present, but PORTAL_FERNET_KEY missing -> still refuses
        monkeypatch.setenv("JWT_SECRET", _REAL_JWT)
        monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", _REAL_CEK)
        with pytest.raises(SecretConfigError) as ei:
            validate_boot_secrets()
        assert "PORTAL_FERNET_KEY" in str(ei.value)

    def test_does_not_require_jwt_or_cek(self, monkeypatch):
        _prod(monkeypatch); _clear(monkeypatch)
        monkeypatch.setenv("PLIMSOL_SERVICE_ROLE", "browser-worker")
        # ONLY PORTAL_FERNET_KEY set; JWT_SECRET + CREDENTIAL_ENCRYPTION_KEY unset
        monkeypatch.setenv("PORTAL_FERNET_KEY", _REAL_FERNET)
        validate_boot_secrets()   # no raise — JWT/CEK deliberately not required

    def test_passes_with_only_portal_fernet(self, monkeypatch):
        _prod(monkeypatch); _clear(monkeypatch)
        monkeypatch.setenv("PLIMSOL_SERVICE_ROLE", "browser-worker")
        monkeypatch.setenv("PORTAL_FERNET_KEY", _REAL_FERNET)
        validate_boot_secrets()


# (d) the other roles == legacy set (JWT + CEK), nothing else
class TestLegacyEquivalentRoles:
    @pytest.mark.parametrize("role", ["web", "worker", "scheduler"])
    def test_role_requires_jwt_and_cek(self, monkeypatch, role):
        _prod(monkeypatch); _clear(monkeypatch)
        monkeypatch.setenv("PLIMSOL_SERVICE_ROLE", role)
        monkeypatch.setenv("JWT_SECRET", _REAL_JWT)
        monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", _REAL_CEK)
        validate_boot_secrets()   # no raise

    @pytest.mark.parametrize("role", ["web", "worker", "scheduler"])
    def test_role_raises_on_missing_cek(self, monkeypatch, role):
        _prod(monkeypatch); _clear(monkeypatch)
        monkeypatch.setenv("PLIMSOL_SERVICE_ROLE", role)
        monkeypatch.setenv("JWT_SECRET", _REAL_JWT)
        with pytest.raises(SecretConfigError):
            validate_boot_secrets()

    @pytest.mark.parametrize("role", ["web", "worker", "scheduler"])
    def test_role_does_not_require_portal_fernet(self, monkeypatch, role):
        _prod(monkeypatch); _clear(monkeypatch)
        monkeypatch.setenv("PLIMSOL_SERVICE_ROLE", role)
        monkeypatch.setenv("JWT_SECRET", _REAL_JWT)
        monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", _REAL_CEK)
        validate_boot_secrets()   # no raise despite PORTAL_FERNET_KEY unset


# non-prod: the whole gate is a no-op regardless of role
def test_noop_outside_production(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("PLIMSOL_SERVICE_ROLE", "browser-worker")
    validate_boot_secrets()   # no raise even with PORTAL_FERNET_KEY unset


def test_role_whitespace_is_normalised(monkeypatch):
    # A trailing/leading space (a common env-var accident) normalises to the
    # valid role via .strip() — matching is_production()'s convention — rather
    # than failing closed on a cosmetic difference.
    _prod(monkeypatch); _clear(monkeypatch)
    monkeypatch.setenv("PLIMSOL_SERVICE_ROLE", "  browser-worker  ")
    monkeypatch.setenv("PORTAL_FERNET_KEY", _REAL_FERNET)
    validate_boot_secrets()   # no raise — treated as browser-worker
