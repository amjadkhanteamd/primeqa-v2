"""Service layer for the intelligence domain.

Business logic: explanation assembly (pattern-first / LLM-fallback),
entity dependency extraction, pattern detection with decay, causal links, behaviour facts.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)


def compute_pattern_signature(failure_type, root_entity, target_object, error_message):
    normalized = (error_message or "").lower().strip()
    for prefix in ["field_custom_validation_exception:", "entity_is_deleted:",
                    "required_field_missing:", "delete_failed:"]:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):].strip()
    raw = f"{failure_type}|{root_entity}|{target_object}|{normalized}"
    return hashlib.sha256(raw.encode()).hexdigest()


class IntelligenceService:
    def __init__(self, dep_repo, explanation_repo, pattern_repo,
                 fact_repo, causal_repo, llm_client=None):
        self.dep_repo = dep_repo
        self.explanation_repo = explanation_repo
        self.pattern_repo = pattern_repo
        self.fact_repo = fact_repo
        self.causal_repo = causal_repo
        self.llm_client = llm_client

    # --- Entity Dependencies ---

    def extract_dependencies(self, meta_version_id, metadata_repo):
        self.dep_repo.delete_for_version(meta_version_id)
        deps = []

        for vr in metadata_repo.get_validation_rules(meta_version_id):
            obj_name = vr.meta_object.api_name if vr.meta_object else "Unknown"
            deps.append({
                "meta_version_id": meta_version_id,
                "source_entity": f"ValidationRule.{obj_name}.{vr.rule_name}",
                "source_type": "validation_rule",
                "target_entity": obj_name,
                "dependency_type": "validates",
                "discovery_source": "metadata_parse",
                "confidence": 1.0,
            })

        for flow in metadata_repo.get_flows(meta_version_id):
            dep_type = "updates"
            if flow.trigger_event and "create" in (flow.trigger_event or ""):
                dep_type = "creates"
            deps.append({
                "meta_version_id": meta_version_id,
                "source_entity": f"Flow.{flow.api_name}",
                "source_type": "flow",
                "target_entity": flow.trigger_object or "Unknown",
                "dependency_type": dep_type,
                "discovery_source": "metadata_parse",
                "confidence": 1.0,
            })

        for trigger in metadata_repo.get_triggers(meta_version_id):
            obj_name = trigger.meta_object.api_name if trigger.meta_object else "Unknown"
            events = (trigger.events or "").split(",")
            dep_type = "updates"
            if "insert" in events:
                dep_type = "creates"
            elif "delete" in events:
                dep_type = "deletes"
            deps.append({
                "meta_version_id": meta_version_id,
                "source_entity": f"Trigger.{trigger.trigger_name}",
                "source_type": "trigger",
                "target_entity": obj_name,
                "dependency_type": dep_type,
                "discovery_source": "metadata_parse",
                "confidence": 1.0,
            })

        if deps:
            self.dep_repo.store_dependencies(deps)
        return len(deps)

    def learn_dependency_from_execution(self, meta_version_id, source_entity,
                                         source_type, target_entity, dependency_type):
        self.dep_repo.store_dependencies([{
            "meta_version_id": meta_version_id,
            "source_entity": source_entity,
            "source_type": source_type,
            "target_entity": target_entity,
            "dependency_type": dependency_type,
            "discovery_source": "execution_trace",
            "confidence": 0.85,
        }])

    def get_dependencies(self, meta_version_id, object_name=None):
        if object_name:
            deps = self.dep_repo.get_dependencies_for_object(meta_version_id, object_name)
        else:
            deps = self.dep_repo.get_dependencies(meta_version_id)
        return [self._dep_dict(d) for d in deps]

    # --- Explanation Engine ---

    def explain_failure(self, run_step_result_id, run_test_result_id,
                        tenant_id, environment_id, step_data, metadata_context=None):
        failure_type = step_data.get("failure_type", "system_error")
        target_object = step_data.get("target_object", "")
        error_message = step_data.get("error_message", "")
        root_entity = self._infer_root_entity(error_message, target_object)

        signature = compute_pattern_signature(
            failure_type, root_entity, target_object, error_message,
        )

        # Step 1: Pattern match
        pattern = self.pattern_repo.find_matching_pattern(
            tenant_id, environment_id, signature,
        )
        if pattern and pattern.confidence > 0.5:
            self.pattern_repo.upsert_pattern(
                tenant_id, environment_id, signature, failure_type,
                test_case_id=step_data.get("test_case_id"),
            )
            explanation = {
                "root_cause": pattern.description or f"Known pattern: {pattern.failure_type}",
                "root_cause_entity": pattern.root_entity,
                "fix_suggestion": f"This is a known failure pattern (seen {pattern.occurrence_count} times)",
                "confidence": pattern.confidence,
                "source": "pattern_matched",
            }
            req = self.explanation_repo.create_request(
                run_test_result_id, "failure_analysis",
                {"pattern_signature": signature, "source": "pattern_matched"},
                run_step_result_id,
            )
            self.explanation_repo.complete_request(
                req.id, None, explanation, "pattern_cache",
            )
            return explanation

        # Step 2: Deterministic VR match
        if "FIELD_CUSTOM_VALIDATION_EXCEPTION" in error_message.upper():
            vr_explanation = self._try_deterministic_vr(
                error_message, target_object, metadata_context,
            )
            if vr_explanation:
                req = self.explanation_repo.create_request(
                    run_test_result_id, "failure_analysis",
                    {"source": "deterministic", "error_message": error_message},
                    run_step_result_id,
                )
                self.explanation_repo.complete_request(
                    req.id, None, vr_explanation, "deterministic",
                )
                self.pattern_repo.upsert_pattern(
                    tenant_id, environment_id, signature, failure_type,
                    root_entity=vr_explanation.get("root_cause_entity"),
                    description=vr_explanation.get("root_cause"),
                    test_case_id=step_data.get("test_case_id"),
                )
                return vr_explanation

        # Step 3: LLM fallback
        structured_input = self._build_structured_input(
            step_data, metadata_context, signature,
        )
        req = self.explanation_repo.create_request(
            run_test_result_id, "failure_analysis", structured_input,
            run_step_result_id,
        )

        if self.llm_client:
            llm_result = self._call_llm(
                structured_input,
                tenant_id=tenant_id,
                run_test_result_id=run_test_result_id,
            )
            parsed = llm_result.get("parsed_explanation", {})
            self.explanation_repo.complete_request(
                req.id, llm_result.get("raw_response"),
                parsed, llm_result.get("model", "claude-sonnet-4-20250514"),
                llm_result.get("prompt_tokens", 0),
                llm_result.get("completion_tokens", 0),
            )
            self.pattern_repo.upsert_pattern(
                tenant_id, environment_id, signature, failure_type,
                root_entity=parsed.get("root_cause_entity"),
                description=parsed.get("root_cause"),
                test_case_id=step_data.get("test_case_id"),
            )
            parsed["source"] = "llm_generated"
            return parsed

        fallback = {
            "root_cause": f"Unanalyzed failure: {error_message[:200]}",
            "root_cause_entity": root_entity,
            "fix_suggestion": "Review the step details and API response",
            "confidence": 0.1,
            "source": "no_llm_available",
        }
        self.explanation_repo.complete_request(
            req.id, None, fallback, "none",
        )
        return fallback

    def _try_deterministic_vr(self, error_message, target_object, metadata_context):
        if not metadata_context or not metadata_context.get("validation_rules"):
            return None
        for vr in metadata_context["validation_rules"]:
            vr_msg = (vr.get("error_message") or "").lower()
            if vr_msg and vr_msg in error_message.lower():
                return {
                    "root_cause": f"ValidationRule.{target_object}.{vr['rule_name']} blocked the operation: {vr.get('error_message')}",
                    "root_cause_entity": f"ValidationRule.{target_object}.{vr['rule_name']}",
                    "fix_suggestion": f"Ensure fields satisfy: {vr.get('error_condition_formula', 'unknown formula')}",
                    "confidence": 0.95,
                    "source": "deterministic",
                    "reasoning_chain": [
                        f"Error message matches VR '{vr['rule_name']}' on {target_object}",
                        f"VR error message: {vr.get('error_message')}",
                    ],
                }
        return None

    def _build_structured_input(self, step_data, metadata_context, signature):
        return {
            "failure_context": {
                "step_order": step_data.get("step_order"),
                "step_action": step_data.get("step_action"),
                "target_object": step_data.get("target_object"),
                "error_message": step_data.get("error_message"),
                "api_request": step_data.get("api_request"),
                "api_response": step_data.get("api_response"),
                "before_state": step_data.get("before_state"),
                "after_state": step_data.get("after_state"),
            },
            "related_metadata": metadata_context or {},
            "entity_dependencies": step_data.get("entity_dependencies", []),
            "causal_links": step_data.get("causal_links", []),
            "prior_failures_same_run": step_data.get("prior_failures", []),
            "historical_pattern": {"pattern_signature": signature},
            "extensions": {},
        }

    def _call_llm(self, structured_input, tenant_id=None, run_test_result_id=None):
        """Failure analysis LLM call \u2014 routed through the Gateway so
        the usage log captures it and backoff handles transient errors."""
        from primeqa.intelligence.llm import llm_call, LLMError
        # Extract api key + tenant from the llm_client the caller passed.
        api_key = getattr(self.llm_client, "api_key", None)
        effective_tenant = tenant_id or getattr(self.llm_client, "_pqa_tenant_id", None)

        if not api_key or not effective_tenant:
            # No gateway context \u2014 fall back to direct call (legacy).
            return self._call_llm_legacy(structured_input)

        try:
            # Build a concise failure_analysis context
            # (structured_input is already rich; pass key fields verbatim)
            resp = llm_call(
                task="failure_analysis",
                tenant_id=effective_tenant,
                api_key=api_key,
                context={
                    "error_text": json.dumps(structured_input)[:3000],
                    "step_context": structured_input.get("step_summary", ""),
                    "run_test_result_id": run_test_result_id,
                },
            )
            parsed = resp.parsed_content or {}
            return {
                "raw_response": {"content": resp.raw_text},
                "parsed_explanation": parsed,
                "model": resp.model,
                "prompt_tokens": resp.input_tokens,
                "completion_tokens": resp.output_tokens,
            }
        except LLMError as e:
            return {
                "raw_response": {"error": e.message},
                "parsed_explanation": {
                    "root_cause": f"LLM analysis failed: {e.message}",
                    "confidence": 0.0,
                },
                "model": "claude-sonnet-4-20250514",
                "prompt_tokens": 0,
                "completion_tokens": 0,
            }

    def _call_llm_legacy(self, structured_input):
        """Fallback for tests / legacy callers that don't supply tenant_id."""
        try:
            response = self.llm_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1024,
                messages=[{
                    "role": "user",
                    "content": json.dumps({
                        "task": "failure_analysis",
                        "input": structured_input,
                    }),
                }],
            )
            content = response.content[0].text
            parsed = json.loads(content) if content.strip().startswith("{") else {"root_cause": content, "confidence": 0.5}
            return {
                "raw_response": {"content": content},
                "parsed_explanation": parsed,
                "model": response.model,
                "prompt_tokens": getattr(response.usage, "input_tokens", 0),
                "completion_tokens": getattr(response.usage, "output_tokens", 0),
            }
        except Exception as e:
            log.error(f"Legacy LLM call failed: {e}")
            return {
                "raw_response": {"error": str(e)},
                "parsed_explanation": {
                    "root_cause": f"LLM analysis failed: {e}",
                    "confidence": 0.0,
                },
                "model": "claude-sonnet-4-20250514",
                "prompt_tokens": 0,
                "completion_tokens": 0,
            }

    @staticmethod
    def _infer_root_entity(error_message, target_object):
        msg_upper = error_message.upper()
        if "FIELD_CUSTOM_VALIDATION_EXCEPTION" in msg_upper:
            return f"ValidationRule.{target_object}"
        if "REQUIRED_FIELD_MISSING" in msg_upper:
            return f"Field.{target_object}"
        return target_object

    # --- Failure Patterns ---

    def apply_decay(self, decay_days=7, decay_amount=0.1, min_confidence=0.3):
        return self.pattern_repo.decay_stale_patterns(decay_days, decay_amount, min_confidence)

    def list_active_patterns(self, tenant_id, environment_id=None):
        patterns = self.pattern_repo.list_active_patterns(tenant_id, environment_id)
        return [self._pattern_dict(p) for p in patterns]

    def get_pattern(self, pattern_id):
        p = self.pattern_repo.get_pattern(pattern_id)
        return self._pattern_dict(p) if p else None

    def resolve_pattern(self, pattern_id):
        return self.pattern_repo.resolve_pattern(pattern_id)

    # --- Step Causal Links ---

    def detect_causal_links(self, run_test_result_id, step_results):
        links_created = 0
        failed_steps = [s for s in step_results if s.status in ("failed", "error")]

        for failed in failed_steps:
            if not failed.before_state:
                continue
            for prev in step_results:
                if prev.step_order >= failed.step_order:
                    continue
                if prev.status not in ("passed",):
                    continue
                if not prev.field_diff:
                    continue
                if prev.target_object != failed.target_object and \
                   prev.target_record_id != failed.target_record_id:
                    continue

                overlap = set(prev.field_diff.keys()) & set(failed.before_state.keys())
                if overlap:
                    reason = f"Step {prev.step_order} modified {', '.join(overlap)} which affected step {failed.step_order}"
                    link_type = "state_mutation"
                    if "StageName" in overlap or "Status" in overlap:
                        link_type = "validation_block"
                    self.causal_repo.create_link(
                        run_test_result_id=run_test_result_id,
                        from_step_result_id=prev.id,
                        to_step_result_id=failed.id,
                        link_type=link_type,
                        discovery_source="execution_trace",
                        reason=reason,
                        confidence=1.0,
                    )
                    links_created += 1

        return links_created

    def get_causal_links(self, run_test_result_id):
        links = self.causal_repo.get_links(run_test_result_id)
        return [self._link_dict(l) for l in links]

    # --- Behaviour Facts ---

    def seed_facts(self, tenant_id, environment_id):
        seeds = [
            ("Case.Status", "constraint", "Status field is required and has restricted picklist values"),
            ("Case.Origin", "default", "Origin defaults to 'Phone' if not specified"),
            ("Opportunity.Stage", "sequence", "Stage must follow: Prospecting → Qualification → Proposal → Negotiation → Closed Won/Lost"),
            ("Opportunity.Amount", "constraint", "Amount is often required by VRs when Stage = Closed Won"),
            ("Opportunity.CloseDate", "constraint", "CloseDate cannot be in the past for open opportunities"),
            ("Lead.Status", "sequence", "Status follows: Open → Working → Closed - Converted / Not Converted"),
            ("Lead.Convert", "side_effect", "Converting a Lead creates Account, Contact, and optionally Opportunity"),
            ("Account.Name", "constraint", "Account Name is always required"),
            ("Contact.AccountId", "dependency", "Contact typically requires a parent Account"),
        ]
        count = 0
        for entity_ref, fact_type, description in seeds:
            existing = self.fact_repo.get_facts_for_entity(tenant_id, environment_id, entity_ref)
            if not existing:
                self.fact_repo.create_fact(
                    tenant_id, environment_id, entity_ref, fact_type,
                    description, "seeded",
                )
                count += 1
        return count

    def list_facts(self, tenant_id, environment_id):
        facts = self.fact_repo.list_facts(tenant_id, environment_id)
        return [self._fact_dict(f) for f in facts]

    # --- Dict helpers ---

    @staticmethod
    def _dep_dict(d):
        return {
            "id": d.id, "meta_version_id": d.meta_version_id,
            "source_entity": d.source_entity, "source_type": d.source_type,
            "target_entity": d.target_entity, "dependency_type": d.dependency_type,
            "discovery_source": d.discovery_source, "confidence": d.confidence,
        }

    @staticmethod
    def _pattern_dict(p):
        return {
            "id": p.id, "tenant_id": p.tenant_id,
            "environment_id": p.environment_id,
            "pattern_signature": p.pattern_signature,
            "failure_type": p.failure_type, "root_entity": p.root_entity,
            "description": p.description,
            "occurrence_count": p.occurrence_count,
            "confidence": p.confidence, "status": p.status,
            "affected_test_case_ids": p.affected_test_case_ids,
            "first_seen": p.first_seen.isoformat() if p.first_seen else None,
            "last_seen": p.last_seen.isoformat() if p.last_seen else None,
        }

    @staticmethod
    def _link_dict(l):
        return {
            "id": l.id, "run_test_result_id": l.run_test_result_id,
            "from_step_result_id": l.from_step_result_id,
            "to_step_result_id": l.to_step_result_id,
            "link_type": l.link_type, "reason": l.reason,
            "confidence": l.confidence, "discovery_source": l.discovery_source,
        }

    @staticmethod
    def _fact_dict(f):
        return {
            "id": f.id, "entity_ref": f.entity_ref, "fact_type": f.fact_type,
            "fact_description": f.fact_description, "source": f.source,
            "confidence": f.confidence, "is_active": f.is_active,
        }
