"""Smoke tests for Phase 2 step 1E — summary columns on
validation_rule_details and flow_details.

Per Phase 2 plan §3.5 and decisions D-044, D-045, D-048.

Six scenarios across two parallel classes:
  A. validation_rule_details: NULL roundtrip, full roundtrip, similarity query
  B. flow_details: NULL roundtrip, full roundtrip, similarity query

Both tables receive the same 5 columns + ivfflat index, so the test
classes mirror each other.
"""
import pytest
from sqlalchemy import text


pytestmark = pytest.mark.integration


ENTITIES_PREFIX = "_test_phase2_summary_"
ORGS_PREFIX = "_test_co_summary_"


def make_vec_1536(first_value: float = 0.1, fill: float = 0.0) -> str:
    """Construct a 1536-dim vector literal string for pgvector.

    Salesforce-relevant note: real embeddings are dense; this synthetic
    sparse pattern is fine for write-roundtrip and operator smoke tests.
    """
    parts = [str(first_value)] + [str(fill)] * 1535
    return "[" + ",".join(parts) + "]"


# ----------------------------------------------------------------------
# Cleanup fixture — 3-pass ordered deletion per prompt
# ----------------------------------------------------------------------

@pytest.fixture
def cleanup_summary_test_data(conn_factory):
    """3-pass cleanup respecting FK dependencies:
       1. DELETE detail rows (validation_rule_details, flow_details)
          using prefix on the entity's sf_api_name via JOIN
       2. DELETE entities rows by sf_api_name prefix
       3. DELETE connected_orgs rows by label prefix
    """
    yield  # test runs here

    # Pass 1: detail rows
    with conn_factory() as conn:
        conn.execute(text("""
            DELETE FROM validation_rule_details
            WHERE entity_id IN (
                SELECT id FROM entities WHERE sf_api_name LIKE :p
            )
        """), {"p": f"{ENTITIES_PREFIX}%"})
        conn.execute(text("""
            DELETE FROM flow_details
            WHERE entity_id IN (
                SELECT id FROM entities WHERE sf_api_name LIKE :p
            )
        """), {"p": f"{ENTITIES_PREFIX}%"})

    # Pass 2: entities (CASCADE clears any remaining detail rows;
    # delete order matters within entities — VR/Flow before parent Object
    # because validation_rule_details.object_entity_id has no CASCADE.
    # Pass 1 already cleared detail rows so this should succeed.)
    with conn_factory() as conn:
        conn.execute(text("""
            DELETE FROM entities
            WHERE sf_api_name LIKE :p
              AND entity_type IN ('ValidationRule', 'Flow')
        """), {"p": f"{ENTITIES_PREFIX}%"})
    with conn_factory() as conn:
        conn.execute(text("""
            DELETE FROM entities
            WHERE sf_api_name LIKE :p
              AND entity_type = 'Object'
        """), {"p": f"{ENTITIES_PREFIX}%"})

    # Pass 3: connected_orgs (entities no longer reference them)
    with conn_factory() as conn:
        conn.execute(text("""
            DELETE FROM connected_orgs WHERE label LIKE :p
        """), {"p": f"{ORGS_PREFIX}%"})


# ----------------------------------------------------------------------
# Helpers to create the entity stack each test needs
# ----------------------------------------------------------------------

def _create_org(conn, label_suffix: str):
    """Create a connected_orgs row, return its id."""
    return conn.execute(text("""
        INSERT INTO connected_orgs (org_type, sf_instance_url, label)
        VALUES ('sandbox', 'https://test.sandbox.example.com',
                :label)
        RETURNING id
    """), {"label": f"{ORGS_PREFIX}{label_suffix}"}).scalar()


def _create_entity(conn, entity_type: str, sf_api_name_suffix: str,
                    org_id, valid_from_seq: int):
    """Create an entities row, return its id."""
    return conn.execute(text("""
        INSERT INTO entities (
            entity_type, sf_api_name, valid_from_seq,
            entity_origin, last_synced_from_org_id, last_synced_at
        )
        VALUES (:et, :n, :s, 'sync', :o, NOW())
        RETURNING id
    """), {
        "et": entity_type,
        "n": f"{ENTITIES_PREFIX}{sf_api_name_suffix}",
        "s": valid_from_seq,
        "o": org_id,
    }).scalar()


def _max_version_seq(conn) -> int:
    return conn.execute(text("SELECT MAX(version_seq) FROM logical_versions")).scalar()


# ======================================================================
# A. validation_rule_details
# ======================================================================

