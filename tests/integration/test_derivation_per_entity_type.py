"""Integration tests for derivation per entity type, against real DB.

Each test inserts an entity + detail row(s) + any auxiliary table rows,
calls supersede_and_derive, then asserts the expected edge set is in
the edges table with correct properties.

Cleanup uses the cleanup_test_entities fixture from conftest.py.
"""
import pytest
from sqlalchemy import text

from primeqa.semantic.derivation import supersede_and_derive


pytestmark = pytest.mark.integration


PREFIX = "_test_drv_"


class TestFieldDerivation:
    def test_simple_field_produces_belongs_to(self, conn_factory, cleanup_test_entities):
        cleanup_test_entities.add(PREFIX)

        with conn_factory() as conn:
            seq = conn.execute(text("SELECT MAX(version_seq) FROM logical_versions")).scalar()
            obj_id = conn.execute(text("""
                INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
                VALUES ('Object', :n, :s, NOW()) RETURNING id
            """), {"n": f"{PREFIX}simple_obj", "s": seq}).scalar()
            field_id = conn.execute(text("""
                INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
                VALUES ('Field', :n, :s, NOW()) RETURNING id
            """), {"n": f"{PREFIX}simple_field", "s": seq}).scalar()
            conn.execute(text("""
                INSERT INTO field_details (entity_id, object_entity_id, field_type,
                    is_custom, is_unique, is_external_id, is_nillable,
                    is_calculated, is_filterable, is_sortable)
                VALUES (:f, :o, 'text', FALSE, FALSE, FALSE, TRUE, FALSE, TRUE, TRUE)
            """), {"f": field_id, "o": obj_id})

        with conn_factory() as conn:
            seq = conn.execute(text("SELECT MAX(version_seq) FROM logical_versions")).scalar()
            counts = supersede_and_derive(field_id, seq, conn)
            assert counts == {"superseded": 0, "inserted": 1, "unchanged": 0}

        with conn_factory() as conn:
            edges = conn.execute(text("""
                SELECT edge_type, target_entity_id, edge_category
                FROM edges WHERE source_entity_id = :s AND valid_to_seq IS NULL
            """), {"s": field_id}).fetchall()
            assert len(edges) == 1
            assert edges[0][0] == "BELONGS_TO"
            assert str(edges[0][1]) == str(obj_id)
            assert edges[0][2] == "STRUCTURAL"

    def test_lookup_field_produces_two_edges(self, conn_factory, cleanup_test_entities):
        cleanup_test_entities.add(PREFIX)

        with conn_factory() as conn:
            seq = conn.execute(text("SELECT MAX(version_seq) FROM logical_versions")).scalar()
            obj_id = conn.execute(text("""
                INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
                VALUES ('Object', :n, :s, NOW()) RETURNING id
            """), {"n": f"{PREFIX}lookup_obj", "s": seq}).scalar()
            ref_obj_id = conn.execute(text("""
                INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
                VALUES ('Object', :n, :s, NOW()) RETURNING id
            """), {"n": f"{PREFIX}lookup_ref_obj", "s": seq}).scalar()
            field_id = conn.execute(text("""
                INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
                VALUES ('Field', :n, :s, NOW()) RETURNING id
            """), {"n": f"{PREFIX}lookup_field", "s": seq}).scalar()
            conn.execute(text("""
                INSERT INTO field_details (entity_id, object_entity_id,
                    references_object_entity_id, field_type,
                    is_custom, is_unique, is_external_id, is_nillable,
                    is_calculated, is_filterable, is_sortable)
                VALUES (:f, :o, :ref, 'lookup', FALSE, FALSE, FALSE, TRUE,
                        FALSE, TRUE, TRUE)
            """), {"f": field_id, "o": obj_id, "ref": ref_obj_id})

        with conn_factory() as conn:
            seq = conn.execute(text("SELECT MAX(version_seq) FROM logical_versions")).scalar()
            counts = supersede_and_derive(field_id, seq, conn)
            assert counts["inserted"] == 2

        with conn_factory() as conn:
            edges = conn.execute(text("""
                SELECT edge_type, target_entity_id FROM edges
                WHERE source_entity_id = :s AND valid_to_seq IS NULL
                ORDER BY edge_type
            """), {"s": field_id}).fetchall()
            edge_types = [e[0] for e in edges]
            assert edge_types == ["BELONGS_TO", "HAS_RELATIONSHIP_TO"]
            # BELONGS_TO -> obj, HAS_RELATIONSHIP_TO -> ref_obj
            assert str(edges[0][1]) == str(obj_id)
            assert str(edges[1][1]) == str(ref_obj_id)


