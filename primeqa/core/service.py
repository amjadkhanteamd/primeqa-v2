"""Service layer for the core domain.

Business logic: user management, auth, tenant operations, environment management.
"""

import hashlib
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

logger = logging.getLogger(__name__)

from primeqa.core.authz import (
    ROLE_TO_TIER,
    AuthorizationError,
    can_assign_user_role,
    rank,
)

ACCESS_TOKEN_EXPIRY = timedelta(minutes=30)
REFRESH_TOKEN_EXPIRY = timedelta(days=7)
MAX_USERS_PER_TENANT = 20
MAX_REFRESH_TOKENS_PER_USER = 5


def _get_jwt_secret():
    # SEC-P1: sign tokens through the fail-closed secret chokepoint (mirrors
    # core/auth.py) — never the forgeable `dev-secret-change-me` default in
    # production. Signer and verifier now resolve the SAME secret.
    from primeqa.core.secrets import get_jwt_secret
    return get_jwt_secret()


def _caller_tenant(caller):
    """Tenant id of the acting caller (the ``request.user`` dict or a row)."""
    if isinstance(caller, dict):
        return caller.get("tenant_id")
    return getattr(caller, "tenant_id", None)


def _caller_role(caller):
    """Stored role value of the acting caller."""
    if isinstance(caller, dict):
        return caller.get("role")
    return getattr(caller, "role", None)