class TestValidationRuleSummaryColumns:
    """Summary columns on validation_rule_details."""

    def test_null_summary_insert(self, conn_factory, cleanup_summary_test_data):
        """Scenario A1: insert without setting any of the 5 new columns;
        verify all 5 come back as NULL."""
        with conn_factory() as conn:
            seq = _max_version_seq(conn)
            org_id = _create_org(conn, "vr_null")
            obj_id = _create_entity(conn, "Object", "vr_null_obj", org_id, seq)
            vr_id = _create_entity(conn, "ValidationRule", "vr_null_rule", org_id, seq)
            conn.execute(text("""
                INSERT INTO validation_rule_details (entity_id, object_entity_id, is_active)
                VALUES (:r, :o, TRUE)
            """), {"r": vr_id, "o": obj_id})

        with conn_factory() as conn:
            row = conn.execute(text("""
                SELECT plain_english_summary, summary_model, summary_prompt_version,
                       summary_generated_at, summary_embedding
                FROM validation_rule_details WHERE entity_id = :r
            """), {"r": vr_id}).fetchone()
            assert row is not None
            assert row[0] is None  # plain_english_summary
            assert row[1] is None  # summary_model
            assert row[2] is None  # summary_prompt_version
            assert row[3] is None  # summary_generated_at
            assert row[4] is None  # summary_embedding

    def test_full_summary_roundtrip(self, conn_factory, cleanup_summary_test_data):
        """Scenario A2: insert with all 5 columns set; verify roundtrip."""
        emb = make_vec_1536(0.1, 0.0)
        with conn_factory() as conn:
            seq = _max_version_seq(conn)
            org_id = _create_org(conn, "vr_full")
            obj_id = _create_entity(conn, "Object", "vr_full_obj", org_id, seq)
            vr_id = _create_entity(conn, "ValidationRule", "vr_full_rule", org_id, seq)
            conn.execute(text("""
                INSERT INTO validation_rule_details
                    (entity_id, object_entity_id, is_active,
                     plain_english_summary, summary_model,
                     summary_prompt_version, summary_generated_at,
                     summary_embedding)
                VALUES (:r, :o, TRUE,
                        'Prevents save when amount is null on close.',
                        'anthropic/claude-haiku-4.5',
                        'v1',
                        NOW(),
                        :e ::vector)
            """), {"r": vr_id, "o": obj_id, "e": emb})

        with conn_factory() as conn:
            row = conn.execute(text("""
                SELECT plain_english_summary, summary_model,
                       summary_prompt_version, summary_generated_at IS NOT NULL,
                       summary_embedding IS NOT NULL
                FROM validation_rule_details WHERE entity_id = :r
            """), {"r": vr_id}).fetchone()
            assert row[0] == 'Prevents save when amount is null on close.'
            assert row[1] == 'anthropic/claude-haiku-4.5'
            assert row[2] == 'v1'
            assert row[3] is True   # summary_generated_at not null
            assert row[4] is True   # summary_embedding not null

    def test_similarity_query_executes(self, conn_factory, cleanup_summary_test_data):
        """Scenario A3: insert two rows with different summary_embeddings;
        verify <=> similarity ORDER BY runs without error."""
        emb_a = make_vec_1536(0.9, 0.0)  # near (1,0,...,0)
        emb_b = make_vec_1536(0.0, 0.0)  # far from (1,0,...,0)
        with conn_factory() as conn:
            seq = _max_version_seq(conn)
            org_id = _create_org(conn, "vr_sim")
            obj_id = _create_entity(conn, "Object", "vr_sim_obj", org_id, seq)
            vr_a = _create_entity(conn, "ValidationRule", "vr_sim_a", org_id, seq)
            vr_b = _create_entity(conn, "ValidationRule", "vr_sim_b", org_id, seq)
            conn.execute(text("""
                INSERT INTO validation_rule_details
                    (entity_id, object_entity_id, is_active, summary_embedding)
                VALUES (:r, :o, TRUE, :e ::vector)
            """), {"r": vr_a, "o": obj_id, "e": emb_a})
            conn.execute(text("""
                INSERT INTO validation_rule_details
                    (entity_id, object_entity_id, is_active, summary_embedding)
                VALUES (:r, :o, TRUE, :e ::vector)
            """), {"r": vr_b, "o": obj_id, "e": emb_b})

        with conn_factory() as conn:
            query_vec = make_vec_1536(1.0, 0.0)
            result = conn.execute(text("""
                SELECT entity_id
                FROM validation_rule_details
                WHERE summary_embedding IS NOT NULL
                  AND entity_id IN (:a, :b)
                ORDER BY summary_embedding <=> :q ::vector
                LIMIT 1
            """), {"a": vr_a, "b": vr_b, "q": query_vec}).fetchone()
            assert result is not None  # similarity query returned a result


