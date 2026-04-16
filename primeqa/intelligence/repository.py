"""Repository for the intelligence domain.

DB queries scoped to: entity_dependencies, explanation_requests,
                      failure_patterns, behaviour_facts, step_causal_links
"""


class EntityDependencyRepository:
    def __init__(self, db):
        self.db = db

    def store_dependencies(self, meta_version_id, dependencies):
        pass

    def get_dependencies(self, meta_version_id, source_entity=None, target_entity=None):
        pass

    def get_dependency_graph(self, meta_version_id):
        pass


class ExplanationRepository:
    def __init__(self, db):
        self.db = db

    def create_request(self, run_test_result_id, explanation_type, structured_input, run_step_result_id=None):
        pass

    def complete_request(self, request_id, llm_response, parsed_explanation, model_used, prompt_tokens, completion_tokens):
        pass

    def get_explanation(self, request_id):
        pass

    def list_explanations(self, run_test_result_id):
        pass


class FailurePatternRepository:
    def __init__(self, db):
        self.db = db

    def upsert_pattern(self, tenant_id, environment_id, pattern_signature, failure_type, **kwargs):
        pass

    def find_matching_pattern(self, tenant_id, environment_id, pattern_signature):
        pass

    def list_active_patterns(self, tenant_id, environment_id=None):
        pass

    def decay_stale_patterns(self, max_age_days=30):
        pass

    def resolve_pattern(self, pattern_id):
        pass


class BehaviourFactRepository:
    def __init__(self, db):
        self.db = db

    def create_fact(self, tenant_id, environment_id, entity_ref, fact_type, fact_description, source):
        pass

    def get_facts_for_entity(self, tenant_id, environment_id, entity_ref):
        pass

    def list_facts(self, tenant_id, environment_id, fact_type=None):
        pass

    def deactivate_fact(self, fact_id):
        pass


class StepCausalLinkRepository:
    def __init__(self, db):
        self.db = db

    def create_link(self, run_test_result_id, from_step_result_id, to_step_result_id, link_type, discovery_source, **kwargs):
        pass

    def get_links(self, run_test_result_id):
        pass

    def get_causal_chain(self, step_result_id):
        pass
