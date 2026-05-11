"""Phase 2 step 1C smoke: sync_runs CRUD + 2 CHECK constraints + mutual FK
to connected_orgs.

Verifies migration 20260430_0020. Column names + CHECK constraint names +
FK constraint name are inspection-confirmed from the production schema
(Phase 1 lesson: don't write from memory).

Cleanup pattern:
  Mutual FK between connected_orgs.last_sync_run_id and sync_runs.id
  requires care. Order:
    1. UPDATE connected_orgs SET last_sync_run_id = NULL for test rows
    2. DELETE FROM sync_runs WHERE source_org_id IN test connected_orgs
    3. DELETE FROM connected_orgs WHERE label LIKE prefix
"""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


pytestmark = pytest.mark.integration


LABEL_PREFIX = "_test_co_sr_"


@pytest.fixture
def cleanup_test_sync_runs(conn_factory):
    """Mutual FK cleanup: NULL connected_orgs.last_sync_run_id for test
    rows, then delete sync_runs whose source_org_id points at test
    connected_orgs, then delete the connected_orgs themselves."""
    yield

    # Pass 1: break the connected_orgs -> sync_runs reference for test rows
    with conn_factory() as conn:
        conn.execute(
            text("""
                UPDATE connected_orgs SET last_sync_run_id = NULL
                WHERE label LIKE :p
            """),
            {"p": f"{LABEL_PREFIX}%"},
        )

    # Pass 2: delete sync_runs rows whose source_org_id is a test connected_org
    with conn_factory() as conn:
        conn.execute(
            text("""
                DELETE FROM sync_runs WHERE source_org_id IN (
                    SELECT id FROM connected_orgs WHERE label LIKE :p
                )
            """),
            {"p": f"{LABEL_PREFIX}%"},
        )

    # Pass 3: delete connected_orgs
    with conn_factory() as conn:
        conn.execute(
            text("DELETE FROM connected_orgs WHERE label LIKE :p"),
            {"p": f"{LABEL_PREFIX}%"},
        )


def _create_test_org(conn, label_suffix: str):
    """Helper: insert a connected_orgs row, return its id."""
    return conn.execute(text("""
        INSERT INTO connected_orgs (org_type, sf_instance_url, label)
        VALUES ('sandbox', 'https://test.my.salesforce.com', :lbl)
        RETURNING id
    """), {"lbl": f"{LABEL_PREFIX}{label_suffix}"}).scalar()


