"""1c enrichment gate — embed/summary input hashing + the shared summary-input
context, so the S1 materialize gate can carry a prior entity version's
embedding/summary FORWARD when the EXACT enrichment input is unchanged, instead
of re-embedding / re-summarizing on every changed entity.

CORRECTNESS INVARIANT: the hash must capture everything that determines the
OUTPUT, so hash-unchanged ⟺ output-would-be-identical. That is BOTH the org-data
input AND the PRODUCER (the embedding model / the summary prompt version) — a
deploy that bumps the producer must force a re-enrich even when the input is
unchanged, else a changed entity would carry a stale old-producer embedding/summary
forward. So:
  - Embed: ``embed_input_hash(semantic_text, model_tag)`` hashes the exact string
    the worker feeds Voyage TOGETHER WITH the embedding model tag.
  - Summary: ``summary_input_hash(..., prompt_version)`` hashes the composite
    context (via ``build_summary_context`` — the SINGLE assembly called by BOTH the
    worker, to FEED the LLM, and the gate, to HASH, so they cannot diverge) TOGETHER
    WITH the summary prompt VERSION. The context is a function only of immutable
    entity-version fields, so it is stable between materialize-time (hash) and
    worker-time (feed).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from sqlalchemy import text


# Entity types that carry summary storage (flow_details / validation_rule_details).
# Mirrors materialize.SUMMARY_ENABLED_ENTITY_TYPES (kept here too so the gate has
# no import cycle back into materialize).
SUMMARY_ENABLED_ENTITY_TYPES = frozenset({"Flow", "ValidationRule"})

# entity_type → the LLM task whose prompt produces its summary (same mapping the
# worker uses to pick the prompt, so the VERSION we fold into the hash is exactly
# the one the worker stamps on a fresh summary).
_SUMMARY_TASK = {
    "Flow": "entity_summary_flow",
    "ValidationRule": "entity_summary_validation_rule",
}


def embed_model_tag() -> str:
    """The current embedding model tag the worker stamps on a fresh embedding —
    folded into the embed hash so a model bump invalidates carry-forward."""
    from primeqa.intelligence.embeddings import EMBEDDING_MODEL_TAG
    return EMBEDDING_MODEL_TAG


def summary_prompt_version(entity_type: str) -> str:
    """The current summary-prompt VERSION for ``entity_type`` — the SAME value the
    worker stamps (``resp.prompt_version``), resolved from the same prompt registry
    by the same task. Folded into the summary hash so a prompt bump invalidates
    carry-forward (re-summarize under the new prompt)."""
    from primeqa.intelligence.llm.prompts.registry import get as get_prompt
    task = _SUMMARY_TASK.get(entity_type)
    if task is None:
        return ""
    return getattr(get_prompt(task), "VERSION", task)


def summary_model_tag() -> str:
    """The current summary MODEL id the worker summarizes with — folded into the
    summary hash so a model swap (the SUMMARY_MODEL env var) invalidates
    carry-forward (re-summarize under the new model), mirroring ``embed_model_tag``.

    SINGLE SOURCE OF TRUTH: resolves through the SAME ``router.summary_model()``
    that the router uses to route the entity-summary tasks, so the model id folded
    into the hash can never drift from the model the worker actually calls."""
    from primeqa.intelligence.llm.router import summary_model
    return summary_model()


def embed_input_hash(semantic_text: Optional[str], model_tag: str) -> str:
    """SHA-256 of the exact embed input (``semantic_text``, the string the worker
    feeds Voyage) NAMESPACED BY the embedding ``model_tag``. An equal hash ⟺ the
    embedding would be identical — same input AND same model. NULL/empty
    semantic_text hashes to a stable, comparable value."""
    return hashlib.sha256(
        f"{model_tag}\x00{semantic_text or ''}".encode("utf-8")).hexdigest()


def build_summary_context(
    execute: Any, entity_type: str, entity_id: str,
) -> Optional[dict]:
    """Assemble the summary-LLM prompt context for one entity, or None if the
    entity / detail row isn't found.

    THE single source of the summary input — called by the worker (to feed the
    summary prompt) AND by the gate (to hash it), so the two can never diverge.
    ``execute`` is any object with ``.execute()`` (a SQLAlchemy Connection or
    Session). Keys mirror what the entity_summary_{flow,validation_rule} prompts
    expect; those prompts are defensive (every key optional)."""
    if entity_type == "Flow":
        row = execute.execute(text("""
            SELECT e.sf_api_name, e.semantic_text, e.attributes,
                   fd.flow_type, fd.trigger_type, fd.is_active,
                   obj.sf_api_name AS trigger_obj
            FROM entities e
            JOIN flow_details fd ON fd.entity_id = e.id
            LEFT JOIN entities obj
                ON obj.id = fd.triggers_on_object_entity_id
            WHERE e.id = :id
        """), {"id": entity_id}).fetchone()
        if row is None:
            return None
        attrs = row[2] or {}
        meta = attrs.get("Metadata") or {}
        return {
            "name": row[0],
            "semantic_text": row[1],
            "process_type": row[3],
            "trigger_type": row[4],
            "is_active": row[5],
            "triggers_on_object": row[6],
            "description": meta.get("description") or attrs.get("description"),
        }
    if entity_type == "ValidationRule":
        row = execute.execute(text("""
            SELECT e.sf_api_name, e.semantic_text, e.attributes,
                   obj.sf_api_name AS obj_name
            FROM entities e
            JOIN validation_rule_details vrd ON vrd.entity_id = e.id
            LEFT JOIN entities obj ON obj.id = vrd.object_entity_id
            WHERE e.id = :id
        """), {"id": entity_id}).fetchone()
        if row is None:
            return None
        attrs = row[2] or {}
        meta = attrs.get("Metadata") or {}
        return {
            "name": row[0],
            "semantic_text": row[1],
            "object": row[3],
            "error_message": meta.get("errorMessage"),
            "formula_text": meta.get("errorConditionFormula"),
            "error_display_field": meta.get("errorDisplayField"),
        }
    return None


def summary_input_signature(context: dict) -> str:
    """Canonical, order-stable serialization of the summary context — the exact
    bytes hashed. The summary prompt is a pure function of this context, so the
    context IS the input."""
    return json.dumps(context, sort_keys=True, ensure_ascii=False, default=str)


def summary_input_hash(
    execute: Any, entity_type: str, entity_id: str,
    prompt_version: str, summary_model: str,
) -> Optional[str]:
    """SHA-256 of the EXACT summary input for one entity — the composite context
    (via the shared ``build_summary_context``) NAMESPACED BY the summary
    ``prompt_version`` AND the summary ``summary_model`` (the model id from
    ``summary_model_tag()``). None when the entity/detail isn't found — the gate
    then won't carry forward (re-summarize, never stale). An equal hash ⟺ the
    summary would be identical: same context AND same prompt AND same model.

    The model fold mirrors ``embed_input_hash``'s ``model_tag`` fold, so swapping
    SUMMARY_MODEL invalidates carry-forward exactly the way an embedding model bump
    does. ``summary_model`` is passed in (resolved once by the caller via
    ``summary_model_tag()``) rather than read here, mirroring ``prompt_version``."""
    context = build_summary_context(execute, entity_type, entity_id)
    if context is None:
        return None
    return hashlib.sha256(
        f"{prompt_version}\x00{summary_model}\x00{summary_input_signature(context)}"
        .encode("utf-8")
    ).hexdigest()