class AuthService:
    def __init__(self, user_repo, token_repo):
        self.user_repo = user_repo
        self.token_repo = token_repo

    def login(self, email, password, tenant_id=None):
        """Authenticate a user. Tenant resolution (audit fix C-1):

          - If `tenant_id` is None, look up the user by email across ALL
            tenants and accept the first active match whose password
            verifies. This is the normal path — email is the primary
            identifier the human types.
          - If `tenant_id` is explicitly provided (e.g. by an SSO flow
            that already knows the tenant), honour it and only accept
            a user from that tenant.

        Returning the same `None` for "bad email" + "bad password" +
        "wrong tenant" prevents user-enumeration (attacker can't tell
        if an email exists in a given tenant).
        """
        if tenant_id is not None:
            candidates = [self.user_repo.get_user_by_email(tenant_id, email)]
        else:
            candidates = self.user_repo.get_users_by_email_any_tenant(email)

        for user in candidates:
            if not user or not user.is_active:
                continue
            if not bcrypt.checkpw(password.encode("utf-8"),
                                  user.password_hash.encode("utf-8")):
                continue
            # Found a match.
            self.user_repo.update_last_login(user.id)
            access_token = self._create_access_token(user)
            raw_refresh, _ = self._create_refresh_token(user.id)
            return {
                "access_token": access_token,
                "refresh_token": raw_refresh,
                "user": self._user_dict(user),
            }
        return None

    def refresh(self, raw_refresh_token):
        token_hash = self._hash_token(raw_refresh_token)
        stored = self.token_repo.get_refresh_token(token_hash)

        if not stored:
            return None
        if stored.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
            self.token_repo.revoke_refresh_token(stored.id)
            return None

        user = self.user_repo.get_user_by_id(stored.user_id)
        if not user or not user.is_active:
            return None

        self.token_repo.revoke_refresh_token(stored.id)

        access_token = self._create_access_token(user)
        new_raw_refresh, _ = self._create_refresh_token(user.id)

        return {
            "access_token": access_token,
            "refresh_token": new_raw_refresh,
        }

    def logout(self, user_id):
        self.token_repo.revoke_all_user_tokens(user_id)

    def create_user(self, tenant_id, email, password, full_name, role, caller):
        # F-1 (create side): the granted role must be a real DB role AND within
        # the caller's own tier — an admin may create admins-and-below but never
        # a superadmin (rank(role) <= caller tier). This is the single chokepoint
        # both create routes funnel through: the API route previously validated
        # the role string while the web route (POST /users/new) did NOT, so a
        # tenant admin could mint a superadmin through the form. `caller` is the
        # acting request.user; it is required (fail-closed — no anonymous create).
        if str(role).strip().lower() not in ROLE_TO_TIER:
            raise ValueError("Invalid role")
        ok, reason = can_assign_user_role(rank(_caller_role(caller)), role)
        if not ok:
            raise AuthorizationError(reason)

        active_count = self.user_repo.count_active_users(tenant_id)
        if active_count >= MAX_USERS_PER_TENANT:
            raise ValueError(f"Tenant has reached the maximum of {MAX_USERS_PER_TENANT} active users")

        existing = self.user_repo.get_user_by_email(tenant_id, email)
        if existing:
            raise ValueError("A user with this email already exists in this tenant")

        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")
        user = self.user_repo.create_user(tenant_id, email, password_hash, full_name, role)

        # D-245: authorization is the role ladder now — there is no
        # permission-set union to seed on user creation; the `role` column is
        # the grant.
        return self._user_dict(user)

    def update_user(self, user_id, caller, **kwargs):
        # Single authorization chokepoint for every user-row mutation. `caller`
        # is the acting request.user and is REQUIRED (fail-closed — there is no
        # tenant-/role-less update path). Both the API PATCH route and the web
        # edit/toggle routes funnel through here, so the F-1/F-2 guards live in
        # one place rather than per-route.
        allowed = {"role", "is_active", "full_name"}
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not updates:
            raise ValueError("No valid fields to update")

        caller_tenant = _caller_tenant(caller)
        if caller_tenant is None:
            # No caller tenant context -> never authorized (fail closed).
            raise AuthorizationError("caller tenant unknown")
        caller_tier = rank(_caller_role(caller))

        # F-2: load the target SCOPED to the caller's tenant. A cross-tenant id
        # is invisible to this caller -> "not found", so the mutation can never
        # reach a row outside the caller's tenant.
        old = self.user_repo.get_user_by_id(user_id, tenant_id=caller_tenant)
        if not old:
            raise ValueError("User not found")
        old_role = old.role

        # F-1: a new role value must be real AND within the caller's tier (no
        # promotion above self, incl. self-promotion to superadmin). Decapitation
        # guard: the caller may not modify a user whose current tier is above its
        # own (an admin cannot edit/demote/deactivate a superadmin). Both via the
        # single authz predicate.
        new_role = updates.get("role")
        if new_role is not None and str(new_role).strip().lower() not in ROLE_TO_TIER:
            raise ValueError("Invalid role")
        ok, reason = can_assign_user_role(caller_tier, new_role, rank(old_role))
        if not ok:
            raise AuthorizationError(reason)

        user = self.user_repo.update_user(user_id, updates, tenant_id=caller_tenant)
        if not user:
            raise ValueError("User not found")

        # Audit fix M-3 (2026-04-19): revoke all refresh tokens on any
        # role change (especially downgrades). The user's existing
        # JWT access_token still works until expiry (that's JWTs), but
        # once it expires they can't refresh without the new role
        # claim, and any fresh login issues tokens under the new role.
        # Also revoke on is_active=false so disabled users can't ride
        # existing tokens to expiry.
        try:
            if "role" in updates and updates["role"] != old_role:
                self.token_repo.revoke_all_user_tokens(user_id)
            elif updates.get("is_active") is False:
                self.token_repo.revoke_all_user_tokens(user_id)
        except Exception:
            # Don't block the user-update on a revocation failure —
            # the row change already committed.
            pass
        return self._user_dict(user)

    def list_users(self, tenant_id):
        users = self.user_repo.list_users(tenant_id)
        return [self._user_dict(u) for u in users]

    def get_user(self, user_id):
        user = self.user_repo.get_user_by_id(user_id)
        if not user:
            return None
        return self._user_dict(user)

    def _create_access_token(self, user):
        now = datetime.now(timezone.utc)
        payload = {
            "sub": str(user.id),
            "tenant_id": user.tenant_id,
            "email": user.email,
            "role": user.role,
            "full_name": user.full_name,
            "iat": now,
            "exp": now + ACCESS_TOKEN_EXPIRY,
        }
        return jwt.encode(payload, _get_jwt_secret(), algorithm="HS256")

    def _create_refresh_token(self, user_id):
        active_count = self.token_repo.count_active_tokens(user_id)
        if active_count >= MAX_REFRESH_TOKENS_PER_USER:
            self.token_repo.revoke_all_user_tokens(user_id)

        raw_token = secrets.token_hex(32)
        token_hash = self._hash_token(raw_token)
        expires_at = datetime.now(timezone.utc) + REFRESH_TOKEN_EXPIRY
        stored = self.token_repo.create_refresh_token(user_id, token_hash, expires_at)
        return raw_token, stored

    @staticmethod
    def _hash_token(raw_token):
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    @staticmethod
    def _user_dict(user):
        return {
            "id": user.id,
            "tenant_id": user.tenant_id,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            "is_active": user.is_active,
            "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
            "created_at": user.created_at.isoformat() if user.created_at else None,
        }


