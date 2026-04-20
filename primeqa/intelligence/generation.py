"""AI test case generator.

Given a requirement and environment, generates a structured **test plan**
\u2014 an array of test cases covering different scenario angles (positive,
negative_validation, boundary, edge_case, regression). Single-test
`generate()` kept for backwards compatibility (it calls `generate_plan`
and returns the first item).
"""

import json
import logging

log = logging.getLogger(__name__)


STEP_GRAMMAR_SPEC = """
Each step must be one of these structured actions:

1. create \u2014 create a new Salesforce record
   Fields: target_object (string), field_values (dict), state_ref (string, starts with $, optional)
   NOTE: if a later step references the created record via $foo, you MUST set state_ref="$foo" on THIS step.
   Example: {"step_order": 1, "action": "create", "target_object": "Account",
             "field_values": {"Name": "Test Corp", "Industry": "Technology"},
             "state_ref": "$primary_account"}

2. update \u2014 update an existing record
   Fields: target_object (string), record_ref (string \u2014 a $var from earlier step), field_values (dict)
   Example: {"step_order": 2, "action": "update", "target_object": "Account",
             "record_ref": "$primary_account",
             "field_values": {"Industry": "Finance"}}

3. query \u2014 run a SOQL query
   Fields: target_object (string), soql (string, full SOQL using $vars where needed)
   Example: {"step_order": 3, "action": "query", "target_object": "Opportunity",
             "soql": "SELECT Id, StageName FROM Opportunity WHERE AccountId = '$primary_account'"}

4. verify \u2014 assert field values on a record
   Fields: target_object (string), record_ref (string), assertions (dict of field \u2192 expected value)
   Example: {"step_order": 4, "action": "verify", "target_object": "Account",
             "record_ref": "$primary_account",
             "assertions": {"Industry": "Finance"}}

5. delete \u2014 delete a record
   Fields: target_object (string), record_ref (string)
   Example: {"step_order": 5, "action": "delete", "target_object": "Account",
             "record_ref": "$primary_account"}

6. convert \u2014 Lead conversion
   Fields: target_object ("Lead"), record_ref (string), convert_to (list of "Account"/"Contact"/"Opportunity")

7. wait \u2014 pause execution
   Fields: duration_sec (integer), reason (string)
"""


# Coverage types the generator targets. Keep in sync with migration 028.
COVERAGE_TYPES = [
    "positive",
    "negative_validation",
    "boundary",
    "edge_case",
    "regression",
]


PLAN_OUTPUT_SCHEMA = {
    "test_plan": {
        "explanation": "1-3 sentences: why this coverage mix was chosen for this requirement",
        "test_cases": [
            {
                "title": "Short specific title \u2014 what this test proves",
                "coverage_type": "one of: positive | negative_validation | boundary | edge_case | regression",
                "description": "one-line description of what this test validates",
                "preconditions": ["prerequisites"],
                "steps": ["array of step objects following the grammar"],
                "expected_results": ["per-step expected outcomes"],
                "referenced_entities": ["Account.Industry", "ValidationRule.Account.Foo"],
                "confidence_score": "float 0-1",
            }
        ],
    }
}


