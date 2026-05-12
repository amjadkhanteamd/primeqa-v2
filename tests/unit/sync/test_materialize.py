"""Tests for primeqa.sync.materialize — batched normalize→bucket→
write→enqueue cycle.

Strategy: patch the four batched DB helpers (_batch_read_existing,
_batch_insert_new_entities, _batch_close_superseded,
_batch_touch_existing, _batch_upsert_queue) plus the upstream
normalize / presentation / to_semantic_text / hash_normalized
helpers. Verify which batched helpers run, with which inputs,
and the resulting PhaseResult counter values. The SQL itself is
exercised by the live integration test.
"""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch

from primeqa.sync.context import SyncContext
from primeqa.sync.materialize import (
    DEFAULT_CHUNK_SIZE,
    batched_materialize,
)
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


def _patch_pipeline(
    *,
    normalize_return=None,
    hash_return="h",
    presentation_return=None,
    semantic_text_return="text",
    existing_return=None,
    insert_return=None,
    close_return=None,
    touch_return=None,
    upsert_return=None,
):
    """Helper to patch the full pipeline in one ContextManager.

    Returns a list of patch context managers to enter via ExitStack
    or nested with.
    """
    return [
        patch(_patch_path("normalize"),
              return_value=normalize_return or {"n": True}),
        patch(_patch_path("hash_normalized"),
              return_value=hash_return),
        patch(_patch_path("to_presentation"),
              return_value=presentation_return or {"label": "X", "name": "X"}),
        patch(_patch_path("to_semantic_text"),
              return_value=semantic_text_return),
        patch(_patch_path("_batch_read_existing"),
              return_value=existing_return or {}),
        patch(_patch_path("_batch_insert_new_entities"),
              return_value=insert_return or []),
        patch(_patch_path("_batch_close_superseded"),
              return_value=close_return),
        patch(_patch_path("_batch_touch_existing"),
              return_value=touch_return),
        patch(_patch_path("_batch_upsert_queue"),
              return_value=upsert_return),
    ]


class TestBatchedMaterializeEmptyInput:
    def test_batched_materialize_empty_input(self) -> None:
        """Empty raw_payloads → no batched helpers called, no
        counter changes."""
        result = PhaseResult(entity_type="Object")
        conn = MagicMock()
        from contextlib import ExitStack
        with ExitStack() as stack:
            mocks = {
                name: stack.enter_context(p)
                for name, p in zip(
                    ("normalize", "hash", "pres", "text",
                     "read", "insert", "close", "touch", "upsert"),
                    _patch_pipeline(),
                )
            }
            batched_materialize(
                _stub_ctx(), conn, "Object", raw_payloads=[],
                result=result,
            )
        # No batched helpers were called
        assert mocks["read"].call_count == 0
        assert mocks["insert"].call_count == 0
        assert mocks["close"].call_count == 0
        assert mocks["touch"].call_count == 0
        assert mocks["upsert"].call_count == 0
        # Counters unchanged
        assert result.entities_inserted == 0
        assert result.entities_superseded == 0
        assert result.entities_unchanged == 0


class TestBatchedMaterializeAllNew:
    def test_batched_materialize_all_new_entities(self) -> None:
        """All entities are new (empty existing) → _batch_insert
        called, _batch_upsert_queue called, _batch_close +
        _batch_touch NOT called."""
        result = PhaseResult(entity_type="Object")
        conn = MagicMock()
        from contextlib import ExitStack
        raw_payloads = [{"name": f"Entity_{i}"} for i in range(3)]
        new_ids = ["id-1", "id-2", "id-3"]

        with ExitStack() as stack:
            for p in _patch_pipeline(
                existing_return={},  # nothing exists
                insert_return=new_ids,
            ):
                stack.enter_context(p)
            mock_insert = stack.enter_context(
                patch(_patch_path("_batch_insert_new_entities"),
                      return_value=new_ids)
            )
            mock_close = stack.enter_context(
                patch(_patch_path("_batch_close_superseded"))
            )
            mock_touch = stack.enter_context(
                patch(_patch_path("_batch_touch_existing"))
            )
            mock_upsert = stack.enter_context(
                patch(_patch_path("_batch_upsert_queue"))
            )

            batched_materialize(
                _stub_ctx(), conn, "Object",
                raw_payloads=raw_payloads, result=result,
            )

        # INSERT called once with all 3 entities
        assert mock_insert.call_count == 1
        # UPSERT to queue called once with the 3 new IDs
        mock_upsert.assert_called_once()
        upsert_call_args = mock_upsert.call_args.args
        assert sorted(upsert_call_args[2]) == sorted(new_ids)
        # close + touch NOT called
        mock_close.assert_not_called()
        mock_touch.assert_not_called()
        # Counters
        assert result.entities_inserted == 3
        assert result.entities_superseded == 0
        assert result.entities_unchanged == 0


