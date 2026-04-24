"""Story-view enricher.

Turns the mechanical output of `generate_test_plan` into a short, BA-
readable story (title / description / preconditions / expected outcome)
via the LLM Gateway. Runs on Haiku. Best-effort: any failure returns
None and the caller persists `version.story_view = NULL`. The render
path falls back to the mechanical step view.

Feature-gated per-tenant via
`tenant_agent_settings.llm_enable_story_enrichment` (migration 048).
Caller is responsible for the flag check — this module just does the
work when asked.

Prompt + router wiring:
  - task: "story_view_generation"
  - prompt module: primeqa/intelligence/llm/prompts/story_view.py
  - router chain: {low: [HAIKU], default: [HAIKU]} — no fallback
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from primeqa.intelligence.llm import LLMError, llm_call

log = logging.getLogger(__name__)


_REQUIRED_KEYS = (
    "title", "description", "preconditions_narrative", "expected_outcome",
)


class StoryViewEnricher:
    """Produces a human-readable story view for a test case via Haiku.

    Thin stateless wrapper over `llm_call` — exists mostly to keep the
    try/except + shape-validation logic out of the service layer's
    generate_test_plan loop.
    """

    def __init__(self, *, tenant_id: int, api_key: str,
                 user_id: Optional[int] = None):
        self.tenant_id = tenant_id
        self.api_key = api_key
        self.user_id = user_id

    def enrich(
        self,
        *,
        plan_tc: Dict[str, Any],
        tc_title: str,
        requirement_context: Optional[Dict[str, Any]] = None,
        test_case_id: Optional[int] = None,
        test_case_version_id: Optional[int] = None,
        generation_batch_id: Optional[int] = None,
        requirement_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Return a story-view dict (ready to drop onto
        test_case_versions.story_view) or None on any failure.

        Never raises. All failure modes log a warning and return None
        so the calling generate_test_plan transaction can commit
        cleanly with story_view=NULL.

        The returned dict shape matches what the _tc_body.html macro
        expects:
            {title, description, preconditions_narrative,
             expected_outcome, model, prompt_version, generated_at}
        """
        try:
            resp = llm_call(
                task="story_view_generation",
                tenant_id=self.tenant_id,
                api_key=self.api_key,
                user_id=self.user_id,
                context={
                    "plan_tc": plan_tc,
                    "tc_title": tc_title,
                    "requirement": requirement_context,
                },
                requirement_id=requirement_id,
                test_case_id=test_case_id,
                generation_batch_id=generation_batch_id,
            )
        except LLMError as e:
            log.warning(
                "story_view enrichment: LLMError for tc=%s version=%s: %s",
                test_case_id, test_case_version_id, e,
            )
            return None
        except Exception as e:  # pragma: no cover — defensive
            log.warning(
                "story_view enrichment: unexpected error for tc=%s: %s",
                test_case_id, e,
            )
            return None

        parsed = resp.parsed_content
        if not isinstance(parsed, dict):
            log.warning(
                "story_view enrichment: parsed_content is not a dict "
                "(got %s) for tc=%s",
                type(parsed).__name__, test_case_id,
            )
            return None

        # Shape validation — all four fields required and non-empty
        missing = [k for k in _REQUIRED_KEYS
                   if not parsed.get(k) or not str(parsed[k]).strip()]
        if missing:
            log.warning(
                "story_view enrichment: missing/empty fields %s for tc=%s",
                missing, test_case_id,
            )
            return None

        # Hard caps on text length so a runaway model can't blow out
        # the DB row or the UI.
        return {
            "title": str(parsed["title"]).strip()[:200],
            "description": str(parsed["description"]).strip()[:2000],
            "preconditions_narrative": str(
                parsed["preconditions_narrative"]).strip()[:2000],
            "expected_outcome": str(parsed["expected_outcome"]).strip()[:2000],
            "model": resp.model,
            "prompt_version": resp.prompt_version,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }


__all__ = ["StoryViewEnricher"]
