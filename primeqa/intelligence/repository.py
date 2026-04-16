"""Repository for the intelligence domain.

DB queries scoped to: entity_dependencies, explanation_requests,
                      failure_patterns, behaviour_facts, step_causal_links
"""

import json
from datetime import datetime, timezone, timedelta

from sqlalchemy import func

from primeqa.intelligence.models import (
    EntityDependency, ExplanationRequest, FailurePattern,
    BehaviourFact, StepCausalLink,
)


class EntityDependencyRepository:
    def __init__(self, db):
        self.db = db

    def store_dependencies(self, dependencies):
        created = []
        for d in dependencies:
            dep = EntityDependency(
                meta_version_id=d["meta_version_id"],
                source_entity=d["source_entity"],
                source_type=d["source_type"],
                target_entity=d["target_entity"],
                dependency_type=d["dependency_type"],
                discovery_source=d.get("discovery_source", "metadata_parse"),
                confidence=d.get("confidence", 1.0),
            )
            self.db.add(dep)
            created.append(dep)
        self.db.commit()
        return created

    def get_dependencies(self, meta_version_id, source_entity=None, target_entity=None):
        q = self.db.query(EntityDependency).filter(
            EntityDependency.meta_version_id == meta_version_id,
        )
        if source_entity:
            q = q.filter(EntityDependency.source_entity == source_entity)
        if target_entity:
            q = q.filter(EntityDependency.target_entity == target_entity)
        return q.all()

    def get_dependencies_for_object(self, meta_version_id, object_name):
        return self.db.query(EntityDependency).filter(
            EntityDependency.meta_version_id == meta_version_id,
            (EntityDependency.source_entity.contains(object_name)) |
            (EntityDependency.target_entity.contains(object_name)),
        ).all()

    def delete_for_version(self, meta_version_id):
        self.db.query(EntityDependency).filter(
            EntityDependency.meta_version_id == meta_version_id,
        ).delete()
        self.db.commit()


class ExplanationRepository:
    def __init__(self, db):
        self.db = db

    def create_request(self, run_test_result_id, explanation_type, structured_input,
                       run_step_result_id=None):
        req = ExplanationRequest(
            run_test_result_id=run_test_result_id,
            run_step_result_id=run_step_result_id,
            explanation_type=explanation_type,
            structured_input=structured_input,
        )
        self.db.add(req)
        self.db.commit()
        self.db.refresh(req)
        return req

    def complete_request(self, request_id, llm_response, parsed_explanation,
                         model_used, prompt_tokens=0, completion_tokens=0):
        req = self.db.query(ExplanationRequest).filter(
            ExplanationRequest.id == request_id,
        ).first()
        if req:
            req.llm_response = llm_response
            req.parsed_explanation = parsed_explanation
            req.model_used = model_used
            req.prompt_tokens = prompt_tokens
            req.completion_tokens = completion_tokens
            req.completed_at = datetime.now(timezone.utc)
            self.db.commit()
            self.db.refresh(req)
        return req

    def get_explanation(self, request_id):
        return self.db.query(ExplanationRequest).filter(
            ExplanationRequest.id == request_id,
        ).first()

    def list_explanations(self, run_test_result_id):
        return self.db.query(ExplanationRequest).filter(
            ExplanationRequest.run_test_result_id == run_test_result_id,
        ).order_by(ExplanationRequest.requested_at.desc()).all()


