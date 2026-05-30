"""Credential resolution: an environment_id → an authenticated client.

The D-106.4 path (credential resolution is **reused** v1 plumbing per D-108.1):
environment → its SF-org connection (decrypted) → ``_oauth_token``
(client_credentials / password per ``auth_flow``) → access token → a thin
S4-local :class:`ToolingReadClient`. The *transport* is S4-owned (the SPEC §3
boundary); only the credential *resolution* is reused.

``_oauth_token`` is reused **in place** from ``metadata.worker_runner`` (the
OAuth grant flow). Lifting the credential plumbing to a neutral module is a
later increment (D-108.1 / F1 — lift per increment's needs, not up front); the
imports are kept function-local to keep the S4→v1 coupling shallow and explicit.
"""
from __future__ import annotations

from primeqa.execution_engine.errors import CredentialResolutionError
from primeqa.execution_engine.tooling_client import ToolingReadClient


def resolve_tooling_client(db, environment_id: int) -> ToolingReadClient:
    """Resolve an authenticated Tooling-read client for ``environment_id``.

    Raises :class:`CredentialResolutionError` when the environment / connection
    is missing or the OAuth flow yields no token — a binding failure, distinct
    from a run outcome (the run never starts)."""
    from primeqa.core.models import Environment
    from primeqa.core.repository import ConnectionRepository
    from primeqa.metadata.worker_runner import _oauth_token

    env = db.query(Environment).filter(Environment.id == environment_id).first()
    if env is None:
        raise CredentialResolutionError(
            f"environment {environment_id} not found")
    if not env.connection_id:
        raise CredentialResolutionError(
            f"environment {environment_id} has no Salesforce connection linked")

    conn = ConnectionRepository(db).get_connection_decrypted(
        env.connection_id, env.tenant_id)
    if not conn:
        raise CredentialResolutionError(
            f"connection {env.connection_id} not found or not decryptable")

    access_token = _oauth_token(env, conn["config"])
    if not access_token:
        raise CredentialResolutionError(
            f"OAuth for environment {environment_id} returned no access_token")

    return ToolingReadClient(
        env.sf_instance_url, env.sf_api_version, access_token)
