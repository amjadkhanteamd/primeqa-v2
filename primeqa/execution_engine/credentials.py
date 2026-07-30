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

from primeqa.execution_engine.data_mutation_client import DataMutationClient
from primeqa.execution_engine.errors import CredentialResolutionError
from primeqa.execution_engine.tooling_client import ToolingReadClient


def _resolve_instance_url(cfg: dict, env) -> str:
    """The Salesforce API host for the run. The CONNECTION's ``instance_url`` is
    authoritative — the OAuth token is minted against it — so it WINS; the
    environment's ``sf_instance_url`` is a user-entered display field that can be a
    non-API host (e.g. ``.lightning.force.com``) and is the fallback only.

    Mirrors the sync path (``sync/credentials.py`` ``resolve_sync_sf_client``);
    replicated here (a local helper, NOT shared) to leave sync's proven resolution
    untouched and avoid an S4→S1 import across the substrate boundary.
    task_247242c3 — fixes the 401 INVALID_SESSION_ID class where an env's display
    URL is not an API host."""
    return cfg.get("instance_url") or env.sf_instance_url or ""


def _resolve_org_token(db, environment_id: int):
    """Shared D-106.4 path: environment → SF-org connection (decrypted) →
    ``_oauth_token``. Returns ``(env, access_token, instance_url)`` — the
    ``instance_url`` resolved the same way sync does (connection-authoritative;
    :func:`_resolve_instance_url`).

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

    instance_url = _resolve_instance_url(conn["config"], env)
    return env, access_token, instance_url


def resolve_tooling_client(db, environment_id: int) -> ToolingReadClient:
    """Resolve an authenticated Tooling-read client for ``environment_id``
    (the metadata-inspection vertical, D-108.1)."""
    env, access_token, instance_url = _resolve_org_token(db, environment_id)
    return ToolingReadClient(
        instance_url, env.sf_api_version, access_token)


def resolve_data_mutation_client(
    db, environment_id: int, *, run_as_username: str | None = None,
) -> DataMutationClient:
    """Resolve an authenticated data-mutation client for ``environment_id``
    (the behavioral-negative vertical, D-110.2). Same D-106.4 credential path
    as :func:`resolve_tooling_client`; a different thin transport.

    ``run_as_username`` (D-416/D-421): when set, the client authenticates AS
    that Salesforce user via the JWT Bearer exchange — a DIFFERENT token path
    (``run_as.mint_run_as_token``) with its own distinguishable failure modes,
    and **no fallback**: the admin path below is structurally unreachable from
    the identity branch (early return), so a run-as request either yields a
    token for that identity or raises ``RunAsResolutionError``. Absent
    identity = the pre-run-as behaviour, byte-identical (same
    ``_resolve_org_token`` call, same client construction).
    """
    if run_as_username is not None:
        from primeqa.core.models import Environment
        from primeqa.core.repository import ConnectionRepository
        from primeqa.execution_engine.run_as import (
            assert_identity_known_and_active, mint_run_as_token)
        from primeqa.semantic.connection import get_tenant_connection
        from primeqa.sync.credentials import resolve_connected_org_or_raise

        env = db.query(Environment).filter(
            Environment.id == environment_id).first()
        if env is None:
            raise CredentialResolutionError(
                f"environment {environment_id} not found")
        if not env.connection_id:
            raise CredentialResolutionError(
                f"environment {environment_id} has no Salesforce connection")
        conn = ConnectionRepository(db).get_connection_decrypted(
            env.connection_id, env.tenant_id)
        if not conn:
            raise CredentialResolutionError(
                f"connection {env.connection_id} not found or not decryptable")
        # S1 pre-check: designated identity exists + active (D-417) — the two
        # LOCAL failure modes, before any org round-trip.
        with get_tenant_connection(env.tenant_id) as tconn:
            org_id = resolve_connected_org_or_raise(tconn, environment_id)
            assert_identity_known_and_active(tconn, org_id, run_as_username)
        token = mint_run_as_token(conn["config"], username=run_as_username)
        instance_url = _resolve_instance_url(conn["config"], env)
        return DataMutationClient(instance_url, env.sf_api_version, token)

    env, access_token, instance_url = _resolve_org_token(db, environment_id)
    return DataMutationClient(
        instance_url, env.sf_api_version, access_token)
