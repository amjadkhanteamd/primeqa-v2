"""Tests for primeqa.sync.batching — pure-logic bucketing.

No mocks, no DB. The bucketing function is a pure transformation
from (existing_rows_dict, incoming_payloads) to three buckets.
"""
from __future__ import annotations

from primeqa.sync.batching import (
    EntityBuckets,
    EntityForWrite,
    bucket_entities,
)


def _make_entity(external_id: str, hash_value: str = "h1") -> EntityForWrite:
    return EntityForWrite(
        external_id=external_id,
        normalized={"name": external_id},
        presentation={"name": external_id, "label": external_id},
        semantic_text=f"text-{external_id}",
        hash_normalized=hash_value,
    )


class TestBucketEntities:
    def test_bucket_entities_all_new(self) -> None:
        """No existing rows → all entities go to new bucket."""
        incoming = [
            _make_entity("Account"),
            _make_entity("Contact"),
            _make_entity("Lead"),
        ]
        buckets = bucket_entities(existing_by_external_id={}, incoming=incoming)

        assert len(buckets.new) == 3
        assert [e.external_id for e in buckets.new] == ["Account", "Contact", "Lead"]
        assert buckets.changed == []
        assert buckets.unchanged_ids == []

    def test_bucket_entities_all_unchanged(self) -> None:
        """All hashes match → all entities go to unchanged_ids."""
        incoming = [
            _make_entity("Account", "samehash"),
            _make_entity("Contact", "samehash"),
        ]
        existing = {
            "Account": {"id": "id-account", "last_seed_hash": "samehash"},
            "Contact": {"id": "id-contact", "last_seed_hash": "samehash"},
        }
        buckets = bucket_entities(existing, incoming)

        assert buckets.new == []
        assert buckets.changed == []
        assert sorted(buckets.unchanged_ids) == ["id-account", "id-contact"]

    def test_bucket_entities_all_changed(self) -> None:
        """All hashes differ → all entities go to changed bucket
        with prior_entity_id populated."""
        incoming = [
            _make_entity("Account", "newhash1"),
            _make_entity("Contact", "newhash2"),
        ]
        existing = {
            "Account": {"id": "id-account", "last_seed_hash": "oldhash1"},
            "Contact": {"id": "id-contact", "last_seed_hash": "oldhash2"},
        }
        buckets = bucket_entities(existing, incoming)

        assert buckets.new == []
        assert buckets.unchanged_ids == []
        assert len(buckets.changed) == 2
        # prior_entity_id populated on each
        changed_by_id = {e.external_id: e for e in buckets.changed}
        assert changed_by_id["Account"].prior_entity_id == "id-account"
        assert changed_by_id["Contact"].prior_entity_id == "id-contact"

    def test_bucket_entities_mixed_buckets(self) -> None:
        """Three entities in three different states get sorted into
        three different buckets."""
        incoming = [
            _make_entity("Account", "newhash"),   # changed
            _make_entity("Contact", "samehash"),  # unchanged
            _make_entity("NewObj", "anyhash"),    # new
        ]
        existing = {
            "Account": {"id": "id-account", "last_seed_hash": "oldhash"},
            "Contact": {"id": "id-contact", "last_seed_hash": "samehash"},
            # NewObj absent
        }
        buckets = bucket_entities(existing, incoming)

        assert len(buckets.new) == 1
        assert buckets.new[0].external_id == "NewObj"
        assert len(buckets.changed) == 1
        assert buckets.changed[0].external_id == "Account"
        assert buckets.changed[0].prior_entity_id == "id-account"
        assert buckets.unchanged_ids == ["id-contact"]

    def test_bucket_entities_changed_carries_prior_entity_id(self) -> None:
        """The prior_entity_id field is populated for changed
        entities — this is what enables SCD Type 2 close-out in
        the materialize layer."""
        entity = _make_entity("Account", "newhash")
        assert entity.prior_entity_id is None  # initial state

        existing = {
            "Account": {"id": "the-old-id", "last_seed_hash": "oldhash"},
        }
        buckets = bucket_entities(existing, [entity])

        assert len(buckets.changed) == 1
        # The same object was mutated in place
        assert buckets.changed[0] is entity
        assert entity.prior_entity_id == "the-old-id"

    def test_bucket_entities_empty_input(self) -> None:
        """Empty incoming list → all three buckets empty."""
        buckets = bucket_entities(existing_by_external_id={}, incoming=[])

        assert buckets.new == []
        assert buckets.changed == []
        assert buckets.unchanged_ids == []
        assert isinstance(buckets, EntityBuckets)
