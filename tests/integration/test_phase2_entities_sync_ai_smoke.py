"""Phase 2 step 1D smoke: entities ALTER for sync-state + AI primitives.

Verifies migration 20260430_0030. Column names, CHECK constraint names,
FK constraint name, and ivfflat index name are inspection-confirmed.

Cleanup ordering:
  - entities first (their FK to connected_orgs is no-cascade)
  - connected_orgs second (only after referencing entities are gone)

The entities table has a tenant_id CHECK that requires
current_setting('app.tenant_id')::integer to match. get_tenant_connection
sets this via SET LOCAL, so inserts via the fixture-supplied connection
work transparently.
"""
import uuid as _uuid
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


pytestmark = pytest.mark.integration


ENTITY_PREFIX = "_test_phase2_e_"
ORG_PREFIX = "_test_co_e_"


@pytest.fixture
def cleanup_phase2_entities_smoke(conn_factory):
    """Two-pass cleanup: entities first (FK to connected_orgs no-cascade),
    then connected_orgs."""
    yield

    with conn_factory() as conn:
        conn.execute(
            text("DELETE FROM entities WHERE sf_api_name LIKE :p"),
            {"p": f"{ENTITY_PREFIX}%"},
        )

    with conn_factory() as conn:
        conn.execute(
            text("DELETE FROM connected_orgs WHERE label LIKE :p"),
            {"p": f"{ORG_PREFIX}%"},
        )


def _make_vec_1536(first_value: float = 1.0, fill: float = 0.0) -> str:
    """Build a 1536-dim vector literal string for pgvector parsing."""
    parts = [str(float(first_value))] + [str(float(fill))] * 1535
    return "[" + ",".join(parts) + "]"


def _insert_entity(conn, **overrides):
    """Helper: insert an entities row with sane defaults; allow overrides
    for columns under test. Returns the id."""
    seq = conn.execute(text(
        "SELECT MAX(version_seq) FROM logical_versions"
    )).scalar()

    cols = {
        "entity_type": overrides.pop("entity_type", "Object"),
        "sf_api_name": overrides.pop("sf_api_name", f"{ENTITY_PREFIX}default"),
        "valid_from_seq": seq,
        "last_synced_at": "NOW()",  # use SQL function via direct SQL string
    }
    cols.update(overrides)

    # Build column list and value list. NOW() inserted as raw SQL.
    column_names = list(cols.keys())
    placeholders = []
    params = {}
    for k, v in cols.items():
        if v == "NOW()":
            placeholders.append("NOW()")
        else:
            placeholders.append(f":{k}")
            params[k] = v
    sql = (
        "INSERT INTO entities ("
        + ", ".join(column_names)
        + ") VALUES ("
        + ", ".join(placeholders)
        + ") RETURNING id"
    )
    return conn.execute(text(sql), params).scalar()


class TestEntityOriginDefault:
    """A. Default entity_origin behavior."""

    def test_default_entity_origin_is_sync(
        self, conn_factory, cleanup_phase2_entities_smoke,
    ):
        with conn_factory() as conn:
            row_id = _insert_entity(
                conn, sf_api_name=f"{ENTITY_PREFIX}A1_default",
            )
        with conn_factory() as conn:
            origin = conn.execute(text(
                "SELECT entity_origin FROM entities WHERE id = :r"
            ), {"r": row_id}).scalar()
            assert origin == "sync"


