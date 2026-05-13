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
        # PicklistValue: composite external_id, parent prefix from
        # _parent_external_id marker (which already inherits SVS:
        # prefix for StandardValueSet sources).
        ("PicklistValue",
         {"_parent_external_id": "SVS:AccountType",
          "valueName": "Analyst"},
         "SVS:AccountType.Analyst"),
        ("PicklistValue",
         {"_parent_external_id": "MyCustomGVS",
          "valueName": "Banking"},
         "MyCustomGVS.Banking"),
        # RecordType: external_id = Salesforce-provided FullName.
        # Same canonical-identifier pattern as GVS — no composite
        # construction needed since the Tooling fetcher hands us
        # the identifier directly.
        ("RecordType",
         {"FullName": "Account.PartnerAccount",
          "developerName": "PartnerAccount"},
         "Account.PartnerAccount"),
        ("RecordType",
         {"FullName": "MyNS__License__c.Trial"},
         "MyNS__License__c.Trial"),
    ])
    def test_extract_external_id_known_types(
        self, entity_type: str, raw: dict, expected: str,
    ) -> None:
        assert _extract_external_id(entity_type, raw) == expected

    def test_extract_external_id_record_type_raises_when_fullname_missing(
        self,
    ) -> None:
        """RecordType without FullName → ValueError. Without the
        Tooling-provided identifier we have no way to compose one
        (developerName alone isn't unique across Objects)."""
        with pytest.raises(ValueError) as excinfo:
            _extract_external_id(
                "RecordType",
                {"developerName": "PartnerAccount"},
            )
        assert "FullName" in str(excinfo.value)

    def test_extract_external_id_unknown_type_raises(self) -> None:
        with pytest.raises(KeyError) as excinfo:
            _extract_external_id("NotAnEntity", {"name": "X"})
        msg = str(excinfo.value)
        assert "NotAnEntity" in msg
        assert "_extract_external_id" in msg

    def test_extract_external_id_picklist_value_missing_parent_raises(
        self,
    ) -> None:
        """PicklistValue without _parent_external_id marker — fail
        loudly with a message naming both missing fields."""
        with pytest.raises(ValueError) as excinfo:
            _extract_external_id(
                "PicklistValue", {"valueName": "Analyst"},
            )
        msg = str(excinfo.value)
        assert "_parent_external_id" in msg
        assert "valueName" in msg


# ----------------------------------------------------------------------
# resolve_entity_id_by_external_id + make_parent_resolver
# ----------------------------------------------------------------------

from primeqa.sync.materialize import (
    make_parent_resolver,
    resolve_entity_id_by_external_id,
)


class _RowStub:
    def __init__(self, id_):
        self.id = id_


class TestResolveEntityIdByExternalId:
    def test_returns_id_when_found(self) -> None:
        ctx = _stub_ctx()
        conn = MagicMock()
        conn.execute.return_value.first.return_value = _RowStub("uuid-abc")
        result = resolve_entity_id_by_external_id(
            conn, ctx, "PicklistValueSet", "SVS:AccountType",
        )
        assert result == "uuid-abc"
        # Verify the bound parameters were passed through
        call_kwargs = conn.execute.call_args[0][1]
        assert call_kwargs["entity_type"] == "PicklistValueSet"
        assert call_kwargs["external_id"] == "SVS:AccountType"
        assert call_kwargs["org_id"] == ctx.connected_org_id

    def test_returns_none_when_not_found(self) -> None:
        ctx = _stub_ctx()
        conn = MagicMock()
        conn.execute.return_value.first.return_value = None
        result = resolve_entity_id_by_external_id(
            conn, ctx, "PicklistValueSet", "SVS:NeverHeardOfIt",
        )
        assert result is None


