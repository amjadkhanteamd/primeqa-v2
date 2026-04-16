"""Service layer for the vector domain.

Business logic: RAG search with tenant + environment scoping.
"""


class VectorService:
    def __init__(self, embedding_repo):
        self.embedding_repo = embedding_repo

    def index_content(self, tenant_id, environment_id, content_type, source_id, content_text):
        pass

    def search(self, tenant_id, environment_id, query_text, content_type=None, limit=10):
        pass

    def reindex(self, tenant_id, environment_id=None):
        pass
