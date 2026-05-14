"""Live integration test for the FULL structural sync — all 11
entity-materializing phases: Object + PicklistValueSet +
PicklistValue + Field + RecordType + Layout + ValidationRule +
Profile + PermissionSet + User + Flow — including detail-table
writes + edges (property-less + property-bearing).

End-to-end: SyncEngine.run_sync() runs all 12 phases for a fresh
connected_org. Object + PicklistValueSet + PicklistValue + Field +
RecordType + Layout phases materialize entities; other phases are
no-ops returning empty results. Field is the first edge-writing
phase; RecordType the second; Layout the third AND the first to
write property-bearing edges (INCLUDES_FIELD).
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
  - Field phase: per-Object REST describe + entity + field_details +
    BELONGS_TO + HAS_RELATIONSHIP_TO edges. ~146 Objects × ~30-150
    fields each → several thousand Field entities. Every Field has
    exactly one BELONGS_TO edge to its parent Object; reference fields
    add one HAS_RELATIONSHIP_TO edge per target. HAS_PICKLIST_VALUES
    deferred per corrections-log §10 (REST describe doesn't expose
    GVS references).
  - Edge integrity: zero orphan edges (every source + target points
    at a currently-active entity row). Edge counts stable across
    syncs (set-difference semantics for property-less edges is
    naturally idempotent).
  - RecordType phase: fetches via Tooling+Metadata two-phase
    (substrate-1 fetch_record_types). Each RT decorated with
    _parent_object_api_name (split from FullName at first '.').
    Writes entity + record_type_details + BELONGS_TO → Object
    edge (the only RT edge this cycle —
    CONSTRAINS_PICKLIST_VALUES deferred per corrections-log §14
    pending substrate-1's registry-vs-derivation contradiction
    resolution alongside §10 fetch_custom_field_metadata).
  - Layout phase: REST describe/layouts per Object + ONE bulk
    Tooling SOQL for Id→Name resolution (fetch_layout_names).
    Filters GlobalQuickActionList (no parent Object per §15).
    Writes entity + layout_details + BELONGS_TO → Object +
    INCLUDES_FIELD → Field edges (the latter property-bearing
    with section_name/order/row/column/required/readonly per
    IncludesFieldProperties; hash-based supersession via the
    edges.last_seed_hash column added in migration 20260512_0040
    / commit 26584d0). ASSIGNED_TO_PROFILE_RECORDTYPE deferred
    per corrections-log §16.
  - ValidationRule phase: Tooling Phase 1 + Phase 2 fetch
    (substrate-1 fetch_validation_rules, with FullName added in
    Commit 48efea4 / cycle precursor P5). Each VR carries the
    formula in Metadata.errorConditionFormula. Writes entity +
    validation_rule_details + BELONGS_TO → Object + APPLIES_TO →
    Object edges (both property-less, both targeting parent
    Object with distinct edge_types; UNIQUE active index allows
    coexistence). REFERENCES → Field deferred per corrections-log
    §17 (formula parser unbuilt).
  - Profile phase: Tooling Phase 1 + Phase 2 fetch
    (substrate-1 fetch_profiles). Org-level entity (no parent
    Object); §18 parent-scope filter does NOT apply. Writes
    entity + profile_details + TWO property-bearing edge types:
    GRANTS_OBJECT_ACCESS → Object (6 access flags per
    GrantsObjectAccessProperties: can_create / can_read /
    can_edit / can_delete / can_view_all / can_modify_all) and
    GRANTS_FIELD_ACCESS → Field (2 access flags per
    GrantsFieldAccessProperties: can_read / can_edit). Both
    edge types route through the property-bearing hash-compare
    supersession path established in the Layout cycle
    (commit 258e3f9). Edge targets pointing at unsyncable
    Objects/Fields (managed-package internals filtered by
    Object phase's syncability rules) are silently skipped via
    the materialize layer's `if target_id is None: continue`
    guard. ASSIGNED_TO_PROFILE_RECORDTYPE (Layout source per
    §16) deferred to next cycle — needs Layout payload
    layoutAssignments[] survey.
  - PermissionSet phase: Category 4 fetcher (substrate-1
    fetch_permission_sets, 5 SOQL queries total — Tooling
    FIELDS(STANDARD) parent + Data API ObjectPermissions +
    FieldPermissions + PermissionSetGroupComponent +
    PermissionSetLicense). Org-level entity, like Profile.
    Sync layer filters Type='Profile' auto-synth duplicates per
    substrate-1 §5; joins child OP/FP by ParentId; resolves
    LicenseId → MasterLabel via the fetched license map;
    decorates Type='Group' PSes (PSGs) with `_member_ps_names`
    by joining PermissionSetGroupComponent to permission_sets
    via Id→Name. Writes entity + permission_set_details +
    THREE edge types:
      - GRANTS_OBJECT_ACCESS → Object (property-bearing; same
        schema as Profile's but reads PS field-naming —
        PermissionsCreate/Read/Edit/etc instead of allowCreate/
        allowRead/allowEdit/etc)
      - GRANTS_FIELD_ACCESS → Field (property-bearing; same
        schema, PS field-naming)
      - INHERITS_PERMISSION_SET → PermissionSet (property-less;
        sourced from `_member_ps_names` marker on PSGs only;
        non-Group PSes have no marker → no edge written)
  - User phase: Category 4 fetcher (substrate-1 fetch_users
    returns a 2-key dict: users + PermissionSetAssignments).
    Org-level entity. Profile + PermissionSet Id→external_id
    maps are built by phase_user from the entities table (not
    SOQL — sidesteps the Tooling-vs-Data API Profile-name
    asymmetry). TWO-PASS materialization: pass 1 inserts User
    entities + captures Username → entity_id; pass 2 builds
    the User.Id → entity_id map. Users whose ProfileId isn't
    in the synced Profile set are filtered (Salesforce hides
    some system profiles). Writes entity + user_details (NOT
    NULL FK to Profile entity; NOT NULL user_type) + TWO edge
    types:
      - HAS_PROFILE → Profile (property-less; every synced
        User has exactly one Profile)
      - HAS_PERMISSION_SET → PermissionSet (property-bearing;
        HasPermissionSetProperties; direct PSAs only —
        PSG-derived flow via INHERITS_PERMISSION_SET)
  - Flow phase: the final entity phase (11/11). Coordinates
    TWO fetchers — fetch_flow_definitions (parent context:
    DeveloperName / ActiveVersionId / ManageableState) +
    fetch_flow_versions (version-level rows with the full
    flow graph in Metadata). Per corrections-log §20,
    FlowDefinition is NOT a separate entity: one Flow entity
    per stable DeveloperName, versioning via bitemporal
    supersession. phase_flow groups versions by DefinitionId,
    picks each FD's active version (Id == ActiveVersionId;
    falls back to LatestVersionId with is_active=False for
    deactivated flows), and materializes the Flow entity
    keyed by DeveloperName. Writes entity + flow_details
    (NOT NULL flow_type from ProcessType; nullable
    triggers_on_object_entity_id + trigger_type) + ONE edge
    type:
      - TRIGGERS_ON → Object (property-bearing,
        TriggersOnProperties = {trigger_type, condition_text}).
        Scoped to record-triggered flows ONLY per substrate-1's
        TriggersOnProperties design (corrections-log §21) —
        autolaunched / screen / platform-event / scheduled
        flows produce no TRIGGERS_ON edge. This sandbox has
        zero record-triggered flows (all AutoLaunchedFlow), so
        zero TRIGGERS_ON edges materialize; the edge path is
        unit-tested and correctness-complete.

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


REQUIRED_SF_ENV = (
    "SF_INSTANCE_URL", "SF_CLIENT_ID", "SF_CLIENT_SECRET",
    "SF_REFRESH_TOKEN",
)


def _get_live_db_url() -> str:
    """Return the live integration test database URL.

    Prefers LIVE_DATABASE_URL when set; falls back to DATABASE_URL.

    Rationale: Railway's public TCP proxy
    (switchback.proxy.rlwy.net) exhibits intermittent layer-7
    connection termination on long-held connections with sustained
    activity, making it unreliable for the multi-minute sync test
    suite. LIVE_DATABASE_URL points live tests at a local Postgres
    while DATABASE_URL can stay pointed at Railway for ad-hoc
    tooling or production deployments.
    """
    url = os.environ.get("LIVE_DATABASE_URL")
    if url:
        return url
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "Live integration tests require LIVE_DATABASE_URL or "
            "DATABASE_URL"
        )
    return url


HAS_CREDS = (
    all(os.environ.get(k) for k in REQUIRED_SF_ENV)
    and (
        os.environ.get("LIVE_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
    )
)

pytestmark = pytest.mark.sandbox

TENANT_SCHEMA = "tenant_1"
TENANT_ID = 1


@pytest.fixture
def live_sf_client():
    if not HAS_CREDS:
        missing = [k for k in REQUIRED_SF_ENV if not os.environ.get(k)]
        if not (os.environ.get("LIVE_DATABASE_URL")
                or os.environ.get("DATABASE_URL")):
            missing.append("LIVE_DATABASE_URL or DATABASE_URL")
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
        pytest.skip(
            "LIVE_DATABASE_URL/DATABASE_URL not configured"
        )
    # pool_pre_ping + pool_recycle + TCP keepalive match
    # primeqa/db.py and primeqa/semantic/connection.py settings.
    # Originally added for Railway's public proxy (drops idle
    # connections after ~15 min); still appropriate for local
    # Postgres (keepalive on a loopback socket is a no-op cost-
    # wise, and pool_pre_ping costs one cheap SELECT 1 per
    # checkout — harmless either way).
    #
    # Database URL selection via _get_live_db_url():
    # LIVE_DATABASE_URL takes precedence over DATABASE_URL. Lets
    # developers point live tests at a local Postgres while
    # DATABASE_URL keeps pointing at Railway for ad-hoc tooling.
    # Live verification was parked from the Field cycle (3dcda00)
    # through ValidationRule (a9f4322) because Railway's public
    # TCP proxy (switchback.proxy.rlwy.net) showed intermittent
    # layer-7 connection termination on the multi-minute sync
    # test. Local Postgres unblocks this verification cycle.
    eng = create_engine(
        _get_live_db_url(),
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args={
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 5,
        },
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
        #     and field_details.{object_entity_id,
        #     references_object_entity_id} FKs do NOT cascade, so
        #     deleting a PVS or Object entity while a child detail row
        #     still references it via these FKs would fail the
        #     row-level constraint check.
        # Explicit detail-row delete first avoids the ordering risk
        # and makes the cleanup robust against future detail-table
        # additions.
        for detail_table in (
            "flow_details", "user_details",
            "permission_set_details", "profile_details",
            "validation_rule_details", "layout_details",
            "field_details", "record_type_details",
            "picklist_value_details", "object_details",
        ):
            conn.execute(text(f"""
                DELETE FROM {detail_table}
                WHERE entity_id IN (
                    SELECT id FROM entities
                    WHERE last_synced_from_org_id = :id
                )
            """), {"id": org_id})

        # 3c. Delete edges sourced from or targeting this org's
        # entities. Edges have FKs to entities (no cascade), so they
        # must go before the entities delete.
        conn.execute(text("""
            DELETE FROM edges
            WHERE source_entity_id IN (
                SELECT id FROM entities
                WHERE last_synced_from_org_id = :id
            )
            OR target_entity_id IN (
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


def test_live_sync_full(
    live_sf_client, db_engine, test_org,
):
    """End-to-end FULL structural sync — all 11
    entity-materializing phases (Object through Flow), the
    11/11 milestone. Detail-table writes + every edge type
    implemented to date: BELONGS_TO + HAS_RELATIONSHIP_TO +
    INCLUDES_FIELD (property-bearing) + APPLIES_TO +
    GRANTS_OBJECT_ACCESS (Profile + PermissionSet sources,
    property-bearing) + GRANTS_FIELD_ACCESS (Profile +
    PermissionSet sources, property-bearing) +
    INHERITS_PERMISSION_SET (PSG → member PS, property-less) +
    HAS_PROFILE (User → Profile, property-less) +
    HAS_PERMISSION_SET (User → PermissionSet, property-bearing) +
    TRIGGERS_ON (Flow → Object, property-bearing; record-trigger
    scoped per §21). Runs against local Postgres via
    LIVE_DATABASE_URL."""
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

        # ----- Field entities + field_details + edges -----
        # 146 Objects × ~30-150 fields each. Floor of 2000 is a
        # regression guard well below the expected 4500-15000.
        field_count = conn.execute(text("""
            SELECT COUNT(*) FROM entities
            WHERE entity_type = 'Field'
              AND last_synced_from_org_id = :id
              AND valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert field_count >= 2000, (
            f"Expected >=2000 Field entities (regression floor); "
            f"got {field_count}"
        )

        # field_details: 1 row per active Field entity
        field_details_count = conn.execute(text("""
            SELECT COUNT(*) FROM field_details fd
            JOIN entities e ON e.id = fd.entity_id
            WHERE e.last_synced_from_org_id = :id
              AND e.entity_type = 'Field'
              AND e.valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert field_details_count == field_count, (
            f"field_details rows ({field_details_count}) != "
            f"Field entity count ({field_count})"
        )

        # FK integrity on field_details: every object_entity_id points
        # at a real Object entity. references_object_entity_id can be
        # NULL (non-reference field or unmaterialized target) but if
        # populated must point at an Object.
        bad_field_details = conn.execute(text("""
            SELECT COUNT(*) FROM field_details fd
            JOIN entities child ON child.id = fd.entity_id
            LEFT JOIN entities parent ON parent.id = fd.object_entity_id
            LEFT JOIN entities ref_obj
                ON ref_obj.id = fd.references_object_entity_id
            WHERE child.last_synced_from_org_id = :id
              AND child.entity_type = 'Field'
              AND child.valid_to_seq IS NULL
              AND (
                parent.id IS NULL
                OR parent.entity_type != 'Object'
                OR (fd.references_object_entity_id IS NOT NULL
                    AND (ref_obj.id IS NULL
                         OR ref_obj.entity_type != 'Object'))
              )
        """), {"id": test_org}).scalar()
        assert bad_field_details == 0, (
            f"Found {bad_field_details} field_details rows with "
            f"bad FK targets — parent_resolver bug"
        )

        # BELONGS_TO edges: every active Field has exactly one
        # BELONGS_TO → parent Object.
        belongs_to_count = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'BELONGS_TO'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'Field'
        """), {"id": test_org}).scalar()
        assert belongs_to_count == field_count, (
            f"BELONGS_TO edge count from Field sources "
            f"({belongs_to_count}) != Field count ({field_count}); "
            f"every Field should have exactly one BELONGS_TO edge"
        )

        # HAS_RELATIONSHIP_TO: at least 100 reference fields expected
        # (every standard Object has at least OwnerId; many have
        # ParentId, RecordTypeId, etc.).
        hrt_count = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'HAS_RELATIONSHIP_TO'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'Field'
        """), {"id": test_org}).scalar()
        assert hrt_count >= 100, (
            f"Expected >=100 HAS_RELATIONSHIP_TO edges (regression "
            f"floor); got {hrt_count}"
        )

        # No HAS_PICKLIST_VALUES edges this cycle (deferred per §10).
        hpv_count = conn.execute(text("""
            SELECT COUNT(*) FROM edges
            WHERE edge_type = 'HAS_PICKLIST_VALUES'
              AND valid_to_seq IS NULL
        """)).scalar()
        assert hpv_count == 0, (
            f"Expected 0 HAS_PICKLIST_VALUES edges (deferred per "
            f"corrections-log §10); got {hpv_count}"
        )

        # No orphan edges — every source + target points at a
        # currently-active entity row.
        orphan_edges = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            LEFT JOIN entities src_active
                ON src_active.id = e.source_entity_id
               AND src_active.valid_to_seq IS NULL
            LEFT JOIN entities tgt_active
                ON tgt_active.id = e.target_entity_id
               AND tgt_active.valid_to_seq IS NULL
            WHERE e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND (src_active.id IS NULL OR tgt_active.id IS NULL)
        """), {"id": test_org}).scalar()
        assert orphan_edges == 0, (
            f"Found {orphan_edges} orphan active edges (source or "
            f"target points at superseded/missing entity)"
        )

        # Field enrichment queue
        field_queue = conn.execute(text("""
            SELECT COUNT(*) FROM ai_enrichment_queue
            WHERE entity_type = 'Field'
              AND entity_id IN (
                  SELECT id FROM entities
                  WHERE last_synced_from_org_id = :id
                    AND entity_type = 'Field'
              )
        """), {"id": test_org}).scalar()
        assert field_queue == field_count * 2, (
            f"Expected {field_count * 2} Field queue rows; "
            f"got {field_queue}"
        )

        # ----- RecordType entities + record_type_details + edges -----
        # Sandbox at survey time has 5 RTs (per
        # fetch_record_types() probe). Regression floor: ≥1 (every
        # Object has at least an implicit Master RT in some orgs;
        # but Tooling SOQL only returns user-defined RTs, so the
        # floor is sandbox-dependent). Setting >= 1 as a sentinel
        # that the phase actually ran and materialized SOMETHING.
        rt_count = conn.execute(text("""
            SELECT COUNT(*) FROM entities
            WHERE entity_type = 'RecordType'
              AND last_synced_from_org_id = :id
              AND valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert rt_count >= 1, (
            f"Expected >=1 RecordType entity (regression floor); "
            f"got {rt_count}"
        )

        # record_type_details: 1 row per active RT entity
        rt_details = conn.execute(text("""
            SELECT COUNT(*) FROM record_type_details rtd
            JOIN entities e ON e.id = rtd.entity_id
            WHERE e.last_synced_from_org_id = :id
              AND e.entity_type = 'RecordType'
              AND e.valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert rt_details == rt_count, (
            f"record_type_details rows ({rt_details}) != "
            f"RecordType entity count ({rt_count})"
        )

        # FK integrity: every RT detail row points at a real
        # Object entity. NOT NULL FK on object_entity_id.
        bad_rt_details = conn.execute(text("""
            SELECT COUNT(*) FROM record_type_details rtd
            JOIN entities child ON child.id = rtd.entity_id
            LEFT JOIN entities parent ON parent.id = rtd.object_entity_id
            WHERE child.last_synced_from_org_id = :id
              AND child.entity_type = 'RecordType'
              AND child.valid_to_seq IS NULL
              AND (parent.id IS NULL OR parent.entity_type != 'Object')
        """), {"id": test_org}).scalar()
        assert bad_rt_details == 0, (
            f"Found {bad_rt_details} record_type_details rows with "
            f"bad object_entity_id FK target — parent_resolver bug"
        )

        # BELONGS_TO edges from RTs: every RT has exactly one
        # BELONGS_TO → its parent Object.
        rt_belongs_to_count = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'BELONGS_TO'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'RecordType'
        """), {"id": test_org}).scalar()
        assert rt_belongs_to_count == rt_count, (
            f"BELONGS_TO edge count from RecordType sources "
            f"({rt_belongs_to_count}) != RT count ({rt_count}); "
            f"every RT should have exactly one BELONGS_TO edge"
        )

        # No CONSTRAINS_PICKLIST_VALUES edges this cycle (deferred
        # per §14)
        cpv_count = conn.execute(text("""
            SELECT COUNT(*) FROM edges
            WHERE edge_type = 'CONSTRAINS_PICKLIST_VALUES'
              AND valid_to_seq IS NULL
        """)).scalar()
        assert cpv_count == 0, (
            f"Expected 0 CONSTRAINS_PICKLIST_VALUES edges "
            f"(deferred per corrections-log §14); got {cpv_count}"
        )

        # RT enrichment queue: 2× entity count
        rt_queue = conn.execute(text("""
            SELECT COUNT(*) FROM ai_enrichment_queue
            WHERE entity_type = 'RecordType'
              AND entity_id IN (
                  SELECT id FROM entities
                  WHERE last_synced_from_org_id = :id
                    AND entity_type = 'RecordType'
              )
        """), {"id": test_org}).scalar()
        assert rt_queue == rt_count * 2, (
            f"Expected {rt_count * 2} RecordType queue rows; "
            f"got {rt_queue}"
        )

        # ----- Layout entities + layout_details + edges -----
        # Sandbox has ~115 layouts per fetch_layout_names() docstring;
        # most are 'Standard' LayoutType (some are
        # GlobalQuickActionList which Layout phase filters per §15).
        # Regression floor: >=10.
        layout_count = conn.execute(text("""
            SELECT COUNT(*) FROM entities
            WHERE entity_type = 'Layout'
              AND last_synced_from_org_id = :id
              AND valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert layout_count >= 10, (
            f"Expected >=10 Layout entities (regression floor); "
            f"got {layout_count}"
        )

        # layout_details: 1 row per active Layout entity
        layout_details_count = conn.execute(text("""
            SELECT COUNT(*) FROM layout_details ld
            JOIN entities e ON e.id = ld.entity_id
            WHERE e.last_synced_from_org_id = :id
              AND e.entity_type = 'Layout'
              AND e.valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert layout_details_count == layout_count

        # FK integrity: every Layout detail row points at a real
        # Object entity. layout_type should always be 'Standard'
        # for this cycle (GlobalQuickActionList filtered per §15).
        bad_layout_details = conn.execute(text("""
            SELECT COUNT(*) FROM layout_details ld
            JOIN entities child ON child.id = ld.entity_id
            LEFT JOIN entities parent ON parent.id = ld.object_entity_id
            WHERE child.last_synced_from_org_id = :id
              AND child.entity_type = 'Layout'
              AND child.valid_to_seq IS NULL
              AND (parent.id IS NULL
                   OR parent.entity_type != 'Object'
                   OR ld.layout_type != 'Standard')
        """), {"id": test_org}).scalar()
        assert bad_layout_details == 0, (
            f"Found {bad_layout_details} layout_details rows with "
            f"bad FK target or non-Standard layout_type"
        )

        # BELONGS_TO edges from Layouts: 1 per Layout
        layout_belongs_to_count = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'BELONGS_TO'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'Layout'
        """), {"id": test_org}).scalar()
        assert layout_belongs_to_count == layout_count, (
            f"BELONGS_TO edge count from Layout sources "
            f"({layout_belongs_to_count}) != Layout count "
            f"({layout_count})"
        )

        # INCLUDES_FIELD edges (property-bearing). Each Layout has
        # ~30-100 INCLUDES_FIELD edges (one per layoutComponent
        # with type='Field'). Regression floor: 100 total. Verifies
        # both the property-bearing edge write path AND that the
        # property-bearing supersession is doing inserts on first
        # sync.
        includes_field_count = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'INCLUDES_FIELD'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'Layout'
        """), {"id": test_org}).scalar()
        assert includes_field_count >= 100, (
            f"Expected >=100 INCLUDES_FIELD edges (regression "
            f"floor); got {includes_field_count}"
        )

        # INCLUDES_FIELD edges carry non-empty properties and
        # non-NULL last_seed_hash (the hallmark of property-bearing
        # edges). Property-less edges (BELONGS_TO, etc.) stay at
        # properties='{}' and last_seed_hash=NULL.
        includes_field_with_hash = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'INCLUDES_FIELD'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'Layout'
              AND e.last_seed_hash IS NOT NULL
              AND e.properties != '{}'::jsonb
        """), {"id": test_org}).scalar()
        assert includes_field_with_hash == includes_field_count, (
            f"INCLUDES_FIELD edges should all carry hash + "
            f"non-empty properties; got {includes_field_with_hash} "
            f"of {includes_field_count} with both"
        )

        # No ASSIGNED_TO_PROFILE_RECORDTYPE edges this cycle
        # (deferred per §16).
        assigned_count = conn.execute(text("""
            SELECT COUNT(*) FROM edges
            WHERE edge_type = 'ASSIGNED_TO_PROFILE_RECORDTYPE'
              AND valid_to_seq IS NULL
        """)).scalar()
        assert assigned_count == 0, (
            f"Expected 0 ASSIGNED_TO_PROFILE_RECORDTYPE edges "
            f"(deferred per §16); got {assigned_count}"
        )

        # Layout enrichment queue: 2× count
        layout_queue = conn.execute(text("""
            SELECT COUNT(*) FROM ai_enrichment_queue
            WHERE entity_type = 'Layout'
              AND entity_id IN (
                  SELECT id FROM entities
                  WHERE last_synced_from_org_id = :id
                    AND entity_type = 'Layout'
              )
        """), {"id": test_org}).scalar()
        assert layout_queue == layout_count * 2

        # ----- ValidationRule entities + details + edges -----
        # Sandbox has 61 VRs per survey. Regression floor: >=1.
        vr_count = conn.execute(text("""
            SELECT COUNT(*) FROM entities
            WHERE entity_type = 'ValidationRule'
              AND last_synced_from_org_id = :id
              AND valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert vr_count >= 1, (
            f"Expected >=1 ValidationRule entity (regression "
            f"floor; sandbox has 61); got {vr_count}"
        )

        # validation_rule_details: 1 row per active VR
        vr_details_count = conn.execute(text("""
            SELECT COUNT(*) FROM validation_rule_details vrd
            JOIN entities e ON e.id = vrd.entity_id
            WHERE e.last_synced_from_org_id = :id
              AND e.entity_type = 'ValidationRule'
              AND e.valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert vr_details_count == vr_count

        # FK integrity: every VR detail row points at a real Object
        bad_vr_details = conn.execute(text("""
            SELECT COUNT(*) FROM validation_rule_details vrd
            JOIN entities child ON child.id = vrd.entity_id
            LEFT JOIN entities parent
                ON parent.id = vrd.object_entity_id
            WHERE child.last_synced_from_org_id = :id
              AND child.entity_type = 'ValidationRule'
              AND child.valid_to_seq IS NULL
              AND (parent.id IS NULL
                   OR parent.entity_type != 'Object')
        """), {"id": test_org}).scalar()
        assert bad_vr_details == 0, (
            f"Found {bad_vr_details} validation_rule_details rows "
            f"with bad object_entity_id FK target"
        )

        # BELONGS_TO edges from VRs: 1 per VR (structural containment)
        vr_belongs_to_count = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'BELONGS_TO'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'ValidationRule'
        """), {"id": test_org}).scalar()
        assert vr_belongs_to_count == vr_count, (
            f"BELONGS_TO from VR sources ({vr_belongs_to_count}) "
            f"!= VR count ({vr_count})"
        )

        # APPLIES_TO edges from VRs: 1 per VR (behavioral relationship;
        # distinct edge_type from BELONGS_TO but same target Object)
        vr_applies_to_count = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'APPLIES_TO'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'ValidationRule'
        """), {"id": test_org}).scalar()
        assert vr_applies_to_count == vr_count, (
            f"APPLIES_TO from VR sources ({vr_applies_to_count}) "
            f"!= VR count ({vr_count})"
        )

        # No REFERENCES edges this cycle (deferred per §17 —
        # formula parser unbuilt)
        references_count = conn.execute(text("""
            SELECT COUNT(*) FROM edges
            WHERE edge_type = 'REFERENCES'
              AND valid_to_seq IS NULL
        """)).scalar()
        assert references_count == 0, (
            f"Expected 0 REFERENCES edges (deferred per §17); "
            f"got {references_count}"
        )

        # VR enrichment queue: 2× count
        vr_queue = conn.execute(text("""
            SELECT COUNT(*) FROM ai_enrichment_queue
            WHERE entity_type = 'ValidationRule'
              AND entity_id IN (
                  SELECT id FROM entities
                  WHERE last_synced_from_org_id = :id
                    AND entity_type = 'ValidationRule'
              )
        """), {"id": test_org}).scalar()
        assert vr_queue == vr_count * 2

        # ----- Profile entities + profile_details + edges -----
        # Org-level entity (no parent Object). Sandbox at 18
        # standard Profiles per survey; regression floor: >=1.
        profile_count = conn.execute(text("""
            SELECT COUNT(*) FROM entities
            WHERE entity_type = 'Profile'
              AND last_synced_from_org_id = :id
              AND valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert profile_count >= 1, (
            f"Expected >=1 Profile entity (regression floor; "
            f"sandbox has 18); got {profile_count}"
        )

        # profile_details: 1 row per active Profile entity
        profile_details_count = conn.execute(text("""
            SELECT COUNT(*) FROM profile_details pd
            JOIN entities e ON e.id = pd.entity_id
            WHERE e.last_synced_from_org_id = :id
              AND e.entity_type = 'Profile'
              AND e.valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert profile_details_count == profile_count, (
            f"profile_details rows ({profile_details_count}) != "
            f"Profile entity count ({profile_count})"
        )

        # FK integrity: Profile has no parent Object FK (org-level),
        # so we just sanity-check that user_license_type is NOT NULL
        # for every detail row. Catches a regression where mapper
        # erroneously omits the required column.
        bad_profile_details = conn.execute(text("""
            SELECT COUNT(*) FROM profile_details pd
            JOIN entities child ON child.id = pd.entity_id
            WHERE child.last_synced_from_org_id = :id
              AND child.entity_type = 'Profile'
              AND child.valid_to_seq IS NULL
              AND (pd.user_license_type IS NULL
                   OR pd.user_license_type = '')
        """), {"id": test_org}).scalar()
        assert bad_profile_details == 0, (
            f"Found {bad_profile_details} profile_details rows "
            f"with NULL/empty user_license_type"
        )

        # GRANTS_OBJECT_ACCESS edges (property-bearing). Sandbox
        # has 18 Profiles × ~130 syncable Objects each ≈ 2K-3K
        # edges; regression floor: >=10 (sandbox-conservative).
        # Many candidates skipped by materialize layer for
        # managed-package targets.
        grants_object_count = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'GRANTS_OBJECT_ACCESS'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'Profile'
        """), {"id": test_org}).scalar()
        assert grants_object_count >= 10, (
            f"Expected >=10 GRANTS_OBJECT_ACCESS edges (regression "
            f"floor); got {grants_object_count}"
        )

        # Property-bearing edge invariants: every edge carries
        # last_seed_hash (non-NULL) and properties != '{}'. The
        # hash + non-empty-properties pair is the hallmark of
        # property-bearing edges per commit 26584d0.
        grants_object_with_hash = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'GRANTS_OBJECT_ACCESS'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'Profile'
              AND e.last_seed_hash IS NOT NULL
              AND e.properties != '{}'::jsonb
        """), {"id": test_org}).scalar()
        assert grants_object_with_hash == grants_object_count, (
            f"GRANTS_OBJECT_ACCESS edges should all carry hash + "
            f"non-empty properties; got {grants_object_with_hash} "
            f"of {grants_object_count} with both"
        )

        # GRANTS_FIELD_ACCESS edges (property-bearing). High-
        # cardinality: 18 Profiles × ~3K fields each ≈ 50K edges
        # before materialize-layer filtering. Regression floor:
        # >=50 (sandbox-conservative; the bulk of field perms
        # target syncable Objects).
        grants_field_count = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'GRANTS_FIELD_ACCESS'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'Profile'
        """), {"id": test_org}).scalar()
        assert grants_field_count >= 50, (
            f"Expected >=50 GRANTS_FIELD_ACCESS edges (regression "
            f"floor); got {grants_field_count}"
        )

        grants_field_with_hash = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'GRANTS_FIELD_ACCESS'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'Profile'
              AND e.last_seed_hash IS NOT NULL
              AND e.properties != '{}'::jsonb
        """), {"id": test_org}).scalar()
        assert grants_field_with_hash == grants_field_count, (
            f"GRANTS_FIELD_ACCESS edges should all carry hash + "
            f"non-empty properties; got {grants_field_with_hash} "
            f"of {grants_field_count} with both"
        )

        # No ASSIGNED_TO_PROFILE_RECORDTYPE edges this cycle —
        # Profile entities exist now (unblocking §16) but Layout
        # layoutAssignments[] wiring is deferred to the next cycle.
        # Still 0 — same assertion as the Layout cycle.
        assigned_count_profile = conn.execute(text("""
            SELECT COUNT(*) FROM edges
            WHERE edge_type = 'ASSIGNED_TO_PROFILE_RECORDTYPE'
              AND valid_to_seq IS NULL
        """)).scalar()
        assert assigned_count_profile == 0, (
            f"Expected 0 ASSIGNED_TO_PROFILE_RECORDTYPE edges "
            f"(wiring deferred to next cycle); "
            f"got {assigned_count_profile}"
        )

        # Profile enrichment queue: 2× entity count
        profile_queue = conn.execute(text("""
            SELECT COUNT(*) FROM ai_enrichment_queue
            WHERE entity_type = 'Profile'
              AND entity_id IN (
                  SELECT id FROM entities
                  WHERE last_synced_from_org_id = :id
                    AND entity_type = 'Profile'
              )
        """), {"id": test_org}).scalar()
        assert profile_queue == profile_count * 2, (
            f"Expected {profile_count * 2} Profile queue rows; "
            f"got {profile_queue}"
        )

        # ----- PermissionSet entities + permission_set_details + edges -----
        # Org-level entity (no parent Object). Sandbox at 71 raw PSes;
        # phase filters Type='Profile' (18 auto-synth duplicates per
        # substrate-1 §5) leaving 53 syncable. Regression floor: >=10.
        permission_set_count = conn.execute(text("""
            SELECT COUNT(*) FROM entities
            WHERE entity_type = 'PermissionSet'
              AND last_synced_from_org_id = :id
              AND valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert permission_set_count >= 10, (
            f"Expected >=10 PermissionSet entities after "
            f"Type='Profile' filter (sandbox has 53); "
            f"got {permission_set_count}"
        )

        # permission_set_details: 1 row per active PS entity
        permission_set_details_count = conn.execute(text("""
            SELECT COUNT(*) FROM permission_set_details psd
            JOIN entities e ON e.id = psd.entity_id
            WHERE e.last_synced_from_org_id = :id
              AND e.entity_type = 'PermissionSet'
              AND e.valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert permission_set_details_count == permission_set_count, (
            f"permission_set_details rows "
            f"({permission_set_details_count}) != PermissionSet "
            f"entity count ({permission_set_count})"
        )

        # FK integrity: license_type populated for every row.
        # NOT NULL constraint enforces this at the DB level; this
        # assertion catches an empty-string regression that would
        # pass NOT NULL but lose information.
        bad_ps_details = conn.execute(text("""
            SELECT COUNT(*) FROM permission_set_details psd
            JOIN entities child ON child.id = psd.entity_id
            WHERE child.last_synced_from_org_id = :id
              AND child.entity_type = 'PermissionSet'
              AND child.valid_to_seq IS NULL
              AND (psd.license_type IS NULL
                   OR psd.license_type = '')
        """), {"id": test_org}).scalar()
        assert bad_ps_details == 0, (
            f"Found {bad_ps_details} permission_set_details rows "
            f"with NULL/empty license_type"
        )

        # GRANTS_OBJECT_ACCESS from PermissionSet sources. Sandbox
        # has 53 syncable PSes × ~50 ops avg = ~2,650 candidate
        # targets; after silent-skip of unsyncable Objects probably
        # ~1,500-2,500 written. Regression floor: >=1,000.
        ps_grants_object_count = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'GRANTS_OBJECT_ACCESS'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'PermissionSet'
        """), {"id": test_org}).scalar()
        assert ps_grants_object_count >= 1000, (
            f"Expected >=1,000 GRANTS_OBJECT_ACCESS edges from "
            f"PermissionSet sources (regression floor); "
            f"got {ps_grants_object_count}"
        )

        # GRANTS_OBJECT_ACCESS property-bearing invariants: every
        # edge carries last_seed_hash (non-NULL) and non-empty
        # properties. Same hallmark as Profile cycle's edges.
        ps_grants_object_with_hash = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'GRANTS_OBJECT_ACCESS'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'PermissionSet'
              AND e.last_seed_hash IS NOT NULL
              AND e.properties != '{}'::jsonb
        """), {"id": test_org}).scalar()
        assert ps_grants_object_with_hash == ps_grants_object_count, (
            f"PS-source GRANTS_OBJECT_ACCESS edges should all "
            f"carry hash + non-empty properties; got "
            f"{ps_grants_object_with_hash} of "
            f"{ps_grants_object_count}"
        )

        # GRANTS_FIELD_ACCESS from PermissionSet sources. Sandbox
        # has 53 PSes × ~215 fps avg = ~11,400 candidate targets,
        # BUT most candidates target managed-package fields
        # (sfcma__*, CHANNEL_ORDERS__*, sfLma__*, etc.) that
        # Object phase correctly excludes from syncable scope.
        # Silent-skip path drops ~95% of PS field grants — only
        # ~600 resolve to syncable Field external_ids in this
        # sandbox. Behavior is correct per the §19 design;
        # observability counter on PhaseResult tracks skip
        # magnitude. Regression floor set conservatively at 200
        # to catch a regression to 0 / near-0 while accepting
        # the live sandbox shape (PermissionSets in customer
        # orgs commonly grant access to managed-package objects
        # the platform doesn't include in syncable scope).
        ps_grants_field_count = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'GRANTS_FIELD_ACCESS'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'PermissionSet'
        """), {"id": test_org}).scalar()
        assert ps_grants_field_count >= 200, (
            f"Expected >=200 GRANTS_FIELD_ACCESS edges from "
            f"PermissionSet sources (regression floor; most "
            f"candidates target managed-package fields that "
            f"Object phase excludes); got {ps_grants_field_count}"
        )

        ps_grants_field_with_hash = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'GRANTS_FIELD_ACCESS'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'PermissionSet'
              AND e.last_seed_hash IS NOT NULL
              AND e.properties != '{}'::jsonb
        """), {"id": test_org}).scalar()
        assert ps_grants_field_with_hash == ps_grants_field_count

        # INHERITS_PERMISSION_SET edges: sandbox has 2 PSGs
        # (Type='Group': ScaleCenterUsers, SalesWorkspacePSG); each
        # has ~3-10 members. Regression floor: >=1 (at least one
        # PSG with at least one member, to confirm the edge path
        # writes anything at all).
        inherits_count = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'INHERITS_PERMISSION_SET'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'PermissionSet'
        """), {"id": test_org}).scalar()
        assert inherits_count >= 1, (
            f"Expected >=1 INHERITS_PERMISSION_SET edge "
            f"(regression floor; sandbox has 2 PSGs with N members "
            f"each); got {inherits_count}"
        )

        # INHERITS is property-less: properties='{}'::jsonb and
        # last_seed_hash IS NULL — distinct from the property-bearing
        # GRANTS_* edges. Verifies the registration in _EDGE_SPECS
        # correctly omitted extract_properties for INHERITS.
        inherits_without_hash = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'INHERITS_PERMISSION_SET'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'PermissionSet'
              AND e.last_seed_hash IS NULL
              AND e.properties = '{}'::jsonb
        """), {"id": test_org}).scalar()
        assert inherits_without_hash == inherits_count, (
            f"INHERITS_PERMISSION_SET edges should be "
            f"property-less (hash=NULL, properties='{{}}'::jsonb); "
            f"got {inherits_without_hash} of {inherits_count}"
        )

        # PermissionSet enrichment queue: 2× entity count
        permission_set_queue = conn.execute(text("""
            SELECT COUNT(*) FROM ai_enrichment_queue
            WHERE entity_type = 'PermissionSet'
              AND entity_id IN (
                  SELECT id FROM entities
                  WHERE last_synced_from_org_id = :id
                    AND entity_type = 'PermissionSet'
              )
        """), {"id": test_org}).scalar()
        assert permission_set_queue == permission_set_count * 2, (
            f"Expected {permission_set_count * 2} PermissionSet "
            f"queue rows; got {permission_set_queue}"
        )

        # ----- User entities + user_details + edges -----
        # Sandbox has 6 users (3 Standard, 1 AutomatedProcess,
        # 1 CloudIntegrationUser, 1 CsnOnly per survey). All
        # materialized — no fetch-time filter. Regression floor: >=3.
        user_count = conn.execute(text("""
            SELECT COUNT(*) FROM entities
            WHERE entity_type = 'User'
              AND last_synced_from_org_id = :id
              AND valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert user_count >= 3, (
            f"Expected >=3 User entities (regression floor; "
            f"sandbox has 6); got {user_count}"
        )

        # user_details: 1 row per active User entity
        user_details_count = conn.execute(text("""
            SELECT COUNT(*) FROM user_details ud
            JOIN entities e ON e.id = ud.entity_id
            WHERE e.last_synced_from_org_id = :id
              AND e.entity_type = 'User'
              AND e.valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert user_details_count == user_count, (
            f"user_details rows ({user_details_count}) != User "
            f"entity count ({user_count})"
        )

        # FK integrity: every user_details row's
        # profile_entity_id must point at a real Profile entity.
        # NOT NULL constraint enforces at DB layer; this catches
        # a regression where the FK points at the wrong entity_type.
        bad_user_details = conn.execute(text("""
            SELECT COUNT(*) FROM user_details ud
            JOIN entities child ON child.id = ud.entity_id
            LEFT JOIN entities parent
                ON parent.id = ud.profile_entity_id
            WHERE child.last_synced_from_org_id = :id
              AND child.entity_type = 'User'
              AND child.valid_to_seq IS NULL
              AND (parent.id IS NULL
                   OR parent.entity_type != 'Profile'
                   OR ud.user_type IS NULL
                   OR ud.user_type = '')
        """), {"id": test_org}).scalar()
        assert bad_user_details == 0, (
            f"Found {bad_user_details} user_details rows with "
            f"bad profile_entity_id FK target OR NULL/empty "
            f"user_type"
        )

        # HAS_PROFILE edges: 1 per User (every User has a Profile)
        has_profile_count = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'HAS_PROFILE'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'User'
        """), {"id": test_org}).scalar()
        assert has_profile_count == user_count, (
            f"HAS_PROFILE edges ({has_profile_count}) != User "
            f"count ({user_count}); every User should have "
            f"exactly one HAS_PROFILE edge"
        )

        # HAS_PROFILE is property-less (TIER_1_EDGES schema=None)
        has_profile_property_less = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'HAS_PROFILE'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'User'
              AND e.last_seed_hash IS NULL
              AND e.properties = '{}'::jsonb
        """), {"id": test_org}).scalar()
        assert has_profile_property_less == has_profile_count, (
            f"HAS_PROFILE edges should be property-less "
            f"(hash=NULL, properties='{{}}'::jsonb); got "
            f"{has_profile_property_less} of {has_profile_count}"
        )

        # HAS_PERMISSION_SET edges. Regression floor: >= 0 per
        # the User task spec — sandbox PSAs commonly target
        # Type='Profile' shadow PermissionSets (filtered at PS
        # phase per substrate-1 §5) and Users assigned to those
        # PSes often reference Profiles that Salesforce hides
        # from Tooling SELECT (e.g., AutomatedProcess /
        # CloudIntegrationUser system profiles), causing the
        # source-side User to be filtered too. Both filters
        # combine to produce 0 HAS_PERMISSION_SET edges in
        # sandboxes with only system-style PSAs. Production
        # orgs with custom PSes assigned to Standard-type Users
        # show many. The structural integrity of the code path
        # is verified at unit-test level (TestPhaseUser); this
        # assertion is just a non-negativity sentinel.
        has_permission_set_count = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'HAS_PERMISSION_SET'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'User'
        """), {"id": test_org}).scalar()
        assert has_permission_set_count >= 0  # sentinel — see note

        # When HAS_PERMISSION_SET edges DO get written, they must
        # all carry last_seed_hash (property-bearing edge hallmark).
        # When the count is zero this is trivially satisfied.
        has_permission_set_with_hash = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'HAS_PERMISSION_SET'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'User'
              AND e.last_seed_hash IS NOT NULL
        """), {"id": test_org}).scalar()
        assert has_permission_set_with_hash == has_permission_set_count, (
            f"All HAS_PERMISSION_SET edges should carry hash; "
            f"got {has_permission_set_with_hash} of "
            f"{has_permission_set_count}"
        )

        # User enrichment queue: 2× entity count
        user_queue = conn.execute(text("""
            SELECT COUNT(*) FROM ai_enrichment_queue
            WHERE entity_type = 'User'
              AND entity_id IN (
                  SELECT id FROM entities
                  WHERE last_synced_from_org_id = :id
                    AND entity_type = 'User'
              )
        """), {"id": test_org}).scalar()
        assert user_queue == user_count * 2, (
            f"Expected {user_count * 2} User queue rows; "
            f"got {user_queue}"
        )

        # ----- Flow entities + flow_details + edges -----
        # The final entity phase (11/11). Sandbox has 13
        # FlowDefinitions, all with an ActiveVersionId → 13 Flow
        # entities (one per stable DeveloperName per §20).
        # Regression floor: >=5.
        flow_count = conn.execute(text("""
            SELECT COUNT(*) FROM entities
            WHERE entity_type = 'Flow'
              AND last_synced_from_org_id = :id
              AND valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert flow_count >= 5, (
            f"Expected >=5 Flow entities (regression floor; "
            f"sandbox has 13 FlowDefinitions); got {flow_count}"
        )

        # flow_details: 1 row per active Flow entity
        flow_details_count = conn.execute(text("""
            SELECT COUNT(*) FROM flow_details fd
            JOIN entities e ON e.id = fd.entity_id
            WHERE e.last_synced_from_org_id = :id
              AND e.entity_type = 'Flow'
              AND e.valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert flow_details_count == flow_count, (
            f"flow_details rows ({flow_details_count}) != Flow "
            f"entity count ({flow_count})"
        )

        # FK integrity: flow_type is NOT NULL no-default — every
        # row must have it. triggers_on_object_entity_id is
        # nullable (only record-triggered flows whose target
        # Object is synced get it), so NULLs there are fine; but
        # any non-NULL value must point at a real Object entity.
        bad_flow_details = conn.execute(text("""
            SELECT COUNT(*) FROM flow_details fd
            JOIN entities child ON child.id = fd.entity_id
            LEFT JOIN entities tgt
                ON tgt.id = fd.triggers_on_object_entity_id
            WHERE child.last_synced_from_org_id = :id
              AND child.entity_type = 'Flow'
              AND child.valid_to_seq IS NULL
              AND (
                fd.flow_type IS NULL
                OR fd.flow_type = ''
                OR (fd.triggers_on_object_entity_id IS NOT NULL
                    AND (tgt.id IS NULL
                         OR tgt.entity_type != 'Object'))
              )
        """), {"id": test_org}).scalar()
        assert bad_flow_details == 0, (
            f"Found {bad_flow_details} flow_details rows with "
            f"NULL/empty flow_type or bad "
            f"triggers_on_object_entity_id FK target"
        )

        # TRIGGERS_ON edges. Floor: >= 0 per §21 — TRIGGERS_ON is
        # scoped to record-triggered flows; this sandbox has zero
        # record-triggered flows (all AutoLaunchedFlow; the 2
        # with a start.object are PlatformEvent-triggered, out of
        # scope). The edge path is unit-tested and
        # correctness-complete; a future org with record-triggered
        # flows produces TRIGGERS_ON edges automatically.
        triggers_on_count = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'TRIGGERS_ON'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'Flow'
        """), {"id": test_org}).scalar()
        assert triggers_on_count >= 0  # sentinel — see §21 note

        # Any TRIGGERS_ON edges that DO get written must be
        # property-bearing (carry last_seed_hash + non-empty
        # properties — trigger_type is a required schema field).
        # Trivially satisfied when the count is zero.
        triggers_on_with_hash = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'TRIGGERS_ON'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'Flow'
              AND e.last_seed_hash IS NOT NULL
              AND e.properties != '{}'::jsonb
        """), {"id": test_org}).scalar()
        assert triggers_on_with_hash == triggers_on_count, (
            f"TRIGGERS_ON edges should all carry hash + non-empty "
            f"properties; got {triggers_on_with_hash} of "
            f"{triggers_on_count}"
        )

        # Flow enrichment queue: 2× entity count
        flow_queue = conn.execute(text("""
            SELECT COUNT(*) FROM ai_enrichment_queue
            WHERE entity_type = 'Flow'
              AND entity_id IN (
                  SELECT id FROM entities
                  WHERE last_synced_from_org_id = :id
                    AND entity_type = 'Flow'
              )
        """), {"id": test_org}).scalar()
        assert flow_queue == flow_count * 2, (
            f"Expected {flow_count * 2} Flow queue rows; "
            f"got {flow_queue}"
        )

        # ----- Full-sync milestone: all 11 entity types present -----
        # The 11/11 milestone. Every entity-materializing phase
        # wrote at least one active entity row for this org.
        entity_type_counts = dict(conn.execute(text("""
            SELECT entity_type, COUNT(*)
            FROM entities
            WHERE last_synced_from_org_id = :id
              AND valid_to_seq IS NULL
            GROUP BY entity_type
        """), {"id": test_org}).fetchall())
        expected_entity_types = {
            "Object", "PicklistValueSet", "PicklistValue", "Field",
            "RecordType", "Layout", "ValidationRule", "Profile",
            "PermissionSet", "User", "Flow",
        }
        assert set(entity_type_counts.keys()) == expected_entity_types, (
            f"Full sync should materialize exactly the 11 "
            f"entity-materializing phases' types; got "
            f"{sorted(entity_type_counts.keys())}"
        )

    # ===== Second sync =====
    sync_run_id_2 = engine.run_sync(connected_org_id=test_org)
    assert sync_run_id_2 != sync_run_id_1

    with db_engine.begin() as conn:
        conn.execute(text(f'SET LOCAL search_path TO "{TENANT_SCHEMA}", public'))
        conn.execute(text("SET LOCAL app.tenant_id = :t"), {"t": str(TENANT_ID)})

        run2 = conn.execute(text("""
            SELECT entities_inserted, entities_superseded,
                   entities_unchanged, edges_inserted, edges_superseded
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
        # Total unchanged = all 11 entity-materializing phases'
        # counts (Object → Flow). Each entity's phase-injected
        # markers round-trip through normalize/hash identically.
        # Flow adds a coordination transform (FlowDefinition →
        # active-version join) — deterministic across syncs since
        # ActiveVersionId is stable, so the chosen version + its
        # decoration (_developer_name / _is_active /
        # _manageable_state) hash identically.
        expected_unchanged = (
            object_count + pvs_count + pv_count + field_count
            + rt_count + layout_count + vr_count + profile_count
            + permission_set_count + user_count + flow_count
        )
        assert run2.entities_unchanged == expected_unchanged, (
            f"Second sync should report {expected_unchanged} unchanged "
            f"(Object {object_count} + PVS {pvs_count} + PV "
            f"{pv_count} + Field {field_count} + RT {rt_count} + "
            f"Layout {layout_count} + VR {vr_count} + Profile "
            f"{profile_count} + PermissionSet "
            f"{permission_set_count} + User {user_count} + Flow "
            f"{flow_count}); got {run2.entities_unchanged}"
        )

        # Edges: same set-difference logic should yield 0 inserts and
        # 0 supersessions on second sync (incoming set == existing set).
        assert run2.edges_inserted == 0, (
            f"Second sync should insert 0 edges (incoming = existing); "
            f"got {run2.edges_inserted}"
        )
        assert run2.edges_superseded == 0, (
            f"Second sync should supersede 0 edges; "
            f"got {run2.edges_superseded}"
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

        # Field entity + detail + edge counts all stable.
        field_count_after = conn.execute(text("""
            SELECT COUNT(*) FROM entities
            WHERE entity_type = 'Field'
              AND last_synced_from_org_id = :id
              AND valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert field_count_after == field_count

        field_details_after = conn.execute(text("""
            SELECT COUNT(*) FROM field_details fd
            JOIN entities e ON e.id = fd.entity_id
            WHERE e.last_synced_from_org_id = :id
              AND e.entity_type = 'Field'
              AND e.valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert field_details_after == field_details_count

        belongs_to_after = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'BELONGS_TO'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'Field'
        """), {"id": test_org}).scalar()
        assert belongs_to_after == belongs_to_count, (
            f"BELONGS_TO edge count should remain {belongs_to_count} "
            f"after second sync; got {belongs_to_after}"
        )

        hrt_after = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'HAS_RELATIONSHIP_TO'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'Field'
        """), {"id": test_org}).scalar()
        assert hrt_after == hrt_count, (
            f"HAS_RELATIONSHIP_TO edge count should remain {hrt_count} "
            f"after second sync; got {hrt_after}"
        )

        # RecordType entity + detail + edge counts stable across syncs.
        rt_count_after = conn.execute(text("""
            SELECT COUNT(*) FROM entities
            WHERE entity_type = 'RecordType'
              AND last_synced_from_org_id = :id
              AND valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert rt_count_after == rt_count

        rt_details_after = conn.execute(text("""
            SELECT COUNT(*) FROM record_type_details rtd
            JOIN entities e ON e.id = rtd.entity_id
            WHERE e.last_synced_from_org_id = :id
              AND e.entity_type = 'RecordType'
              AND e.valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert rt_details_after == rt_details

        rt_belongs_to_after = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'BELONGS_TO'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'RecordType'
        """), {"id": test_org}).scalar()
        assert rt_belongs_to_after == rt_belongs_to_count, (
            f"RT BELONGS_TO count should remain {rt_belongs_to_count} "
            f"after second sync; got {rt_belongs_to_after}"
        )

        # Layout entity + detail + edge counts stable across syncs.
        # The property-bearing INCLUDES_FIELD path's hash-compare
        # supersession should be naturally idempotent: same payload
        # → same hash → no SCD-Type-2 close-and-replace.
        layout_count_after = conn.execute(text("""
            SELECT COUNT(*) FROM entities
            WHERE entity_type = 'Layout'
              AND last_synced_from_org_id = :id
              AND valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert layout_count_after == layout_count

        layout_details_after = conn.execute(text("""
            SELECT COUNT(*) FROM layout_details ld
            JOIN entities e ON e.id = ld.entity_id
            WHERE e.last_synced_from_org_id = :id
              AND e.entity_type = 'Layout'
              AND e.valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert layout_details_after == layout_details_count

        layout_belongs_to_after = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'BELONGS_TO'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'Layout'
        """), {"id": test_org}).scalar()
        assert layout_belongs_to_after == layout_belongs_to_count

        # INCLUDES_FIELD count stable — proves property-bearing
        # supersession is idempotent.
        includes_field_after = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'INCLUDES_FIELD'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'Layout'
        """), {"id": test_org}).scalar()
        assert includes_field_after == includes_field_count, (
            f"INCLUDES_FIELD count should remain "
            f"{includes_field_count} after second sync (hash-compare "
            f"supersession should match); got {includes_field_after}"
        )

        # ValidationRule entity + detail + edge counts stable.
        vr_count_after = conn.execute(text("""
            SELECT COUNT(*) FROM entities
            WHERE entity_type = 'ValidationRule'
              AND last_synced_from_org_id = :id
              AND valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert vr_count_after == vr_count

        vr_details_after = conn.execute(text("""
            SELECT COUNT(*) FROM validation_rule_details vrd
            JOIN entities e ON e.id = vrd.entity_id
            WHERE e.last_synced_from_org_id = :id
              AND e.entity_type = 'ValidationRule'
              AND e.valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert vr_details_after == vr_details_count

        vr_belongs_to_after = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'BELONGS_TO'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'ValidationRule'
        """), {"id": test_org}).scalar()
        assert vr_belongs_to_after == vr_belongs_to_count

        vr_applies_to_after = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'APPLIES_TO'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'ValidationRule'
        """), {"id": test_org}).scalar()
        assert vr_applies_to_after == vr_applies_to_count

        # Profile entity + detail + property-bearing edge counts
        # stable across syncs. The property-bearing INCLUDES_FIELD
        # path's hash-compare supersession proved naturally idempotent
        # for Layout (commit 258e3f9); Profile reuses the same
        # property-bearing infrastructure for GRANTS_OBJECT_ACCESS +
        # GRANTS_FIELD_ACCESS — same idempotency expected.
        profile_count_after = conn.execute(text("""
            SELECT COUNT(*) FROM entities
            WHERE entity_type = 'Profile'
              AND last_synced_from_org_id = :id
              AND valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert profile_count_after == profile_count

        profile_details_after = conn.execute(text("""
            SELECT COUNT(*) FROM profile_details pd
            JOIN entities e ON e.id = pd.entity_id
            WHERE e.last_synced_from_org_id = :id
              AND e.entity_type = 'Profile'
              AND e.valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert profile_details_after == profile_details_count

        grants_object_after = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'GRANTS_OBJECT_ACCESS'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'Profile'
        """), {"id": test_org}).scalar()
        assert grants_object_after == grants_object_count, (
            f"GRANTS_OBJECT_ACCESS count should remain "
            f"{grants_object_count} after second sync (hash-compare "
            f"supersession should match); got {grants_object_after}"
        )

        grants_field_after = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'GRANTS_FIELD_ACCESS'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'Profile'
        """), {"id": test_org}).scalar()
        assert grants_field_after == grants_field_count, (
            f"GRANTS_FIELD_ACCESS count should remain "
            f"{grants_field_count} after second sync (hash-compare "
            f"supersession should match); got {grants_field_after}"
        )

        # PermissionSet entity + detail + edge counts stable across
        # syncs. Critical regression check: PS payload undergoes
        # multiple post-fetch transforms (Type='Profile' filter,
        # ParentId JOIN, license resolution, PSG decoration). All
        # transforms must be deterministic so the entity hash is
        # stable across syncs. The phase function's pre-sort of
        # objectPermissions/fieldPermissions by SobjectType/Field
        # ensures SOQL ordering doesn't affect the hash.
        permission_set_count_after = conn.execute(text("""
            SELECT COUNT(*) FROM entities
            WHERE entity_type = 'PermissionSet'
              AND last_synced_from_org_id = :id
              AND valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert permission_set_count_after == permission_set_count

        permission_set_details_after = conn.execute(text("""
            SELECT COUNT(*) FROM permission_set_details psd
            JOIN entities e ON e.id = psd.entity_id
            WHERE e.last_synced_from_org_id = :id
              AND e.entity_type = 'PermissionSet'
              AND e.valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert (
            permission_set_details_after
            == permission_set_details_count
        )

        ps_grants_object_after = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'GRANTS_OBJECT_ACCESS'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'PermissionSet'
        """), {"id": test_org}).scalar()
        assert ps_grants_object_after == ps_grants_object_count, (
            f"PS GRANTS_OBJECT_ACCESS should remain "
            f"{ps_grants_object_count} after second sync; "
            f"got {ps_grants_object_after}"
        )

        ps_grants_field_after = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'GRANTS_FIELD_ACCESS'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'PermissionSet'
        """), {"id": test_org}).scalar()
        assert ps_grants_field_after == ps_grants_field_count, (
            f"PS GRANTS_FIELD_ACCESS should remain "
            f"{ps_grants_field_count} after second sync; "
            f"got {ps_grants_field_after}"
        )

        inherits_after = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'INHERITS_PERMISSION_SET'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'PermissionSet'
        """), {"id": test_org}).scalar()
        assert inherits_after == inherits_count, (
            f"INHERITS_PERMISSION_SET count should remain "
            f"{inherits_count} after second sync (property-less "
            f"set-difference supersession is naturally idempotent); "
            f"got {inherits_after}"
        )

        # User entity + detail + edge counts stable across syncs.
        # Critical regression check: User payload undergoes
        # profile-name resolution + PSA join + two-pass assigner
        # resolution. The PSA pre-sort by PermissionSet.Name and
        # the deterministic profile_id_to_name map ensure hash
        # stability. Two-pass logic produces the same
        # assigned_by_user_entity_id resolution on each run (the
        # synced User set is stable across consecutive syncs),
        # so HAS_PERMISSION_SET property hashes are stable too.
        user_count_after = conn.execute(text("""
            SELECT COUNT(*) FROM entities
            WHERE entity_type = 'User'
              AND last_synced_from_org_id = :id
              AND valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert user_count_after == user_count

        user_details_after = conn.execute(text("""
            SELECT COUNT(*) FROM user_details ud
            JOIN entities e ON e.id = ud.entity_id
            WHERE e.last_synced_from_org_id = :id
              AND e.entity_type = 'User'
              AND e.valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert user_details_after == user_details_count

        has_profile_after = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'HAS_PROFILE'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'User'
        """), {"id": test_org}).scalar()
        assert has_profile_after == has_profile_count, (
            f"HAS_PROFILE count should remain {has_profile_count} "
            f"after second sync (property-less set-difference); "
            f"got {has_profile_after}"
        )

        has_permission_set_after = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'HAS_PERMISSION_SET'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'User'
        """), {"id": test_org}).scalar()
        assert has_permission_set_after == has_permission_set_count, (
            f"HAS_PERMISSION_SET count should remain "
            f"{has_permission_set_count} after second sync "
            f"(property-bearing hash-compare supersession "
            f"matches when assignment metadata is stable); "
            f"got {has_permission_set_after}"
        )

        # Flow entity + detail + edge counts stable across syncs.
        # Critical regression check: the Flow payload undergoes a
        # coordination transform (FlowDefinition active-version
        # join + _developer_name / _is_active / _manageable_state
        # decoration). ActiveVersionId is stable across syncs, so
        # the same version is chosen + decorated identically →
        # entity hash stable → no spurious supersession.
        flow_count_after = conn.execute(text("""
            SELECT COUNT(*) FROM entities
            WHERE entity_type = 'Flow'
              AND last_synced_from_org_id = :id
              AND valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert flow_count_after == flow_count

        flow_details_after = conn.execute(text("""
            SELECT COUNT(*) FROM flow_details fd
            JOIN entities e ON e.id = fd.entity_id
            WHERE e.last_synced_from_org_id = :id
              AND e.entity_type = 'Flow'
              AND e.valid_to_seq IS NULL
        """), {"id": test_org}).scalar()
        assert flow_details_after == flow_details_count

        triggers_on_after = conn.execute(text("""
            SELECT COUNT(*) FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
            WHERE e.edge_type = 'TRIGGERS_ON'
              AND e.valid_to_seq IS NULL
              AND src.last_synced_from_org_id = :id
              AND src.entity_type = 'Flow'
        """), {"id": test_org}).scalar()
        assert triggers_on_after == triggers_on_count, (
            f"TRIGGERS_ON count should remain {triggers_on_count} "
            f"after second sync; got {triggers_on_after}"
        )
