"""Repository for the execution domain.

DB queries scoped to: pipeline_runs, pipeline_stages, run_test_results,
                      run_step_results, run_artifacts, run_created_entities,
                      run_cleanup_attempts, execution_slots, worker_heartbeats
"""


class PipelineRunRepository:
    def __init__(self, db):
        self.db = db

    def create_run(self, tenant_id, environment_id, triggered_by, run_type, source_type, source_ids, cancellation_token, **kwargs):
        pass

    def get_run(self, run_id, tenant_id=None):
        pass

    def list_runs(self, tenant_id, status=None, limit=50, offset=0):
        pass

    def update_run_status(self, run_id, status, **kwargs):
        pass

    def get_queued_runs(self, limit=10):
        pass

    def cancel_run(self, run_id):
        pass


class PipelineStageRepository:
    def __init__(self, db):
        self.db = db

    def create_stages(self, run_id, stage_definitions):
        pass

    def get_stage(self, stage_id):
        pass

    def update_stage(self, stage_id, status, **kwargs):
        pass

    def get_current_stage(self, run_id):
        pass


class RunTestResultRepository:
    def __init__(self, db):
        self.db = db

    def create_result(self, run_id, test_case_id, test_case_version_id, environment_id, status, **kwargs):
        pass

    def get_result(self, result_id):
        pass

    def list_results(self, run_id):
        pass

    def update_result(self, result_id, updates):
        pass


class RunStepResultRepository:
    def __init__(self, db):
        self.db = db

    def create_step_result(self, run_test_result_id, step_order, step_action, status, **kwargs):
        pass

    def update_step_result(self, step_id, updates):
        pass

    def list_step_results(self, run_test_result_id):
        pass


class RunCreatedEntityRepository:
    def __init__(self, db):
        self.db = db

    def create_entity(self, run_id, run_step_result_id, entity_type, sf_record_id, creation_source, **kwargs):
        pass

    def list_entities_for_cleanup(self, run_id):
        pass

    def find_by_idempotency_key(self, key):
        pass

    def mark_cleaned(self, entity_id):
        pass


class ExecutionSlotRepository:
    def __init__(self, db):
        self.db = db

    def acquire_slot(self, environment_id, run_id):
        pass

    def release_slot(self, slot_id):
        pass

    def count_held_slots(self, environment_id):
        pass

    def release_stale_slots(self, max_age_seconds=3600):
        pass


class WorkerHeartbeatRepository:
    def __init__(self, db):
        self.db = db

    def register_worker(self, worker_id):
        pass

    def update_heartbeat(self, worker_id, current_run_id=None, current_stage=None):
        pass

    def mark_dead(self, worker_id):
        pass

    def find_dead_workers(self, timeout_seconds=120):
        pass
