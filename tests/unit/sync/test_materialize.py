"""Tests for primeqa.sync.materialize — normalize→hash→compare→write
→enqueue.

Strategy: patch the four DB helpers (_read_existing_entity,
_insert_entity, _supersede_existing_row, _touch_existing_row,
_enqueue_ai_primitives) and the upstream normalize / presentation /
to_semantic_text / hash_normalized helpers. Verify call order +
PhaseResult mutation behavior — the SQL itself is exercised by the
live integration test.

Connection-threading: materialize_entity now takes an explicit
`conn` parameter (refactored post-Object-phase). Tests pass a
MagicMock conn alongside the stub context.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from primeqa.sync.context import SyncContext
from primeqa.sync.materialize import materialize_entity
from primeqa.sync.result import PhaseResult


def _stub_ctx() -> SyncContext:
    return SyncContext(
        sf_client=None,
        engine=MagicMock(),
        sync_run_id="11111111-1111-1111-1111-111111111111",
        connected_org_id="22222222-2222-2222-2222-222222222222",
        tenant_schema="tenant_1",
        logical_version_seq=42,
    )


def _patch_path(name: str) -> str:
    return f"primeqa.sync.materialize.{name}"


class TestMaterializeNewEntity:
    """Cases where the entity is new (no existing row)."""

    def test_materialize_entity_inserts_new_entity(self) -> None:
        """When _read_existing_entity returns None, _insert_entity is
        called."""
        result = PhaseResult(entity_type="Object")
        conn = MagicMock()
        with patch(_patch_path("normalize"), return_value={"normalized": True}), \
             patch(_patch_path("hash_normalized"), return_value="abc123"), \
             patch(_patch_path("to_presentation"), return_value={"pres": True}), \
             patch(_patch_path("to_semantic_text"), return_value="text"), \
             patch(_patch_path("_read_existing_entity"), return_value=None) as mock_read, \
             patch(_patch_path("_insert_entity"), return_value="new-entity-id") as mock_insert, \
             patch(_patch_path("_enqueue_ai_primitives")) as mock_enqueue, \
             patch(_patch_path("_supersede_existing_row")) as mock_supersede, \
             patch(_patch_path("_touch_existing_row")) as mock_touch:
            materialize_entity(
                ctx=_stub_ctx(),
                conn=conn,
                entity_type="Object",
                external_id="Account",
                raw_payload={"name": "Account"},
                result=result,
            )

        mock_read.assert_called_once()
        mock_insert.assert_called_once()
        mock_enqueue.assert_called_once()
        mock_supersede.assert_not_called()
        mock_touch.assert_not_called()

    def test_materialize_entity_increments_inserted_counter_on_new(
        self,
    ) -> None:
        """entities_inserted incremented by exactly 1 per new entity."""
        result = PhaseResult(entity_type="Object")
        conn = MagicMock()
        with patch(_patch_path("normalize"), return_value={"n": True}), \
             patch(_patch_path("hash_normalized"), return_value="h"), \
             patch(_patch_path("to_presentation"), return_value={"p": True}), \
             patch(_patch_path("to_semantic_text"), return_value="t"), \
             patch(_patch_path("_read_existing_entity"), return_value=None), \
             patch(_patch_path("_insert_entity"), return_value="eid"), \
             patch(_patch_path("_enqueue_ai_primitives")):
            materialize_entity(_stub_ctx(), conn, "Object", "X",
                               {"name": "X"}, result)
        assert result.entities_inserted == 1
        assert result.entities_superseded == 0
        assert result.entities_unchanged == 0

    def test_materialize_entity_enqueues_both_primitives_on_new(
        self,
    ) -> None:
        """New entity → _enqueue_ai_primitives called once with the
        new entity_id; the helper itself increments
        embeddings_queued + summaries_queued (verified separately).
        Here just verify the call happened with the right args."""
        ctx = _stub_ctx()
        result = PhaseResult(entity_type="Object")
        conn = MagicMock()
        with patch(_patch_path("normalize"), return_value={}), \
             patch(_patch_path("hash_normalized"), return_value="h"), \
             patch(_patch_path("to_presentation"), return_value={}), \
             patch(_patch_path("to_semantic_text"), return_value=""), \
             patch(_patch_path("_read_existing_entity"), return_value=None), \
             patch(_patch_path("_insert_entity"), return_value="new-id"), \
             patch(_patch_path("_enqueue_ai_primitives")) as mock_enqueue:
            materialize_entity(ctx, conn, "Object", "X", {"name": "X"}, result)
        # _enqueue_ai_primitives signature: (conn, entity_type, entity_id, result)
        mock_enqueue.assert_called_once_with(conn, "Object", "new-id", result)


class TestMaterializeChangedEntity:
    """Cases where an existing row exists with a different hash."""

    def test_materialize_entity_supersedes_on_hash_change(self) -> None:
        """When hash differs from stored hash, _supersede_existing_row
        is called for the old row AND _insert_entity is called for
        the new row."""
        result = PhaseResult(entity_type="Object")
        conn = MagicMock()
        existing = {"id": "old-id", "last_seed_hash": "OLDHASH"}
        with patch(_patch_path("normalize"), return_value={}), \
             patch(_patch_path("hash_normalized"), return_value="NEWHASH"), \
             patch(_patch_path("to_presentation"), return_value={}), \
             patch(_patch_path("to_semantic_text"), return_value=""), \
             patch(_patch_path("_read_existing_entity"), return_value=existing), \
             patch(_patch_path("_supersede_existing_row")) as mock_supersede, \
             patch(_patch_path("_insert_entity"), return_value="new-id") as mock_insert, \
             patch(_patch_path("_enqueue_ai_primitives")) as mock_enqueue, \
             patch(_patch_path("_touch_existing_row")) as mock_touch:
            materialize_entity(_stub_ctx(), conn, "Object", "X",
                               {"name": "X"}, result)
        # Old row superseded
        mock_supersede.assert_called_once()
        # New row inserted
        mock_insert.assert_called_once()
        # Enqueue against the NEW entity_id
        mock_enqueue.assert_called_once()
        # Touch NOT called (changed, not unchanged)
        mock_touch.assert_not_called()

    def test_materialize_entity_increments_superseded_on_hash_change(
        self,
    ) -> None:
        """entities_superseded incremented by exactly 1 per changed
        entity; entities_inserted NOT incremented (semantically
        these are supersessions, not new entities)."""
        result = PhaseResult(entity_type="Object")
        conn = MagicMock()
        with patch(_patch_path("normalize"), return_value={}), \
             patch(_patch_path("hash_normalized"), return_value="NEW"), \
             patch(_patch_path("to_presentation"), return_value={}), \
             patch(_patch_path("to_semantic_text"), return_value=""), \
             patch(_patch_path("_read_existing_entity"),
                   return_value={"id": "old-id", "last_seed_hash": "OLD"}), \
             patch(_patch_path("_supersede_existing_row")), \
             patch(_patch_path("_insert_entity"), return_value="new-id"), \
             patch(_patch_path("_enqueue_ai_primitives")):
            materialize_entity(_stub_ctx(), conn, "Object", "X",
                               {"name": "X"}, result)
        assert result.entities_inserted == 0
        assert result.entities_superseded == 1
        assert result.entities_unchanged == 0

    def test_materialize_entity_re_enqueues_on_change_resets_failed_queue(
        self,
    ) -> None:
        """On hash change, _enqueue_ai_primitives is called against
        the NEW entity_id. The UPSERT semantics inside (ON CONFLICT
        DO UPDATE → reset to pending) handle prior queue rows in
        any state. Verify call args here; UPSERT SQL is exercised
        at integration time."""
        ctx = _stub_ctx()
        conn = MagicMock()
        result = PhaseResult(entity_type="Object")
        with patch(_patch_path("normalize"), return_value={}), \
             patch(_patch_path("hash_normalized"), return_value="NEW"), \
             patch(_patch_path("to_presentation"), return_value={}), \
             patch(_patch_path("to_semantic_text"), return_value=""), \
             patch(_patch_path("_read_existing_entity"),
                   return_value={"id": "old-id", "last_seed_hash": "OLD"}), \
             patch(_patch_path("_supersede_existing_row")), \
             patch(_patch_path("_insert_entity"), return_value="new-id"), \
             patch(_patch_path("_enqueue_ai_primitives")) as mock_enqueue:
            materialize_entity(ctx, conn, "Object", "X",
                               {"name": "X"}, result)
        # Enqueue against NEW entity_id, not the old one
        mock_enqueue.assert_called_once_with(conn, "Object", "new-id", result)


class TestMaterializeUnchangedEntity:
    """Cases where the existing row's hash matches — the entity is
    unchanged structurally; only last_synced_at gets bumped."""

    def test_materialize_entity_touches_existing_on_hash_match(
        self,
    ) -> None:
        """Hash match → _touch_existing_row called, _insert_entity
        NOT called, _enqueue_ai_primitives NOT called."""
        result = PhaseResult(entity_type="Object")
        conn = MagicMock()
        with patch(_patch_path("normalize"), return_value={}), \
             patch(_patch_path("hash_normalized"), return_value="SAMEHASH"), \
             patch(_patch_path("to_presentation"), return_value={}), \
             patch(_patch_path("to_semantic_text"), return_value=""), \
             patch(_patch_path("_read_existing_entity"),
                   return_value={"id": "old-id", "last_seed_hash": "SAMEHASH"}), \
             patch(_patch_path("_supersede_existing_row")) as mock_supersede, \
             patch(_patch_path("_insert_entity")) as mock_insert, \
             patch(_patch_path("_enqueue_ai_primitives")) as mock_enqueue, \
             patch(_patch_path("_touch_existing_row")) as mock_touch:
            materialize_entity(_stub_ctx(), conn, "Object", "X",
                               {"name": "X"}, result)
        mock_touch.assert_called_once()
        mock_supersede.assert_not_called()
        mock_insert.assert_not_called()
        mock_enqueue.assert_not_called()
        # Counter incremented
        assert result.entities_inserted == 0
        assert result.entities_superseded == 0
        assert result.entities_unchanged == 1


class TestMaterializePipelineOrder:
    """The presentation step must happen between normalize and
    semantic_text — semantic_text receives the presentation shape,
    not the normalized shape. This is the entire point of the
    presentation module per corrections-log §7."""

    def test_materialize_entity_uses_presentation_for_semantic_text(
        self,
    ) -> None:
        """to_semantic_text receives the OUTPUT of to_presentation,
        not the output of normalize."""
        result = PhaseResult(entity_type="Object")
        conn = MagicMock()
        normalized_payload = {"name": "Account", "custom": False}
        presentation_payload = {
            "name": "Account", "is_custom": False, "label": "Account",
            "description": None, "key_field_names": None,
        }
        with patch(_patch_path("normalize"), return_value=normalized_payload), \
             patch(_patch_path("hash_normalized"), return_value="h"), \
             patch(_patch_path("to_presentation"),
                   return_value=presentation_payload) as mock_pres, \
             patch(_patch_path("to_semantic_text"),
                   return_value="text") as mock_text, \
             patch(_patch_path("_read_existing_entity"), return_value=None), \
             patch(_patch_path("_insert_entity"), return_value="eid"), \
             patch(_patch_path("_enqueue_ai_primitives")):
            materialize_entity(_stub_ctx(), conn, "Object", "X",
                               {"name": "X"}, result)
        # to_presentation called with the normalized payload
        mock_pres.assert_called_once_with("Object", normalized_payload)
        # to_semantic_text called with the presentation payload
        # (NOT the normalized one)
        mock_text.assert_called_once_with("Object", presentation_payload)
