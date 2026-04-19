"""Test plan generation prompt \u2014 produces 3-6 TCs per requirement.

Structured as two cacheable blocks + one dynamic block so prompt caching
does real work across a bulk-generate burst:

  [ system / grammar / coverage-type spec ]  ← CACHED, cross-tenant static
  [ tenant metadata summary               ]  ← CACHED per (tenant, meta_v)
  [ requirement + recent-misses context   ]  ← NOT cached (unique per call)
  [ instructions + output schema          ]  ← NOT cached (small)

Cache semantics (Anthropic):
  - Minimum cacheable size: 1024 tokens for Sonnet/Opus. Our grammar+spec
    block is ~1500 tokens; metadata summary is typically 1000-3000.
  - cache_control marks a block as cacheable; cache entries live 5 min.
  - First call of a burst pays 1.25x for cache_creation; reads from cache
    are 0.1x the normal input rate.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from primeqa.intelligence.llm.prompts.base import PromptSpec


VERSION = "test_plan_generation@v3"   # v3: expect_fail flag for neg/boundary TCs
MAX_TOKENS = 8192
SUPPORTS_CACHE = True
SUPPORTS_ESCALATION = True


# ---- Tool-use schema (Phase 5) --------------------------------------------
# Using Anthropic's tool_use API eliminates the "AI returned broken JSON"
# failure mode entirely \u2014 the model returns a structured dict that
# validates against the schema before we ever see it.

_STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "step_order": {"type": "integer"},
        "action": {"type": "string",
                   "enum": ["create", "update", "query", "verify",
                            "delete", "convert", "wait"]},
        "target_object": {"type": "string"},
        "state_ref": {"type": "string"},
        "field_values": {"type": "object"},
        "record_ref": {"type": "string"},
        "assertions": {"type": "object"},
        "soql": {"type": "string"},
        "convert_to": {"type": "array", "items": {"type": "string"}},
        "duration_sec": {"type": "integer"},
        "reason": {"type": "string"},
        # Set True ONLY on the single step you expect Salesforce to block
        # (validation rule fires, required field missing, flow error).
        # The executor treats a SF-side failure here as the test passing
        # and a SF-side success as the test failing. Use in coverage
        # types negative_validation and boundary; do NOT set on happy-path
        # steps in positive/edge_case/regression tests.
        "expect_fail": {"type": "boolean"},
    },
    "required": ["step_order", "action"],
}

_TEST_CASE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "coverage_type": {
            "type": "string",
            "enum": ["positive", "negative_validation", "boundary",
                     "edge_case", "regression"],
        },
        "description": {"type": "string"},
        "preconditions": {"type": "array", "items": {"type": "string"}},
        "steps": {"type": "array", "items": _STEP_SCHEMA},
        "expected_results": {"type": "array", "items": {"type": "string"}},
        "referenced_entities": {"type": "array", "items": {"type": "string"}},
        "confidence_score": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["title", "coverage_type", "steps", "confidence_score"],
}

_TOOL_SCHEMA = {
    "name": "submit_test_plan",
    "description": (
        "Submit the generated test plan. This is the only way to return "
        "output \u2014 do not reply with text."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "explanation": {
                "type": "string",
                "description": "1-3 sentences: why this coverage mix for this requirement",
            },
            "test_cases": {
                "type": "array",
                "items": _TEST_CASE_SCHEMA,
                "minItems": 1,
                "maxItems": 8,
            },
        },
        "required": ["explanation", "test_cases"],
    },
}


# ---- Static pieces (cacheable across tenants) -----------------------------

STEP_GRAMMAR = """
Each step must be one of these structured actions:

1. create \u2014 create a new Salesforce record
   Fields: target_object (string), field_values (dict), state_ref (string, starts with $, optional)
   NOTE: if a later step references the created record via $foo, you MUST set state_ref="$foo" on THIS step.

2. update \u2014 update an existing record
   Fields: target_object (string), record_ref (string, a $var from earlier step), field_values (dict)

