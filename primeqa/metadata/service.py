"""Service layer for the metadata domain.

Business logic: refresh from Salesforce, diff, impact analysis, lifecycle archival.
"""


class MetadataService:
    def __init__(self, metadata_repo):
        self.metadata_repo = metadata_repo

    def refresh_metadata(self, environment_id):
        pass

    def run_impact_analysis(self, environment_id, new_version_id, old_version_id):
        pass

    def get_current_version_summary(self, environment_id):
        pass

    def get_diff(self, environment_id):
        pass

    def list_pending_impacts(self, environment_id):
        pass