VALID_ENV_TYPES = {"sandbox", "uat", "staging", "production"}
VALID_EXECUTION_POLICIES = {"full", "read_only", "disabled"}
VALID_CAPTURE_MODES = {"minimal", "smart", "full"}

# 2026-07-07 incident: a real production org ("Prod1") saved with the default
# is_production=False let record-writing runs dispatch at production. The
# guard below makes "not production" an explicit human decision whenever a
# save looks like production. Letter-only lookarounds (not \b) so digit- and
# separator-suffixed names ("Prod1", "PROD_EU") match while "product" /
# "preprod" / "delivery" do not.
PROD_NAME_RX = re.compile(r"(?<![a-z])(production|prod|live)(?![a-z])",
                          re.IGNORECASE)


def production_signals(name, env_type, connection_org_type=None):
    """Human-readable reasons an environment save "looks like production":
    prod-ish name, env_type='production', or the linked Salesforce connection
    declared org_type='production' (that declaration is load-bearing — it
    selects login.salesforce.com as the OAuth host — not a cosmetic label)."""
    signals = []
    if name and PROD_NAME_RX.search(name):
        signals.append(f"the name '{name}' matches a production pattern "
                       "(prod/production/live)")
    if env_type == "production":
        signals.append("the environment type is 'production'")
    if connection_org_type == "production":
        signals.append("its Salesforce connection is a production org "
                       "(authenticates via login.salesforce.com)")
    return signals


class ProductionConfirmationRequired(ValueError):
    """Saving is_production=False on an environment that looks like production.
    Carries the trip reasons; callers re-submit with
    ``confirm_not_production=True`` after a human explicitly confirms."""

    def __init__(self, signals):
        self.signals = list(signals)
        super().__init__(
            "This environment looks like a production org: "
            + "; ".join(self.signals)
            + ". Tick 'This is a PRODUCTION org' — or, if it truly is not "
            "production, explicitly confirm that and save again.")


