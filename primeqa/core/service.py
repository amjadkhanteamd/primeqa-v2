"""Service layer for the core domain.

Business logic: user management, auth, tenant operations, environment management.
"""

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

ACCESS_TOKEN_EXPIRY = timedelta(minutes=30)
REFRESH_TOKEN_EXPIRY = timedelta(days=7)
MAX_USERS_PER_TENANT = 20
MAX_REFRESH_TOKENS_PER_USER = 5


def _get_jwt_secret():
    return os.getenv("JWT_SECRET", "dev-secret-change-me")


class AuthService:
    def __init__(self, user_repo, token_repo):
        self.user_repo = user_repo
        self.token_repo = token_repo

    def login(self, tenant_id, email, password):
        user = self.user_repo.get_user_by_email(tenant_id, email)
        if not user or not user.is_active:
            return None

        if not bcrypt.checkpw(password.encode("utf-8"), user.password_hash.encode("utf-8")):
            return None

        self.user_repo.update_last_login(user.id)

        access_token = self._create_access_token(user)
        raw_refresh, _ = self._create_refresh_token(user.id)

        return {
            "access_token": access_token,
            "refresh_token": raw_refresh,
            "user": self._user_dict(user),
        }

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

    def create_user(self, tenant_id, email, password, full_name, role):
        active_count = self.user_repo.count_active_users(tenant_id)
        if active_count >= MAX_USERS_PER_TENANT:
            raise ValueError(f"Tenant has reached the maximum of {MAX_USERS_PER_TENANT} active users")

        existing = self.user_repo.get_user_by_email(tenant_id, email)
        if existing:
            raise ValueError("A user with this email already exists in this tenant")

        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")
        user = self.user_repo.create_user(tenant_id, email, password_hash, full_name, role)
        return self._user_dict(user)

    def update_user(self, user_id, **kwargs):
        allowed = {"role", "is_active", "full_name"}
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not updates:
            raise ValueError("No valid fields to update")
        user = self.user_repo.update_user(user_id, updates)
        if not user:
            raise ValueError("User not found")
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


class EnvironmentService:
    def __init__(self, env_repo):
        self.env_repo = env_repo

    def create_environment(self, tenant_id, name, env_type, sf_instance_url, sf_api_version, **kwargs):
        if env_type not in VALID_ENV_TYPES:
            raise ValueError(f"Invalid env_type. Must be one of: {', '.join(VALID_ENV_TYPES)}")
        ep = kwargs.get("execution_policy", "full")
        if ep not in VALID_EXECUTION_POLICIES:
            raise ValueError(f"Invalid execution_policy. Must be one of: {', '.join(VALID_EXECUTION_POLICIES)}")
        cm = kwargs.get("capture_mode", "smart")
        if cm not in VALID_CAPTURE_MODES:
            raise ValueError(f"Invalid capture_mode. Must be one of: {', '.join(VALID_CAPTURE_MODES)}")

        if env_type == "production":
            kwargs.setdefault("cleanup_mandatory", True)

        env = self.env_repo.create_environment(
            tenant_id, name, env_type, sf_instance_url, sf_api_version, **kwargs,
        )
        return self._env_dict(env)

    def update_environment(self, environment_id, tenant_id, updates):
        if "execution_policy" in updates and updates["execution_policy"] not in VALID_EXECUTION_POLICIES:
            raise ValueError(f"Invalid execution_policy. Must be one of: {', '.join(VALID_EXECUTION_POLICIES)}")
        if "capture_mode" in updates and updates["capture_mode"] not in VALID_CAPTURE_MODES:
            raise ValueError(f"Invalid capture_mode. Must be one of: {', '.join(VALID_CAPTURE_MODES)}")

        env = self.env_repo.update_environment(environment_id, tenant_id, updates)
        if not env:
            raise ValueError("Environment not found")
        return self._env_dict(env)

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

        url = f"{env.sf_instance_url}/services/data/v{env.sf_api_version}/"
        try:
            resp = http_requests.get(url, headers={
                "Authorization": f"Bearer {creds['access_token']}",
            }, timeout=15)
            if resp.status_code == 200:
                return {"status": "connected", "sf_version": env.sf_api_version}
            return {"status": "failed", "status_code": resp.status_code, "detail": resp.text[:500]}
        except http_requests.RequestException as e:
            return {"status": "failed", "detail": str(e)}

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
            "is_active": env.is_active,
            "created_at": env.created_at.isoformat() if env.created_at else None,
            "updated_at": env.updated_at.isoformat() if env.updated_at else None,
            "created_by": env.created_by,
        }


VALID_CONNECTION_TYPES = {"salesforce", "jira", "llm"}
REQUIRED_CONFIG = {
    "salesforce": ["instance_url", "api_version", "client_id", "client_secret"],
    "jira": ["base_url", "auth_type"],
    "llm": ["provider", "api_key"],
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

    def test_connection(self, connection_id, tenant_id):
        import requests as http_requests
        data = self.conn_repo.get_connection_decrypted(connection_id, tenant_id)
        if not data:
            raise ValueError("Connection not found")
        cfg = data["config"]
        ctype = data["connection_type"]
        try:
            if ctype == "salesforce":
                url = f"{cfg['instance_url']}/services/data/v{cfg.get('api_version', '59.0')}/"
                resp = http_requests.get(url, headers={
                    "Authorization": f"Bearer {cfg.get('access_token', '')}",
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
                resp = http_requests.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": cfg["api_key"],
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={"model": cfg.get("model", "claude-sonnet-4-20250514"), "max_tokens": 10,
                          "messages": [{"role": "user", "content": "ping"}]},
                    timeout=15,
                )
                ok = resp.status_code == 200
            else:
                return {"status": "error", "detail": "Unknown connection type"}

            self.conn_repo.update_status(connection_id, "active" if ok else "error")
            if ok:
                return {"status": "connected"}
            return {"status": "failed", "detail": resp.text[:500]}
        except Exception as e:
            self.conn_repo.update_status(connection_id, "error")
            return {"status": "failed", "detail": str(e)}

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
        group = self.group_repo.get_group(group_id, tenant_id)
        if not group:
            raise ValueError("Group not found")
        self.group_repo.add_member(group_id, user_id, added_by)

    def remove_member(self, group_id, tenant_id, user_id):
        group = self.group_repo.get_group(group_id, tenant_id)
        if not group:
            raise ValueError("Group not found")
        self.group_repo.remove_member(group_id, user_id)

    def add_environment(self, group_id, tenant_id, environment_id, added_by):
        group = self.group_repo.get_group(group_id, tenant_id)
        if not group:
            raise ValueError("Group not found")
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
