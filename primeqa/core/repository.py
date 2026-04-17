"""Repository for the core domain.

DB queries scoped to: tenants, users, refresh_tokens, environments,
                      environment_credentials, activity_log
"""

from datetime import datetime, timezone

from sqlalchemy import func

from primeqa.core.models import (
    User, RefreshToken, Environment, EnvironmentCredential, ActivityLog,
    Group, GroupMember, GroupEnvironment, Connection,
)


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
            created_by=kwargs.get("created_by"),
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

    def list_environments(self, tenant_id, user_id=None, role=None):
        q = self.db.query(Environment).filter(
            Environment.tenant_id == tenant_id,
            Environment.is_active == True,
        )
        if role != "admin" and user_id is not None:
            group_env_ids = self.db.query(GroupEnvironment.environment_id).join(
                GroupMember, GroupEnvironment.group_id == GroupMember.group_id,
            ).filter(GroupMember.user_id == user_id).subquery()
            q = q.filter(
                (Environment.created_by == user_id) |
                (Environment.id.in_(group_env_ids))
            )
        return q.all()

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


class ConnectionRepository:
    def __init__(self, db):
        self.db = db

    def create_connection(self, tenant_id, connection_type, name, config, created_by):
        from primeqa.core.crypto import encrypt
        encrypted_config = self._encrypt_config(connection_type, config)
        conn = Connection(
            tenant_id=tenant_id,
            connection_type=connection_type,
            name=name,
            config=encrypted_config,
            created_by=created_by,
        )
        self.db.add(conn)
        self.db.commit()
        self.db.refresh(conn)
        return conn

    def get_connection(self, connection_id, tenant_id=None):
        q = self.db.query(Connection).filter(Connection.id == connection_id)
        if tenant_id:
            q = q.filter(Connection.tenant_id == tenant_id)
        return q.first()

    def list_connections(self, tenant_id, connection_type=None):
        q = self.db.query(Connection).filter(Connection.tenant_id == tenant_id)
        if connection_type:
            q = q.filter(Connection.connection_type == connection_type)
        return q.order_by(Connection.created_at.desc()).all()

    def update_connection(self, connection_id, tenant_id, updates):
        conn = self.get_connection(connection_id, tenant_id)
        if not conn:
            return None
        if "config" in updates:
            from primeqa.core.crypto import encrypt
            updates["config"] = self._encrypt_config(conn.connection_type, updates["config"])
        for k, v in updates.items():
            if hasattr(conn, k) and k not in ("id", "tenant_id", "created_by", "created_at"):
                setattr(conn, k, v)
        conn.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(conn)
        return conn

    def delete_connection(self, connection_id, tenant_id):
        conn = self.get_connection(connection_id, tenant_id)
        if not conn:
            return False
        self.db.delete(conn)
        self.db.commit()
        return True

    def update_status(self, connection_id, status):
        conn = self.db.query(Connection).filter(Connection.id == connection_id).first()
        if conn:
            conn.status = status
            conn.updated_at = datetime.now(timezone.utc)
            self.db.commit()

    def get_connection_decrypted(self, connection_id, tenant_id=None):
        from primeqa.core.crypto import decrypt
        conn = self.get_connection(connection_id, tenant_id)
        if not conn:
            return None
        config = dict(conn.config) if conn.config else {}
        sensitive = self._sensitive_fields(conn.connection_type)
        for field in sensitive:
            if field in config and config[field]:
                try:
                    config[field] = decrypt(config[field])
                except Exception:
                    pass
        return {
            "id": conn.id, "tenant_id": conn.tenant_id,
            "connection_type": conn.connection_type, "name": conn.name,
            "config": config, "status": conn.status,
            "created_by": conn.created_by,
            "created_at": conn.created_at.isoformat() if conn.created_at else None,
        }

    @staticmethod
    def _sensitive_fields(connection_type):
        return {
            "salesforce": ["client_id", "client_secret", "password"],
            "jira": ["credentials", "api_token"],
            "llm": ["api_key"],
        }.get(connection_type, [])

    def _encrypt_config(self, connection_type, config):
        from primeqa.core.crypto import encrypt
        result = dict(config)
        for field in self._sensitive_fields(connection_type):
            if field in result and result[field] and not str(result[field]).startswith("gAAAAA"):
                result[field] = encrypt(str(result[field]))
        return result