class TestCaseGenerator:
    def __init__(self, llm_client, metadata_repo, *,
                 tenant_id=None, user_id=None, api_key=None):
        # llm_client is retained for backwards compatibility but callers
        # that route through the new gateway pass api_key directly.
        self.llm = llm_client
        self.metadata_repo = metadata_repo
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.api_key = api_key

    # ---- New multi-TC API (routed through LLMGateway) ----------------------

    def generate_plan(self, requirement, meta_version_id,
                      model="claude-sonnet-4-20250514",
                      min_tests=3, max_tests=6,
                      requirement_id=None):
        """Generate a test plan via the LLM Gateway (migration 031).

        Returns the same dict shape as before for backwards compatibility
        with the service layer:
          {explanation, test_cases, model_used, prompt_tokens,
           completion_tokens, raw_response, cost_usd, cached_tokens}
        """
        from primeqa.intelligence.llm import llm_call, LLMError

        metadata_context = self._build_metadata_context(meta_version_id)

        try:
            resp = llm_call(
                task="test_plan_generation",
                tenant_id=self.tenant_id,
                api_key=self.api_key or getattr(self.llm, "api_key", None),
                user_id=self.user_id,
                context={
                    "requirement": requirement,
                    "metadata_context": metadata_context,
                    "meta_version_id": meta_version_id,
                    "min_tests": min_tests,
                    "max_tests": max_tests,
                },
                requirement_id=requirement_id or getattr(requirement, "id", None),
                # Intentionally NOT passing model_override here. The
                # connection's config.model is a create-time dropdown
                # choice (e.g. "claude-opus-4-20250514") that was
                # accidentally overriding the whole router chain \u2014
                # meaning complexity detection, Sonnet-first routing, and
                # upgrades to the OPUS / SONNET constants were all
                # silently bypassed. Per-tenant pins now go through
                # tenant_agent_settings.force_model (TenantPolicy path),
                # which is the intentional mechanism.
            )
        except LLMError:
            log.error(f"Test plan generation failed (gateway)")
            raise

        parsed = resp.parsed_content or {}
        plan = parsed.get("test_plan") or parsed
        tcs = plan.get("test_cases") or []

        # Back-compat: single-TC response shape
        if not tcs and "steps" in parsed:
            tcs = [{
                "title": requirement.jira_summary or f"Test for {requirement.jira_key or requirement.id}",
                "coverage_type": "positive",
                "description": parsed.get("explanation", ""),
                "steps": parsed.get("steps", []),
                "expected_results": parsed.get("expected_results", []),
                "preconditions": parsed.get("preconditions", []),
                "referenced_entities": parsed.get("referenced_entities", []),
                "confidence_score": float(parsed.get("confidence_score", 0.7)),
            }]

        # Normalise coverage_type against our known set
        for tc in tcs:
            ct = (tc.get("coverage_type") or "").strip().lower().replace("-", "_").replace(" ", "_")
            if ct not in COVERAGE_TYPES:
                ct = "edge_case"
            tc["coverage_type"] = ct
            tc.setdefault("confidence_score", 0.7)
            tc["confidence_score"] = float(tc["confidence_score"])

        return {
            "explanation": plan.get("explanation", ""),
            "test_cases": tcs,
            "model_used": resp.model,
            "prompt_tokens": resp.input_tokens,
            "completion_tokens": resp.output_tokens,
            "raw_response": resp.raw_text,
            "cost_usd": resp.cost_usd,
            "cached_tokens": resp.cached_input_tokens,
            # Forwarded so the service can back-link usage_log rows
            # to the batch it ends up creating (cost dashboard accuracy).
            # `usage_log_id` is the final successful attempt; the full
            # list in `usage_log_ids` includes every attempt (primary +
            # any escalation + any error rows). Escalated chains fire
            # multiple billable calls; attributing only the final one
            # under-reported spend in the per-run panel by 50%+.
            "usage_log_id": getattr(resp, "usage_log_id", None),
            "usage_log_ids": getattr(resp, "usage_log_ids", None) or [],
            "escalated": resp.escalated,
            "complexity": resp.complexity,
        }

    # ---- Backwards-compat single-TC path -----------------------------------
    # Kept so the existing single-click "Generate" UI and bulk-generate
    # endpoints don't need to change all at once.

    def generate(self, requirement, meta_version_id,
                 model="claude-sonnet-4-20250514"):
        """Single-test variant \u2014 returns the first TC from a plan.

        Kept for backwards compatibility. New callers should use
        generate_plan() and create one TC per item.
        """
        plan = self.generate_plan(
            requirement, meta_version_id, model=model,
            min_tests=1, max_tests=1,
        )
        tcs = plan.get("test_cases") or []
        if not tcs:
            raise RuntimeError("Generator produced no test cases")
        tc = tcs[0]
        return {
            "steps": tc.get("steps", []),
            "expected_results": tc.get("expected_results", []),
            "preconditions": tc.get("preconditions", []),
            "referenced_entities": tc.get("referenced_entities", []),
            "confidence_score": tc.get("confidence_score", 0.7),
            "explanation": plan.get("explanation", ""),
            "coverage_type": tc.get("coverage_type", "positive"),
            "title": tc.get("title"),
            "description": tc.get("description", ""),
            "model_used": plan["model_used"],
            "prompt_tokens": plan["prompt_tokens"],
            "completion_tokens": plan["completion_tokens"],
            "raw_response": plan["raw_response"],
        }

    # ---- Prompt construction -----------------------------------------------

    def _build_metadata_context(self, meta_version_id):
        objects = self.metadata_repo.get_objects(meta_version_id)
        lines = []
        for obj in objects[:50]:
            if not obj.is_createable:
                continue
            fields = self.metadata_repo.get_fields(meta_version_id, obj.id)
            required_fields = [f for f in fields if f.is_required and f.is_createable]
            custom_fields = [f for f in fields if f.is_custom]
            line = f"{obj.api_name}"
            if required_fields:
                req_names = ", ".join(f.api_name for f in required_fields[:10])
                line += f" [required: {req_names}]"
            if custom_fields:
                custom_names = ", ".join(f.api_name for f in custom_fields[:10])
                line += f" [custom: {custom_names}]"
            lines.append(line)

        vrs = self.metadata_repo.get_validation_rules(meta_version_id)
        vr_lines = []
        for vr in vrs[:20]:
            obj_name = vr.meta_object.api_name if vr.meta_object else "Unknown"
            vr_lines.append(f"{obj_name}.{vr.rule_name}: {vr.error_message or ''}")

        return {"objects": lines, "validation_rules": vr_lines}

    def _build_plan_prompt(self, requirement, metadata_context, min_tests, max_tests):
        jira_part = ""
        if requirement.jira_key:
            jira_part = f"Jira ticket: {requirement.jira_key}\n"
            if requirement.jira_summary:
                jira_part += f"Summary: {requirement.jira_summary}\n"

        return f"""You are a senior Salesforce QA engineer. Generate a TEST PLAN for this requirement.

A test plan is a set of INDEPENDENT test cases that together cover the requirement from
multiple angles. Each test case must set up its own state, execute its scenario,
verify its outcome, and clean up. Do NOT assume one test's state is available to another.

{jira_part}Description:
{requirement.jira_description or ''}

Acceptance Criteria:
{requirement.acceptance_criteria or ''}

## Available Salesforce Metadata

Objects (createable):
{chr(10).join(metadata_context['objects'][:30])}

Validation Rules:
{chr(10).join(metadata_context['validation_rules'][:15])}

## Step Grammar

{STEP_GRAMMAR_SPEC}

## Coverage Types (pick 1 per test case)

- **positive**: the happy-path scenario works end-to-end. The system behaves as
  specified when given valid input.
- **negative_validation**: a forbidden combination is correctly REJECTED by a
  validation rule, required-field check, or flow error. Use `expected_results`
  to state which error you expect to see.
- **boundary**: at-threshold values \u2014 null, zero, max length, expected-to-error
  exact edges.
- **edge_case**: unusual but legal input that users might actually trigger
  (status transitions that aren't the primary path, cross-object scenarios,
  alternative flows).
- **regression**: existing records that already satisfy the new constraint
  should not be broken; unrelated fields on related objects should not be
  mutated by the new behavior.

## Your job

Produce {min_tests} to {max_tests} test cases. Selection rules:

- At least 1 **positive** test (happy path).
- At least 1 **negative_validation** test when the requirement describes any
  "cannot be X when Y" / "required when Y" / validation logic.
- Add **boundary** tests when the requirement mentions thresholds, required
  fields, or value limits.
- Add **edge_case** tests for requirements that span multiple objects or
  describe flow-like behavior.
- Add a **regression** test when changing behavior could affect pre-existing
  records or adjacent unrelated fields.

Quality bar:
- Each test MUST be fully independent \u2014 no shared state across tests.
- When a later step references a created record via $foo, the creating step
  MUST set state_ref="$foo". Unreferenced $vars will cause the test to fail
  fast with a clear error.
- Use only objects and fields present in the metadata above.
- Respect validation rules. When deliberately triggering a VR, assert on the
  expected error in `expected_results`.
- Include cleanup steps (delete records you create) unless the scenario
  inherently removes them (e.g. a delete test).

## Output

Respond with ONLY a JSON object matching this schema. No markdown, no prose
outside the JSON.

{json.dumps(PLAN_OUTPUT_SCHEMA, indent=2)}
"""

    @staticmethod
    def _parse_response(text):
        """Extract JSON from LLM response, handling markdown fences if present."""
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        return json.loads(text)
