"""Repository for the core domain.

DB queries scoped to: tenants, users, refresh_tokens, environments,
                      environment_credentials, activity_log
"""


class UserRepository:
    def __init__(self, db):
        self.db = db

    def get_user_by_email(self, tenant_id, email):
        pass

    def get_user_by_id(self, user_id):
        pass

    def create_user(self, tenant_id, email, password_hash, full_name, role):
        pass

    def update_user(self, user_id, updates):
        pass

    def list_users(self, tenant_id):
        pass

    def count_active_users(self, tenant_id):
        pass


class RefreshTokenRepository:
    def __init__(self, db):
        self.db = db

    def create_refresh_token(self, user_id, token_hash, expires_at):
        pass

    def get_refresh_token(self, token_hash):
        pass

    def revoke_refresh_token(self, token_id):
        pass

    def revoke_all_user_tokens(self, user_id):
        pass


class EnvironmentRepository:
    def __init__(self, db):
        self.db = db

    def create_environment(self, tenant_id, name, env_type, sf_instance_url, sf_api_version, **kwargs):
        pass

    def get_environment(self, environment_id, tenant_id):
        pass

    def list_environments(self, tenant_id):
        pass

    def update_environment(self, environment_id, updates):
        pass

    def store_credentials(self, environment_id, client_id, client_secret, access_token=None, refresh_token=None):
        pass

    def get_credentials(self, environment_id):
        pass


class ActivityLogRepository:
    def __init__(self, db):
        self.db = db

    def log_activity(self, tenant_id, user_id, action, entity_type, entity_id=None, details=None):
        pass

    def list_activity(self, tenant_id, limit=50, offset=0):
        pass
