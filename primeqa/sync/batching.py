"""Pure-logic bucketing for batched entity materialization.

Splits incoming entity payloads into three buckets based on
existing-state lookup: new (no prior row), changed (hash differs
from prior), unchanged (hash matches prior). Pure function with
no DB access or side effects.

Per PHASE_2_STEP_4_SYNC_DESIGN.md §7. The bucketing decision
drives three distinct batched DB operations downstream in
materialize.py — separating the logic here makes the bucketing
unit-testable without DB mocking.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class EntityForWrite:
    """Materialization-ready entity record.

    Attributes:
        external_id: Salesforce API name (used as sf_api_name in
            entities table)
        normalized: substrate-1's hashable canonical form
        presentation: presentation-shape dict for semantic_text input
        semantic_text: generated semantic text string
        hash_normalized: SHA-256 hex digest of normalized canonical
        prior_entity_id: for changed entities only — the existing
            entity row's id, used for SCD Type 2 supersession's
            UPDATE valid_to_seq step. Populated by bucket_entities,
            not by the caller.
    """
    external_id: str
    normalized: dict[str, Any]
    presentation: dict[str, Any]
    semantic_text: str
    hash_normalized: str
    prior_entity_id: str | None = None


@dataclass
class EntityBuckets:
    """Three-bucket split for batched materialization.

    new: payloads with no existing currently-active row
    changed: payloads whose existing row's hash differs
        (prior_entity_id populated)
    unchanged_ids: existing-row ids whose hash matched the
        incoming payload — only need to refresh last_synced_at,
        no payload data needed
    """
    new: list[EntityForWrite]
    changed: list[EntityForWrite]
    unchanged_ids: list[str]


def bucket_entities(
    existing_by_external_id: dict[str, dict[str, Any]],
    incoming: list[EntityForWrite],
) -> EntityBuckets:
    """Split incoming entities into new / changed / unchanged.

    For each incoming entity:
    - external_id NOT in existing → new bucket
    - external_id in existing, hash matches → unchanged
      (existing row's id added to unchanged_ids)
    - external_id in existing, hash differs → changed
      (prior_entity_id populated from existing row, then added
      to changed bucket)

    Args:
        existing_by_external_id: output of a single batched
            SELECT keyed by sf_api_name for the (org, entity_type)
            being processed. Each value is a dict with at minimum
            'id' and 'last_seed_hash' keys. Should contain only
            currently-active rows (valid_to_seq IS NULL); caller's
            responsibility to filter.
        incoming: the chunk's incoming entities to materialize.

    Returns:
        EntityBuckets with three lists. Mutates incoming entities'
        prior_entity_id field for the changed bucket members.

    Pure function: no DB access, no side effects beyond mutation of
    the incoming list's elements (setting prior_entity_id on changed
    entities, which is part of the EntityForWrite contract).
    """
    new_bucket: list[EntityForWrite] = []
    changed_bucket: list[EntityForWrite] = []
    unchanged_ids: list[str] = []

    for entity in incoming:
        existing = existing_by_external_id.get(entity.external_id)
        if existing is None:
            new_bucket.append(entity)
        elif existing["last_seed_hash"] == entity.hash_normalized:
            unchanged_ids.append(existing["id"])
        else:
            entity.prior_entity_id = existing["id"]
            changed_bucket.append(entity)

    return EntityBuckets(
        new=new_bucket,
        changed=changed_bucket,
        unchanged_ids=unchanged_ids,
    )