class TestMakeParentResolver:
    def test_caches_repeated_lookups(self) -> None:
        """A resolver for one chunk should issue at most one query
        per (entity_type, external_id) pair, regardless of call
        count. Critical for the PicklistValue chunk shape (many
        children share each parent)."""
        ctx = _stub_ctx()
        conn = MagicMock()
        conn.execute.return_value.first.return_value = _RowStub("uuid-parent")

        resolver = make_parent_resolver(conn, ctx)
        # Same parent called 5 times → 1 query
        for _ in range(5):
            r = resolver(
                entity_type="PicklistValueSet",
                external_id="SVS:AccountType",
            )
            assert r == "uuid-parent"
        assert conn.execute.call_count == 1

    def test_distinct_lookups_issue_distinct_queries(self) -> None:
        """Distinct (entity_type, external_id) pairs each get their
        own query. Cache is a memoizer, not a single-result lock."""
        ctx = _stub_ctx()
        conn = MagicMock()
        # Different rows on subsequent calls
        conn.execute.return_value.first.side_effect = [
            _RowStub("uuid-a"), _RowStub("uuid-b"), _RowStub("uuid-c"),
        ]

        resolver = make_parent_resolver(conn, ctx)
        a = resolver(entity_type="PicklistValueSet", external_id="P1")
        b = resolver(entity_type="PicklistValueSet", external_id="P2")
        c = resolver(entity_type="PicklistValueSet", external_id="P3")
        assert (a, b, c) == ("uuid-a", "uuid-b", "uuid-c")
        assert conn.execute.call_count == 3

    def test_caches_none_results(self) -> None:
        """Resolver should also memoize None — re-querying an
        unresolvable external_id within a chunk is wasted IO."""
        ctx = _stub_ctx()
        conn = MagicMock()
        conn.execute.return_value.first.return_value = None

        resolver = make_parent_resolver(conn, ctx)
        for _ in range(3):
            r = resolver(
                entity_type="PicklistValueSet",
                external_id="SVS:Ghost",
            )
            assert r is None
        assert conn.execute.call_count == 1


# ----------------------------------------------------------------------
# Detail-table integration with _materialize_chunk
# ----------------------------------------------------------------------


