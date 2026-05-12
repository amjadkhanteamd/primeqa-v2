"""Live integration test for Object + PicklistValueSet phases.

End-to-end: SyncEngine.run_sync() runs all 12 phases for a fresh
connected_org. Object + PicklistValueSet phases materialize
entities; other phases are no-ops returning empty results.
Verifies:
  - entities table populated with Object rows
  - ai_enrichment_queue populated (2 rows per entity: embedding + summary)
  - sync_run state advances (phase='enrichment', last_completed_phase='Flow')
  - logical_versions row allocated per sync_run
  - second sync reports all entities as unchanged (hash match)
  - second sync doesn't grow the queue
  - PicklistValueSet phase ran cleanly (sandbox has 0 GVSes per
    sf_client.fetch_global_value_sets docstring; exercises the
    empty path)

Cleanup: deletes all rows referencing the test connected_org's id
(FK-aware: queue → entities → sync_runs back-ref → logical_versions
→ sync_runs → connected_orgs).

Gated on @pytest.mark.sandbox; requires SF_* env vars + DATABASE_URL.
"""
from __future__ import annotations

import os
import uuid

import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()


REQUIRED_ENV = (
    "SF_INSTANCE_URL", "SF_CLIENT_ID", "SF_CLIENT_SECRET",
    "SF_REFRESH_TOKEN", "DATABASE_URL",
)
HAS_CREDS = all(os.environ.get(k) for k in REQUIRED_ENV)

pytestmark = pytest.mark.sandbox

TENANT_SCHEMA = "tenant_1"
TENANT_ID = 1


@pytest.fixture
def live_sf_client():
    if not HAS_CREDS:
        missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
        pytest.skip(f"Required env not configured (missing: {missing})")
    from primeqa.integrations.sf_client import SalesforceClient
    with SalesforceClient(
        instance_url=os.environ["SF_INSTANCE_URL"],
        client_id=os.environ["SF_CLIENT_ID"],
        client_secret=os.environ["SF_CLIENT_SECRET"],
        refresh_token=os.environ["SF_REFRESH_TOKEN"],
    ) as c:
        yield c


@pytest.fixture
def db_engine():
    if not HAS_CREDS:
        pytest.skip("DATABASE_URL not configured")
    eng = create_engine(os.environ["DATABASE_URL"])
    yield eng
    eng.dispose()


@pytest.fixture
def test_org(db_engine):
    """Create a temporary connected_orgs row for this test; yield
    its id; clean up all referenced data afterward."""
    label = f"_test_sync_object_phase_{uuid.uuid4().hex[:8]}"

    # Setup: INSERT connected_orgs row
    with db_engine.begin() as conn:
        conn.execute(text(f'SET LOCAL search_path TO "{TENANT_SCHEMA}", public'))
        conn.execute(text("SET LOCAL app.tenant_id = :t"), {"t": str(TENANT_ID)})
        row = conn.execute(text("""
            INSERT INTO connected_orgs
                (org_type, sf_instance_url, label)
            VALUES
                ('sandbox', :url, :label)
            RETURNING id
        """), {
            "url": os.environ["SF_INSTANCE_URL"],
            "label": label,
        }).fetchone()
        org_id = str(row[0])

    yield org_id

    # Cleanup: FK-aware order
    with db_engine.begin() as conn:
        conn.execute(text(f'SET LOCAL search_path TO "{TENANT_SCHEMA}", public'))
        conn.execute(text("SET LOCAL app.tenant_id = :t"), {"t": str(TENANT_ID)})

        # 1. Break mutual FK: NULL last_sync_run_id on the test org
        conn.execute(text("""
            UPDATE connected_orgs SET last_sync_run_id = NULL
            WHERE id = :id
        """), {"id": org_id})

        # 2. NULL logical_version_seq on this test's sync_runs so we
        # can delete logical_versions independently
        conn.execute(text("""
            UPDATE sync_runs SET logical_version_seq = NULL
            WHERE source_org_id = :id
        """), {"id": org_id})

        # 3. Delete ai_enrichment_queue rows for entities created
        # by this org's syncs
        conn.execute(text("""
            DELETE FROM ai_enrichment_queue
            WHERE entity_id IN (
                SELECT id FROM entities WHERE last_synced_from_org_id = :id
            )
        """), {"id": org_id})

        # 4. Delete entities for this org (including superseded rows)
        conn.execute(text("""
            DELETE FROM entities WHERE last_synced_from_org_id = :id
        """), {"id": org_id})

        # 5. Delete logical_versions allocated by this org's sync_runs
        conn.execute(text("""
            DELETE FROM logical_versions
            WHERE created_by_sync_run_id IN (
                SELECT id FROM sync_runs WHERE source_org_id = :id
            )
        """), {"id": org_id})

        # 6. Delete sync_runs for this org
        conn.execute(text("""
            DELETE FROM sync_runs WHERE source_org_id = :id
        """), {"id": org_id})

        # 7. Delete the connected_orgs row itself
        conn.execute(text("""
            DELETE FROM connected_orgs WHERE id = :id
        """), {"id": org_id})