class EnvironmentService:
    def __init__(self, env_repo, conn_repo=None):
        self.env_repo = env_repo
        self.conn_repo = conn_repo

    def create_environment(self, tenant_id, name, env_type, sf_instance_url=None, sf_api_version=None, **kwargs):
        if env_type not in VALID_ENV_TYPES:
            raise ValueError(f"Invalid env_type. Must be one of: {', '.join(VALID_ENV_TYPES)}")
        ep = kwargs.get("execution_policy", "full")
        if ep not in VALID_EXECUTION_POLICIES:
            raise ValueError(f"Invalid execution_policy. Must be one of: {', '.join(VALID_EXECUTION_POLICIES)}")
        cm = kwargs.get("capture_mode", "smart")
        if cm not in VALID_CAPTURE_MODES:
            raise ValueError(f"Invalid capture_mode. Must be one of: {', '.join(VALID_CAPTURE_MODES)}")

        connection_org_type = None
        connection_id = kwargs.get("connection_id")
        if connection_id and self.conn_repo:
            conn_data = self.conn_repo.get_connection_decrypted(connection_id, tenant_id)
            if conn_data and conn_data["connection_type"] == "salesforce":
                cfg = conn_data["config"]
                sf_instance_url = sf_instance_url or cfg.get("instance_url", "")
                sf_api_version = sf_api_version or cfg.get("api_version", "59.0")
                connection_org_type = cfg.get("org_type")

        if not sf_instance_url:
            raise ValueError("Salesforce Instance URL is required (provide directly or via a Connection)")
        # SEC-3: reject a non-Salesforce / private-IP / non-https instance URL at
        # write time so the server can never later be pointed at an internal host
        # with the org's access-token. Raises SalesforceUrlError (a ValueError) ->
        # 400 at the route.
        from primeqa.integrations.sf_url import validate_sf_instance_url
        validate_sf_instance_url(sf_instance_url)
        sf_api_version = sf_api_version or "59.0"

        if env_type == "production":
            kwargs.setdefault("cleanup_mandatory", True)
            # Type 'production' with the enforcement flag off is contradictory;
            # default the flag on unless the caller explicitly says otherwise
            # (an explicit False still has to pass the guard below).
            kwargs.setdefault("is_production", True)

        confirm_not_production = bool(kwargs.pop("confirm_not_production", False))
        kwargs["is_production"] = bool(kwargs.get("is_production", False))
        signals = production_signals(name, env_type, connection_org_type)
        if not kwargs["is_production"] and signals and not confirm_not_production:
            raise ProductionConfirmationRequired(signals)

        env = self.env_repo.create_environment(
            tenant_id, name, env_type, sf_instance_url, sf_api_version, **kwargs,
        )
        if env.is_production:
            self._log_flag_activity(
                tenant_id, kwargs.get("created_by"), "environment.is_production_set",
                env.id, {"environment_name": name, "is_production": True})
        elif signals:
            self._log_flag_activity(
                tenant_id, kwargs.get("created_by"),
                "environment.not_production_confirmed",
                env.id, {"environment_name": name, "signals": signals})
        return self._env_dict(env)

    def update_environment(self, environment_id, tenant_id, updates, actor_user_id=None):
        if "execution_policy" in updates and updates["execution_policy"] not in VALID_EXECUTION_POLICIES:
            raise ValueError(f"Invalid execution_policy. Must be one of: {', '.join(VALID_EXECUTION_POLICIES)}")
        if "capture_mode" in updates and updates["capture_mode"] not in VALID_CAPTURE_MODES:
            raise ValueError(f"Invalid capture_mode. Must be one of: {', '.join(VALID_CAPTURE_MODES)}")

        env = self.env_repo.get_environment(environment_id, tenant_id)
        if not env:
            raise ValueError("Environment not found")

        confirm_not_production = bool(updates.pop("confirm_not_production", False))
        old_flag = bool(env.is_production)
        if "is_production" in updates:
            updates["is_production"] = bool(updates["is_production"])
        new_flag = updates.get("is_production", old_flag)
        # The guard re-evaluates whenever the save touches the flag or an
        # identity-bearing field (name / env_type) and would leave the flag
        # off — a rename to "Prod1" must not slip past on a partial update.
        signals = []
        if not new_flag and ({"is_production", "name", "env_type"} & set(updates)):
            signals = production_signals(
                updates.get("name") or env.name,
                updates.get("env_type") or env.env_type,
                self._connection_org_type(env.connection_id, tenant_id))
            if signals and not confirm_not_production:
                raise ProductionConfirmationRequired(signals)

        env = self.env_repo.update_environment(environment_id, tenant_id, updates)
        if not env:
            raise ValueError("Environment not found")
        if bool(env.is_production) != old_flag:
            self._log_flag_activity(
                tenant_id, actor_user_id, "environment.is_production_changed",
                env.id, {"environment_name": env.name, "from": old_flag,
                         "to": bool(env.is_production),
                         "confirmed_not_production": confirm_not_production,
                         "signals": signals})
        elif signals and confirm_not_production:
            self._log_flag_activity(
                tenant_id, actor_user_id, "environment.not_production_confirmed",
                env.id, {"environment_name": env.name, "signals": signals})
        return self._env_dict(env)

    def _connection_org_type(self, connection_id, tenant_id):
        """Best-effort org_type ('production' / 'sandbox') of the linked
        Salesforce connection — org_type lives in plaintext config, so this is
        a row read, no decryption and no Salesforce call. None when there is
        no connection/repo or on any read failure: the guard then rests on the
        name / env_type signals rather than blocking the save."""
        if not (connection_id and self.conn_repo):
            return None
        try:
            conn = self.conn_repo.get_connection(connection_id, tenant_id)
            if conn is not None and conn.connection_type == "salesforce":
                return (conn.config or {}).get("org_type")
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("connection org_type read failed for connection %s: %s",
                           connection_id, e)
        return None

    def _log_flag_activity(self, tenant_id, user_id, action, environment_id, details):
        """activity_log write for is_production changes (2026-07-07 incident).
        Best-effort — an audit-log failure must not break the save."""
        try:
            from primeqa.core.repository import ActivityLogRepository
            ActivityLogRepository(self.env_repo.db).log_activity(
                tenant_id, user_id, action, "environment", environment_id, details)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("environment activity-log write failed (%s): %s",
                           action, e)

    def get_environment(self, environment_id, tenant_id):
        env = self.env_repo.get_environment(environment_id, tenant_id)
        if not env:
            return None
        return self._env_dict(env)

    def store_credentials(self, environment_id, tenant_id, client_id, client_secret, access_token=None, refresh_token=None):
        env = self.env_repo.get_environment(environment_id, tenant_id)
        if not env:
            raise ValueError("Environment not found")
        self.env_repo.store_credentials(environment_id, client_id, client_secret, access_token, refresh_token)
        return {"status": "stored"}

    def get_credentials(self, environment_id, tenant_id):
        env = self.env_repo.get_environment(environment_id, tenant_id)
        if not env:
            raise ValueError("Environment not found")
        return self.env_repo.get_credentials_decrypted(environment_id)

    def test_connection(self, environment_id, tenant_id):
        import requests as http_requests

        env = self.env_repo.get_environment(environment_id, tenant_id)
        if not env:
            raise ValueError("Environment not found")

        creds = self.env_repo.get_credentials_decrypted(environment_id)
        if not creds or not creds.get("access_token"):
            raise ValueError("No credentials or access token stored for this environment")

        # SEC-3: re-validate before the outbound call (defense-in-depth — the env
        # may predate the write-time guard). Never send the access-token to a
        # non-Salesforce / private host.
        from primeqa.integrations.sf_url import validate_sf_instance_url, SalesforceUrlError
        try:
            validate_sf_instance_url(env.sf_instance_url)
        except SalesforceUrlError as e:
            return {"status": "failed", "detail": str(e)}
        url = f"{env.sf_instance_url.rstrip('/')}/services/data/v{env.sf_api_version}/"
        try:
            resp = http_requests.get(url, headers={
                "Authorization": f"Bearer {creds['access_token']}",
            }, timeout=15)
            if resp.status_code == 200:
                return {"status": "connected", "sf_version": env.sf_api_version}
            # SEC-9: log the raw upstream body server-side only; return a generic,
            # category-keyed message (the caller places this into a redirect URL
            # that lands in history/Referer/proxy logs).
            logger.warning("env %s SF connection test failed: HTTP %s body=%r",
                           environment_id, resp.status_code, resp.text[:500])
            return {"status": "failed", "status_code": resp.status_code,
                    "detail": f"Salesforce returned HTTP {resp.status_code}."}
        except http_requests.RequestException as e:
            logger.warning("env %s SF connection test network error: %s", environment_id, e)
            return {"status": "failed",
                    "detail": "Could not reach the Salesforce instance (network error)."}

    def refresh_sf_token(self, environment_id, tenant_id):
        pass

    def list_environments(self, tenant_id, user_id=None, role=None):
        envs = self.env_repo.list_environments(tenant_id, user_id, role)
        return [self._env_dict(e) for e in envs]

    @staticmethod
    def _env_dict(env):
        return {
            "id": env.id,
            "tenant_id": env.tenant_id,
            "name": env.name,
            "env_type": env.env_type,
            "sf_instance_url": env.sf_instance_url,
            "sf_api_version": env.sf_api_version,
            "execution_policy": env.execution_policy,
            "capture_mode": env.capture_mode,
            "max_execution_slots": env.max_execution_slots,
            "cleanup_mandatory": env.cleanup_mandatory,
            "is_production": env.is_production,
            "is_active": env.is_active,
            "created_at": env.created_at.isoformat() if env.created_at else None,
            "updated_at": env.updated_at.isoformat() if env.updated_at else None,
            "created_by": env.created_by,
            "connection_id": env.connection_id,
            "jira_connection_id": env.jira_connection_id,
            "llm_connection_id": env.llm_connection_id,
        }


