"""Integration tests for verify_derivation_integrity."""
import pytest
from sqlalchemy import text

from primeqa.semantic.derivation import (
    supersede_and_derive,
    verify_derivation_integrity,
)


pytestmark = pytest.mark.integration


PREFIX = "_test_verify_"


class TestVerifyIntegrity:
    def test_clean_state_no_discrepancies(self, conn_factory, cleanup_test_entities):
        cleanup_test_entities.add(PREFIX)

        with conn_factory() as conn:
            seq = conn.execute(text("SELECT MAX(version_seq) FROM logical_versions")).scalar()
            obj = conn.execute(text("""
                INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
                VALUES ('Object', :n, :s, NOW()) RETURNING id
            """), {"n": f"{PREFIX}clean_obj", "s": seq}).scalar()
            field = conn.execute(text("""
                INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
                VALUES ('Field', :n, :s, NOW()) RETURNING id
            """), {"n": f"{PREFIX}clean_field", "s": seq}).scalar()
            conn.execute(text("""
                INSERT INTO field_details (entity_id, object_entity_id, field_type,
                    is_custom, is_unique, is_external_id, is_nillable,
                    is_calculated, is_filterable, is_sortable)
                VALUES (:f, :o, 'text', FALSE, FALSE, FALSE, TRUE, FALSE, TRUE, TRUE)
            """), {"f": field, "o": obj})

        with conn_factory() as conn:
            seq = conn.execute(text("SELECT MAX(version_seq) FROM logical_versions")).scalar()
            supersede_and_derive(field, seq, conn)

        with conn_factory() as conn:
            disc = verify_derivation_integrity(conn)
            our_disc = [d for d in disc if d["entity_id"] == str(field)]
            assert len(our_disc) == 0

    def test_missing_edge_is_detected(self, conn_factory, cleanup_test_entities):
        """If a detail row exists but its derived edge is missing, verify
        catches it with kind='missing'."""
        cleanup_test_entities.add(PREFIX)

        with conn_factory() as conn:
            seq = conn.execute(text("SELECT MAX(version_seq) FROM logical_versions")).scalar()
            obj = conn.execute(text("""
                INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
                VALUES ('Object', :n, :s, NOW()) RETURNING id
            """), {"n": f"{PREFIX}miss_obj", "s": seq}).scalar()
            field = conn.execute(text("""
                INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
                VALUES ('Field', :n, :s, NOW()) RETURNING id
            """), {"n": f"{PREFIX}miss_field", "s": seq}).scalar()
            conn.execute(text("""
                INSERT INTO field_details (entity_id, object_entity_id, field_type,
                    is_custom, is_unique, is_external_id, is_nillable,
                    is_calculated, is_filterable, is_sortable)
                VALUES (:f, :o, 'text', FALSE, FALSE, FALSE, TRUE, FALSE, TRUE, TRUE)
            """), {"f": field, "o": obj})

        # Deliberately do NOT call supersede_and_derive — leave the edge missing

        with conn_factory() as conn:
            disc = verify_derivation_integrity(conn)
            our_disc = [
                d for d in disc
                if d["entity_id"] == str(field) and d["kind"] == "missing"
            ]
            assert len(our_disc) == 1
            assert our_disc[0]["edge_type"] == "BELONGS_TO"
            assert our_disc[0]["target_entity_id"] == str(obj)

    def test_extra_edge_is_detected(self, conn_factory, cleanup_test_entities):
        """If an edge exists in DB without source-row support, verify
        catches it with kind='extra'."""
        cleanup_test_entities.add(PREFIX)

        with conn_factory() as conn:
            seq = conn.execute(text("SELECT MAX(version_seq) FROM logical_versions")).scalar()
            obj = conn.execute(text("""
                INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
                VALUES ('Object', :n, :s, NOW()) RETURNING id
            """), {"n": f"{PREFIX}extra_obj", "s": seq}).scalar()
            spurious_target = conn.execute(text("""
                INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
                VALUES ('Object', :n, :s, NOW()) RETURNING id
            """), {"n": f"{PREFIX}extra_spurious", "s": seq}).scalar()
            field = conn.execute(text("""
                INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
                VALUES ('Field', :n, :s, NOW()) RETURNING id
            """), {"n": f"{PREFIX}extra_field", "s": seq}).scalar()
            conn.execute(text("""
                INSERT INTO field_details (entity_id, object_entity_id, field_type,
                    is_custom, is_unique, is_external_id, is_nillable,
                    is_calculated, is_filterable, is_sortable)
                VALUES (:f, :o, 'text', FALSE, FALSE, FALSE, TRUE, FALSE, TRUE, TRUE)
            """), {"f": field, "o": obj})

        with conn_factory() as conn:
            seq = conn.execute(text("SELECT MAX(version_seq) FROM logical_versions")).scalar()
            supersede_and_derive(field, seq, conn)

        # Now manually insert a spurious edge that derivation would not produce
        # (HAS_RELATIONSHIP_TO from this Field to a different Object — but the
        # field_details row has references_object_entity_id=NULL).
        with conn_factory() as conn:
            seq = conn.execute(text("SELECT MAX(version_seq) FROM logical_versions")).scalar()
            conn.execute(text("""
                INSERT INTO edges
                    (source_entity_id, target_entity_id, edge_type,
                     edge_category, properties, valid_from_seq)
                VALUES (:s, :t, 'HAS_RELATIONSHIP_TO', 'STRUCTURAL',
                        CAST('{}' AS JSONB), :seq)
            """), {"s": field, "t": spurious_target, "seq": seq})

        with conn_factory() as conn:
            disc = verify_derivation_integrity(conn)
            our_extras = [
                d for d in disc
                if d["entity_id"] == str(field) and d["kind"] == "extra"
            ]
            assert len(our_extras) == 1
            assert our_extras[0]["edge_type"] == "HAS_RELATIONSHIP_TO"
            assert our_extras[0]["target_entity_id"] == str(spurious_target)
