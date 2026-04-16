"""Service layer for the core domain.

Business logic: user management, auth, tenant operations, environment management.
"""


class AuthService:
    def __init__(self, user_repo, token_repo):
        self.user_repo = user_repo
        self.token_repo = token_repo

    def login(self, email, password):
        pass

    def refresh(self, raw_refresh_token):
        pass

    def logout(self, user_id):
        pass

    def create_user(self, tenant_id, email, password, full_name, role):
        pass

    def update_user(self, user_id, role=None, is_active=None):
        pass

    def list_users(self, tenant_id):
        pass


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
