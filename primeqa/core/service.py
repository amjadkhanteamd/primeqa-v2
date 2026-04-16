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


class EnvironmentService:
    def __init__(self, env_repo):
        self.env_repo = env_repo

    def create_environment(self, tenant_id, name, env_type, sf_instance_url, sf_api_version, **kwargs):
        pass

    def update_environment(self, environment_id, updates):
        pass

    def test_connection(self, environment_id):
        pass

    def refresh_sf_token(self, environment_id):
        pass

    def list_environments(self, tenant_id):
        pass
