"""Phase 2 step 1B smoke: connected_orgs CRUD + CHECK constraint.

Verifies the connected_orgs migration (20260430_0010) at the integration
level. Column names and CHECK constraint name are inspection-confirmed
from the production schema, not hand-written from memory (Phase 1 lesson).

Cleanup note: the existing 8-pass cleanup_test_entities fixture in
conftest.py operates on the entities graph (entities/edges/detail tables),
which is a different table family. connected_orgs gets a local fixture
that deletes by label prefix.
"""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


pytestmark = pytest.mark.integration


LABEL_PREFIX = "_test_co_"


@pytest.fixture
def cleanup_test_connected_orgs(conn_factory):
    """Delete any connected_orgs rows whose label starts with LABEL_PREFIX
    after the test runs. connected_orgs has no FK dependencies pointing
    at it from elsewhere in the schema yet (sync_runs FK is added in 1C),
    so a single DELETE is sufficient."""
    yield  # test runs

    with conn_factory() as conn:
        conn.execute(
            text("DELETE FROM connected_orgs WHERE label LIKE :p"),
            {"p": f"{LABEL_PREFIX}%"},
        )


class TestConnectedOrgsBasicInsert:
    def test_sandbox_org_inserts_and_roundtrips(
        self, conn_factory, cleanup_test_connected_orgs,
    ):
        with conn_factory() as conn:
            row_id = conn.execute(text("""
                INSERT INTO connected_orgs (org_type, sf_instance_url, label)
                VALUES ('sandbox', 'https://test-sandbox.my.salesforce.com',
                        :lbl)
                RETURNING id
            """), {"lbl": f"{LABEL_PREFIX}sandbox_basic"}).scalar()

        with conn_factory() as conn:
            row = conn.execute(text("""
                SELECT id, org_type, sf_instance_url, label,
                       sf_org_id, release_label,
                       oauth_access_token, oauth_refresh_token,
                       oauth_token_expires_at, last_sync_completed_at,
                       last_sync_run_id, created_at
                FROM connected_orgs
                WHERE id = :rid
            """), {"rid": row_id}).fetchone()

            assert row is not None
            assert str(row[0]) == str(row_id)
            assert row[1] == 'sandbox'
            assert row[2] == 'https://test-sandbox.my.salesforce.com'
            assert row[3] == f"{LABEL_PREFIX}sandbox_basic"
            # Nullable columns default to NULL when not provided
            assert row[4] is None  # sf_org_id
            assert row[5] is None  # release_label
            assert row[6] is None  # oauth_access_token
            assert row[7] is None  # oauth_refresh_token
            assert row[8] is None  # oauth_token_expires_at
            assert row[9] is None  # last_sync_completed_at
            assert row[10] is None  # last_sync_run_id
            # created_at is NOT NULL with DEFAULT NOW()
            assert row[11] is not None

    def test_developer_org_inserts(
        self, conn_factory, cleanup_test_connected_orgs,
    ):
        with conn_factory() as conn:
            row_id = conn.execute(text("""
                INSERT INTO connected_orgs (org_type, sf_instance_url, label)
                VALUES ('developer', 'https://my-dev-org.my.salesforce.com',
                        :lbl)
                RETURNING id
            """), {"lbl": f"{LABEL_PREFIX}developer_basic"}).scalar()

        with conn_factory() as conn:
            row = conn.execute(text("""
                SELECT org_type FROM connected_orgs WHERE id = :rid
            """), {"rid": row_id}).fetchone()
            assert row[0] == 'developer'


class TestConnectedOrgsCheckConstraint:
    def test_invalid_org_type_rejected(
        self, conn_factory, cleanup_test_connected_orgs,
    ):
        """Invalid org_type values must be rejected by the
        connected_orgs_org_type_known CHECK constraint, raising
        IntegrityError. Verify the transaction is rolled back so a
        subsequent transaction is not poisoned."""
        saw_check_violation = False
        try:
            with conn_factory() as conn:
                conn.execute(text("""
                    INSERT INTO connected_orgs
                        (org_type, sf_instance_url, label)
                    VALUES ('invalid_value',
                            'https://wont-stick.my.salesforce.com', :lbl)
                """), {"lbl": f"{LABEL_PREFIX}invalid_attempt"})
        except IntegrityError as e:
            err = str(e).lower()
            if 'check' in err or 'org_type_known' in err:
                saw_check_violation = True

        assert saw_check_violation, \
            "FAIL: invalid org_type should raise CHECK violation"

        # Verify a subsequent transaction is unaffected (no poisoned tx)
        with conn_factory() as conn:
            valid_id = conn.execute(text("""
                INSERT INTO connected_orgs (org_type, sf_instance_url, label)
                VALUES ('production',
                        'https://prod-org.my.salesforce.com', :lbl)
                RETURNING id
            """), {"lbl": f"{LABEL_PREFIX}post_failure_valid"}).scalar()
            assert valid_id is not None


class TestConnectedOrgsDefaultUuid:
    def test_id_default_uses_gen_random_uuid(
        self, conn_factory, cleanup_test_connected_orgs,
    ):
        """gen_random_uuid() must resolve via search_path (pgcrypto in
        tenant schema per Phase 0 bootstrap). INSERT without explicit
        id should still produce a non-NULL UUID."""
        with conn_factory() as conn:
            row_id = conn.execute(text("""
                INSERT INTO connected_orgs (org_type, sf_instance_url, label)
                VALUES ('scratch', 'https://scratch.my.salesforce.com', :lbl)
                RETURNING id
            """), {"lbl": f"{LABEL_PREFIX}default_uuid"}).scalar()

            assert row_id is not None
            # Returned id should be a UUID-shaped string of length 36
            assert len(str(row_id)) == 36
            assert str(row_id).count('-') == 4