class TestBatchedMaterializeAllUnchanged:
    def test_batched_materialize_all_unchanged(self) -> None:
        """All entities match existing hash → _batch_touch called,
        no _batch_insert, no _batch_upsert."""
        result = PhaseResult(entity_type="Object")
        conn = MagicMock()
        from contextlib import ExitStack

        # 3 incoming, all matching the same hash
        raw_payloads = [{"name": f"Entity_{i}"} for i in range(3)]
        existing = {
            "Entity_0": {"id": "old-0", "last_seed_hash": "h"},
            "Entity_1": {"id": "old-1", "last_seed_hash": "h"},
            "Entity_2": {"id": "old-2", "last_seed_hash": "h"},
        }

        with ExitStack() as stack:
            for p in _patch_pipeline(
                hash_return="h",  # matches existing
                existing_return=existing,
            ):
                stack.enter_context(p)
            mock_insert = stack.enter_context(
                patch(_patch_path("_batch_insert_new_entities"))
            )
            mock_close = stack.enter_context(
                patch(_patch_path("_batch_close_superseded"))
            )
            mock_touch = stack.enter_context(
                patch(_patch_path("_batch_touch_existing"))
            )
            mock_upsert = stack.enter_context(
                patch(_patch_path("_batch_upsert_queue"))
            )

            # Override _extract_external_id since "name" is used in
            # the Object branch — match against raw["name"]
            batched_materialize(
                _stub_ctx(), conn, "Object",
                raw_payloads=raw_payloads, result=result,
            )

        # Touch called once with all 3 existing IDs
        mock_touch.assert_called_once()
        touch_ids = mock_touch.call_args.args[1]
        assert sorted(touch_ids) == sorted(["old-0", "old-1", "old-2"])
        # No insert, no upsert
        mock_insert.assert_not_called()
        mock_close.assert_not_called()
        mock_upsert.assert_not_called()
        # Counters
        assert result.entities_inserted == 0
        assert result.entities_superseded == 0
        assert result.entities_unchanged == 3


