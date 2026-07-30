"""Run-as identity resolution — the JWT Bearer exchange (D-416/D-421).

The ONLY path that mints an access token for a non-admin execution identity.
Mechanism per D-416: OAuth 2.0 JWT Bearer (``urn:ietf:params:oauth:grant-type:
jwt-bearer``) — an RS256 assertion signed with the connected app's private key,
``sub`` = the designated user's username. The private key lives in the
connection config (``jwt_signing_key``), Fernet-encrypted at rest by the
existing connection store — the same custody class as ``client_secret``.
Stored per-user credentials were REJECTED (D-416): per-identity secret custody
forever, against SEC-5.

FAIL LOUD, every mode distinguishable (D-416): each failure raises
:class:`RunAsResolutionError` with a machine-readable ``reason`` code. There is
NO fallback of any kind — see :func:`mint_run_as_token`'s structure: the admin
credential path (``_oauth_token``) is not imported here, not reachable from
here, and the caller contract (``credentials.resolve_data_mutation_client``)
routes to it only when NO identity was requested. A run-as execution that
silently fell back to the admin identity would produce a green that means
nothing — the worst possible failure (D-416); the separation is structural,
not a guarded branch.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import requests

from primeqa.integrations.sf_url import validate_sf_instance_url

log = logging.getLogger("primeqa.execution.run_as")

# Distinguishable failure reasons (the D-416 contract). Values are stable —
# they surface in job error_codes and evidence.
NO_SIGNING_KEY = "run_as_no_signing_key"          # no jwt_signing_key in config
IDENTITY_NOT_FOUND = "run_as_identity_not_found"  # username not in S1's User set
IDENTITY_INACTIVE = "run_as_identity_inactive"    # S1 says is_active=false
NOT_PREAUTHORIZED = "run_as_not_preauthorized"    # org: user hasn't approved / not admin-approved
SIGNATURE_REJECTED = "run_as_signature_rejected"  # org: assertion/cert invalid
APP_REJECTED = "run_as_app_rejected"              # org: client_id unknown / digital signatures off
EXCHANGE_FAILED = "run_as_exchange_failed"        # transport / unclassifiable org error


class RunAsResolutionError(Exception):
    """A run-as identity could not be resolved to a token. ``reason`` is one
    of the module codes above; the run NEVER starts (binding failure, the
    ``CredentialResolutionError`` posture) and NEVER falls back to admin."""

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        super().__init__(f"{reason}: {detail}")


def assert_identity_known_and_active(conn, connected_org_id: str,
                                     username: str) -> None:
    """S1 pre-check: the designated username must exist in the org's synced
    ``User`` set and be active — the two LOCAL failure modes, distinguishable
    from each other and from every org-side one. ``conn`` is a tenant-schema
    connection. Fails loud; never filters or substitutes."""
    from sqlalchemy import text
    row = conn.execute(text(
        "SELECT ud.is_active FROM entities e "
        "JOIN user_details ud ON ud.entity_id = e.id "
        "WHERE e.connected_org_id = CAST(:org AS uuid) "
        "  AND e.entity_type = 'User' AND e.valid_to_seq IS NULL "
        "  AND e.sf_api_name = :u"),
        {"org": connected_org_id, "u": username}).first()
    if row is None:
        raise RunAsResolutionError(
            IDENTITY_NOT_FOUND,
            f"user {username!r} is not in the org's synced User set "
            f"(org {connected_org_id}) — designate an existing user (D-417)")
    if not row[0]:
        raise RunAsResolutionError(
            IDENTITY_INACTIVE,
            f"user {username!r} exists but is inactive — an inactive identity "
            f"cannot authenticate and its designation has drifted (D-417/S8)")


def mint_run_as_token(cfg: dict[str, Any], *, username: str,
                      timeout: int = 20) -> str:
    """Exchange a signed JWT assertion for an access token AS ``username``.

    ``cfg`` is the decrypted connection config (``client_id``, optional
    ``jwt_signing_key`` PEM, ``instance_url``/``org_type``). Raises
    :class:`RunAsResolutionError` on every failure — there is no fallback
    return and no admin path reachable from this function.
    """
    signing_key = (cfg.get("jwt_signing_key") or "").strip()
    if not signing_key:
        raise RunAsResolutionError(
            NO_SIGNING_KEY,
            "connection config has no jwt_signing_key — upload a certificate "
            "to the connected app and store its private key (D-416 org-side "
            "setup); run-as cannot ship on the client_credentials grant")

    import jwt  # PyJWT — already a dependency (app auth)

    login_url = (cfg.get("instance_url") or "").rstrip("/")
    if not login_url:
        org_type = cfg.get("org_type", "sandbox")
        login_url = ("https://test.salesforce.com" if org_type == "sandbox"
                     else "https://login.salesforce.com")
    # SEC-5: same host guard as the shared credential chokepoint — never POST
    # an assertion to a non-Salesforce host.
    validate_sf_instance_url(login_url)

    # The JWT Bearer aud is the LOGIN host, not the instance (Salesforce
    # rejects instance-host audiences with invalid_grant/audience).
    aud = ("https://test.salesforce.com" if "test.salesforce.com" in login_url
           or ".sandbox." in login_url or cfg.get("org_type", "sandbox") == "sandbox"
           else "https://login.salesforce.com")
    assertion = jwt.encode(
        {"iss": cfg.get("client_id", ""), "sub": username, "aud": aud,
         "exp": int(time.time()) + 180},
        signing_key, algorithm="RS256")

    try:
        resp = requests.post(
            f"{login_url}/services/oauth2/token",
            data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                  "assertion": assertion},
            timeout=timeout)
    except requests.RequestException as e:
        raise RunAsResolutionError(EXCHANGE_FAILED,
                                   f"token endpoint unreachable: {e}") from e

    if resp.status_code == 200:
        token = (resp.json() or {}).get("access_token")
        if token:
            return token
        raise RunAsResolutionError(EXCHANGE_FAILED,
                                   "200 response carried no access_token")

    # Map Salesforce's oauth error vocabulary onto the distinguishable codes.
    try:
        body = resp.json()
    except ValueError:
        body = {}
    err = (body.get("error") or "").lower()
    desc = (body.get("error_description") or resp.text or "")[:300]
    dl = desc.lower()
    if err == "invalid_client_id" or err == "invalid_client":
        raise RunAsResolutionError(APP_REJECTED, f"{err}: {desc}")
    if err == "invalid_grant":
        if "user hasn't approved" in dl or "admin" in dl and "approv" in dl:
            raise RunAsResolutionError(NOT_PREAUTHORIZED, f"{err}: {desc}")
        if "audience" in dl or "assertion" in dl or "signature" in dl \
                or "expired" in dl:
            raise RunAsResolutionError(SIGNATURE_REJECTED, f"{err}: {desc}")
        if "user" in dl and ("inactive" in dl or "invalid" in dl):
            raise RunAsResolutionError(IDENTITY_NOT_FOUND, f"{err}: {desc}")
        raise RunAsResolutionError(NOT_PREAUTHORIZED, f"{err}: {desc}")
    if err == "invalid_app_access" or "digital signature" in dl:
        raise RunAsResolutionError(APP_REJECTED, f"{err}: {desc}")
    raise RunAsResolutionError(
        EXCHANGE_FAILED, f"HTTP {resp.status_code} {err}: {desc}")