class TestMaterializeChunkDetailRows:
    """Verify _materialize_chunk routes through detail_mappers when
    the entity_type has a registered mapper, and skips otherwise."""

    def test_calls_batch_insert_details_for_object(self) -> None:
        """Object has a registered mapper; new entities → detail
        rows inserted in the Object detail table."""
        result = PhaseResult(entity_type="Object")
        conn = MagicMock()
        from contextlib import ExitStack
        with ExitStack() as stack:
            # Pipeline patches
            mocks = {
                name: stack.enter_context(p)
                for name, p in zip(
                    ("normalize", "hash", "pres", "text",
                     "read", "insert", "close", "touch", "upsert"),
                    _patch_pipeline(
                        normalize_return={"name": "Account", "custom": False,
                                          "queryable": True},
                        presentation_return={"name": "Account",
                                              "label": "Account"},
                        existing_return={},  # all-new
                        insert_return=["entity-uuid-1"],
                    ),
                )
            }
            # Patch the detail-table INSERT helper
            mock_details = stack.enter_context(
                patch(_patch_path("_batch_insert_details")),
            )
            batched_materialize(
                _stub_ctx(), conn, "Object",
                raw_payloads=[{"name": "Account"}],
                result=result,
            )
        mock_details.assert_called_once()
        # Verify call shape: conn, table_name, rows
        call_args = mock_details.call_args[0]
        assert call_args[1] == "object_details"
        rows = call_args[2]
        assert len(rows) == 1
        assert rows[0]["entity_id"] == "entity-uuid-1"

    def test_skips_detail_writes_for_picklist_value_set(self) -> None:
        """PicklistValueSet has no detail-table mapper — _batch_
        insert_details must NOT be called even when entities are
        inserted."""
        result = PhaseResult(entity_type="PicklistValueSet")
        conn = MagicMock()
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch(
                _patch_path("get_detail_mapper"),
                return_value=None,
            ))
            mocks = {
                name: stack.enter_context(p)
                for name, p in zip(
                    ("normalize", "hash", "pres", "text",
                     "read", "insert", "close", "touch", "upsert"),
                    _patch_pipeline(
                        existing_return={},
                        insert_return=["e1"],
                    ),
                )
            }
            mock_details = stack.enter_context(
                patch(_patch_path("_batch_insert_details")),
            )
            batched_materialize(
                _stub_ctx(), conn, "PicklistValueSet",
                raw_payloads=[{"FullName": "X"}],
                result=result,
            )
        mock_details.assert_not_called()

    def test_detail_writes_cover_changed_bucket_too(self) -> None:
        """Changed-bucket entities also need fresh detail rows for
        their new entity_ids. The mapper is called once per (new +
        changed) row total."""
        result = PhaseResult(entity_type="Object")
        conn = MagicMock()
        # Two payloads: one new, one changed
        from contextlib import ExitStack

        def fake_normalize(et, raw):
            return {"name": raw["name"], "custom": False}

        def fake_hash(n):
            # Different hash than the "existing" one to force changed
            return f"h_{n['name']}"

        with ExitStack() as stack:
            stack.enter_context(patch(_patch_path("normalize"),
                                      side_effect=fake_normalize))
            stack.enter_context(patch(_patch_path("hash_normalized"),
                                      side_effect=fake_hash))
            stack.enter_context(patch(_patch_path("to_presentation"),
                                      return_value={"name": "X", "label": "X"}))
            stack.enter_context(patch(_patch_path("to_semantic_text"),
                                      return_value="text"))
            # Account exists with old hash; Contact is new
            stack.enter_context(patch(
                _patch_path("_batch_read_existing"),
                return_value={
                    "Account": {"id": "existing-uuid-1",
                                "last_seed_hash": "old_hash"},
                },
            ))
            stack.enter_context(patch(
                _patch_path("_batch_insert_new_entities"),
                # Called once for new (Contact), once for changed (Account)
                side_effect=[["new-uuid-contact"], ["new-uuid-account"]],
            ))
            stack.enter_context(patch(_patch_path("_batch_close_superseded")))
            stack.enter_context(patch(_patch_path("_batch_touch_existing")))
            stack.enter_context(patch(_patch_path("_batch_upsert_queue")))
            mock_details = stack.enter_context(
                patch(_patch_path("_batch_insert_details")),
            )
            batched_materialize(
                _stub_ctx(), conn, "Object",
                raw_payloads=[
                    {"name": "Account"},  # changed
                    {"name": "Contact"},  # new
                ],
                result=result,
            )
        mock_details.assert_called_once()
        rows = mock_details.call_args[0][2]
        # 1 new + 1 changed = 2 detail rows
        assert len(rows) == 2
        entity_ids = {r["entity_id"] for r in rows}
        assert entity_ids == {"new-uuid-contact", "new-uuid-account"}


from primeqa.sync.materialize import _batch_insert_details


class TestBatchInsertDetails:
    def test_empty_input_is_noop(self) -> None:
        """No detail rows → no SQL executed (guard against malformed
        INSERT with empty VALUES list)."""
        conn = MagicMock()
        _batch_insert_details(conn, "object_details", [])
        conn.execute.assert_not_called()

    def test_builds_multi_row_insert(self) -> None:
        """Multiple rows → one INSERT with VALUES (...), (...), ...
        and bound parameters keyed by col_index."""
        conn = MagicMock()
        rows = [
            {"entity_id": "u1", "is_custom": True},
            {"entity_id": "u2", "is_custom": False},
        ]
        _batch_insert_details(conn, "object_details", rows)
        conn.execute.assert_called_once()
        # First positional arg is the text() clause; second is params
        params = conn.execute.call_args[0][1]
        assert params["entity_id_0"] == "u1"
        assert params["is_custom_0"] is True
        assert params["entity_id_1"] == "u2"
        assert params["is_custom_1"] is False


# ----------------------------------------------------------------------
# Edge writes — property-less variant
# ----------------------------------------------------------------------

