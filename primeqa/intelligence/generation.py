"""AI test case generator.

Given a requirement and environment, generates a structured test case using the
configured LLM connection (Anthropic) and metadata context from the environment.
"""

import json
import logging

log = logging.getLogger(__name__)


STEP_GRAMMAR_SPEC = """
Each step must be one of these structured actions:

1. create — create a new Salesforce record
   Fields: target_object (string), field_values (dict), logical_id (string, optional)
   Example: {"step_order": 1, "action": "create", "target_object": "Account",
             "field_values": {"Name": "Test Corp", "Industry": "Technology"},
             "logical_id": "primary_account"}

2. update — update an existing record
   Fields: target_object (string), record_ref (string — a logical_id from earlier step), field_values (dict)
   Example: {"step_order": 2, "action": "update", "target_object": "Account",
             "record_ref": "primary_account",
             "field_values": {"Industry": "Finance"}}

3. query — run a SOQL query
   Fields: target_object (string), soql (string, full SOQL), store_as (string, optional for later reference)
   Example: {"step_order": 3, "action": "query", "target_object": "Opportunity",
             "soql": "SELECT Id, StageName FROM Opportunity WHERE AccountId = '$primary_account'"}

4. verify — assert field values on a record
   Fields: target_object (string), record_ref (string), assertions (dict of field → expected value)
   Example: {"step_order": 4, "action": "verify", "target_object": "Account",
             "record_ref": "primary_account",
             "assertions": {"Industry": "Finance"}}

5. delete — delete a record
   Fields: target_object (string), record_ref (string)
   Example: {"step_order": 5, "action": "delete", "target_object": "Account", "record_ref": "primary_account"}

6. convert — Lead conversion
   Fields: target_object ("Lead"), record_ref (string), convert_to (list of "Account"/"Contact"/"Opportunity")
   Example: {"step_order": 2, "action": "convert", "target_object": "Lead",
             "record_ref": "new_lead", "convert_to": ["Account", "Contact", "Opportunity"]}

7. wait — pause execution
   Fields: duration_sec (integer), reason (string)
   Example: {"step_order": 3, "action": "wait", "duration_sec": 5, "reason": "Allow flow to complete"}
"""


OUTPUT_SCHEMA = {
    "steps": "array of step objects following the grammar",
    "expected_results": "array of strings describing expected outcomes per step",
    "preconditions": "array of strings describing prerequisites",
    "referenced_entities": "array of strings like 'Account.Industry', 'ValidationRule.Account.RequireIndustry'",
    "confidence_score": "float 0-1 indicating your confidence the test is correct",
    "explanation": "brief (1-3 sentences) explaining the test strategy"
}


class TestCaseGenerator:
    def __init__(self, llm_client, metadata_repo):
        self.llm = llm_client
        self.metadata_repo = metadata_repo

    def generate(self, requirement, meta_version_id, model="claude-sonnet-4-20250514"):
        """Generate a structured test case from a requirement + metadata."""
        metadata_context = self._build_metadata_context(meta_version_id)
        prompt = self._build_prompt(requirement, metadata_context)

        try:
            response = self.llm.messages.create(
                model=model,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_content = response.content[0].text
            parsed = self._parse_response(raw_content)
            return {
                "steps": parsed.get("steps", []),
                "expected_results": parsed.get("expected_results", []),
                "preconditions": parsed.get("preconditions", []),
                "referenced_entities": parsed.get("referenced_entities", []),
                "confidence_score": float(parsed.get("confidence_score", 0.7)),
                "explanation": parsed.get("explanation", ""),
                "model_used": model,
                "prompt_tokens": getattr(response.usage, "input_tokens", 0),
                "completion_tokens": getattr(response.usage, "output_tokens", 0),
                "raw_response": raw_content,
            }
        except Exception as e:
            log.error(f"Test generation failed: {e}")
            raise

    def _build_metadata_context(self, meta_version_id):
        """Build a compact metadata summary for the prompt."""
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

        return {
            "objects": lines,
            "validation_rules": vr_lines,
        }

    def _build_prompt(self, requirement, metadata_context):
        jira_part = ""
        if requirement.jira_key:
            jira_part = f"Jira ticket: {requirement.jira_key}\n"
            if requirement.jira_summary:
                jira_part += f"Summary: {requirement.jira_summary}\n"

        return f"""You are a Salesforce test automation expert. Generate a structured test case for this requirement.

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

## Instructions

1. Design a test that validates the requirement end-to-end
2. Use only objects and fields that exist in the metadata above
3. Respect validation rules (set required fields, avoid triggering VR errors unless testing them)
4. Include cleanup steps (delete records you create) where appropriate
5. Use logical_id to reference records across steps
6. Compute referenced_entities as dot-notation (Object.Field, ValidationRule.Object.RuleName, etc.)
7. Confidence score: 0.9+ if straightforward, 0.7 if some uncertainty, 0.5 if metadata gaps

## Output

Respond with ONLY a JSON object matching this schema, no markdown:

{json.dumps(OUTPUT_SCHEMA, indent=2)}
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
