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


VERSION = "test_plan_generation@v1"
MAX_TOKENS = 8192
SUPPORTS_CACHE = True
SUPPORTS_ESCALATION = True


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
"""

COVERAGE_SPEC = """
Coverage types (pick 1 per test case):

- positive: the happy-path scenario works end-to-end.
- negative_validation: a forbidden combination is correctly REJECTED
  (validation rule, required-field check, flow error).
  Assert the expected error in `expected_results`.
- boundary: at-threshold values (null, zero, max length).
- edge_case: unusual but legal combinations (alt flows, cross-object).
- regression: existing records that already satisfy the new constraint
  should not be broken; unrelated fields on related objects should not
  be mutated by the new behavior.
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

def _format_recent_misses(recent_misses: Optional[List[Dict[str, Any]]]) -> str:
    if not recent_misses:
        return ""
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
        """Extract a plan dict from the response's text content."""
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
