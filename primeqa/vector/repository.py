"""Repository for the vector domain.

DB queries scoped to: embeddings
"""


class EmbeddingRepository:
    def __init__(self, db):
        self.db = db

    def store_embedding(self, tenant_id, environment_id, content_type, source_id, content_text, embedding):
        pass

    def search_similar(self, tenant_id, environment_id, query_embedding, content_type=None, limit=10):
        pass

    def delete_by_source(self, tenant_id, content_type, source_id):
        pass

    def count_embeddings(self, tenant_id, environment_id=None):
        pass