VALID_CONNECTION_TYPES = {"salesforce", "jira", "llm"}
REQUIRED_CONFIG = {
    "salesforce": ["client_id", "client_secret"],
    "jira": ["base_url"],
    "llm": ["api_key"],
}


class ConnectionService:
    def __init__(self, conn_repo):
        self.conn_repo = conn_repo

    def create_connection(self, tenant_id, connection_type, name, config, created_by):
        if connection_type not in VALID_CONNECTION_TYPES:
            raise ValueError(f"Invalid connection_type. Must be one of: {', '.join(VALID_CONNECTION_TYPES)}")
        required = REQUIRED_CONFIG.get(connection_type, [])
        missing = [f for f in required if not config.get(f)]
        if missing:
            raise ValueError(f"Missing config fields for {connection_type}: {', '.join(missing)}")
        conn = self.conn_repo.create_connection(tenant_id, connection_type, name, config, created_by)
        return self._conn_dict(conn)

    def update_connection(self, connection_id, tenant_id, updates):
        conn = self.conn_repo.update_connection(connection_id, tenant_id, updates)
        if not conn:
            raise ValueError("Connection not found")
        return self._conn_dict(conn)

    def delete_connection(self, connection_id, tenant_id):
        if not self.conn_repo.delete_connection(connection_id, tenant_id):
            raise ValueError("Connection not found")

    def list_connections(self, tenant_id, connection_type=None):
        conns = self.conn_repo.list_connections(tenant_id, connection_type)
        return [self._conn_dict(c) for c in conns]

    def get_connection(self, connection_id, tenant_id):
        return self.conn_repo.get_connection_decrypted(connection_id, tenant_id)

    def get_connection_display(self, connection_id, tenant_id):
        """SEC-1: redacted connection detail for API/UI display — NO decrypted
        secrets. Returns the same shape as ``list_connections`` (``_conn_dict``:
        id/type/name/status, no ``config``). ``get_connection_decrypted()`` is
        reserved for server-side credential resolution (sync/execution/worker),
        never an HTTP response body. Tenant-scoped via the repo (returns None if
        the connection is absent or belongs to another tenant)."""
        conn = self.conn_repo.get_connection(connection_id, tenant_id)
        if not conn:
            return None
        return self._conn_dict(conn)

    def test_connection(self, connection_id, tenant_id):
        import requests as http_requests
        data = self.conn_repo.get_connection_decrypted(connection_id, tenant_id)
        if not data:
            raise ValueError("Connection not found")
        cfg = data["config"]
        ctype = data["connection_type"]
        try:
            if ctype == "salesforce":
                from primeqa.integrations.sf_url import validate_sf_instance_url
                login_url = cfg.get("instance_url", "").rstrip("/")
                if not login_url:
                    org_type = cfg.get("org_type", "sandbox")
                    login_url = "https://test.salesforce.com" if org_type == "sandbox" else "https://login.salesforce.com"
                # SEC-5: never POST the org's client_secret (+ password in the
                # password flow) to a non-Salesforce / private login host. Raises
                # SalesforceUrlError -> caught by this method's except -> failed.
                validate_sf_instance_url(login_url)
                auth_flow = cfg.get("auth_flow", "client_credentials")
                token_data_body = {
                    "client_id": cfg.get("client_id", ""),
                    "client_secret": cfg.get("client_secret", ""),
                }
                if auth_flow == "password":
                    token_data_body["grant_type"] = "password"
                    token_data_body["username"] = cfg.get("username", "")
                    token_data_body["password"] = cfg.get("password", "")
                else:
                    token_data_body["grant_type"] = "client_credentials"
                token_resp = http_requests.post(
                    f"{login_url}/services/oauth2/token",
                    data=token_data_body,
                    timeout=15,
                )
                if token_resp.status_code != 200:
                    self.conn_repo.update_status(connection_id, "error", tenant_id)
                    # SEC-9: log the raw OAuth error body server-side; return a
                    # generic message (never echoed into a redirect URL).
                    logger.warning("connection %s SF OAuth failed: HTTP %s body=%r",
                                   connection_id, token_resp.status_code, token_resp.text[:500])
                    return {"status": "failed",
                            "detail": f"Salesforce authentication failed (HTTP {token_resp.status_code})."}
                token_data = token_resp.json()
                access_token = token_data.get("access_token", "")
                instance_url = token_data.get("instance_url", cfg.get("instance_url", ""))
                # SEC-3: validate the returned instance_url before sending the
                # access-token to it (defense-in-depth over the OAuth response).
                validate_sf_instance_url(instance_url)
                api_url = f"{instance_url.rstrip('/')}/services/data/v{cfg.get('api_version', '59.0')}/"
                resp = http_requests.get(api_url, headers={
                    "Authorization": f"Bearer {access_token}",
                }, timeout=15)
                ok = resp.status_code == 200
            elif ctype == "jira":
                url = f"{cfg['base_url'].rstrip('/')}/rest/api/2/myself"
                headers = {}
                if cfg.get("auth_type") == "basic" and cfg.get("username") and cfg.get("api_token"):
                    import base64
                    cred = base64.b64encode(f"{cfg['username']}:{cfg['api_token']}".encode()).decode()
                    headers["Authorization"] = f"Basic {cred}"
                resp = http_requests.get(url, headers=headers, timeout=15)
                ok = resp.status_code == 200
            elif ctype == "llm":
                # A key test needs ANY valid model, not a choice — ping with
                # the cheap canonical Haiku id. (The per-connection model was
                # removed: models are tenant-governed via llm_model_override,
                # migration 060; the old config value could name a retired id
                # and 404 the test even with a perfectly good key.)
                from primeqa.intelligence.llm.router import HAIKU
                resp = http_requests.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": cfg["api_key"],
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={"model": HAIKU, "max_tokens": 10,
                          "messages": [{"role": "user", "content": "ping"}]},
                    timeout=15,
                )
                ok = resp.status_code == 200
            else:
                return {"status": "error", "detail": "Unknown connection type"}

            self.conn_repo.update_status(connection_id, "active" if ok else "error", tenant_id)
            if ok:
                return {"status": "connected"}
            # SEC-9: log the raw upstream body server-side; return a generic message.
            logger.warning("connection %s test failed: HTTP %s body=%r",
                           connection_id, resp.status_code, resp.text[:500])
            return {"status": "failed",
                    "detail": f"Connection test failed (HTTP {resp.status_code})."}
        except Exception as e:
            self.conn_repo.update_status(connection_id, "error", tenant_id)
            # SEC-9: log the exception server-side; never echo it to the client.
            logger.warning("connection %s test error: %s", connection_id, e)
            return {"status": "failed", "detail": "Connection test failed. See server logs for details."}

    @staticmethod
    def _conn_dict(c):
        return {
            "id": c.id, "tenant_id": c.tenant_id,
            "connection_type": c.connection_type, "name": c.name,
            "status": c.status, "created_by": c.created_by,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }


