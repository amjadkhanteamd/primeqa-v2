"""Interpretation-phrasing enricher (D-117).

Turns S6's deterministic structured `Interpretation` into QA-readable prose via
the LLM Gateway (Haiku). Best-effort: any failure returns None (the caller caches
nothing and falls back to the deterministic attribution). Mirrors
`StoryViewEnricher`.

This lives in **v1** (`intelligence/`) — the substrate's `interpretation/` package
is deliberately LLM-free (deterministic-first). The phrased prose is cached onto
the substrate's `s6_interpretations.phrasing` column via the substrate's pure
`result_store.set_phrasing` helper, so the LLM stays out of `interpretation/`
(v1 → substrate is the allowed direction).

Feature-gated per-tenant via `tenant_agent_settings.llm_enable_interpretation_
phrasing` (migration 050) — the CALLER checks the flag (as the story_view caller
does), then calls `get_or_phrase`; this module is the flag-agnostic
cache-or-phrase primitive.
"""
from __future__ import annotations

import dataclasses
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from primeqa.intelligence.llm import LLMError, llm_call
from primeqa.interpretation.model import Interpretation
from primeqa.interpretation.result_store import S6Interpretation, set_phrasing

log = logging.getLogger(__name__)

_REQUIRED_KEYS = ("headline", "explanation")


class InterpretationPhrasingEnricher:
    """Produces QA-readable prose for an S6 `Interpretation` via Haiku.

    Thin stateless wrapper over `llm_call` (mirrors `StoryViewEnricher`) — keeps
    the try/except + shape-validation out of the caller. Never raises.
    """

    def __init__(self, *, tenant_id: int, api_key: str):
        self.tenant_id = tenant_id
        self.api_key = api_key

    def phrase(self, interpretation: Interpretation) -> Optional[Dict[str, Any]]:
        """Return a phrasing dict ({headline, explanation, model, prompt_version,
        generated_at}) or None on any failure. Never raises — a failure caches
        nothing and the caller falls back to the deterministic attribution.

        Only the deterministic facts reach the model (outcome / verdict /
        attribution / cause), so it can restate them but never source new facts.
        """
        cause = (dataclasses.asdict(interpretation.cause)
                 if interpretation.cause is not None else None)
        try:
            resp = llm_call(
                task="interpretation_phrasing_generation",
                tenant_id=self.tenant_id,
                api_key=self.api_key,
                context={
                    "outcome": interpretation.outcome,
                    "verdict": interpretation.verdict,
                    "attribution": interpretation.attribution,
                    "cause": cause,
                },
            )
        except LLMError as e:
            log.warning("interpretation phrasing: LLMError for run=%s: %s",
                        interpretation.run_id, e)
            return None
        except Exception as e:  # pragma: no cover — defensive
            log.warning("interpretation phrasing: unexpected error for run=%s: %s",
                        interpretation.run_id, e)
            return None

        parsed = resp.parsed_content
        if not isinstance(parsed, dict):
            log.warning("interpretation phrasing: parsed_content not a dict (%s) "
                        "for run=%s", type(parsed).__name__, interpretation.run_id)
            return None
        missing = [k for k in _REQUIRED_KEYS
                   if not parsed.get(k) or not str(parsed[k]).strip()]
        if missing:
            log.warning("interpretation phrasing: missing/empty fields %s for "
                        "run=%s", missing, interpretation.run_id)
            return None

        # Hard caps so a runaway model can't blow out the DB row / UI.
        return {
            "headline": str(parsed["headline"]).strip()[:200],
            "explanation": str(parsed["explanation"]).strip()[:2000],
            "model": resp.model,
            "prompt_version": resp.prompt_version,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }


def get_or_phrase(
    session, interpretation: Interpretation, *, tenant_id: int, api_key: str,
) -> Optional[Dict[str, Any]]:
    """On-demand + cache (D-117). Return the cached phrasing on the run's
    ``s6_interpretations`` row, or phrase it once (via the enricher) and cache it
    through the substrate's ``set_phrasing`` writer.

    The CALLER gates on the per-tenant flag (mirroring the story_view caller) —
    this helper is the flag-agnostic cache-or-phrase primitive. Best-effort: a
    failed phrasing returns None and caches nothing. ``session`` must reach the
    per-tenant ``s6_interpretations`` (the tenant-scoped session).
    """
    row = (session.query(S6Interpretation)
           .filter(S6Interpretation.run_id == interpretation.run_id)
           .one_or_none())
    if row is not None and row.phrasing:
        return row.phrasing                               # cache hit
    phrasing = InterpretationPhrasingEnricher(
        tenant_id=tenant_id, api_key=api_key).phrase(interpretation)
    if phrasing is None:
        return None                                       # best-effort: nothing cached
    set_phrasing(session, interpretation.run_id, phrasing)
    return phrasing


__all__ = ["InterpretationPhrasingEnricher", "get_or_phrase"]