from primeqa.sync.materialize import (
    _batch_close_superseded_edges,
    _batch_insert_new_edges,
    _batch_read_existing_edges_for_sources,
    _lookup_edge_category,
    batched_materialize_property_less_edges,
    materialize_edges_for_entities,
)


class TestLookupEdgeCategory:
    def test_returns_structural_for_belongs_to(self) -> None:
        """BELONGS_TO is STRUCTURAL per substrate-1's TIER_1_EDGES."""
        assert _lookup_edge_category("BELONGS_TO") == "STRUCTURAL"

    def test_returns_structural_for_has_relationship_to(self) -> None:
        """HAS_RELATIONSHIP_TO is STRUCTURAL."""
        assert _lookup_edge_category("HAS_RELATIONSHIP_TO") == "STRUCTURAL"

    def test_raises_for_unknown_edge_type(self) -> None:
        """Lookup is strict — unknown edge_type raises KeyError from
        TIER_1_EDGES (not silently returns a default)."""
        import pytest as _pytest
        with _pytest.raises(KeyError):
            _lookup_edge_category("NOT_A_REAL_EDGE")


class TestBatchReadExistingEdgesForSources:
    def test_empty_source_list_returns_empty_set(self) -> None:
        """Empty input short-circuits — no DB call."""
        conn = MagicMock()
        result = _batch_read_existing_edges_for_sources(
            conn, "BELONGS_TO", [],
        )
        assert result == set()
        conn.execute.assert_not_called()

    def test_returns_set_of_source_target_tuples(self) -> None:
        """Rows from the SELECT are reshaped into a set of
        (source_id, target_id) tuples for set-difference bucketing."""
        conn = MagicMock()
        row1 = type("R", (), {"source_entity_id": "s1",
                              "target_entity_id": "t1"})()
        row2 = type("R", (), {"source_entity_id": "s2",
                              "target_entity_id": "t2"})()
        conn.execute.return_value.fetchall.return_value = [row1, row2]
        result = _batch_read_existing_edges_for_sources(
            conn, "BELONGS_TO", ["s1", "s2"],
        )
        assert result == {("s1", "t1"), ("s2", "t2")}


class TestBatchInsertNewEdges:
    def test_empty_pairs_is_noop(self) -> None:
        """Defensive empty-input check — no malformed VALUES list."""
        conn = MagicMock()
        ctx = _stub_ctx()
        _batch_insert_new_edges(conn, ctx, "BELONGS_TO", "STRUCTURAL", [])
        conn.execute.assert_not_called()

    def test_builds_multi_row_insert_with_uuid_casts(self) -> None:
        """Bound parameters keyed by source_{i}/target_{i}. SQL uses
        CAST(... AS uuid) form per the materialize module's
        documented idiom (avoids :: parser ambiguity)."""
        conn = MagicMock()
        ctx = _stub_ctx()
        pairs = [("s1", "t1"), ("s2", "t2")]
        _batch_insert_new_edges(
            conn, ctx, "BELONGS_TO", "STRUCTURAL", pairs,
        )
        conn.execute.assert_called_once()
        sql_text = str(conn.execute.call_args[0][0])
        assert "CAST(:source_0 AS uuid)" in sql_text
        assert "CAST(:target_1 AS uuid)" in sql_text
        params = conn.execute.call_args[0][1]
        assert params["source_0"] == "s1"
        assert params["target_1"] == "t2"
        assert params["edge_type"] == "BELONGS_TO"
        assert params["edge_category"] == "STRUCTURAL"
        assert params["valid_from_seq"] == ctx.logical_version_seq


class TestBatchCloseSupersededEdges:
    def test_empty_pairs_is_noop(self) -> None:
        conn = MagicMock()
        ctx = _stub_ctx()
        _batch_close_superseded_edges(conn, ctx, "BELONGS_TO", [])
        conn.execute.assert_not_called()

    def test_uses_tuple_in_with_uuid_casts(self) -> None:
        """UPDATE close uses Postgres tuple-IN with explicit UUID
        casts on each side of each pair."""
        conn = MagicMock()
        ctx = _stub_ctx()
        pairs = [("s1", "t1"), ("s2", "t2")]
        _batch_close_superseded_edges(
            conn, ctx, "BELONGS_TO", pairs,
        )
        conn.execute.assert_called_once()
        sql_text = str(conn.execute.call_args[0][0])
        assert "(source_entity_id, target_entity_id) IN" in sql_text
        assert "CAST(:source_0 AS uuid)" in sql_text
        params = conn.execute.call_args[0][1]
        assert params["close_seq"] == ctx.logical_version_seq
        assert params["edge_type"] == "BELONGS_TO"