class TestBatchedMaterializeMixedBuckets:
    def test_batched_materialize_mixed_buckets(self) -> None:
        """3 entities split 1/1/1 across new/changed/unchanged → all
        three batched writes invoked."""
        result = PhaseResult(entity_type="Object")
        conn = MagicMock()
        from contextlib import ExitStack

        raw_payloads = [
            {"name": "NewObj"},      # new
            {"name": "ChangedObj"},  # changed
            {"name": "SameObj"},     # unchanged
        ]
        # Hash routing: normalize returns the raw payload (verified
        # below), then hash_normalized returns a per-entity hash.
        # We'll patch hash to return name-derived hashes.
        def hash_side_effect(normalized):
            name = normalized.get("name", "")
            return f"hash_{name}"

        existing = {
            "ChangedObj": {"id": "old-changed",
                          "last_seed_hash": "hash_DIFFERENT"},
            "SameObj": {"id": "old-same",
                       "last_seed_hash": "hash_SameObj"},
            # NewObj absent
        }

        with ExitStack() as stack:
            stack.enter_context(patch(
                _patch_path("normalize"),
                side_effect=lambda et, raw: raw,
            ))
            stack.enter_context(patch(
                _patch_path("hash_normalized"),
                side_effect=hash_side_effect,
            ))
            stack.enter_context(patch(
                _patch_path("to_presentation"),
                side_effect=lambda et, n: {"label": n["name"], "name": n["name"]},
            ))
            stack.enter_context(patch(
                _patch_path("to_semantic_text"),
                return_value="text",
            ))
            stack.enter_context(patch(
                _patch_path("_batch_read_existing"),
                return_value=existing,
            ))
            mock_insert = stack.enter_context(patch(
                _patch_path("_batch_insert_new_entities"),
                side_effect=[["new-1"], ["new-2"]],
            ))
            mock_close = stack.enter_context(patch(
                _patch_path("_batch_close_superseded"),
            ))
            mock_touch = stack.enter_context(patch(
                _patch_path("_batch_touch_existing"),
            ))
            mock_upsert = stack.enter_context(patch(
                _patch_path("_batch_upsert_queue"),
            ))

            batched_materialize(
                _stub_ctx(), conn, "Object",
                raw_payloads=raw_payloads, result=result,
            )

        # _batch_insert_new_entities called TWICE: once for new
        # bucket, once for changed bucket (SCD Type 2 new versions)
        assert mock_insert.call_count == 2
        # close called once for the changed bucket
        mock_close.assert_called_once()
        close_ids = mock_close.call_args.args[2]
        assert close_ids == ["old-changed"]
        # touch called once with the unchanged id
        mock_touch.assert_called_once()
        touch_ids = mock_touch.call_args.args[1]
        assert touch_ids == ["old-same"]
        # upsert called once with new + changed_new ids
        mock_upsert.assert_called_once()
        upsert_ids = mock_upsert.call_args.args[2]
        assert sorted(upsert_ids) == sorted(["new-1", "new-2"])
        # Counters
        assert result.entities_inserted == 1
        assert result.entities_superseded == 1
        assert result.entities_unchanged == 1


class TestBatchedMaterializeChunking:
    def test_batched_materialize_chunks_respect_chunk_size(self) -> None:
        """1100 entities at chunk_size=500 → 3 chunks
        (500 + 500 + 100)."""
        result = PhaseResult(entity_type="Object")
        conn = MagicMock()
        from contextlib import ExitStack

        raw_payloads = [{"name": f"O_{i}"} for i in range(1100)]

        with ExitStack() as stack:
            stack.enter_context(patch(
                _patch_path("normalize"), side_effect=lambda et, raw: raw,
            ))
            stack.enter_context(patch(
                _patch_path("hash_normalized"), return_value="h",
            ))
            stack.enter_context(patch(
                _patch_path("to_presentation"),
                side_effect=lambda et, n: {"label": n.get("name"), "name": n.get("name")},
            ))
            stack.enter_context(patch(
                _patch_path("to_semantic_text"), return_value="text",
            ))
            mock_read = stack.enter_context(patch(
                _patch_path("_batch_read_existing"), return_value={},
            ))
            stack.enter_context(patch(
                _patch_path("_batch_insert_new_entities"),
                # Each chunk returns its own list of ids
                side_effect=[
                    [f"id-{i}" for i in range(500)],
                    [f"id-{i}" for i in range(500, 1000)],
                    [f"id-{i}" for i in range(1000, 1100)],
                ],
            ))
            stack.enter_context(patch(_patch_path("_batch_close_superseded")))
            stack.enter_context(patch(_patch_path("_batch_touch_existing")))
            stack.enter_context(patch(_patch_path("_batch_upsert_queue")))

            batched_materialize(
                _stub_ctx(), conn, "Object",
                raw_payloads=raw_payloads, result=result,
                chunk_size=500,
            )

        # _batch_read_existing called once per chunk → 3 times
        assert mock_read.call_count == 3
        # Chunk sizes: 500, 500, 100
        chunk_sizes = [
            len(call.args[3])  # external_ids is 4th positional arg
            for call in mock_read.call_args_list
        ]
        assert chunk_sizes == [500, 500, 100]

    def test_batched_materialize_counters_accumulate_across_chunks(
        self,
    ) -> None:
        """Counters accumulate across chunks."""
        result = PhaseResult(entity_type="Object")
        conn = MagicMock()
        from contextlib import ExitStack

        raw_payloads = [{"name": f"O_{i}"} for i in range(1100)]

        with ExitStack() as stack:
            stack.enter_context(patch(
                _patch_path("normalize"), side_effect=lambda et, raw: raw,
            ))
            stack.enter_context(patch(
                _patch_path("hash_normalized"), return_value="h",
            ))
            stack.enter_context(patch(
                _patch_path("to_presentation"),
                side_effect=lambda et, n: {"label": n.get("name"), "name": n.get("name")},
            ))
            stack.enter_context(patch(
                _patch_path("to_semantic_text"), return_value="text",
            ))
            stack.enter_context(patch(
                _patch_path("_batch_read_existing"), return_value={},
            ))
            stack.enter_context(patch(
                _patch_path("_batch_insert_new_entities"),
                side_effect=[
                    [f"id-{i}" for i in range(500)],
                    [f"id-{i}" for i in range(500, 1000)],
                    [f"id-{i}" for i in range(1000, 1100)],
                ],
            ))
            stack.enter_context(patch(_patch_path("_batch_close_superseded")))
            stack.enter_context(patch(_patch_path("_batch_touch_existing")))
            stack.enter_context(patch(_patch_path("_batch_upsert_queue")))

            batched_materialize(
                _stub_ctx(), conn, "Object",
                raw_payloads=raw_payloads, result=result,
                chunk_size=500,
            )

        assert result.entities_inserted == 1100  # 500 + 500 + 100
        assert result.entities_superseded == 0
        assert result.entities_unchanged == 0
        assert result.embeddings_queued == 1100
        assert result.summaries_queued == 1100


