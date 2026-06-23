"""S1-sync credential resolution + connected_org provisioning (D-150).

The S1-sync production trigger (cutover Step 0) needs two things the dormant
``SyncEngine`` assumes: a Salesforce client + a ``connected_orgs`` sync target.

**Credential resolution REUSES the v1 path** (exactly as S4's
``execution_engine/credentials.py`` does): environment → its SF-org connection
(decrypted, Fernet) → ``metadata.worker_runner._oauth_token``
(client_credentials / password per ``auth_flow``) → an access token. The
engine's ``integrations.sf_client.SalesforceClient`` is refresh-token-grant, and
v1 connections carry no refresh_token — so we **pre-seed** the client with the
resolved access token (the additive ``access_token`` param, D-150). This reuses
v1's auth machinery + the engine's transport, both untouched. (A mid-sync 401
past the ~2h token life → ``SFAuthError`` → the job fails + retries with a fresh
token.)

**Provisioning:** ``connected_orgs`` has no link to ``environments`` and is never
INSERTed in production, so :func:`ensure_connected_org_for_environment` upserts a
sync-target row per environment (idempotent on ``environment_id``). The sync
*targets* a connected_org; the *environment* is the credential source.

The imports of the v1 plumbing are function-local (keep the S1→v1 coupling
shallow + explicit, the S4 convention).
"""
from __future__ import annotations

from sqlalchemy import text

from primeqa.integrations.sf_client import SalesforceClient


class CredentialResolutionError(Exception):
    """The environment / connection is missing or yields no token — a binding
    failure (the sync never starts), distinct from a sync outcome. Sync-local
    (mirrors ``execution_engine.errors.CredentialResolutionError``) to avoid a
    sync → execution_engine import coupling."""


def resolve_sync_sf_client(db, environment_id: int) -> SalesforceClient:
    """Resolve an authenticated ``SalesforceClient`` for ``environment_id`` via
    the v1 environment → connection → ``_oauth_token`` path (D-150), seeding the
    engine's client with the resolved access token.

    ``db`` is the v1 main-DB session (where ``environments`` / ``connections``
    live). Raises :class:`CredentialResolutionError` on a missing environment /
    connection or an empty token (a binding failure — the sync never starts).
    """
    from primeqa.core.models import Environment
    from primeqa.core.repository import ConnectionRepository
    from primeqa.metadata.worker_runner import _oauth_token

    env = db.query(Environment).filter(Environment.id == environment_id).first()
    if env is None:
        raise CredentialResolutionError(f"environment {environment_id} not found")
    if not env.connection_id:
        raise CredentialResolutionError(
            f"environment {environment_id} has no Salesforce connection linked")

    conn = ConnectionRepository(db).get_connection_decrypted(
        env.connection_id, env.tenant_id)
    if not conn:
        raise CredentialResolutionError(
            f"connection {env.connection_id} not found or not decryptable")

    cfg = conn["config"]
    access_token = _oauth_token(env, cfg)
    if not access_token:
        raise CredentialResolutionError(
            f"OAuth for environment {environment_id} returned no access_token")

    instance_url = cfg.get("instance_url") or getattr(env, "sf_instance_url", "") or ""
    return SalesforceClient(
        instance_url=instance_url,
        client_id=cfg.get("client_id", ""),
        client_secret=cfg.get("client_secret", ""),
        refresh_token=cfg.get("refresh_token", ""),
        access_token=access_token,          # pre-seeded — skips the refresh grant
    )


def ensure_connected_org_for_environment(
    conn, environment_id: int, sf_instance_url: str, *,
    org_type: str = "production",
):
    """Idempotently provision the ``connected_orgs`` sync target for an
    environment (D-150). Returns the connected_org id (existing or new), keyed on
    ``environment_id`` (one connected_org per environment).

    ``conn`` is a **tenant-scoped** connection (``get_tenant_connection``) — the
    per-tenant schema where ``connected_orgs`` lives. ``org_type`` ∈
    ``production / sandbox / scratch / developer`` (the table's CHECK)."""
    existing = conn.execute(text(
        "SELECT id FROM connected_orgs WHERE environment_id = :eid"),
        {"eid": environment_id}).scalar()
    if existing is not None:
        return existing
    return conn.execute(text(
        "INSERT INTO connected_orgs (org_type, sf_instance_url, label, environment_id) "
        "VALUES (:t, :url, :label, :eid) RETURNING id"),
        {"t": org_type, "url": sf_instance_url,
         "label": f"env-{environment_id}", "eid": environment_id}).scalar()


def get_connected_org_for_environment(conn, environment_id: int) -> "str | None":
    """READ-ONLY resolution ``environment_id`` → ``connected_orgs.id`` (as text),
    or ``None`` when no connected_org is provisioned for the environment.

    The single per-org resolution seam (per-org Slice 3): every execution / read
    consumer that scopes S1 to a run's org calls this. Returns the id as a string
    (``SemanticOrgModel(conn, connected_org_id=...)`` casts to uuid in SQL).

    Cardinality is 1:1 — guaranteed by the partial UNIQUE index
    ``uq_connected_orgs_environment_id`` (Slice 3a) on top of
    :func:`ensure_connected_org_for_environment`'s upsert — so this returns 0 or 1
    row. Mirrors the correlated lookup in ``metadata_bridge/s1_sync_console.py``;
    the write/upsert sibling is :func:`ensure_connected_org_for_environment`.
    ``conn`` is a tenant-scoped connection (``connected_orgs`` lives per-tenant)."""
    if environment_id is None:
        return None
    return conn.execute(text(
        "SELECT CAST(id AS text) FROM connected_orgs WHERE environment_id = :eid"),
        {"eid": environment_id}).scalar()