class TestBatchedMaterializePropertyLessEdges:
    def _setup(self, existing_pairs, incoming):
        """Common setup: ctx, conn with _batch_read returning
        existing_pairs as a set; existing_pairs is what's currently
        in the DB; incoming is what this sync wants."""
        ctx = _stub_ctx()
        conn = MagicMock()
        result = PhaseResult(entity_type="Field")
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch(
            _patch_path("_batch_read_existing_edges_for_sources"),
            return_value=existing_pairs,
        ))
        mock_insert = stack.enter_context(patch(
            _patch_path("_batch_insert_new_edges"),
        ))
        mock_close = stack.enter_context(patch(
            _patch_path("_batch_close_superseded_edges"),
        ))
        return ctx, conn, result, mock_insert, mock_close, stack

    def test_inserts_only_new_pairs(self) -> None:
        """Pairs in incoming but not in existing → INSERT; pairs in
        both → no-op (unchanged)."""
        existing = {("s1", "t1")}
        incoming = [
            ("s1", "t1", "BELONGS_TO", "STRUCTURAL"),
            ("s2", "t2", "BELONGS_TO", "STRUCTURAL"),
        ]
        ctx, conn, result, mock_insert, mock_close, stack = self._setup(
            existing, incoming,
        )
        with stack:
            batched_materialize_property_less_edges(
                ctx, conn, incoming, result,
            )
        mock_insert.assert_called_once()
        new_pairs = mock_insert.call_args[0][4]
        assert set(new_pairs) == {("s2", "t2")}
        mock_close.assert_not_called()
        assert result.edges_inserted == 1
        assert result.edges_superseded == 0

    def test_closes_only_removed_pairs(self) -> None:
        """Pairs in existing but not in incoming → close. Pairs in
        incoming but not in existing → INSERT."""
        existing = {("s1", "t1"), ("s2", "t2")}
        incoming = [
            ("s1", "t1", "BELONGS_TO", "STRUCTURAL"),
            ("s3", "t3", "BELONGS_TO", "STRUCTURAL"),
        ]
        ctx, conn, result, mock_insert, mock_close, stack = self._setup(
            existing, incoming,
        )
        with stack:
            batched_materialize_property_less_edges(
                ctx, conn, incoming, result,
            )
        # New: (s3, t3) only
        assert set(mock_insert.call_args[0][4]) == {("s3", "t3")}
        # Superseded: (s2, t2) only
        assert set(mock_close.call_args[0][3]) == {("s2", "t2")}
        assert result.edges_inserted == 1
        assert result.edges_superseded == 1

    def test_idempotent_when_incoming_equals_existing(self) -> None:
        """All pairs in incoming match existing → unchanged set is
        full → no INSERT, no UPDATE. Idempotency for repeated syncs."""
        existing = {("s1", "t1"), ("s2", "t2")}
        incoming = [
            ("s1", "t1", "BELONGS_TO", "STRUCTURAL"),
            ("s2", "t2", "BELONGS_TO", "STRUCTURAL"),
        ]
        ctx, conn, result, mock_insert, mock_close, stack = self._setup(
            existing, incoming,
        )
        with stack:
            batched_materialize_property_less_edges(
                ctx, conn, incoming, result,
            )
        mock_insert.assert_not_called()
        mock_close.assert_not_called()
        assert result.edges_inserted == 0
        assert result.edges_superseded == 0

    def test_groups_by_edge_type(self) -> None:
        """Multiple edge_types in incoming → bucketed per edge_type
        independently; each gets its own existing-set lookup."""
        existing_calls = []

        def fake_read(conn, edge_type, source_ids):
            existing_calls.append(edge_type)
            return set()
        conn = MagicMock()
        ctx = _stub_ctx()
        result = PhaseResult(entity_type="Field")
        incoming = [
            ("s1", "t1", "BELONGS_TO", "STRUCTURAL"),
            ("s1", "tref", "HAS_RELATIONSHIP_TO", "STRUCTURAL"),
        ]
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch(
                _patch_path("_batch_read_existing_edges_for_sources"),
                side_effect=fake_read,
            ))
            stack.enter_context(patch(
                _patch_path("_batch_insert_new_edges"),
            ))
            batched_materialize_property_less_edges(
                ctx, conn, incoming, result,
            )
        # Both edge_types' existing sets were independently queried
        assert set(existing_calls) == {"BELONGS_TO",
                                        "HAS_RELATIONSHIP_TO"}