class FailurePatternRepository:
    def __init__(self, db):
        self.db = db

    def upsert_pattern(self, tenant_id, environment_id, pattern_signature,
                       failure_type, **kwargs):
        existing = self.find_matching_pattern(tenant_id, environment_id, pattern_signature)
        if existing:
            existing.occurrence_count += 1
            existing.last_seen = datetime.now(timezone.utc)
            existing.last_validated_at = datetime.now(timezone.utc)
            existing.confidence = 1.0
            if existing.status == "decayed":
                existing.status = "active"
            tc_ids = existing.affected_test_case_ids or []
            new_tc_id = kwargs.get("test_case_id")
            if new_tc_id and new_tc_id not in tc_ids:
                tc_ids.append(new_tc_id)
                existing.affected_test_case_ids = tc_ids
            if kwargs.get("description"):
                existing.description = kwargs["description"]
            self.db.commit()
            self.db.refresh(existing)
            return existing, False

        pattern = FailurePattern(
            tenant_id=tenant_id,
            environment_id=environment_id,
            pattern_signature=pattern_signature,
            failure_type=failure_type,
            root_entity=kwargs.get("root_entity"),
            description=kwargs.get("description"),
            affected_test_case_ids=[kwargs["test_case_id"]] if kwargs.get("test_case_id") else [],
        )
        self.db.add(pattern)
        self.db.commit()
        self.db.refresh(pattern)
        return pattern, True

    def find_matching_pattern(self, tenant_id, environment_id, pattern_signature):
        return self.db.query(FailurePattern).filter(
            FailurePattern.tenant_id == tenant_id,
            FailurePattern.pattern_signature == pattern_signature,
            FailurePattern.status.in_(["active", "decayed"]),
        ).first()

    def list_active_patterns(self, tenant_id, environment_id=None):
        q = self.db.query(FailurePattern).filter(
            FailurePattern.tenant_id == tenant_id,
            FailurePattern.status == "active",
        )
        if environment_id:
            q = q.filter(FailurePattern.environment_id == environment_id)
        return q.order_by(FailurePattern.last_seen.desc()).all()

    def get_pattern(self, pattern_id):
        return self.db.query(FailurePattern).filter(
            FailurePattern.id == pattern_id,
        ).first()

    def decay_stale_patterns(self, decay_days=7, decay_amount=0.1, min_confidence=0.3):
        cutoff = datetime.now(timezone.utc) - timedelta(days=decay_days)
        stale = self.db.query(FailurePattern).filter(
            FailurePattern.status == "active",
            FailurePattern.last_validated_at < cutoff,
        ).all()
        decayed_count = 0
        for p in stale:
            p.confidence = max(0.0, p.confidence - decay_amount)
            if p.confidence < min_confidence:
                p.status = "decayed"
                decayed_count += 1
        self.db.commit()
        return decayed_count

    def resolve_pattern(self, pattern_id):
        p = self.get_pattern(pattern_id)
        if p:
            p.status = "resolved"
            self.db.commit()
        return p


class BehaviourFactRepository:
    def __init__(self, db):
        self.db = db

    def create_fact(self, tenant_id, environment_id, entity_ref, fact_type,
                    fact_description, source, confidence=1.0):
        fact = BehaviourFact(
            tenant_id=tenant_id,
            environment_id=environment_id,
            entity_ref=entity_ref,
            fact_type=fact_type,
            fact_description=fact_description,
            source=source,
            confidence=confidence,
        )
        self.db.add(fact)
        self.db.commit()
        self.db.refresh(fact)
        return fact

    def get_facts_for_entity(self, tenant_id, environment_id, entity_ref):
        return self.db.query(BehaviourFact).filter(
            BehaviourFact.tenant_id == tenant_id,
            BehaviourFact.environment_id == environment_id,
            BehaviourFact.entity_ref == entity_ref,
            BehaviourFact.is_active == True,
        ).all()

    def list_facts(self, tenant_id, environment_id, fact_type=None):
        q = self.db.query(BehaviourFact).filter(
            BehaviourFact.tenant_id == tenant_id,
            BehaviourFact.environment_id == environment_id,
            BehaviourFact.is_active == True,
        )
        if fact_type:
            q = q.filter(BehaviourFact.fact_type == fact_type)
        return q.all()

    def deactivate_fact(self, fact_id):
        fact = self.db.query(BehaviourFact).filter(BehaviourFact.id == fact_id).first()
        if fact:
            fact.is_active = False
            self.db.commit()


class StepCausalLinkRepository:
    def __init__(self, db):
        self.db = db

    def create_link(self, run_test_result_id, from_step_result_id, to_step_result_id,
                    link_type, discovery_source, reason=None, confidence=1.0):
        link = StepCausalLink(
            run_test_result_id=run_test_result_id,
            from_step_result_id=from_step_result_id,
            to_step_result_id=to_step_result_id,
            link_type=link_type,
            reason=reason,
            confidence=confidence,
            discovery_source=discovery_source,
        )
        self.db.add(link)
        self.db.commit()
        self.db.refresh(link)
        return link

    def get_links(self, run_test_result_id):
        return self.db.query(StepCausalLink).filter(
            StepCausalLink.run_test_result_id == run_test_result_id,
        ).all()

    def get_causal_chain(self, step_result_id):
        return self.db.query(StepCausalLink).filter(
            (StepCausalLink.from_step_result_id == step_result_id) |
            (StepCausalLink.to_step_result_id == step_result_id),
        ).all()