class TestBatchedMaterializeSupersession:
    def test_batched_materialize_supersedes_changed_entities(self) -> None:
        """Changed entities go through SCD Type 2: close-out + new
        INSERT. The new INSERT's returned ids drive the queue UPSERT."""
        result = PhaseResult(entity_type="Object")
        conn = MagicMock()
        from contextlib import ExitStack

        raw_payloads = [{"name": "Account"}]
        existing = {
            "Account": {"id": "old-id", "last_seed_hash": "OLDHASH"},
        }

        with ExitStack() as stack:
            stack.enter_context(patch(
                _patch_path("normalize"), side_effect=lambda et, raw: raw,
            ))
            stack.enter_context(patch(
                _patch_path("hash_normalized"), return_value="NEWHASH",
            ))
            stack.enter_context(patch(
                _patch_path("to_presentation"),
                side_effect=lambda et, n: {"label": "Account", "name": "Account"},
            ))
            stack.enter_context(patch(
                _patch_path("to_semantic_text"), return_value="text",
            ))
            stack.enter_context(patch(
                _patch_path("_batch_read_existing"), return_value=existing,
            ))
            mock_insert = stack.enter_context(patch(
                _patch_path("_batch_insert_new_entities"),
                return_value=["new-id"],
            ))
            mock_close = stack.enter_context(patch(
                _patch_path("_batch_close_superseded"),
            ))
            mock_upsert = stack.enter_context(patch(
                _patch_path("_batch_upsert_queue"),
            ))
            stack.enter_context(patch(_patch_path("_batch_touch_existing")))

            batched_materialize(
                _stub_ctx(), conn, "Object",
                raw_payloads=raw_payloads, result=result,
            )

        # Close called with prior id; insert called for the new version
        mock_close.assert_called_once()
        close_ids = mock_close.call_args.args[2]
        assert close_ids == ["old-id"]
        # INSERT called exactly once (only the changed bucket; no
        # new bucket members)
        assert mock_insert.call_count == 1
        # Queue UPSERT against NEW entity id, NOT the old prior id
        mock_upsert.assert_called_once()
        upsert_ids = mock_upsert.call_args.args[2]
        assert upsert_ids == ["new-id"]
        # Counter
        assert result.entities_superseded == 1