class TestEntityOriginCheckEnum:
    """B. entity_origin CHECK constraint enum."""

    def test_requirements_origin_accepted(
        self, conn_factory, cleanup_phase2_entities_smoke,
    ):
        with conn_factory() as conn:
            row_id = _insert_entity(
                conn,
                sf_api_name=f"{ENTITY_PREFIX}B2_requirements",
                entity_origin="requirements",
            )
        with conn_factory() as conn:
            origin = conn.execute(text(
                "SELECT entity_origin FROM entities WHERE id = :r"
            ), {"r": row_id}).scalar()
            assert origin == "requirements"

    def test_manual_curation_origin_accepted(
        self, conn_factory, cleanup_phase2_entities_smoke,
    ):
        with conn_factory() as conn:
            row_id = _insert_entity(
                conn,
                sf_api_name=f"{ENTITY_PREFIX}B3_manual",
                entity_origin="manual_curation",
            )
        with conn_factory() as conn:
            origin = conn.execute(text(
                "SELECT entity_origin FROM entities WHERE id = :r"
            ), {"r": row_id}).scalar()
            assert origin == "manual_curation"

    def test_invalid_origin_rejected_then_recovers(
        self, conn_factory, cleanup_phase2_entities_smoke,
    ):
        saw_check = False
        try:
            with conn_factory() as conn:
                _insert_entity(
                    conn,
                    sf_api_name=f"{ENTITY_PREFIX}B4_invalid",
                    entity_origin="invalid_value",
                )
        except IntegrityError as e:
            err = str(e).lower()
            if "check" in err or "entity_origin_known" in err:
                saw_check = True
        assert saw_check, "FAIL: invalid entity_origin should violate CHECK"

        # Recovery: subsequent tx must work after aborted-tx rollback.
        with conn_factory() as conn:
            row_id = _insert_entity(
                conn,
                sf_api_name=f"{ENTITY_PREFIX}B4_recovery",
                entity_origin="sync",
            )
            assert row_id is not None


class TestHashOnlyForSyncCheck:
    """C. Conditional last_seed_hash CHECK."""

    def test_sync_with_hash_accepted(
        self, conn_factory, cleanup_phase2_entities_smoke,
    ):
        with conn_factory() as conn:
            row_id = _insert_entity(
                conn,
                sf_api_name=f"{ENTITY_PREFIX}C5_sync_hash",
                entity_origin="sync",
                last_seed_hash="0" * 64,
            )
        with conn_factory() as conn:
            stored = conn.execute(text(
                "SELECT last_seed_hash FROM entities WHERE id = :r"
            ), {"r": row_id}).scalar()
            assert stored == "0" * 64

    def test_requirements_with_null_hash_accepted(
        self, conn_factory, cleanup_phase2_entities_smoke,
    ):
        with conn_factory() as conn:
            row_id = _insert_entity(
                conn,
                sf_api_name=f"{ENTITY_PREFIX}C6_req_nullhash",
                entity_origin="requirements",
                last_seed_hash=None,
            )
            assert row_id is not None

    def test_requirements_with_hash_rejected(
        self, conn_factory, cleanup_phase2_entities_smoke,
    ):
        saw_check = False
        try:
            with conn_factory() as conn:
                _insert_entity(
                    conn,
                    sf_api_name=f"{ENTITY_PREFIX}C7_req_with_hash",
                    entity_origin="requirements",
                    last_seed_hash="0" * 64,
                )
        except IntegrityError as e:
            err = str(e).lower()
            if "check" in err or "hash_only_for_sync" in err:
                saw_check = True
        assert saw_check, \
            "FAIL: non-sync entity_origin with last_seed_hash should violate CHECK"


class TestSyncedFromOnlyForSyncCheck:
    """D. Conditional last_synced_from_org_id CHECK."""

    def _make_org(self, conn, suffix: str):
        return conn.execute(text("""
            INSERT INTO connected_orgs (org_type, sf_instance_url, label)
            VALUES ('sandbox', 'https://test.my.salesforce.com', :lbl)
            RETURNING id
        """), {"lbl": f"{ORG_PREFIX}{suffix}"}).scalar()

    def test_sync_with_synced_from_accepted(
        self, conn_factory, cleanup_phase2_entities_smoke,
    ):
        with conn_factory() as conn:
            org_id = self._make_org(conn, "D8_sync_with_org")
            row_id = _insert_entity(
                conn,
                sf_api_name=f"{ENTITY_PREFIX}D8_sync_with_org",
                entity_origin="sync",
                last_synced_from_org_id=org_id,
            )
        with conn_factory() as conn:
            stored = conn.execute(text(
                "SELECT last_synced_from_org_id FROM entities WHERE id = :r"
            ), {"r": row_id}).scalar()
            assert str(stored) == str(org_id)

    def test_requirements_with_null_synced_from_accepted(
        self, conn_factory, cleanup_phase2_entities_smoke,
    ):
        with conn_factory() as conn:
            row_id = _insert_entity(
                conn,
                sf_api_name=f"{ENTITY_PREFIX}D9_req_null",
                entity_origin="requirements",
                last_synced_from_org_id=None,
            )
            assert row_id is not None

    def test_manual_curation_with_synced_from_rejected(
        self, conn_factory, cleanup_phase2_entities_smoke,
    ):
        with conn_factory() as conn:
            org_id = self._make_org(conn, "D10_manual_with_org")

        saw_check = False
        try:
            with conn_factory() as conn:
                _insert_entity(
                    conn,
                    sf_api_name=f"{ENTITY_PREFIX}D10_manual_with_org",
                    entity_origin="manual_curation",
                    last_synced_from_org_id=org_id,
                )
        except IntegrityError as e:
            err = str(e).lower()
            if "check" in err or "synced_from_only_for_sync" in err:
                saw_check = True
        assert saw_check, \
            "FAIL: manual_curation with last_synced_from_org_id should violate CHECK"