class GroupService:
    def __init__(self, group_repo):
        self.group_repo = group_repo

    def create_group(self, tenant_id, name, created_by, description=None):
        group = self.group_repo.create_group(tenant_id, name, created_by, description)
        return self._group_dict(group)

    def list_groups(self, tenant_id, user_id=None, role=None):
        if role == "admin":
            groups = self.group_repo.list_groups(tenant_id)
        else:
            groups = self.group_repo.list_groups(tenant_id, user_id)
        result = []
        for g in groups:
            d = self._group_dict(g)
            d["member_count"] = self.group_repo.get_member_count(g.id)
            d["environment_count"] = self.group_repo.get_environment_count(g.id)
            result.append(d)
        return result

    def get_group_detail(self, group_id, tenant_id):
        group = self.group_repo.get_group(group_id, tenant_id)
        if not group:
            return None
        members = self.group_repo.get_members(group_id)
        envs = self.group_repo.get_environments(group_id)
        d = self._group_dict(group)
        d["members"] = [{"id": u.id, "email": u.email, "full_name": u.full_name,
                         "role": u.role, "is_active": u.is_active} for u in members]
        d["environments"] = [{"id": e.id, "name": e.name, "env_type": e.env_type,
                              "sf_instance_url": e.sf_instance_url} for e in envs]
        return d

    def delete_group(self, group_id, tenant_id):
        if not self.group_repo.delete_group(group_id, tenant_id):
            raise ValueError("Group not found")

    def add_member(self, group_id, tenant_id, user_id, added_by):
        from primeqa.core.models import User
        group = self.group_repo.get_group(group_id, tenant_id)
        if not group:
            raise ValueError("Group not found")
        # Tenant isolation (SEC-2): the user must belong to the caller's tenant
        # before it can be linked. group_members has no tenant column, so — like
        # release/service.py add_requirement — the guard must live here; without
        # it a foreign user_id links into the group and leaks the user's
        # email/full_name back through get_group_detail.
        own = self.group_repo.db.query(User.id).filter(
            User.id == user_id, User.tenant_id == tenant_id,
        ).first()
        if not own:
            raise ValueError("User not found")
        self.group_repo.add_member(group_id, user_id, added_by)

    def remove_member(self, group_id, tenant_id, user_id):
        group = self.group_repo.get_group(group_id, tenant_id)
        if not group:
            raise ValueError("Group not found")
        self.group_repo.remove_member(group_id, user_id)

    def add_environment(self, group_id, tenant_id, environment_id, added_by):
        from primeqa.core.models import Environment
        group = self.group_repo.get_group(group_id, tenant_id)
        if not group:
            raise ValueError("Group not found")
        # Tenant isolation (SEC-2): the environment must belong to the caller's
        # tenant before linking (group_environments has no tenant column). Without
        # it a foreign environment_id links in and leaks its sf_instance_url back
        # through get_group_detail.
        own = self.group_repo.db.query(Environment.id).filter(
            Environment.id == environment_id, Environment.tenant_id == tenant_id,
        ).first()
        if not own:
            raise ValueError("Environment not found")
        self.group_repo.add_environment(group_id, environment_id, added_by)

    def remove_environment(self, group_id, tenant_id, environment_id):
        group = self.group_repo.get_group(group_id, tenant_id)
        if not group:
            raise ValueError("Group not found")
        self.group_repo.remove_environment(group_id, environment_id)

    @staticmethod
    def _group_dict(g):
        return {
            "id": g.id, "tenant_id": g.tenant_id, "name": g.name,
            "description": g.description, "created_by": g.created_by,
            "created_at": g.created_at.isoformat() if g.created_at else None,
        }