3. query \u2014 run a SOQL query
   Fields: target_object (string), soql (string using $vars where needed)

4. verify \u2014 assert field values on a record
   Fields: target_object (string), record_ref (string), assertions (dict of field \u2192 expected value)

5. delete \u2014 delete a record
   Fields: target_object (string), record_ref (string)

6. convert \u2014 Lead conversion
   Fields: target_object ("Lead"), record_ref (string), convert_to (list of "Account"/"Contact"/"Opportunity")

7. wait \u2014 pause execution
   Fields: duration_sec (integer), reason (string)

Cross-action flag: `expect_fail` (optional boolean)
   Set `expect_fail: true` on the single step you expect Salesforce to
   reject (validation rule, required field, flow error). The executor
   inverts the result: a SF-side failure on that step = test PASS
   (the rule fired correctly); a SF-side success on that step = test
   FAIL (the rule didn't fire as the spec required). Without this flag,
   any SF-side error makes the whole test fail \u2014 so negative_validation
   and boundary TCs that probe a block MUST set it or they'll never pass.
"""

COVERAGE_SPEC = """
Coverage types (pick 1 per test case):

- positive: the happy-path scenario works end-to-end.
  NO step should have expect_fail=true.
- negative_validation: a forbidden combination is correctly REJECTED
  (validation rule, required-field check, flow error).
  Assert the expected error in `expected_results`, AND set
  `expect_fail: true` on the step you expect Salesforce to block.
- boundary: at-threshold values (null, zero, max length).
  If the boundary is meant to be REJECTED by a rule, set
  `expect_fail: true` on the step that crosses it.
- edge_case: unusual but legal combinations (alt flows, cross-object).
  These SHOULD succeed; do NOT set expect_fail.
- regression: existing records that already satisfy the new constraint
  should not be broken; unrelated fields on related objects should not
  be mutated by the new behavior. Do NOT set expect_fail.
"""

OUTPUT_SCHEMA = {
    "test_plan": {
        "explanation": "1-3 sentences: why this coverage mix for this requirement",
        "test_cases": [
            {
                "title": "Short specific title \u2014 what this test proves",
                "coverage_type": "positive | negative_validation | boundary | edge_case | regression",
                "description": "one-line description",
                "preconditions": ["prerequisites"],
                "steps": ["array of step objects following the grammar"],
                "expected_results": ["per-step expected outcomes"],
                "referenced_entities": ["Account.Industry", "ValidationRule.X.Y"],
                "confidence_score": "float 0-1",
            }
        ],
    }
}


# ---- Complexity detection -------------------------------------------------

def detect_complexity(context: Dict[str, Any]) -> str:
    """Semantic bucket, not a numeric score. Signals:
      - explicit object count (referenced_entities in AC)
      - cross-object keywords ("flow", "trigger", "workflow")
      - state transition keywords ("when", "until", "after")
      - validation density (mentions of "required", "cannot be", "must")
      - acceptance-criteria line count
    """
    req = context.get("requirement")
    if not req:
        return "medium"

    text = " ".join(filter(None, [
        getattr(req, "jira_summary", ""),
        getattr(req, "jira_description", ""),
        getattr(req, "acceptance_criteria", ""),
    ])).lower()

    # Signal counts
    multi_object_kw = sum(1 for kw in [
        "flow", "trigger", "workflow", "approval", "process builder",
    ] if kw in text)
    state_transition_kw = sum(1 for kw in [
        "when ", "until ", "after ", "before ", "threshold",
        "escalat", "convert",
    ] if kw in text)
    validation_kw = sum(1 for kw in [
        "required", "cannot be", "must be", "not allowed",
        "rejected", "blank", "mandatory",
    ] if kw in text)

    ac_lines = [l for l in (getattr(req, "acceptance_criteria", "") or "").split("\n")
                if l.strip().startswith(("*", "-", "#", "Given", "When", "Then"))]

    # Bucketing (tunable; err toward simpler on ambiguity)
    if multi_object_kw >= 1 and state_transition_kw >= 2:
        return "high"
    if multi_object_kw >= 2 or len(ac_lines) >= 6:
        return "high"
    if validation_kw >= 2 or len(ac_lines) >= 3:
        return "medium"
    return "low"


# ---- Builder --------------------------------------------------------------

def _format_recent_misses(recent_misses) -> str:
    """Accept either:
      (a) a pre-rendered string from feedback_rules.build_rules_block()
          (the Phase 7+ path — the string is already a prompt-ready
          "Common mistakes to avoid" block)
      (b) a raw list of signal dicts (legacy callers + tests)
      (c) None or empty — returns ""

    We keep the legacy list path so non-gateway callers (offline eval,
    tests, ad-hoc scripts) don't break. The canonical production path
    is (a) — rules block rendered once in feedback_rules.
    """
    if not recent_misses:
        return ""
    if isinstance(recent_misses, str):
        # Already prompt-ready — append verbatim.
        return "\n" + recent_misses

    lines = []
    for m in recent_misses[:5]:
        sig = m.get("signal_type", "unknown")
        detail = m.get("detail") or {}
        if sig == "validation_critical" and detail.get("field"):
            lines.append(f"  - Hallucinated field {detail.get('object','?')}.{detail['field']} "
                         f"(does not exist in this tenant's metadata)")
        elif sig == "execution_failed" and detail.get("error"):
            lines.append(f"  - Runtime failure: {detail['error'][:140]}")
        elif sig == "regenerated_soon":
            lines.append(f"  - User rejected a prior draft: {detail.get('reason','no reason recorded')}")
        elif sig == "user_thumbs_down":
            lines.append(f"  - Explicit thumbs-down: {detail.get('reason','')} "
                         f"{detail.get('reason_text','')}".rstrip())
        elif sig == "ba_rejected":
            lines.append(f"  - BA rejected a prior version: {detail.get('reason_text') or detail.get('reason','no reason given')}")
        elif sig == "user_edited":
            lines.append(f"  - User edited an AI-generated TC (implicit correction)")
        else:
            lines.append(f"  - {sig}: {json.dumps(detail)[:140]}")
    return ("\n## Recent failures in this tenant (learn from these; do NOT repeat)\n\n"
            + "\n".join(lines)
            + "\nWhen in doubt, prefer fields that exist in the metadata above.\n")


def build(
    context: Dict[str, Any],
    *,
    tenant_id: int,
    recent_misses: Optional[List[Dict[str, Any]]] = None,
) -> PromptSpec:
    """Assemble a cached prompt for test-plan generation.

    Required context:
      requirement        \u2014 Requirement ORM object
      metadata_context   \u2014 dict {objects: [...], validation_rules: [...]}
      min_tests / max_tests (optional, default 3/6)
    """
    req = context["requirement"]
    meta = context["metadata_context"] or {}
    min_tests = context.get("min_tests", 3)
    max_tests = context.get("max_tests", 6)

    # ---- SYSTEM (cached cross-tenant) -----------------------------------
    system_blocks = [
        {
            "type": "text",
            "text": (
                "You are a senior Salesforce QA engineer generating a TEST PLAN "
                "for a requirement. A test plan is a set of INDEPENDENT test cases "
                "covering the requirement from multiple angles. Each test case must "
                "set up its own state, execute its scenario, verify the outcome, "
                "and clean up. Do NOT assume one test's state is available to "
                "another.\n\n"
                "## Step Grammar\n"
                + STEP_GRAMMAR
                + "\n## Coverage Types\n"
                + COVERAGE_SPEC
            ),
            "cache_control": {"type": "ephemeral"},
        },
    ]

    # ---- METADATA BLOCK (cached per tenant + meta-version) --------------
    metadata_text = (
        "## Available Salesforce Metadata\n\n"
        "Objects (createable):\n"
        + "\n".join(meta.get("objects", [])[:30])
        + "\n\nValidation Rules:\n"
        + "\n".join(meta.get("validation_rules", [])[:15])
    )
    # Tenant / meta-version namespaced cache key via Anthropic convention:
    # the same block of text will cache only for the same tenant+version
    # since both appear inside `metadata_text`. We could additionally set
    # `cache_control.cache_key` but the built-in key derivation on content
    # hash is sufficient for our tenant isolation goals.

    metadata_block = {
        "type": "text",
        "text": metadata_text,
        "cache_control": {"type": "ephemeral"},
    }

    # ---- DYNAMIC BLOCK (not cached) --------------------------------------
    jira_part = ""
    if getattr(req, "jira_key", None):
        jira_part = f"Jira ticket: {req.jira_key}\n"
        if getattr(req, "jira_summary", None):
            jira_part += f"Summary: {req.jira_summary}\n"

    dynamic_text = (
        f"{jira_part}Description:\n{getattr(req, 'jira_description', '') or ''}\n\n"
        f"Acceptance Criteria:\n{getattr(req, 'acceptance_criteria', '') or ''}\n"
        + _format_recent_misses(recent_misses)
        + f"\n## Task\n\nProduce {min_tests} to {max_tests} test cases. Selection rules:\n"
          "- At least 1 positive test.\n"
          "- At least 1 negative_validation when the requirement describes "
          "\"cannot be X when Y\" / required-when-Y / validation logic.\n"
          "- Add boundary when thresholds / required fields / value limits appear.\n"
          "- Add edge_case for cross-object or flow-like behavior.\n"
          "- Add regression when behavior changes affect pre-existing records.\n\n"
          "Quality bar:\n"
          "- Each test MUST be fully independent.\n"
          "- When a later step references a created record via $foo, the creating "
          "step MUST set state_ref=\"$foo\". Unreferenced $vars fail fast at runtime.\n"
          "- Use only objects and fields present in the metadata above.\n"
          "- Respect validation rules. When deliberately triggering one, assert "
          "on the expected error in `expected_results`.\n"
          "- Include cleanup steps unless the scenario inherently removes records.\n\n"
          "## Output\n\n"
          "Respond with ONLY a JSON object matching this schema. No markdown, "
          "no prose outside the JSON.\n\n"
        + json.dumps(OUTPUT_SCHEMA, indent=2)
    )

    user_blocks = [metadata_block, {"type": "text", "text": dynamic_text}]

    def _parse(resp):
        """Tool-use path: the structured plan arrives pre-parsed as
        resp.tool_input. Text-path fallback handles models that ignore
        the tool_choice constraint (rare, but possible on overloaded
        retries that swapped to a different model family)."""
        if resp.tool_input is not None:
            # Already a dict matching the tool's input_schema. Wrap in
            # the {test_plan: {...}} shape the service expects.
            return {"test_plan": resp.tool_input}

        text = (resp.raw_text or "").strip()
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"_parse_error": True, "_raw": text[:500]}

    return PromptSpec(
        messages=[{"role": "user", "content": user_blocks}],
        system=system_blocks,
        parse=_parse,
        max_tokens=MAX_TOKENS,
        has_cache_blocks=True,
        tools=[_TOOL_SCHEMA],
        force_tool_name="submit_test_plan",
        context_for_log={
            "requirement_id": getattr(req, "id", None),
            "meta_version_id": context.get("meta_version_id"),
            "min_tests": min_tests,
            "max_tests": max_tests,
        },
    )


def should_escalate(parsed: Any, raw_response: Any) -> bool:
    """Escalate to Opus on one hop if:
      - parse failed (model returned malformed JSON)
      - plan produced zero test cases
      - mean confidence across TCs is < 0.7
    """
    if isinstance(parsed, dict) and parsed.get("_parse_error"):
        return True
    plan = (parsed or {}).get("test_plan") or parsed or {}
    tcs = plan.get("test_cases") or []
    if not tcs:
        return True
    confidences = [float(t.get("confidence_score", 1.0)) for t in tcs]
    if confidences and sum(confidences) / len(confidences) < 0.7:
        return True
    return False
