"""Service layer for the intelligence domain.

Business logic: explanation assembly (pattern-first / LLM-fallback),
pattern detection, confidence decay, behaviour fact management.
"""


class ExplanationService:
    def __init__(self, explanation_repo, pattern_repo, fact_repo, causal_repo):
        self.explanation_repo = explanation_repo
        self.pattern_repo = pattern_repo
        self.fact_repo = fact_repo
        self.causal_repo = causal_repo

    def explain_failure(self, run_test_result_id, run_step_result_id=None):
        pass

    def detect_patterns(self, tenant_id, environment_id, run_test_results):
        pass

    def apply_decay(self, max_age_days=30):
        pass

    def get_explanation(self, request_id):
        pass

    def list_active_patterns(self, tenant_id, environment_id=None):
        pass

    def build_causal_links(self, run_test_result_id):
        pass
