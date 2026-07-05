"""Readable-body phrasing (Stage 2) — constrained LLM prose over the Stage-1
skeleton, with a fail-loud grounding validator and a Stage-1 fallback.

Stage 1 (:mod:`primeqa.intelligence.readable_body`) is deterministic and always
rendered. This module OPTIONALLY rephrases the skeleton's facts into a "plain
terms" paragraph + per-step narration via the LLM Gateway (Haiku), and is
best-effort: any failure — a gateway error, a malformed shape, or (critically) a
grounding violation — returns None, the caller keeps the deterministic Stage-1
baseline, and the reader NEVER sees unverified prose.

Grounding contract (the whole point): the LLM may only RESTATE the skeleton. A
deterministic validator extracts every named token (number, quoted literal,
multi-word label span) from the prose and confirms each is grounded in the
skeleton's ``grounded_tokens`` allow-set — which the builder derives as a
superset of every named token it rendered. Any ungrounded token FAILS LOUD
(logged) and the phrasing is discarded. Number/value fabrication (e.g. a boundary
"650" when the skeleton grounds "649") is caught with reject-on-doubt.

Cache: process-local + content-addressed, keyed on
``(skeleton_content_hash, PROMPT_VERSION)``. No schema change, no table, no
migration — a warm process serves repeat views free; a cold process re-phrases
once; loss on restart is acceptable (best-effort, the Stage-1 baseline is always
available). A DURABLE cross-process cache would need a migration and is a
deliberate future decision, out of scope here.

Feature-gated OFF by default (:func:`readable_body_phrasing_enabled`) so this
whole path is dormant until explicitly enabled.
"""
from __future__ import annotations

import logging
import os
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from primeqa.intelligence.llm import LLMError, llm_call
from primeqa.intelligence.llm.prompts.readable_body_phrasing import (
    VERSION as PROMPT_VERSION,
)
from primeqa.intelligence.readable_body import (
    ReadableBodySkeleton,
    extract_named_tokens,
)

log = logging.getLogger(__name__)

# Hard output caps so a runaway model can't blow out the page.
_MAX_PLAIN_TERMS = 1200
_MAX_STEP = 300
_MAX_STEPS = 40


def _context(skeleton: ReadableBodySkeleton) -> Dict[str, Any]:
    """The skeleton FACTS handed to the model — nothing else reaches it, so it
    cannot source new facts."""
    return {
        "kind": skeleton.kind,
        "headline": skeleton.headline,
        "checks": skeleton.checks,
        "preconditions": list(skeleton.preconditions),
        "test_data": [[a, b] for (a, b) in skeleton.test_data],
        "steps": [s.narration for s in skeleton.steps],
        "expected_result": skeleton.expected_result,
        "probes": [{"label": p.label, "input_value": p.input_value,
                    "expected": p.expected} for p in skeleton.probes],
    }


def _validate_grounding(plain_terms: str, step_narration: List[str],
                        skeleton: ReadableBodySkeleton) -> List[str]:
    """Return the list of OFFENDING (ungrounded) named tokens in the LLM prose;
    empty means fully grounded. A token passes if it is in the skeleton's
    ``grounded_tokens`` allow-set, or (for a multi-word label) every one of its
    words is grounded — so a faithful rephrase of a grounded label passes while
    a fabricated value/label/number fails."""
    grounded = skeleton.grounded_tokens
    prose = plain_terms + "\n" + "\n".join(step_narration or [])
    offending: List[str] = []
    for tok in extract_named_tokens(prose):
        if tok in grounded:
            continue
        if all(w in grounded for w in tok.split()):
            continue
        offending.append(tok)
    return offending