class TestMaterializeEdgesForEntities:
    def _normalized_field(self, name, parent="Account",
                           reference_to=None) -> dict:
        n = {"name": name, "_parent_object_api_name": parent}
        if reference_to is not None:
            n["referenceTo"] = list(reference_to)
        return n

    def test_no_edges_when_no_specs_for_entity_type(self) -> None:
        """If get_edge_specs returns [] for the source entity_type,
        no edge work happens at all (no parent_resolver creation,
        no batched_materialize_property_less_edges call)."""
        ctx = _stub_ctx()
        conn = MagicMock()
        result = PhaseResult(entity_type="Object")
        from contextlib import ExitStack
        with ExitStack() as stack:
            mock_bm = stack.enter_context(patch(
                _patch_path("batched_materialize_property_less_edges"),
            ))
            materialize_edges_for_entities(
                ctx, conn, "Object",
                entity_id_map={"Account": "obj-acc"},
                normalized_payloads=[{"name": "Account"}],
                result=result,
            )
        mock_bm.assert_not_called()

    def test_writes_belongs_to_for_every_field(self) -> None:
        """Each Field with a resolved parent Object produces one
        BELONGS_TO edge_write tuple."""
        ctx = _stub_ctx()
        conn = MagicMock()
        result = PhaseResult(entity_type="Field")
        normalized_payloads = [
            self._normalized_field("Industry"),
            self._normalized_field("Name"),
        ]
        entity_id_map = {
            "Account.Industry": "fld-001",
            "Account.Name": "fld-002",
        }

        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch(
                _patch_path("make_parent_resolver"),
                return_value=lambda **_kw: "obj-account",
            ))
            mock_bm = stack.enter_context(patch(
                _patch_path("batched_materialize_property_less_edges"),
            ))
            materialize_edges_for_entities(
                ctx, conn, "Field",
                entity_id_map=entity_id_map,
                normalized_payloads=normalized_payloads,
                result=result,
            )
        mock_bm.assert_called_once()
        edge_writes = mock_bm.call_args[0][2]
        # 2 fields × 1 BELONGS_TO each = 2 edges
        # (no referenceTo → 0 HAS_RELATIONSHIP_TO)
        belongs_to = [e for e in edge_writes if e[2] == "BELONGS_TO"]
        assert len(belongs_to) == 2

    def test_writes_has_relationship_to_for_reference_field(self) -> None:
        """A reference field with referenceTo=['User'] produces a
        HAS_RELATIONSHIP_TO edge to the User Object entity."""
        ctx = _stub_ctx()
        conn = MagicMock()
        result = PhaseResult(entity_type="Field")
        normalized_payloads = [
            self._normalized_field("OwnerId", reference_to=["User"]),
        ]
        entity_id_map = {"Account.OwnerId": "fld-owner"}

        # Resolver: Account → obj-account, User → obj-user
        def resolver(*, entity_type, external_id):
            return {
                ("Object", "Account"): "obj-account",
                ("Object", "User"): "obj-user",
            }.get((entity_type, external_id))

        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch(
                _patch_path("make_parent_resolver"),
                return_value=resolver,
            ))
            mock_bm = stack.enter_context(patch(
                _patch_path("batched_materialize_property_less_edges"),
            ))
            materialize_edges_for_entities(
                ctx, conn, "Field",
                entity_id_map=entity_id_map,
                normalized_payloads=normalized_payloads,
                result=result,
            )
        edge_writes = mock_bm.call_args[0][2]
        # 1 BELONGS_TO + 1 HAS_RELATIONSHIP_TO
        assert len(edge_writes) == 2
        edge_types = {e[2] for e in edge_writes}
        assert edge_types == {"BELONGS_TO", "HAS_RELATIONSHIP_TO"}

    def test_skips_edges_to_unresolvable_targets(self) -> None:
        """If parent_resolver returns None for a target (target Object
        was filtered out or not yet synced), skip the edge silently.
        Don't write a dangling edge; don't fail the whole sync."""
        ctx = _stub_ctx()
        conn = MagicMock()
        result = PhaseResult(entity_type="Field")
        # Reference to an Object that isn't materialized
        normalized_payloads = [
            self._normalized_field(
                "MysteryRef", reference_to=["UnmappedObject"],
            ),
        ]
        entity_id_map = {"Account.MysteryRef": "fld-mystery"}

        def resolver(*, entity_type, external_id):
            return {
                ("Object", "Account"): "obj-account",
                # No UnmappedObject — resolver returns None
            }.get((entity_type, external_id))

        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch(
                _patch_path("make_parent_resolver"),
                return_value=resolver,
            ))
            mock_bm = stack.enter_context(patch(
                _patch_path("batched_materialize_property_less_edges"),
            ))
            materialize_edges_for_entities(
                ctx, conn, "Field",
                entity_id_map=entity_id_map,
                normalized_payloads=normalized_payloads,
                result=result,
            )
        edge_writes = mock_bm.call_args[0][2]
        # Only BELONGS_TO survives; HAS_RELATIONSHIP_TO skipped
        assert len(edge_writes) == 1
        assert edge_writes[0][2] == "BELONGS_TO"


