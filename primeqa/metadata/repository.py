"""Repository for the metadata domain.

DB queries scoped to: meta_versions, meta_objects, meta_fields,
                      meta_validation_rules, meta_flows, meta_triggers, meta_record_types
"""


class MetadataRepository:
    def __init__(self, db):
        self.db = db

    def create_meta_version(self, environment_id, version_label):
        pass

    def complete_meta_version(self, version_id, snapshot_hash, counts):
        pass

    def get_current_version(self, environment_id):
        pass

    def get_previous_version(self, environment_id):
        pass

    def store_objects(self, meta_version_id, objects_list):
        pass

    def store_fields(self, meta_version_id, object_id, fields_list):
        pass

    def store_validation_rules(self, meta_version_id, object_id, rules_list):
        pass

    def store_flows(self, meta_version_id, flows_list):
        pass

    def store_triggers(self, meta_version_id, object_id, triggers_list):
        pass

    def store_record_types(self, meta_version_id, object_id, record_types_list):
        pass

    def diff_fields(self, old_version_id, new_version_id):
        pass

    def diff_validation_rules(self, old_version_id, new_version_id):
        pass

    def diff_flows(self, old_version_id, new_version_id):
        pass

    def archive_old_versions(self, environment_id, keep_count=20):
        pass