class TestValidationRuleDerivationMultiSource:
    """ValidationRule derivation reads BOTH validation_rule_details
    AND validation_rule_field_refs. This is the multi-source pattern."""

    def test_rule_with_multiple_field_refs(self, conn_factory, cleanup_test_entities):
        cleanup_test_entities.add(PREFIX)

        with conn_factory() as conn:
            seq = conn.execute(text("SELECT MAX(version_seq) FROM logical_versions")).scalar()
            obj_id = conn.execute(text("""
                INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
                VALUES ('Object', :n, :s, NOW()) RETURNING id
            """), {"n": f"{PREFIX}vr_obj", "s": seq}).scalar()
            f1 = conn.execute(text("""
                INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
                VALUES ('Field', :n, :s, NOW()) RETURNING id
            """), {"n": f"{PREFIX}vr_f1_amount", "s": seq}).scalar()
            f2 = conn.execute(text("""
                INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
                VALUES ('Field', :n, :s, NOW()) RETURNING id
            """), {"n": f"{PREFIX}vr_f2_status", "s": seq}).scalar()
            rule_id = conn.execute(text("""
                INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
                VALUES ('ValidationRule', :n, :s, NOW()) RETURNING id
            """), {"n": f"{PREFIX}vr_rule", "s": seq}).scalar()
            conn.execute(text("""
                INSERT INTO validation_rule_details (entity_id, object_entity_id, is_active)
                VALUES (:r, :o, TRUE)
            """), {"r": rule_id, "o": obj_id})
            # 3 refs: f1/read, f1/priorvalue (same field, different type), f2/ischanged
            conn.execute(text("""
                INSERT INTO validation_rule_field_refs
                    (validation_rule_entity_id, field_entity_id, reference_type,
                     is_priorvalue, is_ischanged, is_isnew)
                VALUES
                    (:r, :f1, 'read', FALSE, FALSE, FALSE),
                    (:r, :f1, 'priorvalue', TRUE, FALSE, FALSE),
                    (:r, :f2, 'ischanged', FALSE, TRUE, FALSE)
            """), {"r": rule_id, "f1": f1, "f2": f2})

        with conn_factory() as conn:
            seq = conn.execute(text("SELECT MAX(version_seq) FROM logical_versions")).scalar()
            counts = supersede_and_derive(rule_id, seq, conn)
            # 1 BELONGS_TO + 1 APPLIES_TO + 3 REFERENCES = 5
            assert counts["inserted"] == 5

        with conn_factory() as conn:
            edges = conn.execute(text("""
                SELECT edge_type, target_entity_id, properties FROM edges
                WHERE source_entity_id = :s AND valid_to_seq IS NULL
                ORDER BY edge_type, target_entity_id
            """), {"s": rule_id}).fetchall()
            assert len(edges) == 5
            edge_types = [e[0] for e in edges]
            # APPLIES_TO, BELONGS_TO, REFERENCES x3 (alphabetical)
            assert edge_types.count("REFERENCES") == 3
            assert edge_types.count("BELONGS_TO") == 1
            assert edge_types.count("APPLIES_TO") == 1
            # The three REFERENCES edges have correct reference_type values
            ref_props = [
                e[2]["reference_type"] for e in edges if e[0] == "REFERENCES"
            ]
            assert sorted(ref_props) == ["ischanged", "priorvalue", "read"]