class TestSyncedFromOrgFK:
    """E. last_synced_from_org_id FK to connected_orgs."""

    def test_invalid_synced_from_org_id_rejected(
        self, conn_factory, cleanup_phase2_entities_smoke,
    ):
        bogus = _uuid.uuid4()
        saw_fk = False
        try:
            with conn_factory() as conn:
                _insert_entity(
                    conn,
                    sf_api_name=f"{ENTITY_PREFIX}E11_bogus_fk",
                    entity_origin="sync",
                    last_synced_from_org_id=str(bogus),
                )
        except IntegrityError as e:
            err = str(e).lower()
            if "foreign key" in err or "synced_from_org_id_fkey" in err:
                saw_fk = True
        assert saw_fk, \
            "FAIL: bogus last_synced_from_org_id should violate FK"


class TestAIPrimitivesRoundtrip:
    """F. AI primitive columns roundtrip."""

    def test_full_ai_columns_roundtrip(
        self, conn_factory, cleanup_phase2_entities_smoke,
    ):
        vec_str = _make_vec_1536(first_value=0.5, fill=0.0)
        with conn_factory() as conn:
            row_id = _insert_entity(
                conn,
                sf_api_name=f"{ENTITY_PREFIX}F12_ai_roundtrip",
                entity_origin="sync",
                semantic_text="test text for embedding",
                embedding=vec_str,
                embedding_model="openai/text-embedding-3-small",
                embedding_generated_at="NOW()",
            )

        with conn_factory() as conn:
            row = conn.execute(text("""
                SELECT semantic_text, embedding_model, embedding_generated_at,
                       embedding IS NULL AS embedding_is_null,
                       (embedding <=> CAST(:vec AS vector))::float AS self_distance
                FROM entities WHERE id = :r
            """), {"r": row_id, "vec": vec_str}).fetchone()
            assert row[0] == "test text for embedding"
            assert row[1] == "openai/text-embedding-3-small"
            assert row[2] is not None
            assert row[3] is False  # embedding column populated
            # Cosine distance to self is ~0 (allowing small float tolerance)
            assert row[4] is not None
            assert abs(row[4]) < 1e-6


class TestVectorSimilarityQuery:
    """G. Vector similarity query operator works end-to-end."""

    def test_similarity_query_returns_result(
        self, conn_factory, cleanup_phase2_entities_smoke,
    ):
        vec_a = _make_vec_1536(first_value=1.0, fill=0.0)
        vec_b = _make_vec_1536(first_value=0.0, fill=1.0 / 1536)
        query_vec = _make_vec_1536(first_value=0.99, fill=0.01)

        with conn_factory() as conn:
            id_a = _insert_entity(
                conn,
                sf_api_name=f"{ENTITY_PREFIX}G13_similar_a",
                entity_origin="sync",
                embedding=vec_a,
            )
            id_b = _insert_entity(
                conn,
                sf_api_name=f"{ENTITY_PREFIX}G13_similar_b",
                entity_origin="sync",
                embedding=vec_b,
            )

        with conn_factory() as conn:
            row = conn.execute(text("""
                SELECT id FROM entities
                WHERE sf_api_name LIKE :p AND embedding IS NOT NULL
                ORDER BY embedding <=> CAST(:q AS vector)
                LIMIT 1
            """), {"p": f"{ENTITY_PREFIX}G13_%", "q": query_vec}).fetchone()
            # The query exercising the <=> operator must return without error
            # and yield one of the inserted ids.
            assert row is not None
            assert str(row[0]) in (str(id_a), str(id_b))
