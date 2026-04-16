"""Repository for the test management domain.

DB queries scoped to: sections, requirements, test_cases, test_case_versions,
                      test_suites, suite_test_cases, ba_reviews, metadata_impacts
"""


class SectionRepository:
    def __init__(self, db):
        self.db = db

    def create_section(self, tenant_id, name, parent_id=None, created_by=None):
        pass

    def get_section(self, section_id, tenant_id):
        pass

    def list_sections(self, tenant_id, parent_id=None):
        pass

    def update_section(self, section_id, updates):
        pass

    def delete_section(self, section_id):
        pass


class RequirementRepository:
    def __init__(self, db):
        self.db = db

    def create_requirement(self, tenant_id, section_id, source, created_by, **kwargs):
        pass

    def get_requirement(self, requirement_id, tenant_id):
        pass

    def list_requirements(self, tenant_id, section_id=None):
        pass

    def update_requirement(self, requirement_id, updates):
        pass

    def mark_stale(self, requirement_id):
        pass

    def find_by_jira_key(self, tenant_id, jira_key):
        pass


class TestCaseRepository:
    def __init__(self, db):
        self.db = db

    def create_test_case(self, tenant_id, title, owner_id, created_by, **kwargs):
        pass

    def get_test_case(self, test_case_id, tenant_id):
        pass

    def list_test_cases(self, tenant_id, requirement_id=None, section_id=None):
        pass

    def update_test_case(self, test_case_id, updates):
        pass

    def create_version(self, test_case_id, version_number, metadata_version_id, created_by, **kwargs):
        pass

    def get_latest_version(self, test_case_id):
        pass


class TestSuiteRepository:
    def __init__(self, db):
        self.db = db

    def create_suite(self, tenant_id, name, suite_type, created_by, description=None):
        pass

    def get_suite(self, suite_id, tenant_id):
        pass

    def list_suites(self, tenant_id):
        pass

    def add_test_case(self, suite_id, test_case_id, position=0):
        pass

    def remove_test_case(self, suite_id, test_case_id):
        pass


class BAReviewRepository:
    def __init__(self, db):
        self.db = db

    def create_review(self, tenant_id, test_case_version_id, assigned_to):
        pass

    def get_review(self, review_id):
        pass

    def list_reviews(self, tenant_id, status=None, assigned_to=None):
        pass

    def update_review(self, review_id, status, feedback=None, reviewed_by=None):
        pass


class MetadataImpactRepository:
    def __init__(self, db):
        self.db = db

    def create_impact(self, new_meta_version_id, prev_meta_version_id, test_case_id, impact_type, entity_ref, change_details=None):
        pass

    def list_pending_impacts(self, meta_version_id):
        pass

    def resolve_impact(self, impact_id, resolution, resolved_by):
        pass