class TestRecordTypeDerivationMultiSource:
    """RecordType derivation reads BOTH record_type_details
    AND record_type_picklist_value_grants. Second multi-source pattern."""

    def test_record_type_with_grants(self, conn_factory, cleanup_test_entities):
        cleanup_test_entities.add(PREFIX)

        with conn_factory() as conn:
            seq = conn.execute(text("SELECT MAX(version_seq) FROM logical_versions")).scalar()
            obj_id = conn.execute(text("""
                INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
                VALUES ('Object', :n, :s, NOW()) RETURNING id
            """), {"n": f"{PREFIX}rt_obj", "s": seq}).scalar()
            pvs_id = conn.execute(text("""
                INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
                VALUES ('PicklistValueSet', :n, :s, NOW()) RETURNING id
            """), {"n": f"{PREFIX}rt_pvs", "s": seq}).scalar()
            pv1 = conn.execute(text("""
                INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
                VALUES ('PicklistValue', :n, :s, NOW()) RETURNING id
            """), {"n": f"{PREFIX}rt_pv1", "s": seq}).scalar()
            pv2 = conn.execute(text("""
                INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
                VALUES ('PicklistValue', :n, :s, NOW()) RETURNING id
            """), {"n": f"{PREFIX}rt_pv2", "s": seq}).scalar()
            rt_id = conn.execute(text("""
                INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
                VALUES ('RecordType', :n, :s, NOW()) RETURNING id
            """), {"n": f"{PREFIX}rt_rt", "s": seq}).scalar()
            conn.execute(text("""
                INSERT INTO record_type_details
                    (entity_id, object_entity_id, is_active, is_master)
                VALUES (:r, :o, TRUE, FALSE)
            """), {"r": rt_id, "o": obj_id})
            conn.execute(text("""
                INSERT INTO record_type_picklist_value_grants
                    (record_type_entity_id, picklist_value_entity_id)
                VALUES (:r, :p1), (:r, :p2)
            """), {"r": rt_id, "p1": pv1, "p2": pv2})

        with conn_factory() as conn:
            seq = conn.execute(text("SELECT MAX(version_seq) FROM logical_versions")).scalar()
            counts = supersede_and_derive(rt_id, seq, conn)
            # 1 BELONGS_TO + 2 CONSTRAINS_PICKLIST_VALUES = 3
            assert counts["inserted"] == 3

        with conn_factory() as conn:
            edges = conn.execute(text("""
                SELECT edge_type, target_entity_id FROM edges
                WHERE source_entity_id = :s AND valid_to_seq IS NULL
            """), {"s": rt_id}).fetchall()
            edge_types = [e[0] for e in edges]
            assert edge_types.count("BELONGS_TO") == 1
            assert edge_types.count("CONSTRAINS_PICKLIST_VALUES") == 2


class TestUserDerivation:
    def test_user_produces_has_profile(self, conn_factory, cleanup_test_entities):
        cleanup_test_entities.add(PREFIX)

        with conn_factory() as conn:
            seq = conn.execute(text("SELECT MAX(version_seq) FROM logical_versions")).scalar()
            prof_id = conn.execute(text("""
                INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
                VALUES ('Profile', :n, :s, NOW()) RETURNING id
            """), {"n": f"{PREFIX}user_prof", "s": seq}).scalar()
            user_id = conn.execute(text("""
                INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
                VALUES ('User', :n, :s, NOW()) RETURNING id
            """), {"n": f"{PREFIX}user_user", "s": seq}).scalar()
            conn.execute(text("""
                INSERT INTO user_details
                    (entity_id, profile_entity_id, is_active, user_type)
                VALUES (:u, :p, TRUE, 'Standard')
            """), {"u": user_id, "p": prof_id})

        with conn_factory() as conn:
            seq = conn.execute(text("SELECT MAX(version_seq) FROM logical_versions")).scalar()
            counts = supersede_and_derive(user_id, seq, conn)
            assert counts["inserted"] == 1

        with conn_factory() as conn:
            edges = conn.execute(text("""
                SELECT edge_type, edge_category FROM edges
                WHERE source_entity_id = :s AND valid_to_seq IS NULL
            """), {"s": user_id}).fetchall()
            assert len(edges) == 1
            assert edges[0][0] == "HAS_PROFILE"
            assert edges[0][1] == "PERMISSION"