# ======================================================================
# B. flow_details
# ======================================================================

class TestFlowSummaryColumns:
    """Summary columns on flow_details. Parallel to TestValidationRuleSummaryColumns."""

    def test_null_summary_insert(self, conn_factory, cleanup_summary_test_data):
        """Scenario B1: NULL roundtrip on flow_details."""
        with conn_factory() as conn:
            seq = _max_version_seq(conn)
            org_id = _create_org(conn, "fl_null")
            flow_id = _create_entity(conn, "Flow", "fl_null_flow", org_id, seq)
            conn.execute(text("""
                INSERT INTO flow_details
                    (entity_id, flow_type, is_active)
                VALUES (:f, 'AutoLaunchedFlow', TRUE)
            """), {"f": flow_id})

        with conn_factory() as conn:
            row = conn.execute(text("""
                SELECT plain_english_summary, summary_model, summary_prompt_version,
                       summary_generated_at, summary_embedding
                FROM flow_details WHERE entity_id = :f
            """), {"f": flow_id}).fetchone()
            assert row is not None
            assert all(v is None for v in row), f"expected all NULL, got {row}"

    def test_full_summary_roundtrip(self, conn_factory, cleanup_summary_test_data):
        """Scenario B2: full-summary roundtrip on flow_details."""
        emb = make_vec_1536(0.5, 0.0)
        with conn_factory() as conn:
            seq = _max_version_seq(conn)
            org_id = _create_org(conn, "fl_full")
            flow_id = _create_entity(conn, "Flow", "fl_full_flow", org_id, seq)
            conn.execute(text("""
                INSERT INTO flow_details
                    (entity_id, flow_type, is_active,
                     plain_english_summary, summary_model,
                     summary_prompt_version, summary_generated_at,
                     summary_embedding)
                VALUES (:f, 'RecordAfterSave', TRUE,
                        'Closes opportunity when stage reaches Closed Won.',
                        'anthropic/claude-haiku-4.5',
                        'v1',
                        NOW(),
                        :e ::vector)
            """), {"f": flow_id, "e": emb})

        with conn_factory() as conn:
            row = conn.execute(text("""
                SELECT plain_english_summary, summary_model,
                       summary_prompt_version, summary_generated_at IS NOT NULL,
                       summary_embedding IS NOT NULL
                FROM flow_details WHERE entity_id = :f
            """), {"f": flow_id}).fetchone()
            assert row[0] == 'Closes opportunity when stage reaches Closed Won.'
            assert row[1] == 'anthropic/claude-haiku-4.5'
            assert row[2] == 'v1'
            assert row[3] is True
            assert row[4] is True

    def test_similarity_query_executes(self, conn_factory, cleanup_summary_test_data):
        """Scenario B3: <=> similarity query on flow_details."""
        emb_a = make_vec_1536(0.9, 0.0)
        emb_b = make_vec_1536(0.0, 0.0)
        with conn_factory() as conn:
            seq = _max_version_seq(conn)
            org_id = _create_org(conn, "fl_sim")
            flow_a = _create_entity(conn, "Flow", "fl_sim_a", org_id, seq)
            flow_b = _create_entity(conn, "Flow", "fl_sim_b", org_id, seq)
            conn.execute(text("""
                INSERT INTO flow_details
                    (entity_id, flow_type, is_active, summary_embedding)
                VALUES (:f, 'AutoLaunchedFlow', TRUE, :e ::vector)
            """), {"f": flow_a, "e": emb_a})
            conn.execute(text("""
                INSERT INTO flow_details
                    (entity_id, flow_type, is_active, summary_embedding)
                VALUES (:f, 'Screen', TRUE, :e ::vector)
            """), {"f": flow_b, "e": emb_b})

        with conn_factory() as conn:
            query_vec = make_vec_1536(1.0, 0.0)
            result = conn.execute(text("""
                SELECT entity_id
                FROM flow_details
                WHERE summary_embedding IS NOT NULL
                  AND entity_id IN (:a, :b)
                ORDER BY summary_embedding <=> :q ::vector
                LIMIT 1
            """), {"a": flow_a, "b": flow_b, "q": query_vec}).fetchone()
            assert result is not None
