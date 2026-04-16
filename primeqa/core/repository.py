"""Repository for the core domain.

DB queries scoped to: tenants, users, refresh_tokens, environments,
                      environment_credentials, activity_log
"""

from datetime import datetime, timezone

from sqlalchemy import func

from primeqa.core.models import User, RefreshToken, Environment, EnvironmentCredential, ActivityLog


class UserRepository:
    def __init__(self, db):
        self.db = db

    def get_user_by_email(self, tenant_id, email):
        return self.db.query(User).filter(
            User.tenant_id == tenant_id,
            User.email == email,
        ).first()

    def get_user_by_id(self, user_id):
        return self.db.query(User).filter(User.id == user_id).first()

    def create_user(self, tenant_id, email, password_hash, full_name, role):
        user = User(
            tenant_id=tenant_id,
            email=email,
            password_hash=password_hash,
            full_name=full_name,
            role=role,
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def update_user(self, user_id, updates):
        user = self.get_user_by_id(user_id)
        if not user:
            return None
        for key, value in updates.items():
            if hasattr(user, key):
                setattr(user, key, value)
        user.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(user)
        return user

    def list_users(self, tenant_id):
        return self.db.query(User).filter(User.tenant_id == tenant_id).all()

    def count_active_users(self, tenant_id):
        return self.db.query(func.count(User.id)).filter(
            User.tenant_id == tenant_id,
            User.is_active == True,
        ).scalar()

    def update_last_login(self, user_id):
        user = self.get_user_by_id(user_id)
        if user:
            user.last_login_at = datetime.now(timezone.utc)
            self.db.commit()


class RefreshTokenRepository:
    def __init__(self, db):
        self.db = db

    def create_refresh_token(self, user_id, token_hash, expires_at):
        token = RefreshToken(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        self.db.add(token)
        self.db.commit()
        self.db.refresh(token)
        return token

    def get_refresh_token(self, token_hash):
        return self.db.query(RefreshToken).filter(
            RefreshToken.token_hash == token_hash,
            RefreshToken.revoked == False,
        ).first()

    def revoke_refresh_token(self, token_id):
        token = self.db.query(RefreshToken).filter(RefreshToken.id == token_id).first()
        if token:
            token.revoked = True
            self.db.commit()

    def revoke_all_user_tokens(self, user_id):
        self.db.query(RefreshToken).filter(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked == False,
        ).update({"revoked": True})
        self.db.commit()

    def count_active_tokens(self, user_id):
        return self.db.query(func.count(RefreshToken.id)).filter(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked == False,
        ).scalar()


class EnvironmentRepository:
    def __init__(self, db):
        self.db = db

    def create_environment(self, tenant_id, name, env_type, sf_instance_url, sf_api_version, **kwargs):
        env = Environment(
            tenant_id=tenant_id,
            name=name,
            env_type=env_type,
            sf_instance_url=sf_instance_url,
            sf_api_version=sf_api_version,
            execution_policy=kwargs.get("execution_policy", "full"),
            capture_mode=kwargs.get("capture_mode", "smart"),
            max_execution_slots=kwargs.get("max_execution_slots", 2),
            cleanup_mandatory=kwargs.get("cleanup_mandatory", False),
        )
        self.db.add(env)
        self.db.commit()
        self.db.refresh(env)
        return env

    def get_environment(self, environment_id, tenant_id=None):
        q = self.db.query(Environment).filter(Environment.id == environment_id)
        if tenant_id is not None:
            q = q.filter(Environment.tenant_id == tenant_id)
        return q.first()

    def list_environments(self, tenant_id):
        return self.db.query(Environment).filter(
            Environment.tenant_id == tenant_id,
            Environment.is_active == True,
        ).all()

    def update_environment(self, environment_id, tenant_id, updates):
        env = self.get_environment(environment_id, tenant_id)
        if not env:
            return None
        for key, value in updates.items():
            if hasattr(env, key):
                setattr(env, key, value)
        env.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(env)
        return env

    def store_credentials(self, environment_id, client_id, client_secret, access_token=None, refresh_token=None):
        from primeqa.core.crypto import encrypt
        existing = self.db.query(EnvironmentCredential).filter(
            EnvironmentCredential.environment_id == environment_id,
        ).first()
        if existing:
            existing.client_id = encrypt(client_id)
            existing.client_secret = encrypt(client_secret)
            existing.access_token = encrypt(access_token) if access_token else None
            existing.refresh_token = encrypt(refresh_token) if refresh_token else None
            existing.status = "valid"
            self.db.commit()
            self.db.refresh(existing)
            return existing
        cred = EnvironmentCredential(
            environment_id=environment_id,
            client_id=encrypt(client_id),
            client_secret=encrypt(client_secret),
            access_token=encrypt(access_token) if access_token else None,
            refresh_token=encrypt(refresh_token) if refresh_token else None,
        )
        self.db.add(cred)
        self.db.commit()
        self.db.refresh(cred)
        return cred

    def get_credentials(self, environment_id):
        return self.db.query(EnvironmentCredential).filter(
            EnvironmentCredential.environment_id == environment_id,
        ).first()

    def get_credentials_decrypted(self, environment_id):
        from primeqa.core.crypto import decrypt
        cred = self.get_credentials(environment_id)
        if not cred:
            return None
        return {
            "id": cred.id,
            "environment_id": cred.environment_id,
            "client_id": decrypt(cred.client_id),
            "client_secret": decrypt(cred.client_secret),
            "access_token": decrypt(cred.access_token),
            "refresh_token": decrypt(cred.refresh_token),
            "status": cred.status,
            "token_expires_at": cred.token_expires_at.isoformat() if cred.token_expires_at else None,
            "last_refreshed_at": cred.last_refreshed_at.isoformat() if cred.last_refreshed_at else None,
        }


class ActivityLogRepository:
    def __init__(self, db):
        self.db = db

    def log_activity(self, tenant_id, user_id, action, entity_type, entity_id=None, details=None):
        entry = ActivityLog(
            tenant_id=tenant_id,
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details or {},
        )
        self.db.add(entry)
        self.db.commit()

    def list_activity(self, tenant_id, limit=50, offset=0):
        pass