class TestFlowDerivation:
    def test_record_triggered_flow_produces_triggers_on(self, conn_factory, cleanup_test_entities):
        cleanup_test_entities.add(PREFIX)

        with conn_factory() as conn:
            seq = conn.execute(text("SELECT MAX(version_seq) FROM logical_versions")).scalar()
            obj_id = conn.execute(text("""
                INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
                VALUES ('Object', :n, :s, NOW()) RETURNING id
            """), {"n": f"{PREFIX}flow_obj", "s": seq}).scalar()
            flow_id = conn.execute(text("""
                INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
                VALUES ('Flow', :n, :s, NOW()) RETURNING id
            """), {"n": f"{PREFIX}flow_record", "s": seq}).scalar()
            conn.execute(text("""
                INSERT INTO flow_details
                    (entity_id, triggers_on_object_entity_id, flow_type,
                     trigger_type, is_active, interpreted_at_capability_level)
                VALUES (:f, :o, 'RecordAfterSave', 'AfterSave', TRUE, 'tier_1')
            """), {"f": flow_id, "o": obj_id})

        with conn_factory() as conn:
            seq = conn.execute(text("SELECT MAX(version_seq) FROM logical_versions")).scalar()
            counts = supersede_and_derive(flow_id, seq, conn)
            assert counts["inserted"] == 1

        with conn_factory() as conn:
            edge = conn.execute(text("""
                SELECT edge_type, edge_category, properties FROM edges
                WHERE source_entity_id = :s AND valid_to_seq IS NULL
            """), {"s": flow_id}).fetchone()
            assert edge[0] == "TRIGGERS_ON"
            assert edge[1] == "BEHAVIOR"
            assert edge[2]["trigger_type"] == "AfterSave"

    def test_screen_flow_produces_no_edges(self, conn_factory, cleanup_test_entities):
        cleanup_test_entities.add(PREFIX)

        with conn_factory() as conn:
            seq = conn.execute(text("SELECT MAX(version_seq) FROM logical_versions")).scalar()
            flow_id = conn.execute(text("""
                INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
                VALUES ('Flow', :n, :s, NOW()) RETURNING id
            """), {"n": f"{PREFIX}flow_screen", "s": seq}).scalar()
            conn.execute(text("""
                INSERT INTO flow_details
                    (entity_id, flow_type, is_active)
                VALUES (:f, 'Screen', TRUE)
            """), {"f": flow_id})

        with conn_factory() as conn:
            seq = conn.execute(text("SELECT MAX(version_seq) FROM logical_versions")).scalar()
            counts = supersede_and_derive(flow_id, seq, conn)
            assert counts["inserted"] == 0

        with conn_factory() as conn:
            edges_count = conn.execute(text("""
                SELECT count(*) FROM edges
                WHERE source_entity_id = :s AND valid_to_seq IS NULL
            """), {"s": flow_id}).scalar()
            assert edges_count == 0


class TestIdempotencyAcrossEntityTypes:
    """Calling supersede_and_derive twice produces no duplicates regardless of entity type."""

    def test_field_idempotent(self, conn_factory, cleanup_test_entities):
        cleanup_test_entities.add(PREFIX)
        with conn_factory() as conn:
            seq = conn.execute(text("SELECT MAX(version_seq) FROM logical_versions")).scalar()
            obj = conn.execute(text("""
                INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
                VALUES ('Object', :n, :s, NOW()) RETURNING id
            """), {"n": f"{PREFIX}idem_obj", "s": seq}).scalar()
            field = conn.execute(text("""
                INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
                VALUES ('Field', :n, :s, NOW()) RETURNING id
            """), {"n": f"{PREFIX}idem_field", "s": seq}).scalar()
            conn.execute(text("""
                INSERT INTO field_details (entity_id, object_entity_id, field_type,
                    is_custom, is_unique, is_external_id, is_nillable,
                    is_calculated, is_filterable, is_sortable)
                VALUES (:f, :o, 'text', FALSE, FALSE, FALSE, TRUE, FALSE, TRUE, TRUE)
            """), {"f": field, "o": obj})

        with conn_factory() as conn:
            seq = conn.execute(text("SELECT MAX(version_seq) FROM logical_versions")).scalar()
            c1 = supersede_and_derive(field, seq, conn)
        with conn_factory() as conn:
            seq = conn.execute(text("SELECT MAX(version_seq) FROM logical_versions")).scalar()
            c2 = supersede_and_derive(field, seq, conn)

        assert c1["inserted"] == 1
        assert c2["unchanged"] == 1
        assert c2["inserted"] == 0

        with conn_factory() as conn:
            edge_count = conn.execute(text("""
                SELECT count(*) FROM edges
                WHERE source_entity_id = :s AND valid_to_seq IS NULL
            """), {"s": field}).scalar()
            assert edge_count == 1
