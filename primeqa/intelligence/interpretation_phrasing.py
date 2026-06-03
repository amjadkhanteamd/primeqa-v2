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
from primeqa.interpretation.result_store import (
    S6Interpretation,
    read_interpretation,
    set_phrasing,
)

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


def interpretation_phrasing_enabled(db, tenant_id: int) -> bool:
    """Return True when the tenant has opted into S6 interpretation phrasing
    (migration 050: ``tenant_agent_settings.llm_enable_interpretation_phrasing``).

    Reads the single flag column directly off the v1 ``db`` (a targeted SELECT,
    **not** the ORM model — the column is intentionally unmapped, so existing
    ``TenantAgentSettings`` queries are unaffected in any environment that has not
    yet run migration 050). Absent row / missing column / any error → False: the
    gate fails closed (mirrors ``_story_enrichment_enabled``) so a settings-lookup
    blip can never fire the LLM. The flag lives on the v1 public schema (keyed by
    ``tenant_id``) — unreachable from the substrate session — so the caller
    resolves it here and hands the boolean to :func:`read_and_phrase`.
    """
    try:
        from sqlalchemy import text
        row = db.execute(
            text("SELECT llm_enable_interpretation_phrasing "
                 "FROM tenant_agent_settings WHERE tenant_id = :tid"),
            {"tid": tenant_id}).first()
        return bool(row and row[0])
    except Exception:
        return False


def read_and_phrase(session, run_id, *, tenant_id: int, api_key: str,
                    phrasing_enabled: bool):
    """Read one S6 interpretation and, when ``phrasing_enabled``, fire the
    on-demand LLM phrasing — the live consumer path that finally exercises
    ``get_or_phrase`` outside tests (D-137).

    Returns the :class:`~primeqa.interpretation.result_store.InterpretationRead`
    (``.phrasing`` attached on a successful phrase / cache hit), or None when no
    row exists. Best-effort: a disabled flag or a failed phrasing returns the
    unphrased read.

    The CALLER resolves ``phrasing_enabled`` (e.g. via
    :func:`interpretation_phrasing_enabled` on the v1 db) because the flag lives
    on the v1 ``tenant_agent_settings`` table, unreachable from the substrate
    ``session`` that reaches ``s6_interpretations``.
    """
    read = read_interpretation(session, run_id)
    if read is None or not phrasing_enabled:
        return read
    # Reconstruct the produce-side model from the DTO (same fields, minus the
    # presentation layer) to feed the cache-or-phrase primitive.
    interp = Interpretation(
        run_id=read.run_id, recipe_id=read.recipe_id,
        claim_test_id=read.claim_test_id, outcome=read.outcome,
        verdict=read.verdict, attribution=read.attribution,
        evidence_refs=read.evidence_refs, cause=read.cause)
    phrasing = get_or_phrase(session, interp, tenant_id=tenant_id, api_key=api_key)
    if phrasing is None:
        return read                                    # best-effort: unphrased
    return dataclasses.replace(read, phrasing=phrasing)


__all__ = [
    "InterpretationPhrasingEnricher",
    "get_or_phrase",
    "interpretation_phrasing_enabled",
    "read_and_phrase",
]
