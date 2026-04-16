"""Cleanup engine.

Handles reverse-order deletion of created entities, lineage tracking,
cleanup attempts, and production safety enforcement.
"""


class CleanupEngine:
    def __init__(self, entity_repo, cleanup_repo):
        self.entity_repo = entity_repo
        self.cleanup_repo = cleanup_repo

    def run_cleanup(self, run_id, environment_credentials):
        pass

    def cleanup_entity(self, entity, credentials, attempt_number):
        pass

    def build_deletion_order(self, entities):
        pass

    def reconcile(self, run_id, environment_credentials):
        pass