class ReadableBodyPhrasingEnricher:
    """Produces BA/QA prose for a Stage-1 skeleton via Haiku. Thin stateless
    wrapper over ``llm_call`` (mirrors the interpretation-phrasing enricher) —
    keeps the try/except + shape + grounding validation out of the caller. Never
    raises; returns None on any failure so the caller falls back to Stage 1."""

    def __init__(self, *, tenant_id: int, api_key: str):
        self.tenant_id = tenant_id
        self.api_key = api_key

    def phrase(self, skeleton: ReadableBodySkeleton) -> Optional[Dict[str, Any]]:
        """Return ``{plain_terms, step_narration, model, prompt_version,
        generated_at}`` or None. None on: gateway error, malformed shape, or a
        grounding violation (which is logged loud)."""
        try:
            resp = llm_call(
                task="readable_body_phrasing_generation",
                tenant_id=self.tenant_id,
                api_key=self.api_key,
                context=_context(skeleton),
            )
        except LLMError as e:
            log.warning("readable_body phrasing: LLMError for hash=%s: %s",
                        skeleton.skeleton_content_hash, e)
            return None
        except Exception as e:  # pragma: no cover — defensive
            log.warning("readable_body phrasing: unexpected error hash=%s: %s",
                        skeleton.skeleton_content_hash, e)
            return None

        parsed = resp.parsed_content
        if not isinstance(parsed, dict):
            log.warning("readable_body phrasing: parsed_content not a dict (%s) "
                        "hash=%s", type(parsed).__name__,
                        skeleton.skeleton_content_hash)
            return None

        plain_terms = parsed.get("plain_terms")
        steps = parsed.get("step_narration")
        if not plain_terms or not str(plain_terms).strip():
            log.warning("readable_body phrasing: empty plain_terms hash=%s",
                        skeleton.skeleton_content_hash)
            return None
        if not isinstance(steps, list) or not all(isinstance(s, str) for s in steps):
            log.warning("readable_body phrasing: step_narration not a list of "
                        "str hash=%s", skeleton.skeleton_content_hash)
            return None

        plain_terms = str(plain_terms).strip()
        steps = [s.strip() for s in steps][:_MAX_STEPS]

        # FAIL LOUD on any ungrounded token — the reader must never see prose
        # that names a value/field/entity the skeleton did not ground.
        offending = _validate_grounding(plain_terms, steps, skeleton)
        if offending:
            log.warning("readable_body phrasing REJECTED (ungrounded) hash=%s "
                        "offending=%r — falling back to Stage-1 baseline",
                        skeleton.skeleton_content_hash, offending)
            return None

        return {
            "plain_terms": plain_terms[:_MAX_PLAIN_TERMS],
            "step_narration": [s[:_MAX_STEP] for s in steps],
            "model": resp.model,
            "prompt_version": resp.prompt_version,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }


# ---------------------------------------------------------------------------
# Process-local, content-addressed cache (no schema change)
# ---------------------------------------------------------------------------

_CACHE: "OrderedDict[tuple, Dict[str, Any]]" = OrderedDict()
_CACHE_MAX = 512


def _cache_key(skeleton: ReadableBodySkeleton) -> tuple:
    # PROMPT_VERSION stands in for the phrasing_model_version: the model is
    # Haiku-by-router and the prompt VERSION moves on any prompt change; a
    # skeleton fact change already moves skeleton_content_hash. (Bumping the
    # prompt VERSION busts the cache; a rare model swap is picked up on the next
    # process restart, which clears this in-memory cache anyway.)
    return (skeleton.skeleton_content_hash, PROMPT_VERSION)


def cache_clear() -> None:
    """Test/ops hook: drop the in-memory phrasing cache."""
    _CACHE.clear()


def get_or_phrase(skeleton: ReadableBodySkeleton, *, tenant_id: int,
                  api_key: str) -> Optional[Dict[str, Any]]:
    """Return the cached phrasing for this skeleton, or phrase it once and cache
    it. Best-effort: a failed/ungrounded phrasing returns None and caches
    nothing (so a transient issue retries next time). The CALLER gates on
    :func:`readable_body_phrasing_enabled` — this is the flag-agnostic
    cache-or-phrase primitive."""
    key = _cache_key(skeleton)
    hit = _CACHE.get(key)
    if hit is not None:
        _CACHE.move_to_end(key)                       # LRU touch
        return hit
    phrasing = ReadableBodyPhrasingEnricher(
        tenant_id=tenant_id, api_key=api_key).phrase(skeleton)
    if phrasing is None:
        return None                                   # best-effort: cache nothing
    _CACHE[key] = phrasing
    _CACHE.move_to_end(key)
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)                    # evict least-recently-used
    return phrasing


def readable_body_phrasing_enabled(db=None, tenant_id: int = None) -> bool:
    """Return True when readable-body phrasing is enabled. v1 gate is a
    SCHEMA-FREE global env flag (default OFF) — ``PLIMSOL_READABLE_BODY_PHRASING``
    in {1,true,yes,on}. ``db``/``tenant_id`` are accepted for a future per-tenant
    gate (which would reuse an existing settings flag rather than add a column —
    no schema change). Fails closed on anything unexpected."""
    try:
        return os.getenv("PLIMSOL_READABLE_BODY_PHRASING", "").strip().lower() in (
            "1", "true", "yes", "on")
    except Exception:
        return False


__all__ = [
    "ReadableBodyPhrasingEnricher",
    "get_or_phrase",
    "readable_body_phrasing_enabled",
    "cache_clear",
]
