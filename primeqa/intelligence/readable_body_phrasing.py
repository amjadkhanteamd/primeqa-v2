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
from primeqa.intelligence.llm.prompts.readable_run_phrasing import (
    VERSION as RUN_PROMPT_VERSION,
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
                        skeleton) -> List[str]:
    """Return the list of OFFENDING (ungrounded) named tokens in the LLM prose;
    empty means fully grounded. A token passes if it is in the skeleton's
    ``grounded_tokens`` allow-set, or (for a multi-word label) every one of its
    words is grounded — so a faithful rephrase of a grounded label passes while
    a fabricated value/label/number fails. Duck-typed on ``grounded_tokens``:
    the test-case skeleton and the run skeleton both satisfy it."""
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


def _phrase_validated(*, task: str, context: Dict[str, Any], skeleton,
                      tenant_id: int, api_key: str) -> Optional[Dict[str, Any]]:
    """Call the gateway with ``task``+``context``, validate the output shape +
    grounding against ``skeleton`` (duck-typed: needs ``grounded_tokens`` +
    ``skeleton_content_hash``). Returns ``{plain_terms, step_narration, model,
    prompt_version, generated_at}`` or None on: gateway error, malformed shape,
    or a grounding violation (which is logged loud). Never raises."""
    try:
        resp = llm_call(
            task=task,
            tenant_id=tenant_id,
            api_key=api_key,
            context=context,
        )
    except LLMError as e:
        log.warning("%s: LLMError for hash=%s: %s",
                    task, skeleton.skeleton_content_hash, e)
        return None
    except Exception as e:  # pragma: no cover — defensive
        log.warning("%s: unexpected error hash=%s: %s",
                    task, skeleton.skeleton_content_hash, e)
        return None

    parsed = resp.parsed_content
    if not isinstance(parsed, dict):
        log.warning("%s: parsed_content not a dict (%s) hash=%s",
                    task, type(parsed).__name__, skeleton.skeleton_content_hash)
        return None

    plain_terms = parsed.get("plain_terms")
    steps = parsed.get("step_narration")
    if not plain_terms or not str(plain_terms).strip():
        log.warning("%s: empty plain_terms hash=%s",
                    task, skeleton.skeleton_content_hash)
        return None
    if not isinstance(steps, list) or not all(isinstance(s, str) for s in steps):
        log.warning("%s: step_narration not a list of str hash=%s",
                    task, skeleton.skeleton_content_hash)
        return None

    plain_terms = str(plain_terms).strip()
    steps = [s.strip() for s in steps][:_MAX_STEPS]

    # FAIL LOUD on any ungrounded token — the reader must never see prose
    # that names a value/field/entity the skeleton did not ground.
    offending = _validate_grounding(plain_terms, steps, skeleton)
    if offending:
        log.warning("%s REJECTED (ungrounded) hash=%s offending=%r — falling "
                    "back to the deterministic baseline",
                    task, skeleton.skeleton_content_hash, offending)
        return None

    return {
        "plain_terms": plain_terms[:_MAX_PLAIN_TERMS],
        "step_narration": [s[:_MAX_STEP] for s in steps],
        "model": resp.model,
        "prompt_version": resp.prompt_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


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
        generated_at}`` or None (gateway error / malformed shape / grounding
        violation)."""
        return _phrase_validated(
            task="readable_body_phrasing_generation",
            context=_context(skeleton), skeleton=skeleton,
            tenant_id=self.tenant_id, api_key=self.api_key)


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


def _get_or_phrase_cached(key: tuple, phrase_fn) -> Optional[Dict[str, Any]]:
    """The shared cache-or-phrase flow: LRU hit, else call ``phrase_fn`` once;
    a failed/ungrounded phrasing returns None and caches nothing (a transient
    issue retries on the next view)."""
    hit = _CACHE.get(key)
    if hit is not None:
        _CACHE.move_to_end(key)                       # LRU touch
        return hit
    phrasing = phrase_fn()
    if phrasing is None:
        return None                                   # best-effort: cache nothing
    _CACHE[key] = phrasing
    _CACHE.move_to_end(key)
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)                    # evict least-recently-used
    return phrasing


def get_or_phrase(skeleton: ReadableBodySkeleton, *, tenant_id: int,
                  api_key: str) -> Optional[Dict[str, Any]]:
    """Return the cached phrasing for this skeleton, or phrase it once and cache
    it. Best-effort: a failed/ungrounded phrasing returns None and caches
    nothing (so a transient issue retries next time). The CALLER gates on
    :func:`readable_body_phrasing_enabled` — this is the flag-agnostic
    cache-or-phrase primitive."""
    return _get_or_phrase_cached(
        _cache_key(skeleton),
        lambda: ReadableBodyPhrasingEnricher(
            tenant_id=tenant_id, api_key=api_key).phrase(skeleton))


def _run_context(skeleton) -> Dict[str, Any]:
    """The readable-run skeleton FACTS handed to the model — nothing else
    reaches it. Padding pairs are summarized as a COUNT (they are grounded but
    non-semantic; the model must not narrate them as test inputs)."""
    return {
        "outcome": skeleton.outcome,
        "headline": skeleton.headline,
        "narrative": skeleton.narrative,
        "test_data": [[a, b] for (a, b) in skeleton.test_data],
        "supporting_field_count": len(skeleton.supporting_data),
        "steps": [s.narration for s in skeleton.steps],
        "expected": skeleton.expected,
        "result_sentence": skeleton.result_sentence,
    }


def get_or_phrase_run(skeleton, *, tenant_id: int,
                      api_key: str) -> Optional[Dict[str, Any]]:
    """The run-result counterpart of :func:`get_or_phrase` — same cache, same
    grounding validator, the run-phrasing prompt. ``skeleton`` is a
    :class:`primeqa.intelligence.readable_run.ReadableRunSkeleton` (duck-typed:
    grounded_tokens + skeleton_content_hash + the run sections). Runs are
    immutable, so a cached phrasing is valid forever (within a process)."""
    return _get_or_phrase_cached(
        (skeleton.skeleton_content_hash, RUN_PROMPT_VERSION),
        lambda: _phrase_validated(
            task="readable_run_phrasing_generation",
            context=_run_context(skeleton), skeleton=skeleton,
            tenant_id=tenant_id, api_key=api_key))


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
    "get_or_phrase_run",
    "readable_body_phrasing_enabled",
    "cache_clear",
]