class TestBatchedMaterializeQueueReEnqueue:
    def test_batched_materialize_re_enqueues_changed_via_upsert(
        self,
    ) -> None:
        """Changed entities trigger queue UPSERT — the ON CONFLICT
        clause in the SQL handles the case where a prior queue row
        exists (resets to pending). Here we verify the UPSERT call
        happens for changed entities; the SQL's ON CONFLICT
        semantics are exercised by the live test."""
        result = PhaseResult(entity_type="Object")
        conn = MagicMock()
        from contextlib import ExitStack

        raw_payloads = [{"name": "Account"}, {"name": "Contact"}]
        existing = {
            "Account": {"id": "id-A", "last_seed_hash": "OLD"},
            "Contact": {"id": "id-C", "last_seed_hash": "OLD"},
        }

        with ExitStack() as stack:
            stack.enter_context(patch(
                _patch_path("normalize"), side_effect=lambda et, raw: raw,
            ))
            stack.enter_context(patch(
                _patch_path("hash_normalized"), return_value="NEW",
            ))
            stack.enter_context(patch(
                _patch_path("to_presentation"),
                side_effect=lambda et, n: {"label": n["name"], "name": n["name"]},
            ))
            stack.enter_context(patch(
                _patch_path("to_semantic_text"), return_value="text",
            ))
            stack.enter_context(patch(
                _patch_path("_batch_read_existing"), return_value=existing,
            ))
            stack.enter_context(patch(
                _patch_path("_batch_insert_new_entities"),
                return_value=["new-A", "new-C"],
            ))
            stack.enter_context(patch(_patch_path("_batch_close_superseded")))
            stack.enter_context(patch(_patch_path("_batch_touch_existing")))
            mock_upsert = stack.enter_context(patch(
                _patch_path("_batch_upsert_queue"),
            ))

            batched_materialize(
                _stub_ctx(), conn, "Object",
                raw_payloads=raw_payloads, result=result,
            )

        # UPSERT called once with both new entity_ids (NOT prior ids)
        mock_upsert.assert_called_once()
        upsert_ids = mock_upsert.call_args.args[2]
        assert sorted(upsert_ids) == ["new-A", "new-C"]
        # embeddings + summaries each queued for both
        assert result.embeddings_queued == 2
        assert result.summaries_queued == 2


# ----------------------------------------------------------------------
# _extract_external_id — per-entity-type external_id routing
# ----------------------------------------------------------------------

import pytest

from primeqa.sync.materialize import _extract_external_id


class TestExtractExternalId:
    """Verifies the per-entity-type router. Each new phase cycle
    adds a branch here; the test parametrizes the known mappings."""

    @pytest.mark.parametrize("entity_type,raw,expected", [
        ("Object", {"name": "Account"}, "Account"),
        ("Object", {"name": "MyCustom__c"}, "MyCustom__c"),
        # PicklistValueSet defaults to GVS shape when no _source
        # marker (backward compat with pre-SVS-cycle fixtures).
        ("PicklistValueSet",
         {"FullName": "RegionVS", "MasterLabel": "Region"},
         "RegionVS"),
        ("PicklistValueSet",
         {"FullName": "MyNamespace__MyVS"},
         "MyNamespace__MyVS"),
        # _source='GlobalValueSet' explicit — same as default.
        ("PicklistValueSet",
         {"_source": "GlobalValueSet", "FullName": "RegionVS"},
         "RegionVS"),
        # _source='StandardValueSet' — namespaced with SVS: prefix
        # per corrections-log §8 addendum (collision avoidance).
        ("PicklistValueSet",
         {"_source": "StandardValueSet", "FullName": "AccountSource"},
         "SVS:AccountSource"),
        ("PicklistValueSet",
         {"_source": "StandardValueSet",
          "FullName": "Industry"},  # collision-prone catalog name
         "SVS:Industry"),
    ])
    def test_extract_external_id_known_types(
        self, entity_type: str, raw: dict, expected: str,
    ) -> None:
        assert _extract_external_id(entity_type, raw) == expected

    def test_extract_external_id_unknown_type_raises(self) -> None:
        with pytest.raises(KeyError) as excinfo:
            _extract_external_id("NotAnEntity", {"name": "X"})
        msg = str(excinfo.value)
        assert "NotAnEntity" in msg
        assert "_extract_external_id" in msg