# ----------------------------------------------------------------------
# batched_materialize return_id_map
# ----------------------------------------------------------------------


class TestBatchedMaterializeReturnIdMap:
    def test_default_returns_none(self) -> None:
        """When return_id_map=False (default), batched_materialize
        returns None — the caller pays no accumulation cost."""
        from contextlib import ExitStack
        result = PhaseResult(entity_type="Object")
        conn = MagicMock()
        with ExitStack() as stack:
            for _name, p in zip(
                ("normalize", "hash", "pres", "text",
                 "read", "insert", "close", "touch", "upsert"),
                _patch_pipeline(),
            ):
                stack.enter_context(p)
            stack.enter_context(patch(_patch_path("_batch_insert_details")))
            ret = batched_materialize(
                _stub_ctx(), conn, "Object",
                raw_payloads=[{"name": "Account"}],
                result=result,
            )
        assert ret is None

    def test_returns_dict_when_enabled(self) -> None:
        """return_id_map=True → returns {external_id: entity_id} for
        every input payload (new + changed + unchanged)."""
        from contextlib import ExitStack
        result = PhaseResult(entity_type="Object")
        conn = MagicMock()
        with ExitStack() as stack:
            for _name, p in zip(
                ("normalize", "hash", "pres", "text",
                 "read", "insert", "close", "touch", "upsert"),
                _patch_pipeline(
                    normalize_return={"name": "Account", "custom": False},
                    existing_return={},  # all new
                    insert_return=["entity-uuid-1"],
                ),
            ):
                stack.enter_context(p)
            stack.enter_context(patch(_patch_path("_batch_insert_details")))
            ret = batched_materialize(
                _stub_ctx(), conn, "Object",
                raw_payloads=[{"name": "Account"}],
                result=result,
                return_id_map=True,
            )
        assert ret == {"Account": "entity-uuid-1"}
