"""Cleanup engine.

Handles reverse-order deletion of created entities, lineage tracking,
cleanup attempts with retry, dependency chain resolution, and production safety.
"""

import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

MAX_CLEANUP_PASSES = 3


def classify_failure(status_code, response_body):
    """Classify a delete failure into a failure_type."""
    if status_code == 403:
        return "permission"
    if status_code != 400:
        return "system_error"
    if not response_body:
        return "system_error"

    errors = response_body if isinstance(response_body, list) else [response_body]
    for err in errors:
        code = err.get("errorCode", "") if isinstance(err, dict) else ""
        msg = err.get("message", "") if isinstance(err, dict) else str(err)
        if code == "ENTITY_IS_DELETED" or "entity is deleted" in msg.lower():
            return "already_deleted"
        if "DELETE_FAILED" in code or "delete" in msg.lower() and "related" in msg.lower():
            return "dependency"
        if code == "FIELD_CUSTOM_VALIDATION_EXCEPTION":
            return "validation_rule"
    return "system_error"


class CleanupEngine:
    def __init__(self, entity_repo, cleanup_repo, sf_client=None):
        self.entity_repo = entity_repo
        self.cleanup_repo = cleanup_repo
        self.sf = sf_client

    def run_cleanup(self, run_id, environment):
        """Run multi-pass cleanup for all created entities in a run."""
        entities = self.entity_repo.list_entities_for_cleanup(run_id)
        if not entities:
            return {"cleaned": 0, "failed": 0, "orphaned": []}

        ordered = self._build_deletion_order(entities)

        remaining = list(ordered)
        total_cleaned = 0
        all_failures = []

        for pass_num in range(1, MAX_CLEANUP_PASSES + 1):
            if not remaining:
                break

            still_failing = []
            for entity in remaining:
                attempt_num = self._get_attempt_count(entity.id) + 1
                success, failure_type, api_resp = self._delete_entity(entity)

                self.cleanup_repo.create_attempt(
                    entity.id, attempt_num,
                    "success" if success else "failed",
                    failure_reason=None if success else str(api_resp),
                    failure_type=None if success else failure_type,
                    api_response=api_resp,
                )

                if success:
                    self.entity_repo.mark_cleaned(entity.id)
                    total_cleaned += 1
                elif failure_type == "dependency" and pass_num < MAX_CLEANUP_PASSES:
                    still_failing.append(entity)
                else:
                    all_failures.append(entity)

            remaining = still_failing

        all_failures.extend(remaining)

        orphaned = []
        for entity in all_failures:
            orphaned.append({
                "entity_type": entity.entity_type,
                "sf_record_id": entity.sf_record_id,
                "failure_reason": self._get_last_failure_reason(entity.id),
            })

        cleanup_mandatory = getattr(environment, "cleanup_mandatory", False)
        if orphaned and cleanup_mandatory:
            self._log_incomplete_cleanup(run_id, environment, orphaned)

        return {
            "cleaned": total_cleaned,
            "failed": len(all_failures),
            "orphaned": orphaned,
            "cleanup_mandatory": cleanup_mandatory,
        }

    def retry_cleanup(self, run_id, environment):
        """Retry cleanup for entities that previously failed."""
        entities = self.entity_repo.list_entities_for_cleanup(run_id)
        if not entities:
            return {"cleaned": 0, "failed": 0, "orphaned": []}
        return self.run_cleanup(run_id, environment)

    def get_cleanup_status(self, run_id):
        """Get cleanup status for all created entities in a run."""
        from primeqa.execution.models import RunCreatedEntity, RunCleanupAttempt
        db = self.entity_repo.db
        entities = db.query(RunCreatedEntity).filter(
            RunCreatedEntity.run_id == run_id,
        ).order_by(RunCreatedEntity.created_at.desc()).all()

        result = []
        for e in entities:
            attempts = db.query(RunCleanupAttempt).filter(
                RunCleanupAttempt.run_created_entity_id == e.id,
            ).order_by(RunCleanupAttempt.attempt_number).all()

            latest_status = "pending"
            if attempts:
                latest_status = attempts[-1].status

            result.append({
                "id": e.id,
                "entity_type": e.entity_type,
                "sf_record_id": e.sf_record_id,
                "creation_source": e.creation_source,
                "logical_identifier": e.logical_identifier,
                "cleanup_required": e.cleanup_required,
                "cleanup_status": latest_status,
                "attempts": [{
                    "attempt_number": a.attempt_number,
                    "status": a.status,
                    "failure_type": a.failure_type,
                    "failure_reason": a.failure_reason,
                } for a in attempts],
            })
        return result

    def get_orphaned_records(self, environment_id):
        """Get all orphaned records across runs for an environment."""
        from primeqa.execution.models import RunCreatedEntity, RunCleanupAttempt, PipelineRun
        db = self.entity_repo.db

        entities = db.query(RunCreatedEntity).join(
            PipelineRun, RunCreatedEntity.run_id == PipelineRun.id,
        ).filter(
            PipelineRun.environment_id == environment_id,
            RunCreatedEntity.cleanup_required == True,
        ).all()

        orphaned = []
        for e in entities:
            has_success = db.query(RunCleanupAttempt).filter(
                RunCleanupAttempt.run_created_entity_id == e.id,
                RunCleanupAttempt.status == "success",
            ).first()
            if not has_success:
                orphaned.append({
                    "run_id": e.run_id,
                    "entity_type": e.entity_type,
                    "sf_record_id": e.sf_record_id,
                    "creation_source": e.creation_source,
                    "logical_identifier": e.logical_identifier,
                })
        return orphaned

    def emergency_cleanup(self, environment, sobject_types=None):
        """Query Salesforce for all PQA_% records and delete them."""
        if not self.sf:
            raise ValueError("No Salesforce client configured")

        types = sobject_types or ["Account", "Contact", "Opportunity", "Lead", "Case", "Task"]
        results = {"deleted": 0, "failed": 0, "details": []}

        for sobject in types:
            try:
                query_result = self.sf.query(
                    f"SELECT Id, Name FROM {sobject} WHERE Name LIKE 'PQA_%'"
                )
                if not query_result.get("success"):
                    continue
                records = query_result["api_response"]["body"].get("records", [])
                for rec in records:
                    del_result = self.sf.delete_record(sobject, rec["Id"])
                    if del_result.get("success"):
                        results["deleted"] += 1
                    else:
                        results["failed"] += 1
                    results["details"].append({
                        "sobject": sobject,
                        "record_id": rec["Id"],
                        "name": rec.get("Name"),
                        "deleted": del_result.get("success", False),
                    })
            except Exception as e:
                log.warning(f"Emergency cleanup failed for {sobject}: {e}")

        return results

    def _build_deletion_order(self, entities):
        """Order entities for deletion: trigger-created children first, then parents, all in reverse creation order."""
        children = [e for e in entities if e.creation_source != "direct"]
        parents = [e for e in entities if e.creation_source == "direct"]

        children.sort(key=lambda e: e.created_at, reverse=True)
        parents.sort(key=lambda e: e.created_at, reverse=True)

        return children + parents

    def _delete_entity(self, entity):
        """Attempt to delete a single entity from Salesforce."""
        if not self.sf:
            return True, None, None

        result = self.sf.delete_record(entity.entity_type, entity.sf_record_id)

        if result.get("success"):
            return True, None, result.get("api_response")

        status_code = result["api_response"]["status_code"]
        body = result["api_response"]["body"]
        failure_type = classify_failure(status_code, body)

        if failure_type == "already_deleted":
            return True, None, result.get("api_response")

        return False, failure_type, result.get("api_response")

    def _get_attempt_count(self, entity_id):
        from primeqa.execution.models import RunCleanupAttempt
        db = self.entity_repo.db
        from sqlalchemy import func
        return db.query(func.count(RunCleanupAttempt.id)).filter(
            RunCleanupAttempt.run_created_entity_id == entity_id,
        ).scalar() or 0

    def _get_last_failure_reason(self, entity_id):
        from primeqa.execution.models import RunCleanupAttempt
        db = self.entity_repo.db
        last = db.query(RunCleanupAttempt).filter(
            RunCleanupAttempt.run_created_entity_id == entity_id,
        ).order_by(RunCleanupAttempt.attempt_number.desc()).first()
        return last.failure_reason if last else None

    def _log_incomplete_cleanup(self, run_id, environment, orphaned):
        from primeqa.core.models import ActivityLog
        db = self.entity_repo.db
        entry = ActivityLog(
            tenant_id=environment.tenant_id,
            action="cleanup.incomplete",
            entity_type="pipeline_run",
            entity_id=run_id,
            details={"orphaned_records": orphaned},
        )
        db.add(entry)
        db.commit()


class CleanupAttemptRepository:
    def __init__(self, db):
        self.db = db

    def create_attempt(self, entity_id, attempt_number, status,
                       failure_reason=None, failure_type=None, api_response=None):
        from primeqa.execution.models import RunCleanupAttempt
        attempt = RunCleanupAttempt(
            run_created_entity_id=entity_id,
            attempt_number=attempt_number,
            status=status,
            failure_reason=failure_reason,
            failure_type=failure_type,
            api_response=api_response,
        )
        self.db.add(attempt)
        self.db.commit()
        self.db.refresh(attempt)
        return attempt

    def list_attempts(self, entity_id):
        from primeqa.execution.models import RunCleanupAttempt
        return self.db.query(RunCleanupAttempt).filter(
            RunCleanupAttempt.run_created_entity_id == entity_id,
        ).order_by(RunCleanupAttempt.attempt_number).all()
