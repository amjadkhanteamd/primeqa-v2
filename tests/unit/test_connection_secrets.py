"""Unit tests for the LLM connection's secret fields (D-179).

The LLM connection carries TWO secrets now: the Anthropic ``api_key``
and the optional Voyage ``voyage_api_key`` used by S1 enrichment. Both
must be Fernet-encrypted at rest and recovered on decrypted read. These
tests exercise ``ConnectionRepository._encrypt_config`` (the at-rest
write transform) + ``crypto.decrypt`` (the read transform) without a DB
— the encryption is the security property under test, not persistence.
"""
from __future__ import annotations

import pytest

from primeqa.core import crypto
from primeqa.core.repository import ConnectionRepository


pytestmark = pytest.mark.unit

# A 32-byte hex string — crypto._derive_fernet SHA-256s it into a Fernet key.
_TEST_KEY = "a" * 64


@pytest.fixture(autouse=True)
def _enc_key(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", _TEST_KEY)
    monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEY_OLD", raising=False)


def _repo() -> ConnectionRepository:
    # _encrypt_config never touches self.db, so a None binding is fine.
    return ConnectionRepository(db=None)


class TestVoyageKeySensitivity:
    def test_voyage_key_in_llm_sensitive_fields(self) -> None:
        fields = ConnectionRepository._sensitive_fields("llm")
        assert "voyage_api_key" in fields
        assert "api_key" in fields  # the Anthropic key stays sensitive too

    def test_voyage_key_not_sensitive_for_other_types(self) -> None:
        for ct in ("salesforce", "jira"):
            assert "voyage_api_key" not in \
                ConnectionRepository._sensitive_fields(ct)


class TestRoundTrip:
    def test_voyage_key_encrypted_at_rest_and_recovered(self) -> None:
        cfg = {
            "api_key": "sk-ant-secret",
            "voyage_api_key": "pa-voyage-secret",
            "model": "claude-opus-4-20250514",
        }
        enc = _repo()._encrypt_config("llm", cfg)

        # both secrets are Fernet tokens at rest, != plaintext
        assert enc["voyage_api_key"].startswith("gAAAAA")
        assert enc["voyage_api_key"] != "pa-voyage-secret"
        assert enc["api_key"].startswith("gAAAAA")
        # non-secret field is untouched
        assert enc["model"] == "claude-opus-4-20250514"

        # the decrypted-read transform recovers both
        assert crypto.decrypt(enc["voyage_api_key"]) == "pa-voyage-secret"
        assert crypto.decrypt(enc["api_key"]) == "sk-ant-secret"

    def test_already_encrypted_value_not_double_encrypted(self) -> None:
        """Re-encrypting a config whose voyage_api_key is already a
        Fernet token is a no-op for that field (the gAAAAA guard) — an
        update that keeps the existing key shouldn't wrap it twice."""
        once = _repo()._encrypt_config(
            "llm", {"voyage_api_key": "pa-voyage-secret"})
        twice = _repo()._encrypt_config("llm", once)
        assert twice["voyage_api_key"] == once["voyage_api_key"]
        assert crypto.decrypt(twice["voyage_api_key"]) == "pa-voyage-secret"

    def test_blank_voyage_key_left_untouched(self) -> None:
        """An empty voyage_api_key is not encrypted (nothing to protect)
        — the field stays falsy so the decrypted-read skips it cleanly."""
        enc = _repo()._encrypt_config(
            "llm", {"api_key": "sk-ant", "voyage_api_key": ""})
        assert enc["voyage_api_key"] == ""

    def test_absent_voyage_key_not_added(self) -> None:
        """A connection saved without a Voyage key has no such field —
        encryption never invents one."""
        enc = _repo()._encrypt_config("llm", {"api_key": "sk-ant"})
        assert "voyage_api_key" not in enc
