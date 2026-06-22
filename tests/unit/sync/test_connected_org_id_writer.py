"""per-org S1 Slice 1 — the sync WRITER stamps connected_org_id on every
entity / edge / logical_version it inserts.

Red-proofs (offline, MagicMock connection — no DB):
  * entities INSERT carries a ``connected_org_id`` column, adjacent to
    ``last_synced_from_org_id``, and stamps it = ctx.connected_org_id (the
    same value as the provenance column — the stable partition key starts
    equal to the "most recently sourced" stamp, but never rotates).
  * edges INSERT carries a ``connected_org_id`` column and stamps it =
    ctx.connected_org_id (an edge is intra-org).
  * _allocate_logical_version INSERT carries ``connected_org_id`` and stamps
    the org threaded down from the sync.

These guard against a writer site silently leaving the new partition key NULL
on freshly-synced rows (which would defeat the per-org partition + fail a later
NOT NULL tightening).
"""
import pytest
from unittest.mock import MagicMock

pytestmark = pytest.mark.unit

from primeqa.sync.batching import EntityForWrite
from primeqa.sync.context import SyncContext
from primeqa.sync.materialize import (
    _batch_insert_new_entities,
    _batch_insert_new_edges,
)

ORG = "22222222-2222-2222-2222-222222222222"


def _ctx() -> SyncContext:
    return SyncContext(
        sf_client=None,
        engine=MagicMock(),
        sync_run_id="11111111-1111-1111-1111-111111111111",
        connected_org_id=ORG,
        tenant_schema="tenant_1",
        logical_version_seq=7,
    )


def _entity(**normalized) -> EntityForWrite:
    return EntityForWrite(
        external_id=normalized.get("external_id", "ext-1"),
        normalized=normalized,
        presentation={"label": "X", "name": "X"},
        semantic_text="text",
        hash_normalized="h",
    )


class TestEntityWriterStampsOrg:
    def test_column_present_and_adjacent_to_provenance(self) -> None:
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        _batch_insert_new_entities(
            conn, _ctx(), "Object",
            entities=[_entity(Id="001D000000abcDEF")],
            now=MagicMock(),
        )
        sql = str(conn.execute.call_args[0][0])
        assert "connected_org_id" in sql
        # Column order: the partition key sits right after the provenance stamp.
        assert "last_synced_from_org_id, connected_org_id" in sql

    def test_stamps_ctx_org_for_both_provenance_and_partition(self) -> None:
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        ctx = _ctx()
        _batch_insert_new_entities(
            conn, ctx, "Object",
            entities=[_entity(Id="001D000000abcDEF")],
            now=MagicMock(),
        )
        sql = str(conn.execute.call_args[0][0])
        params = conn.execute.call_args[0][1]
        # Single value drives both columns; the VALUES tuple references it twice
        # (last_synced_from_org_id AND connected_org_id) for the one row.
        assert params["org_id"] == ctx.connected_org_id == ORG
        assert sql.count(":org_id") == 2

    def test_multi_row_stamps_each_row(self) -> None:
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        _batch_insert_new_entities(
            conn, _ctx(), "ValidationRule",
            entities=[
                _entity(external_id="a", Id="03dD000000aaaAAA"),
                _entity(external_id="b", Id="03dD000000bbbBBB"),
            ],
            now=MagicMock(),
        )
        sql = str(conn.execute.call_args[0][0])
        # Two rows × two :org_id references each = 4 (no row left unstamped).
        assert sql.count(":org_id") == 4


class TestEdgeWriterStampsOrg:
    def test_column_present_and_stamped(self) -> None:
        conn = MagicMock()
        ctx = _ctx()
        _batch_insert_new_edges(
            conn, ctx, "BELONGS_TO", "STRUCTURAL",
            [("s1", "t1", {}, None), ("s2", "t2", {}, None)],
        )
        conn.execute.assert_called_once()
        sql = str(conn.execute.call_args[0][0])
        params = conn.execute.call_args[0][1]
        assert "connected_org_id" in sql
        assert params["org_id"] == ctx.connected_org_id == ORG
        # One shared :org_id param drives every row's partition column.
        assert ":org_id" in sql

    def test_empty_input_is_noop(self) -> None:
        conn = MagicMock()
        _batch_insert_new_edges(conn, _ctx(), "BELONGS_TO", "STRUCTURAL", [])
        conn.execute.assert_not_called()


class TestLogicalVersionWriterStampsOrg:
    def test_allocate_stamps_connected_org_id(self) -> None:
        from primeqa.sync.engine import SyncEngine
        engine = SyncEngine.__new__(SyncEngine)  # bypass __init__ — pure SQL test
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = (42,)
        seq = engine._allocate_logical_version(conn, "run-9", ORG)
        assert seq == 42
        sql = str(conn.execute.call_args[0][0])
        params = conn.execute.call_args[0][1]
        assert "connected_org_id" in sql
        assert params["connected_org_id"] == ORG
        assert params["sync_run_id"] == "run-9"