class GroupRepository:
    def __init__(self, db):
        self.db = db

    def create_group(self, tenant_id, name, created_by, description=None):
        group = Group(
            tenant_id=tenant_id, name=name,
            description=description, created_by=created_by,
        )
        self.db.add(group)
        self.db.commit()
        self.db.refresh(group)
        return group

    def get_group(self, group_id, tenant_id=None):
        q = self.db.query(Group).filter(Group.id == group_id)
        if tenant_id:
            q = q.filter(Group.tenant_id == tenant_id)
        return q.first()

    def list_groups(self, tenant_id, user_id=None):
        q = self.db.query(Group).filter(Group.tenant_id == tenant_id)
        if user_id:
            member_group_ids = self.db.query(GroupMember.group_id).filter(
                GroupMember.user_id == user_id,
            ).subquery()
            q = q.filter(Group.id.in_(member_group_ids))
        return q.order_by(Group.name).all()

    def update_group(self, group_id, tenant_id, updates):
        group = self.get_group(group_id, tenant_id)
        if not group:
            return None
        for k, v in updates.items():
            if hasattr(group, k) and k not in ("id", "tenant_id", "created_by", "created_at"):
                setattr(group, k, v)
        group.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(group)
        return group

    def delete_group(self, group_id, tenant_id):
        group = self.get_group(group_id, tenant_id)
        if not group:
            return False
        self.db.delete(group)
        self.db.commit()
        return True

    def add_member(self, group_id, user_id, added_by):
        existing = self.db.query(GroupMember).filter(
            GroupMember.group_id == group_id, GroupMember.user_id == user_id,
        ).first()
        if existing:
            return existing
        gm = GroupMember(group_id=group_id, user_id=user_id, added_by=added_by)
        self.db.add(gm)
        self.db.commit()
        self.db.refresh(gm)
        return gm

    def remove_member(self, group_id, user_id):
        gm = self.db.query(GroupMember).filter(
            GroupMember.group_id == group_id, GroupMember.user_id == user_id,
        ).first()
        if gm:
            self.db.delete(gm)
            self.db.commit()
            return True
        return False

    def get_members(self, group_id):
        return self.db.query(User).join(
            GroupMember, GroupMember.user_id == User.id,
        ).filter(GroupMember.group_id == group_id).all()

    def add_environment(self, group_id, environment_id, added_by):
        existing = self.db.query(GroupEnvironment).filter(
            GroupEnvironment.group_id == group_id,
            GroupEnvironment.environment_id == environment_id,
        ).first()
        if existing:
            return existing
        ge = GroupEnvironment(group_id=group_id, environment_id=environment_id, added_by=added_by)
        self.db.add(ge)
        self.db.commit()
        self.db.refresh(ge)
        return ge

    def remove_environment(self, group_id, environment_id):
        ge = self.db.query(GroupEnvironment).filter(
            GroupEnvironment.group_id == group_id,
            GroupEnvironment.environment_id == environment_id,
        ).first()
        if ge:
            self.db.delete(ge)
            self.db.commit()
            return True
        return False

    def get_environments(self, group_id):
        return self.db.query(Environment).join(
            GroupEnvironment, GroupEnvironment.environment_id == Environment.id,
        ).filter(GroupEnvironment.group_id == group_id).all()

    def get_member_count(self, group_id):
        return self.db.query(func.count(GroupMember.id)).filter(
            GroupMember.group_id == group_id,
        ).scalar()

    def get_environment_count(self, group_id):
        return self.db.query(func.count(GroupEnvironment.id)).filter(
            GroupEnvironment.group_id == group_id,
        ).scalar()
