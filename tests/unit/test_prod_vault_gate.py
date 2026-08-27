"""Productionisation pure merge-gate tests — vault refusal semantics,
the argv secret-input discipline (structural), and the totp_env
production-role refusal."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_VAULT_SRC = (Path(__file__).parents[2] / "primeqa" / "browser_worker" /
              "vault.py").read_text("utf-8")


def test_unsupported_refused_at_the_service_before_any_db():
    from primeqa.browser_worker.vault import VaultError, register_persona

    with pytest.raises(VaultError) as ei:
        register_persona(None, tenant_id=1, persona_key="p", site="s",
                         auth_mode="UNSUPPORTED", username="u",
                         password="pw", totp_seed=None, actor_user_id=7)
    assert "refused at registration" in str(ei.value)
    assert "fact to report" in str(ei.value)


def test_totp_seed_iff_provisioned_at_the_service():
    from primeqa.browser_worker.vault import VaultError, register_persona

    with pytest.raises(VaultError, match="exactly when"):
        register_persona(None, tenant_id=1, persona_key="p", site="s",
                         auth_mode="TOTP_PROVISIONED", username="u",
                         password="pw", totp_seed=None, actor_user_id=7)
    with pytest.raises(VaultError, match="exactly when"):
        register_persona(None, tenant_id=1, persona_key="p", site="s",
                         auth_mode="NONE", username="u", password="pw",
                         totp_seed="SEED", actor_user_id=7)


def test_unknown_auth_mode_refused():
    from primeqa.browser_worker.vault import VaultError, register_persona

    with pytest.raises(VaultError, match="unknown auth_mode"):
        register_persona(None, tenant_id=1, persona_key="p", site="s",
                         auth_mode="MAGIC", username="u", password="pw",
                         totp_seed=None, actor_user_id=7)


def test_cli_takes_no_secret_via_argv():
    # Structural (the GO amendment): no argparse argument may name a
    # secret. Secrets arrive via getpass or PORTAL_REG_* session env.
    for arg in re.findall(r'add_argument\("(--[\w-]+)"', _VAULT_SRC):
        assert not re.search(r"password|username|seed|secret|totp",
                             arg, re.IGNORECASE), f"secret in argv: {arg}"
    assert "getpass" in _VAULT_SRC
    assert "PORTAL_REG_USERNAME" in _VAULT_SRC


def test_totp_env_refused_under_the_production_role(monkeypatch):
    from primeqa.browser_worker.consume import _resolve_auth_credentials
    from primeqa.browser_worker.session import (
        DEV_AUTH_MODE_REFUSED, LoginError)

    monkeypatch.setenv("PLIMSOL_SERVICE_ROLE", "browser-worker")
    with pytest.raises(LoginError) as ei:
        _resolve_auth_credentials(None, {"mode": "totp_env"})
    assert ei.value.code == DEV_AUTH_MODE_REFUSED
    assert ei.value.retryable is False        # permanent, never resubmits


def test_persona_classes_are_permanent():
    from primeqa.browser_worker.session import (
        _PERMANENT, DEV_AUTH_MODE_REFUSED, PERSONA_INACTIVE,
        PERSONA_NOT_FOUND)

    assert {PERSONA_NOT_FOUND, PERSONA_INACTIVE,
            DEV_AUTH_MODE_REFUSED} <= _PERMANENT