class TestSyncRunsBasicLifecycle:
    """(a) Insert running, (b) update to success, (c) update to partial_success."""

    def test_insert_running_status(
        self, conn_factory, cleanup_test_sync_runs,
    ):
        with conn_factory() as conn:
            org_id = _create_test_org(conn, "a_running")
            run_id = conn.execute(text("""
                INSERT INTO sync_runs (source_org_id, status)
                VALUES (:o, 'running')
                RETURNING id
            """), {"o": org_id}).scalar()

        with conn_factory() as conn:
            row = conn.execute(text("""
                SELECT id, source_org_id, status, started_at, completed_at,
                       entities_inserted, summaries_failed,
                       logical_version_seq, error_message
                FROM sync_runs WHERE id = :r
            """), {"r": run_id}).fetchone()
            assert row is not None
            assert str(row[0]) == str(run_id)
            assert str(row[1]) == str(org_id)
            assert row[2] == 'running'
            assert row[3] is not None  # started_at DEFAULT NOW()
            assert row[4] is None  # completed_at NULL while running
            assert row[5] == 0  # entities_inserted DEFAULT 0
            assert row[6] == 0  # summaries_failed DEFAULT 0
            assert row[7] is None  # logical_version_seq nullable
            assert row[8] is None  # error_message nullable

    def test_update_to_success(
        self, conn_factory, cleanup_test_sync_runs,
    ):
        with conn_factory() as conn:
            org_id = _create_test_org(conn, "b_success")
            run_id = conn.execute(text("""
                INSERT INTO sync_runs (source_org_id, status)
                VALUES (:o, 'running')
                RETURNING id
            """), {"o": org_id}).scalar()

        with conn_factory() as conn:
            conn.execute(text("""
                UPDATE sync_runs
                SET status = 'success',
                    completed_at = NOW(),
                    entities_inserted = 42,
                    embeddings_generated = 42,
                    summaries_generated = 5
                WHERE id = :r
            """), {"r": run_id})

        with conn_factory() as conn:
            row = conn.execute(text("""
                SELECT status, completed_at, entities_inserted,
                       embeddings_generated, summaries_generated
                FROM sync_runs WHERE id = :r
            """), {"r": run_id}).fetchone()
            assert row[0] == 'success'
            assert row[1] is not None  # completed_at populated
            assert row[2] == 42
            assert row[3] == 42
            assert row[4] == 5

    def test_update_to_partial_success(
        self, conn_factory, cleanup_test_sync_runs,
    ):
        """D-048: partial_success means structural sync committed but
        AI primitives failed. summaries_failed > 0 is the canonical shape."""
        with conn_factory() as conn:
            org_id = _create_test_org(conn, "c_partial")
            run_id = conn.execute(text("""
                INSERT INTO sync_runs (source_org_id, status)
                VALUES (:o, 'running')
                RETURNING id
            """), {"o": org_id}).scalar()

        with conn_factory() as conn:
            conn.execute(text("""
                UPDATE sync_runs
                SET status = 'partial_success',
                    completed_at = NOW(),
                    entities_inserted = 100,
                    summaries_generated = 8,
                    summaries_failed = 3,
                    error_message = 'LLM rate-limited; 3 summaries deferred'
                WHERE id = :r
            """), {"r": run_id})

        with conn_factory() as conn:
            row = conn.execute(text("""
                SELECT status, summaries_generated, summaries_failed,
                       error_message
                FROM sync_runs WHERE id = :r
            """), {"r": run_id}).fetchone()
            assert row[0] == 'partial_success'
            assert row[1] == 8
            assert row[2] == 3
            assert 'LLM rate-limited' in row[3]


class TestSyncRunsStatusInvariants:
    """(d) running with timestamp rejected, (e) terminal without timestamp rejected."""

    def test_running_with_completed_at_rejected(
        self, conn_factory, cleanup_test_sync_runs,
    ):
        """The sync_runs_completion_implies_terminal CHECK requires
        status='running' to have completed_at IS NULL."""
        with conn_factory() as conn:
            org_id = _create_test_org(conn, "d_running_with_ts")

        saw_check = False
        try:
            with conn_factory() as conn:
                conn.execute(text("""
                    INSERT INTO sync_runs
                        (source_org_id, status, completed_at)
                    VALUES (:o, 'running', NOW())
                """), {"o": org_id})
        except IntegrityError as e:
            err = str(e).lower()
            if 'check' in err or 'completion_implies_terminal' in err:
                saw_check = True
        assert saw_check, \
            "FAIL: 'running' with non-NULL completed_at should violate CHECK"

        # Recovery check: a fresh transaction should still work after
        # the aborted-tx rolled back.
        with conn_factory() as conn:
            run_id = conn.execute(text("""
                INSERT INTO sync_runs (source_org_id, status)
                VALUES (:o, 'running')
                RETURNING id
            """), {"o": org_id}).scalar()
            assert run_id is not None

    def test_terminal_without_completed_at_rejected(
        self, conn_factory, cleanup_test_sync_runs,
    ):
        """Terminal statuses (success, partial_success, failure) must
        have completed_at IS NOT NULL."""
        with conn_factory() as conn:
            org_id = _create_test_org(conn, "e_term_without_ts")

        saw_check = False
        try:
            with conn_factory() as conn:
                conn.execute(text("""
                    INSERT INTO sync_runs
                        (source_org_id, status)
                    VALUES (:o, 'success')
                """), {"o": org_id})
        except IntegrityError as e:
            err = str(e).lower()
            if 'check' in err or 'completion_implies_terminal' in err:
                saw_check = True
        assert saw_check, \
            "FAIL: 'success' with NULL completed_at should violate CHECK"

        # Recovery check
        with conn_factory() as conn:
            run_id = conn.execute(text("""
                INSERT INTO sync_runs
                    (source_org_id, status, completed_at)
                VALUES (:o, 'success', NOW())
                RETURNING id
            """), {"o": org_id}).scalar()
            assert run_id is not None


