"""Fail-closed resolution of the app's security-critical secrets (F-3).

A security-decision input must fail **closed** — never silently degrade to a
wrong, empty, or public-default value. This module is the single chokepoint for
the three secrets:

  - ``JWT_SECRET`` — MANDATORY in production. The historical resolver fell back
    to the in-repo public default ``dev-secret-change-me`` when unset, so a deploy
    that forgot the var would **sign and verify** JWTs with a forgeable secret —
    anyone could mint a god-mode token for any tenant (F-3). Here, production
    refuses the unset/empty/default value (raise); outside production it falls
    back to the dev default so local + test runs work.
  - ``CREDENTIAL_ENCRYPTION_KEY`` — MANDATORY in production. ``core/crypto`` already
    raises at *use* time if it is unset; this surfaces the misconfiguration at
    **boot** instead of at the first decrypt.
  - ``WEBHOOK_SECRET`` — OPTIONAL. Its only consumer (``release/routes`` CI-trigger)
    already fails closed (HTTP 503, endpoint disabled) when it is unset, so absence
    is a deliberate "webhook disabled", not a vulnerability — the boot gate does
    NOT require it. (Making it mandatory would brick a prod that runs fine today
    with it unset.)

Production is detected via ``FLASK_ENV == "production"`` (Railway sets it). The
boot gate is a no-op outside production, so it never affects local dev or tests.
"""
from __future__ import annotations

import os

# The historical fail-open value JWT_SECRET defaulted to (F-3). Rejected in prod.
_JWT_DEV_DEFAULT = "dev-secret-change-me"


class SecretConfigError(RuntimeError):
    """A MANDATORY security secret is unset / empty / the public default while
    running in production. Raised at boot (or at first resolution) so the process
    refuses to run with a forgeable or absent secret rather than failing open."""


def is_production() -> bool:
    """True iff this process runs in production (``FLASK_ENV == 'production'``)."""
    return os.getenv("FLASK_ENV", "").strip().lower() == "production"


def get_jwt_secret() -> str:
    """The JWT signing/verification secret.

    Production: fail **closed** on unset / empty / the public dev default —
    refusing to sign tokens with a forgeable secret (F-3). Non-production: fall
    back to the dev default so local + test runs work without configuration.
    """
    raw = os.getenv("JWT_SECRET", "")
    if is_production():
        if not raw or raw == _JWT_DEV_DEFAULT:
            raise SecretConfigError(
                "JWT_SECRET is unset/empty or the public dev default in "
                "production — refusing to sign/verify tokens with a forgeable "
                "secret (anyone could mint a god-mode token). Set JWT_SECRET to "
                "a real value in Railway for every service."
            )
        return raw
    return raw or _JWT_DEV_DEFAULT


def get_credential_encryption_key() -> str:
    """The Fernet master key for credential encryption. MANDATORY in production
    (surfaced at boot here; ``core/crypto`` also raises at use time)."""
    raw = os.getenv("CREDENTIAL_ENCRYPTION_KEY", "")
    if is_production() and not raw:
        raise SecretConfigError(
            "CREDENTIAL_ENCRYPTION_KEY is unset in production — credential "
            "encryption/decryption cannot work and stored secrets are "
            "unreadable. Set it in Railway for every service."
        )
    return raw


def get_webhook_secret() -> str:
    """The CI-webhook HMAC secret — OPTIONAL. Returns the configured value or
    ``""`` when unset; never raises. The consumer fails closed (503) on ``""``,
    so an unset value is a deliberate "webhook disabled", not a fail-open."""
    return os.getenv("WEBHOOK_SECRET", "")


def validate_boot_secrets() -> None:
    """Boot-time fail-closed gate, called from every entrypoint (web
    ``create_app`` + the worker + the scheduler). In production, the MANDATORY
    secrets (``JWT_SECRET`` + ``CREDENTIAL_ENCRYPTION_KEY``) must each be a real,
    non-default value or the process refuses to start (raises
    :class:`SecretConfigError`). ``WEBHOOK_SECRET`` is intentionally NOT required
    (optional; its consumer fails closed). A no-op outside production."""
    get_jwt_secret()
    get_credential_encryption_key()
