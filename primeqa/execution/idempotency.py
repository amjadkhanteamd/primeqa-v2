"""Idempotency manager.

Handles key management, state reconciliation, creation fingerprinting,
and trigger-created entity detection.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)


class IdempotencyManager:
    def __init__(self, entity_repo, sf_client=None):
        self.entity_repo = entity_repo
        self.sf = sf_client

    def generate_key(self, run_id, step_order, entity_type, logical_identifier):
        return f"{run_id}_{step_order}_{entity_type}_{logical_identifier}"

    def check_existing(self, idempotency_key):
        return self.entity_repo.find_by_idempotency_key(idempotency_key)

    def compute_fingerprint(self, entity_type, field_values):
        data = {"type": entity_type}
        data.update(sorted(
            ((k, str(v)) for k, v in field_values.items()),
            key=lambda x: x[0],
        ))
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def detect_triggered_entities(self, run_id, step_result_id, parent_record_id,
                                  parent_entity_type, step_start_time, directly_created_ids):
        if not self.sf:
            return []

        iso_time = step_start_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        exclude_ids = "', '".join(directly_created_ids) if directly_created_ids else ""
        exclude_clause = f"AND Id NOT IN ('{exclude_ids}')" if exclude_ids else ""

        soql = (
            f"SELECT Id, CreatedDate, Name "
            f"FROM {parent_entity_type} "
            f"WHERE CreatedDate > {iso_time} "
            f"{exclude_clause} "
            f"ORDER BY CreatedDate ASC LIMIT 50"
        )

        try:
            result = self.sf.query(soql)
            if not result.get("success"):
                return []
            records = result["api_response"]["body"].get("records", [])
        except Exception as e:
            log.warning(f"Trigger detection query failed: {e}")
            return []

        detected = []
        for record in records:
            parent = self.entity_repo.create_entity(
                run_id=run_id,
                run_step_result_id=step_result_id,
                entity_type=parent_entity_type,
                sf_record_id=record["Id"],
                creation_source="trigger",
                logical_identifier=f"triggered_{record['Id'][:8]}",
                parent_entity_id=None,
            )
            detected.append(parent)

        return detected
