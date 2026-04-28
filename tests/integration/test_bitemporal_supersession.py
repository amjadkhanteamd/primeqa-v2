"""Integration tests for bitemporal supersession of derived edges.

When an entity is superseded (new version_seq), its old edges should
be marked valid_to_seq = new_seq and new edges inserted with
valid_from_seq = new_seq. History is preserved, never deleted.
"""
import pytest
from sqlalchemy import text

from primeqa.semantic.derivation import supersede_and_derive


pytestmark = pytest.mark.integration


PREFIX = "_test_bitemporal_"


class TestBitemporalSupersession:
    def test_field_superseded_at_new_seq(self, conn_factory, cleanup_test_entities):
        cleanup_test_entities.add(PREFIX)

        # Phase A: insert at seq=N, derive
        with conn_factory() as conn:
            seq_n = conn.execute(text("SELECT MAX(version_seq) FROM logical_versions")).scalar()
            obj = conn.execute(text("""
                INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
                VALUES ('Object', :n, :s, NOW()) RETURNING id
            """), {"n": f"{PREFIX}obj", "s": seq_n}).scalar()
            field = conn.execute(text("""
                INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
                VALUES ('Field', :n, :s, NOW()) RETURNING id
            """), {"n": f"{PREFIX}field", "s": seq_n}).scalar()
            conn.execute(text("""
                INSERT INTO field_details (entity_id, object_entity_id, field_type,
                    is_custom, is_unique, is_external_id, is_nillable,
                    is_calculated, is_filterable, is_sortable)
                VALUES (:f, :o, 'text', FALSE, FALSE, FALSE, TRUE, FALSE, TRUE, TRUE)
            """), {"f": field, "o": obj})

        with conn_factory() as conn:
            counts_a = supersede_and_derive(field, seq_n, conn)
            assert counts_a["inserted"] == 1

        # Phase B: advance to new seq
        with conn_factory() as conn:
            import uuid as _uuid
            seq_n_plus_1 = conn.execute(text("""
                INSERT INTO logical_versions (version_name, version_type, description)
                VALUES (:vn, 'manual_checkpoint', 'bitemporal supersession test')
                RETURNING version_seq
            """), {"vn": f"{PREFIX}new_seq_{_uuid.uuid4().hex[:8]}"}).scalar()

        # Phase C: re-derive at new seq. Old edge superseded, new edge inserted.
        with conn_factory() as conn:
            counts_c = supersede_and_derive(field, seq_n_plus_1, conn)
            assert counts_c["superseded"] == 1
            assert counts_c["inserted"] == 1

        # Verify final state: 1 superseded edge (valid_from=N, valid_to=N+1)
        # + 1 active edge (valid_from=N+1, valid_to=NULL)
        with conn_factory() as conn:
            all_edges = conn.execute(text("""
                SELECT valid_from_seq, valid_to_seq FROM edges
                WHERE source_entity_id = :s
                ORDER BY valid_from_seq
            """), {"s": field}).fetchall()
            assert len(all_edges) == 2
            old_edge, new_edge = all_edges
            assert old_edge[0] == seq_n and old_edge[1] == seq_n_plus_1
            assert new_edge[0] == seq_n_plus_1 and new_edge[1] is None

    def test_supersede_idempotent_on_same_new_seq(
        self, conn_factory, cleanup_test_entities,
    ):
        """Calling supersede_and_derive twice with the same new_seq should
        not double-supersede or duplicate edges."""
        cleanup_test_entities.add(PREFIX)

        # Setup
        with conn_factory() as conn:
            seq_n = conn.execute(text("SELECT MAX(version_seq) FROM logical_versions")).scalar()
            obj = conn.execute(text("""
                INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
                VALUES ('Object', :n, :s, NOW()) RETURNING id
            """), {"n": f"{PREFIX}idem_obj", "s": seq_n}).scalar()
            field = conn.execute(text("""
                INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
                VALUES ('Field', :n, :s, NOW()) RETURNING id
            """), {"n": f"{PREFIX}idem_field", "s": seq_n}).scalar()
            conn.execute(text("""
                INSERT INTO field_details (entity_id, object_entity_id, field_type,
                    is_custom, is_unique, is_external_id, is_nillable,
                    is_calculated, is_filterable, is_sortable)
                VALUES (:f, :o, 'text', FALSE, FALSE, FALSE, TRUE, FALSE, TRUE, TRUE)
            """), {"f": field, "o": obj})

        with conn_factory() as conn:
            supersede_and_derive(field, seq_n, conn)

        with conn_factory() as conn:
            import uuid as _uuid
            seq_n_plus_1 = conn.execute(text("""
                INSERT INTO logical_versions (version_name, version_type, description)
                VALUES (:vn, 'manual_checkpoint', 'idempotent test')
                RETURNING version_seq
            """), {"vn": f"{PREFIX}idem_new_seq_{_uuid.uuid4().hex[:8]}"}).scalar()

        # First supersede: 1 superseded, 1 inserted
        with conn_factory() as conn:
            c1 = supersede_and_derive(field, seq_n_plus_1, conn)
            assert c1 == {"superseded": 1, "inserted": 1, "unchanged": 0}

        # Second supersede on same new_seq: 0 superseded (no active < new_seq),
        # 0 inserted, 1 unchanged
        with conn_factory() as conn:
            c2 = supersede_and_derive(field, seq_n_plus_1, conn)
            assert c2 == {"superseded": 0, "inserted": 0, "unchanged": 1}

        # Edge inventory unchanged: 1 superseded edge + 1 active
        with conn_factory() as conn:
            edge_count = conn.execute(text("""
                SELECT count(*) FROM edges WHERE source_entity_id = :s
            """), {"s": field}).scalar()
            assert edge_count == 2