class TestConnectedOrgsLastSyncRunFK:
    """(f) valid FK accepts, (g) invalid FK rejects."""

    def test_valid_last_sync_run_id_accepted(
        self, conn_factory, cleanup_test_sync_runs,
    ):
        with conn_factory() as conn:
            org_id = _create_test_org(conn, "f_valid_fk")
            run_id = conn.execute(text("""
                INSERT INTO sync_runs (source_org_id, status)
                VALUES (:o, 'running')
                RETURNING id
            """), {"o": org_id}).scalar()

        with conn_factory() as conn:
            conn.execute(text("""
                UPDATE connected_orgs SET last_sync_run_id = :r
                WHERE id = :o
            """), {"r": run_id, "o": org_id})

        with conn_factory() as conn:
            stored = conn.execute(text("""
                SELECT last_sync_run_id FROM connected_orgs WHERE id = :o
            """), {"o": org_id}).scalar()
            assert str(stored) == str(run_id)

    def test_invalid_last_sync_run_id_rejected(
        self, conn_factory, cleanup_test_sync_runs,
    ):
        """A UUID that doesn't exist in sync_runs.id must be rejected
        by the connected_orgs_last_sync_run_id_fkey FK."""
        import uuid as _uuid

        with conn_factory() as conn:
            org_id = _create_test_org(conn, "g_invalid_fk")
            bogus_run_id = _uuid.uuid4()  # not in sync_runs

        saw_fk = False
        try:
            with conn_factory() as conn:
                conn.execute(text("""
                    UPDATE connected_orgs SET last_sync_run_id = :r
                    WHERE id = :o
                """), {"r": str(bogus_run_id), "o": org_id})
        except IntegrityError as e:
            err = str(e).lower()
            if 'foreign key' in err or 'last_sync_run_id_fkey' in err:
                saw_fk = True
        assert saw_fk, \
            "FAIL: bogus last_sync_run_id should violate FK"

        # Recovery: the connected_orgs row should still exist with
        # last_sync_run_id NULL (the failed UPDATE rolled back).
        with conn_factory() as conn:
            stored = conn.execute(text("""
                SELECT last_sync_run_id FROM connected_orgs WHERE id = :o
            """), {"o": org_id}).scalar()
            assert stored is None


class TestSyncRunsLogicalVersionSeqFK:
    """(h) NULL accepted; valid bigint version_seq accepted."""

    def test_logical_version_seq_null_accepted(
        self, conn_factory, cleanup_test_sync_runs,
    ):
        with conn_factory() as conn:
            org_id = _create_test_org(conn, "h_lv_null")
            run_id = conn.execute(text("""
                INSERT INTO sync_runs
                    (source_org_id, status, logical_version_seq)
                VALUES (:o, 'running', NULL)
                RETURNING id
            """), {"o": org_id}).scalar()
            assert run_id is not None

    def test_logical_version_seq_valid_accepted(
        self, conn_factory, cleanup_test_sync_runs,
    ):
        """Reference an existing logical_versions.version_seq.
        version_seq is BIGINT; passing a Python int works."""
        with conn_factory() as conn:
            existing_seq = conn.execute(text("""
                SELECT MAX(version_seq) FROM logical_versions
            """)).scalar()
            assert existing_seq is not None, \
                "Test precondition: at least one logical_versions row"

        with conn_factory() as conn:
            org_id = _create_test_org(conn, "h_lv_valid")
            run_id = conn.execute(text("""
                INSERT INTO sync_runs
                    (source_org_id, status, logical_version_seq)
                VALUES (:o, 'running', :s)
                RETURNING id
            """), {"o": org_id, "s": existing_seq}).scalar()

        with conn_factory() as conn:
            stored = conn.execute(text("""
                SELECT logical_version_seq FROM sync_runs WHERE id = :r
            """), {"r": run_id}).scalar()
            assert stored == existing_seq