def test_live_object_and_picklist_value_set_sync(
    live_sf_client, db_engine, test_org,
):
    """End-to-end Object + PicklistValueSet phase cycle."""
    from primeqa.sync.engine import SyncEngine

    engine = SyncEngine(
        engine_db=db_engine,
        sf_client=live_sf_client,
        tenant_schema=TENANT_SCHEMA,
    )

    # ===== First sync =====
    sync_run_id_1 = engine.run_sync(connected_org_id=test_org)
    assert sync_run_id_1 is not None

    with db_engine.begin() as conn:
        conn.execute(text(f'SET LOCAL search_path TO "{TENANT_SCHEMA}", public'))
        conn.execute(text("SET LOCAL app.tenant_id = :t"), {"t": str(TENANT_ID)})

        # sync_run state after run 1
        run1 = conn.execute(text("""
            SELECT phase, last_completed_phase, logical_version_seq,
                   entities_inserted, entities_superseded, entities_unchanged
            FROM sync_runs WHERE id = :id
        """), {"id": sync_run_id_1}).fetchone()

        assert run1.last_completed_phase == "Flow", (
            f"Expected last_completed_phase='Flow' after all 12 phases, "
            f"got {run1.last_completed_phase!r}"
        )
        assert run1.phase == "enrichment", (
            f"Expected phase='enrichment' after structural complete, "
            f"got {run1.phase!r}"
        )
        assert run1.logical_version_seq is not None
        assert run1.entities_inserted > 0
        assert run1.entities_superseded == 0
        assert run1.entities_unchanged == 0

        # entities table — Object rows from this org
        object_count = conn.execute(text("""
            SELECT COUNT(*) FROM entities
            WHERE entity_type = 'Object'
              AND last_synced_from_org_id = :id
              AND valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert object_count >= 10, (
            f"Sandbox should yield ≥10 syncable Objects "
            f"(Account, Contact, Lead, Opportunity, Case, ...); "
            f"got {object_count}"
        )
        assert object_count == run1.entities_inserted

        # ai_enrichment_queue — 2 rows per entity (embedding + summary)
        queue_count = conn.execute(text("""
            SELECT COUNT(*) FROM ai_enrichment_queue
            WHERE entity_type = 'Object'
              AND entity_id IN (
                  SELECT id FROM entities
                  WHERE last_synced_from_org_id = :id
                    AND entity_type = 'Object'
              )
              AND status = 'pending'
        """), {"id": test_org}).scalar()
        assert queue_count == object_count * 2, (
            f"Expected 2× queue rows per entity ({object_count}×2="
            f"{object_count * 2}); got {queue_count}"
        )

        # logical_versions row allocated for this sync
        version_count = conn.execute(text("""
            SELECT COUNT(*) FROM logical_versions
            WHERE created_by_sync_run_id = :id
        """), {"id": sync_run_id_1}).scalar()
        assert version_count == 1, (
            f"Expected 1 logical_versions row per sync_run; got {version_count}"
        )

        # PicklistValueSet entities — sandbox has 0 GlobalValueSets per
        # sf_client.fetch_global_value_sets docstring; expect 0
        # materializations. Verifies the phase ran cleanly (no errors,
        # no INSERTs) on the empty path. When the SVS-source phase
        # lands, this assertion updates to assert non-zero from the
        # StandardValueSet catalog.
        pvs_count = conn.execute(text("""
            SELECT COUNT(*) FROM entities
            WHERE entity_type = 'PicklistValueSet'
              AND last_synced_from_org_id = :id
              AND valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert pvs_count == 0, (
            f"Expected 0 PicklistValueSet entities (sandbox has 0 GVSes); "
            f"got {pvs_count}"
        )

        # No queue rows for PicklistValueSet (empty insert path)
        pvs_queue = conn.execute(text("""
            SELECT COUNT(*) FROM ai_enrichment_queue
            WHERE entity_type = 'PicklistValueSet'
              AND entity_id IN (
                  SELECT id FROM entities
                  WHERE last_synced_from_org_id = :id
              )
        """), {"id": test_org}).scalar()
        assert pvs_queue == 0, (
            f"Expected 0 PicklistValueSet queue rows; got {pvs_queue}"
        )

    # ===== Second sync =====
    sync_run_id_2 = engine.run_sync(connected_org_id=test_org)
    assert sync_run_id_2 != sync_run_id_1

    with db_engine.begin() as conn:
        conn.execute(text(f'SET LOCAL search_path TO "{TENANT_SCHEMA}", public'))
        conn.execute(text("SET LOCAL app.tenant_id = :t"), {"t": str(TENANT_ID)})

        run2 = conn.execute(text("""
            SELECT entities_inserted, entities_superseded, entities_unchanged
            FROM sync_runs WHERE id = :id
        """), {"id": sync_run_id_2}).fetchone()
        assert run2.entities_inserted == 0, (
            f"Second sync should report 0 new entities (hashes match); "
            f"got {run2.entities_inserted}"
        )
        assert run2.entities_superseded == 0, (
            f"Second sync should report 0 superseded (hashes match); "
            f"got {run2.entities_superseded}"
        )
        assert run2.entities_unchanged == object_count, (
            f"Second sync should report {object_count} unchanged; "
            f"got {run2.entities_unchanged}"
        )

        # Queue did NOT grow (unchanged entities don't re-enqueue)
        queue_count_after = conn.execute(text("""
            SELECT COUNT(*) FROM ai_enrichment_queue
            WHERE entity_type = 'Object'
              AND entity_id IN (
                  SELECT id FROM entities
                  WHERE last_synced_from_org_id = :id
                    AND entity_type = 'Object'
              )
        """), {"id": test_org}).scalar()
        assert queue_count_after == queue_count, (
            f"Queue should not grow on unchanged sync ({queue_count}); "
            f"got {queue_count_after}"
        )
