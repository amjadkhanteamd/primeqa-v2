"""Idempotency manager.

Handles key management, state reconciliation, and trigger detection
for safe re-execution of pipeline steps.
"""


class IdempotencyManager:
    def __init__(self, entity_repo):
        self.entity_repo = entity_repo

    def generate_key(self, run_id, step_order, target_object, logical_identifier):
        pass

    def check_existing(self, idempotency_key):
        pass

    def compute_fingerprint(self, entity_type, field_values):
        pass

    def detect_triggered_entities(self, run_id, step_result_id, credentials):
        pass
