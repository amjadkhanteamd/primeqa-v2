"""Live integration test for Object + PicklistValueSet + PicklistValue
phases, including detail-table writes.

End-to-end: SyncEngine.run_sync() runs all 12 phases for a fresh
connected_org. Object + PicklistValueSet + PicklistValue phases
materialize entities; other phases are no-ops returning empty results.
Verifies:
  - entities table populated with Object rows
  - ai_enrichment_queue populated (2 rows per entity: embedding + summary)
  - sync_run state advances (phase='enrichment', last_completed_phase='Flow')
  - logical_versions row allocated per sync_run
  - second sync reports all entities as unchanged (hash match)
  - second sync doesn't grow the queue
  - PicklistValueSet phase materialized the unified GVS + SVS
    stream. Sandbox has 0 GVSes per the GVS fetch docstring; SVS
    iterates the 616-entry canonical catalog
    (sf_constants.STANDARD_VALUE_SET_LABELS pinned to API v66.0).
    Not every catalog label is queryable in every org — industry-
    cloud SVSes (Health, Financial Services, Public Sector, ...)
    return HTTP 500 in orgs where the corresponding cloud is not
    enabled, per corrections-log §6 category 3. This sandbox has
    no industry clouds enabled, so the queryable subset is the
    core CRM portion of the catalog (~95 in measurements).
    The assertion `>= 50` is a regression floor — catches a
    regression to 0 or near-0 while accepting the real-world
    sandbox shape. A future org-class-aware test (sandbox with
    Health Cloud enabled, for instance) would assert against a
    different floor.
  - PicklistValueSet entities are tagged with the SVS:-prefixed
    external_id per corrections-log §8 addendum.
  - Second sync: all PVS entities also report as unchanged (no
    spurious supersession from the _source marker round-trip).
  - PicklistValue phase extracts nested values from GVS.Metadata.
    customValue + SVS.Metadata.standardValue. Each child PicklistValue
    entity carries a composite external_id ({parent}.{valueName})
    and a picklist_value_details row with picklist_value_set_entity_id
    resolved via make_parent_resolver. Sandbox produces ~600-1500
    PicklistValue rows (95 SVSes × varying value counts).
  - Detail-table writes: object_details populated for every Object
    entity, picklist_value_details populated for every PicklistValue
    entity. FK integrity: zero orphan rows pointing at non-
    PicklistValueSet entities or NULL.
  - Second-sync idempotency holds for PV entities + detail rows
    (no spurious supersession from _parent_external_id / _sort_order
    round-tripping through normalize/hash).

Cleanup: deletes all rows referencing the test connected_org's id
(FK-aware: queue → entities → sync_runs back-ref → logical_versions
→ sync_runs → connected_orgs; detail rows cascade with entity
deletes via no-cascade FK so they're deleted explicitly).

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
    # pool_pre_ping + pool_recycle match primeqa/db.py and
    # primeqa/semantic/connection.py settings — Railway's proxy
    # drops idle connections after ~15 min, and a sync phase that
    # holds a connection across multi-minute Salesforce REST
    # roundtrips can exceed that timeout. Without these settings
    # the test's engine pool hands out dead connections and the
    # next query hangs in poll() indefinitely (local TCP keepalive
    # default is 2 hours).
    eng = create_engine(
        os.environ["DATABASE_URL"],
        pool_pre_ping=True,
        pool_recycle=300,
    )
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

        # 3b. Delete detail rows BEFORE entities. Two reasons:
        #   - entity_id FKs on detail tables ARE ON DELETE CASCADE,
        #     so they'd auto-clean — but
        #   - picklist_value_details.picklist_value_set_entity_id FK
        #     does NOT cascade, so deleting a PVS entity while a child
        #     PV detail row still references it via this FK would
        #     fail the row-level constraint check.
        # Explicit detail-row delete first avoids the ordering risk
        # and makes the cleanup robust against future detail-table
        # additions.
        for detail_table in ("picklist_value_details", "object_details"):
            conn.execute(text(f"""
                DELETE FROM {detail_table}
                WHERE entity_id IN (
                    SELECT id FROM entities
                    WHERE last_synced_from_org_id = :id
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


def test_live_object_pvs_pv_sync_with_details(
    live_sf_client, db_engine, test_org,
):
    """End-to-end Object + PicklistValueSet + PicklistValue phases
    + detail-table writes."""
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
        # Note: run1.entities_inserted is the cross-phase total
        # (Object + PicklistValueSet); the PVS-vs-Object split is
        # verified below in the PicklistValueSet block.

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

        # PicklistValueSet entities — unified GVS + SVS source.
        # Sandbox typically has 0 GVSes; SVS iterates the 616-entry
        # canonical catalog. Per corrections-log §6 category 3,
        # industry-cloud SVSes return HTTP 500 in orgs where the
        # cloud is not enabled (~85% of the catalog in this sandbox).
        # Floor is 50 — catches regression to 0/near-0 while accepting
        # the real-world catalog-queryable subset.
        pvs_count = conn.execute(text("""
            SELECT COUNT(*) FROM entities
            WHERE entity_type = 'PicklistValueSet'
              AND last_synced_from_org_id = :id
              AND valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert pvs_count >= 50, (
            f"Expected >=50 PicklistValueSet entities (SVS catalog "
            f"regression floor); got {pvs_count}"
        )

        # SVS rows carry the 'SVS:' prefix on sf_api_name per
        # corrections-log §8 addendum — verifies the namespace
        # discipline actually landed in storage.
        svs_count = conn.execute(text("""
            SELECT COUNT(*) FROM entities
            WHERE entity_type = 'PicklistValueSet'
              AND last_synced_from_org_id = :id
              AND valid_to_seq IS NULL
              AND sf_api_name LIKE 'SVS:%'
        """), {"id": test_org}).scalar()
        # Sandbox has 0 GVSes, so every PVS row should be SVS-prefixed.
        assert svs_count == pvs_count, (
            f"Expected all {pvs_count} PVS rows to be SVS-prefixed "
            f"(sandbox has 0 GVSes); got {svs_count} prefixed"
        )

        # Queue rows = 2× entity count for PicklistValueSet
        # (embedding + summary primitives per entity).
        pvs_queue = conn.execute(text("""
            SELECT COUNT(*) FROM ai_enrichment_queue
            WHERE entity_type = 'PicklistValueSet'
              AND entity_id IN (
                  SELECT id FROM entities
                  WHERE last_synced_from_org_id = :id
                    AND entity_type = 'PicklistValueSet'
              )
        """), {"id": test_org}).scalar()
        assert pvs_queue == pvs_count * 2, (
            f"Expected {pvs_count * 2} PicklistValueSet queue rows "
            f"(2× entity count); got {pvs_queue}"
        )

        # ----- PicklistValue entities + picklist_value_details -----
        # PVs come from nested customValue/standardValue inside the
        # 95-PVS sandbox set. Most SVSes have 5-20 values; floor 200
        # for regression guard.
        pv_count = conn.execute(text("""
            SELECT COUNT(*) FROM entities
            WHERE entity_type = 'PicklistValue'
              AND last_synced_from_org_id = :id
              AND valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert pv_count >= 200, (
            f"Expected >=200 PicklistValue entities (regression "
            f"floor; PVs derive from {pvs_count} PVS parents × "
            f"~5-20 values each); got {pv_count}"
        )

        # picklist_value_details: 1 row per active PV entity
        pv_details = conn.execute(text("""
            SELECT COUNT(*) FROM picklist_value_details pvd
            JOIN entities e ON e.id = pvd.entity_id
            WHERE e.last_synced_from_org_id = :id
              AND e.entity_type = 'PicklistValue'
              AND e.valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert pv_details == pv_count, (
            f"picklist_value_details rows ({pv_details}) != "
            f"PicklistValue entity count ({pv_count}); detail "
            f"mapper / batched_materialize misaligned"
        )

        # FK integrity: every PV detail row's
        # picklist_value_set_entity_id must point at a real
        # PicklistValueSet entity row (no NULL, no orphan, no
        # wrong-type misroute from a parent_resolver bug).
        orphan_count = conn.execute(text("""
            SELECT COUNT(*) FROM picklist_value_details pvd
            JOIN entities child ON child.id = pvd.entity_id
            LEFT JOIN entities parent
                ON parent.id = pvd.picklist_value_set_entity_id
            WHERE child.last_synced_from_org_id = :id
              AND child.entity_type = 'PicklistValue'
              AND child.valid_to_seq IS NULL
              AND (parent.id IS NULL
                   OR parent.entity_type != 'PicklistValueSet')
        """), {"id": test_org}).scalar()
        assert orphan_count == 0, (
            f"Found {orphan_count} PicklistValue detail rows with "
            f"orphan or wrong-type parent FK — parent_resolver bug"
        )

        # PV enrichment queue: 2× entity count
        pv_queue = conn.execute(text("""
            SELECT COUNT(*) FROM ai_enrichment_queue
            WHERE entity_type = 'PicklistValue'
              AND entity_id IN (
                  SELECT id FROM entities
                  WHERE last_synced_from_org_id = :id
                    AND entity_type = 'PicklistValue'
              )
        """), {"id": test_org}).scalar()
        assert pv_queue == pv_count * 2, (
            f"Expected {pv_count * 2} PicklistValue queue rows; "
            f"got {pv_queue}"
        )

        # ----- Object detail-table retrofit -----
        # object_details should now have 1 row per active Object
        # entity (this cycle retrofits Object phase to write its
        # detail rows via the same _DETAIL_TABLE_MAPPERS registry).
        obj_details = conn.execute(text("""
            SELECT COUNT(*) FROM object_details od
            JOIN entities e ON e.id = od.entity_id
            WHERE e.last_synced_from_org_id = :id
              AND e.entity_type = 'Object'
              AND e.valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert obj_details == object_count, (
            f"object_details rows ({obj_details}) != Object entity "
            f"count ({object_count}); retrofit incomplete"
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
        # Total unchanged = Object + PVS + PV counts (every entity
        # from sync 1 should have an unchanged-hash match on sync 2,
        # including children whose _parent_external_id + _sort_order
        # markers round-trip through normalize/hash identically).
        expected_unchanged = object_count + pvs_count + pv_count
        assert run2.entities_unchanged == expected_unchanged, (
            f"Second sync should report {expected_unchanged} unchanged "
            f"(Object {object_count} + PVS {pvs_count} + PV "
            f"{pv_count}); got {run2.entities_unchanged}"
        )

        # Object queue did NOT grow (unchanged entities don't
        # re-enqueue).
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
            f"Object queue should not grow on unchanged sync "
            f"({queue_count}); got {queue_count_after}"
        )

        # PicklistValueSet entity count unchanged across syncs
        # (no spurious supersession from the _source marker
        # round-trip — corrections-log §8 addendum lock).
        pvs_count_after = conn.execute(text("""
            SELECT COUNT(*) FROM entities
            WHERE entity_type = 'PicklistValueSet'
              AND last_synced_from_org_id = :id
              AND valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert pvs_count_after == pvs_count, (
            f"PVS active count should remain {pvs_count} after "
            f"second sync; got {pvs_count_after}"
        )

        # PVS queue did NOT grow either.
        pvs_queue_after = conn.execute(text("""
            SELECT COUNT(*) FROM ai_enrichment_queue
            WHERE entity_type = 'PicklistValueSet'
              AND entity_id IN (
                  SELECT id FROM entities
                  WHERE last_synced_from_org_id = :id
                    AND entity_type = 'PicklistValueSet'
              )
        """), {"id": test_org}).scalar()
        assert pvs_queue_after == pvs_queue, (
            f"PVS queue should not grow on unchanged sync "
            f"({pvs_queue}); got {pvs_queue_after}"
        )

        # PV entity count unchanged + no spurious supersession.
        # Catches a bug where _parent_external_id or _sort_order
        # markers change between syncs (which they should not —
        # parent identifiers and list positions are stable).
        pv_count_after = conn.execute(text("""
            SELECT COUNT(*) FROM entities
            WHERE entity_type = 'PicklistValue'
              AND last_synced_from_org_id = :id
              AND valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert pv_count_after == pv_count, (
            f"PV active count should remain {pv_count} after "
            f"second sync; got {pv_count_after}"
        )

        # PV detail-row count unchanged (no new detail rows written
        # for unchanged entities — confirms the materialize layer
        # gates detail writes on new + changed buckets only).
        pv_details_after = conn.execute(text("""
            SELECT COUNT(*) FROM picklist_value_details pvd
            JOIN entities e ON e.id = pvd.entity_id
            WHERE e.last_synced_from_org_id = :id
              AND e.entity_type = 'PicklistValue'
              AND e.valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert pv_details_after == pv_details, (
            f"PV detail count should remain {pv_details} after "
            f"second sync; got {pv_details_after}"
        )

        # PV queue did not grow.
        pv_queue_after = conn.execute(text("""
            SELECT COUNT(*) FROM ai_enrichment_queue
            WHERE entity_type = 'PicklistValue'
              AND entity_id IN (
                  SELECT id FROM entities
                  WHERE last_synced_from_org_id = :id
                    AND entity_type = 'PicklistValue'
              )
        """), {"id": test_org}).scalar()
        assert pv_queue_after == pv_queue, (
            f"PV queue should not grow on unchanged sync "
            f"({pv_queue}); got {pv_queue_after}"
        )

        # Object detail rows also stable across syncs.
        obj_details_after = conn.execute(text("""
            SELECT COUNT(*) FROM object_details od
            JOIN entities e ON e.id = od.entity_id
            WHERE e.last_synced_from_org_id = :id
              AND e.entity_type = 'Object'
              AND e.valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert obj_details_after == obj_details, (
            f"object_details count should remain {obj_details} "
            f"after second sync; got {obj_details_after}"
        )
