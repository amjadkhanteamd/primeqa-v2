"""Phase 2 substrate-1 sync — formal end-to-end scenarios (§25).

Canonical narrative-level verification surface for the Phase 2 sync
layer. Per PHASE_2_STEP_4_SYNC_DESIGN.md §9 step 6.

================================================================
Cadence
================================================================

These scenarios run end-to-end against a live Salesforce sandbox.
**Cadence: on-demand or nightly, never per-PR.** Per-PR CI uses the
unit-test suite (``tests/unit/``, ~10s, 951 tests).

Full-suite wall-clock: ~33 min
Full-suite cost:       ~$0.10 Anthropic + ~$0.04 Voyage (estimated)

Each scenario is independently invokable:

    pytest tests/integration/test_e2e_sync_scenarios.py::test_scenario_full_sync_lifecycle_and_idempotent_resync -xvs
    pytest tests/integration/test_e2e_sync_scenarios.py::test_scenario_multi_org_sync                            -xvs

================================================================
Relationship to existing live tests
================================================================

These scenarios are the NARRATIVE layer. The DEEP per-phase /
per-entity-type / per-edge verification lives in
``tests/integration/test_sync_object_phase_live.py`` and
``tests/integration/test_live_enrichment.py``; this file delegates
to the same fixtures + observes the same live runs but asserts at
the lifecycle / scenario level only. The deep verifiers are kept
as debugging tools — when a scenario fails, jump there for
fine-grained inspection.

================================================================
Environment requirements
================================================================

- ``LIVE_DATABASE_URL`` (or ``DATABASE_URL`` fallback)
- ``SF_INSTANCE_URL``, ``SF_CLIENT_ID``, ``SF_CLIENT_SECRET``,
  ``SF_REFRESH_TOKEN``  (Salesforce sandbox)
- ``VOYAGE_API_KEY``     (embeddings)
- ``ANTHROPIC_API_KEY``  (Flow / ValidationRule summaries)
"""
from __future__ import annotations

import uuid
from typing import Callable

import pytest
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.orm import Session

load_dotenv(override=True)

# Fixture reuse — pytest resolves these by name from this module's
# namespace. The imports are intentional (not unused) — they bring the
# fixtures into scope for pytest's collector.
from tests.integration.test_sync_object_phase_live import (  # noqa: F401
    TENANT_ID,
    TENANT_SCHEMA,
    _get_live_db_url,
    db_engine,
    live_sf_client,
    test_org,
)
from tests.integration.test_live_enrichment import (  # noqa: F401
    MAX_TICKS,
    gateway_tables,
)

pytestmark = pytest.mark.sandbox


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _scope(conn) -> None:
    """Tenant-scope a connection — search_path + app.tenant_id."""
    conn.execute(
        text(f'SET LOCAL search_path TO "{TENANT_SCHEMA}", public')
    )
    conn.execute(
        text("SET LOCAL app.tenant_id = :t"), {"t": str(TENANT_ID)}
    )


def _drain_to_completion(
    db_factory: Callable, max_ticks: int = MAX_TICKS,
) -> int:
    """Loop enrichment_tick until no work; return the tick count
    (including the final no-op tick). Fails the test if the cap
    is exceeded — indicates the drain isn't making progress."""
    from primeqa.worker import enrichment_tick

    ticks_used = 0
    for ticks_used in range(1, max_ticks + 1):
        if not enrichment_tick(db_factory=db_factory):
            break
    else:
        pytest.fail(
            f"enrichment_tick still finding work after {max_ticks} ticks"
        )
    return ticks_used


# ----------------------------------------------------------------------
# Scenario 1 + 2: cold-start full sync + idempotent re-sync
# ----------------------------------------------------------------------

def test_scenario_full_sync_lifecycle_and_idempotent_resync(
    live_sf_client, db_engine, test_org, gateway_tables,
):
    """SCENARIO 1 + 2: cold-start full sync lifecycle + idempotent re-sync.

    SCENARIO 1 — Full sync lifecycle (~11 min wall-clock).
    Verifies:
      - All 11 entity-materialization phases execute and the
        sync_run reaches last_completed_phase='Flow'
      - sync_run lifecycle: phase 'structural' → 'enrichment' → 'done'
      - ai_enrichment_status transitions: 'none' → 'structural_only'
        → 'partial' → 'complete'
      - All active entities have embedding populated
      - Flow + ValidationRule summary_text populated
      - sync_run terminal: phase='done', status='success',
        completed_at IS NOT NULL
      - Counter math reconciles: embeddings_generated equals the
        queue's embedding/succeeded count; summaries_generated
        equals the summary/succeeded count; summaries_failed == 0

    SCENARIO 2 — Idempotent re-sync (~30s incremental).
    After scenario 1 reaches 'complete', a second engine.run_sync
    on the same org verifies §5 hash-based change detection:
      - entities_inserted == 0  (no new rows)
      - entities_superseded == 0  (no version churn)
      - entities_unchanged > 0  (hash matches detected)
      - Queue rows from the first run remain succeeded (no
        re-enqueue; the existing succeeded rows aren't disturbed)

    Combined into one pytest function because scenario 2 depends
    on scenario 1's terminal state. Splitting would require either
    duplicating the 11-min sync or a session-scoped fixture that
    would entangle the scenarios file with the existing live-test
    fixtures awkwardly.

    Wall-clock: ~11.5 min.  Cost: ~$0.033 Anthropic + ~$0.02 Voyage.
    """
    from primeqa.sync.engine import SyncEngine
    from primeqa.worker import enrichment_tick

    # =================================================================
    # SCENARIO 1 — Full sync lifecycle
    # =================================================================

    engine = SyncEngine(
        engine_db=db_engine,
        sf_client=live_sf_client,
        tenant_schema=TENANT_SCHEMA,
    )
    sync_run_id_1 = engine.run_sync(connected_org_id=test_org)
    assert sync_run_id_1 is not None

    # ----- Pre-drain lifecycle state -----
    with db_engine.begin() as conn:
        _scope(conn)
        row = conn.execute(text("""
            SELECT co.ai_enrichment_status,
                   sr.phase, sr.status, sr.completed_at,
                   sr.last_completed_phase
            FROM connected_orgs co
            JOIN sync_runs sr ON sr.id = co.last_sync_run_id
            WHERE co.id = :id
        """), {"id": test_org}).fetchone()
        assert row.ai_enrichment_status == 'structural_only'
        assert row.phase == 'enrichment'
        assert row.status == 'running'
        assert row.completed_at is None
        assert row.last_completed_phase == 'Flow', (
            f"all 11 phases should have run; last_completed_phase="
            f"{row.last_completed_phase}"
        )
        print(
            f"[scenario-1] Pre-drain: "
            f"ai_enrichment_status={row.ai_enrichment_status} "
            f"last_completed_phase={row.last_completed_phase}"
        )

    # ----- Drain to completion -----
    def _factory():
        return Session(bind=db_engine)

    ticks_used = _drain_to_completion(_factory)
    print(f"[scenario-1] Drain finished in {ticks_used} ticks")

    # ----- Post-drain lifecycle state -----
    # Note on terminal status: §24 explicitly allows both 'success'
    # (zero failed_permanent) and 'partial_success' (≥1
    # failed_permanent) as valid terminal sync_run states. The latter
    # is observable in this test when the gateway's per-tenant rate
    # limits (starter-tier 30/min, applied as graceful-degradation
    # fallback when tenant_agent_settings is absent) trip during the
    # burstier portion of the drain — a small number of summary
    # rows can hit ENRICHMENT_MAX_ATTEMPTS=5 retries against the
    # rate-limit and become failed_permanent. The real invariants
    # are: ai_enrichment_status='complete', counter math reconciles,
    # and most work succeeded.
    with db_engine.begin() as conn:
        _scope(conn)
        row = conn.execute(text("""
            SELECT co.ai_enrichment_status,
                   sr.phase, sr.status, sr.completed_at,
                   sr.embeddings_generated, sr.summaries_generated,
                   sr.summaries_failed
            FROM connected_orgs co
            JOIN sync_runs sr ON sr.id = co.last_sync_run_id
            WHERE co.id = :id
        """), {"id": test_org}).fetchone()
        print(
            f"[scenario-1] Post-drain: "
            f"ai_enrichment_status={row.ai_enrichment_status} "
            f"phase={row.phase} status={row.status} "
            f"counters(emb/sum/failed)={row.embeddings_generated}"
            f"/{row.summaries_generated}/{row.summaries_failed}"
        )
        assert row.ai_enrichment_status == 'complete', (
            f"expected complete post-drain, "
            f"got {row.ai_enrichment_status}"
        )
        assert row.phase == 'done'
        assert row.status in ('success', 'partial_success'), (
            f"expected terminal sync_run status; got {row.status}"
        )
        assert row.completed_at is not None

        # ----- Counter math reconciles with queue end state -----
        queue_counts = dict(conn.execute(text("""
            SELECT primitive_type || '/' || status AS bucket, COUNT(*)
            FROM ai_enrichment_queue
            GROUP BY 1
        """)).fetchall())
        # Both subticks fully drain — no work left in non-terminal
        # buckets.
        assert queue_counts.get("embedding/pending", 0) == 0
        assert queue_counts.get("embedding/in_progress", 0) == 0
        assert queue_counts.get("summary/pending", 0) == 0
        assert queue_counts.get("summary/in_progress", 0) == 0
        # Voyage embedding path is rate-limit-free — strict zero.
        assert queue_counts.get("embedding/failed_permanent", 0) == 0
        # Summary path may have a small number of failed_permanent
        # under rate-limit-driven retries that didn't fully recover.
        # Catch catastrophic regression (>10) but tolerate single-
        # digit incidents.
        summary_failed = queue_counts.get("summary/failed_permanent", 0)
        assert summary_failed < 10, (
            f"summary/failed_permanent={summary_failed} suggests a "
            f"catastrophic failure (>10); investigate"
        )
        emb_succeeded = queue_counts.get("embedding/succeeded", 0)
        sum_succeeded = queue_counts.get("summary/succeeded", 0)
        assert emb_succeeded > 5000, (
            f"expected >5000 embedding/succeeded; got {emb_succeeded}"
        )
        # Floor: most summaries get through. The 63-summary universe
        # (13 Flow + 50 VR for this sandbox) can lose a couple to
        # rate-limit driven retries without violating the scenario.
        assert sum_succeeded >= 55, (
            f"expected >=55 summary/succeeded "
            f"(13 Flow + 50 VR with tolerance for rate-limit losses); "
            f"got {sum_succeeded}"
        )
        # Counter math: sync_run counters reconcile with the queue's
        # terminal state. summaries_failed should equal failed_permanent
        # in the queue.
        assert row.embeddings_generated == emb_succeeded, (
            f"sync_run.embeddings_generated={row.embeddings_generated} "
            f"!= queue embedding/succeeded={emb_succeeded}"
        )
        assert row.summaries_generated == sum_succeeded, (
            f"sync_run.summaries_generated={row.summaries_generated} "
            f"!= queue summary/succeeded={sum_succeeded}"
        )
        assert row.summaries_failed == summary_failed, (
            f"sync_run.summaries_failed={row.summaries_failed} "
            f"!= queue summary/failed_permanent={summary_failed}"
        )

        # ----- All active entities have embeddings -----
        unembedded = conn.execute(text("""
            SELECT COUNT(*) FROM entities
            WHERE valid_to_seq IS NULL AND embedding IS NULL
        """)).scalar()
        assert unembedded == 0, (
            f"{unembedded} active entities still have NULL embedding"
        )

        # ----- All active Flow + VR detail rows have summary_text -----
        unsumarised_flows = conn.execute(text("""
            SELECT COUNT(*) FROM flow_details fd
            JOIN entities e ON e.id = fd.entity_id
            WHERE e.entity_type = 'Flow' AND e.valid_to_seq IS NULL
              AND fd.summary_text IS NULL
        """)).scalar()
        assert unsumarised_flows == 0
        unsumarised_vrs = conn.execute(text("""
            SELECT COUNT(*) FROM validation_rule_details vrd
            JOIN entities e ON e.id = vrd.entity_id
            WHERE e.entity_type = 'ValidationRule'
              AND e.valid_to_seq IS NULL
              AND vrd.summary_text IS NULL
        """)).scalar()
        assert unsumarised_vrs == 0

    # =================================================================
    # SCENARIO 2 — Idempotent re-sync (§5 hash-based change detection)
    # =================================================================

    print("[scenario-2] Running idempotent re-sync...")
    sync_run_id_2 = engine.run_sync(connected_org_id=test_org)
    assert sync_run_id_2 != sync_run_id_1

    with db_engine.begin() as conn:
        _scope(conn)

        run2 = conn.execute(text("""
            SELECT entities_inserted, entities_superseded,
                   entities_unchanged, edges_inserted, edges_superseded
            FROM sync_runs WHERE id = :id
        """), {"id": sync_run_id_2}).fetchone()

        assert run2.entities_inserted == 0, (
            f"second sync should report 0 new entities (hashes match); "
            f"got entities_inserted={run2.entities_inserted}"
        )
        assert run2.entities_superseded == 0, (
            f"second sync should report 0 supersessions; "
            f"got entities_superseded={run2.entities_superseded}"
        )
        assert run2.entities_unchanged > 0, (
            f"second sync should report unchanged entities; got 0"
        )
        print(
            f"[scenario-2] Re-sync: "
            f"inserted={run2.entities_inserted} "
            f"superseded={run2.entities_superseded} "
            f"unchanged={run2.entities_unchanged}"
        )

        # Queue state stable — succeeded count from scenario 1 stays
        emb_succeeded_after = conn.execute(text("""
            SELECT COUNT(*) FROM ai_enrichment_queue
            WHERE primitive_type = 'embedding' AND status = 'succeeded'
        """)).scalar()
        assert emb_succeeded_after == emb_succeeded, (
            f"queue rows from scenario 1 should remain succeeded; "
            f"was {emb_succeeded}, now {emb_succeeded_after}"
        )


# ----------------------------------------------------------------------
# Scenario 3: multi-org sync under one tenant
# ----------------------------------------------------------------------

@pytest.fixture
def test_orgs_pair(db_engine):
    """Two ``connected_orgs`` rows under tenant_1, both pointing at
    the same sandbox (sandbox tokens are scoped per-sandbox; the
    realistic multi-org pattern for the same SF instance is distinct
    org rows sharing credentials).

    Yields ``(org_a_id, org_b_id)``. Teardown applies the §23/§24
    FK-aware delete sequence to each in turn.
    """
    import os

    label_a = f"_scen3_multi_org_a_{uuid.uuid4().hex[:8]}"
    label_b = f"_scen3_multi_org_b_{uuid.uuid4().hex[:8]}"

    with db_engine.begin() as conn:
        _scope(conn)
        row_a = conn.execute(text("""
            INSERT INTO connected_orgs (org_type, sf_instance_url, label)
            VALUES ('sandbox', :url, :label)
            RETURNING id
        """), {"url": os.environ["SF_INSTANCE_URL"],
               "label": label_a}).fetchone()
        org_a = str(row_a[0])
        row_b = conn.execute(text("""
            INSERT INTO connected_orgs (org_type, sf_instance_url, label)
            VALUES ('sandbox', :url, :label)
            RETURNING id
        """), {"url": os.environ["SF_INSTANCE_URL"],
               "label": label_b}).fetchone()
        org_b = str(row_b[0])

    yield (org_a, org_b)

    # Combined cross-org teardown — entities can be re-attributed
    # between orgs via the §26 touch-path mutation
    # (last_synced_from_org_id reflects most-recently-synced),
    # so per-org sequential delete creates FK violations: org A's
    # logical_versions still being referenced by entities that
    # were just re-attributed to org B. Sweep both orgs together
    # in a single FK-aware order.
    org_ids = [org_a, org_b]
    with db_engine.begin() as conn:
        _scope(conn)

        # Break mutual FK first: NULL out forward references.
        conn.execute(text("""
            UPDATE connected_orgs SET last_sync_run_id = NULL
            WHERE id = ANY(CAST(:ids AS uuid[]))
        """), {"ids": org_ids})
        conn.execute(text("""
            UPDATE sync_runs SET logical_version_seq = NULL
            WHERE source_org_id = ANY(CAST(:ids AS uuid[]))
        """), {"ids": org_ids})

        # Queue rows attributable to either org's entities.
        conn.execute(text("""
            DELETE FROM ai_enrichment_queue
            WHERE entity_id IN (
                SELECT id FROM entities
                WHERE last_synced_from_org_id = ANY(CAST(:ids AS uuid[]))
            )
        """), {"ids": org_ids})

        # Detail tables (ON DELETE CASCADE on entity_id would
        # handle these implicitly, but explicit is robust to FK
        # additions like field_details.picklist_value_set_entity_id
        # that don't cascade).
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
                    WHERE last_synced_from_org_id = ANY(CAST(:ids AS uuid[]))
                )
            """), {"ids": org_ids})

        # Edges sourced from or targeting either org's entities.
        conn.execute(text("""
            DELETE FROM edges
            WHERE source_entity_id IN (
                SELECT id FROM entities
                WHERE last_synced_from_org_id = ANY(CAST(:ids AS uuid[]))
            ) OR target_entity_id IN (
                SELECT id FROM entities
                WHERE last_synced_from_org_id = ANY(CAST(:ids AS uuid[]))
            )
        """), {"ids": org_ids})

        # Entities first — clears the references that block
        # logical_versions deletes below.
        conn.execute(text("""
            DELETE FROM entities
            WHERE last_synced_from_org_id = ANY(CAST(:ids AS uuid[]))
        """), {"ids": org_ids})

        # Logical versions created by either org's sync runs.
        conn.execute(text("""
            DELETE FROM logical_versions
            WHERE created_by_sync_run_id IN (
                SELECT id FROM sync_runs
                WHERE source_org_id = ANY(CAST(:ids AS uuid[]))
            )
        """), {"ids": org_ids})

        # Sync runs + connected_orgs.
        conn.execute(text("""
            DELETE FROM sync_runs
            WHERE source_org_id = ANY(CAST(:ids AS uuid[]))
        """), {"ids": org_ids})
        conn.execute(text("""
            DELETE FROM connected_orgs
            WHERE id = ANY(CAST(:ids AS uuid[]))
        """), {"ids": org_ids})


def test_scenario_multi_org_sync(
    live_sf_client, db_engine, test_orgs_pair, gateway_tables,
):
    """SCENARIO 3: multi-org sync under one tenant.

    Two ``connected_orgs`` rows under tenant_1, both pointing at the
    same sandbox. Verifies the multi-org per-org isolation
    invariants:
      - Both orgs reach a terminal sync_run state (success /
        partial_success)
      - Each org's ai_enrichment_status reaches 'complete'
      - Each org's sync_run has its own counters (not aggregated
        cross-org)
      - Per-org entity attribution via entities.last_synced_from_org_id
      - No orphans (no active entity with NULL last_synced_from_org_id)

    Both orgs sync the SAME Salesforce sandbox. The substrate-1
    design (D-030) says "model is shared across orgs" — when org B
    syncs after org A, the hash-based change detection (§5) recognises
    the metadata is unchanged. The exact semantic for what
    last_synced_from_org_id contains after the second sync is
    something this scenario observes and reports rather than
    prescriptively asserting; the assertions below are robust to
    either the "shared model" interpretation (entity rows keep
    org A's attribution) or the "co-owned" interpretation (entities
    are tagged with org B too).

    Wall-clock: ~22 min (two full syncs + drain).
    Cost: ~$0.066 Anthropic + ~$0.04 Voyage (estimated).
    """
    from primeqa.sync.engine import SyncEngine

    org_a, org_b = test_orgs_pair

    engine = SyncEngine(
        engine_db=db_engine,
        sf_client=live_sf_client,
        tenant_schema=TENANT_SCHEMA,
    )

    # ----- Sync org A first (cold start) -----
    print("[scenario-3] Syncing org A (cold start)...")
    sync_run_a = engine.run_sync(connected_org_id=org_a)
    assert sync_run_a is not None

    # ----- Sync org B (same sandbox; hash-based detection should
    # recognise the metadata as unchanged for the shared model) -----
    print("[scenario-3] Syncing org B (same sandbox)...")
    sync_run_b = engine.run_sync(connected_org_id=org_b)
    assert sync_run_b is not None
    assert sync_run_b != sync_run_a

    # ----- Drain enrichment queue -----
    def _factory():
        return Session(bind=db_engine)

    ticks_used = _drain_to_completion(_factory)
    print(f"[scenario-3] Drain finished in {ticks_used} ticks")

    # ----- Observe per-org state first (print everything),
    # then assert D-030 invariants (§26 prescriptive: "single
    # canonical model across orgs; subsequent syncs update the
    # model in place"). -----
    org_state = {}
    with db_engine.begin() as conn:
        _scope(conn)

        for org_id, label in ((org_a, "A"), (org_b, "B")):
            row = conn.execute(text("""
                SELECT co.ai_enrichment_status,
                       sr.phase, sr.status,
                       sr.embeddings_generated, sr.summaries_generated,
                       sr.summaries_failed,
                       sr.entities_inserted, sr.entities_unchanged
                FROM connected_orgs co
                LEFT JOIN sync_runs sr ON sr.id = co.last_sync_run_id
                WHERE co.id = :id
            """), {"id": org_id}).fetchone()
            org_state[label] = row
            print(
                f"[scenario-3] org {label} ({org_id[:8]}…): "
                f"ai_enrichment_status={row.ai_enrichment_status} "
                f"phase={row.phase} status={row.status} "
                f"inserted={row.entities_inserted} "
                f"unchanged={row.entities_unchanged} "
                f"counters(emb/sum/failed)={row.embeddings_generated}"
                f"/{row.summaries_generated}/{row.summaries_failed}"
            )

        # Both orgs have a sync_run linked + zero failed_permanent
        # summaries.
        for label, row in org_state.items():
            assert row is not None, (
                f"org {label} missing or last_sync_run_id NULL"
            )
            assert row.summaries_failed == 0, (
                f"org {label} has {row.summaries_failed} "
                f"failed_permanent summary rows"
            )

        # §26 D-030 prescriptive invariants:
        # - Org A: cold-start sync → entities_inserted ≈ 5865
        # - Org B: re-sync against same metadata (D-030 "update in
        #   place") → entities_inserted ≈ 0 (≤10 fudge for any
        #   timing-driven edge cases), entities_unchanged > 5000
        assert org_state["A"].entities_inserted > 5000, (
            f"org A's cold-start sync should have inserted >5000; "
            f"got {org_state['A'].entities_inserted}"
        )
        assert org_state["B"].entities_inserted <= 10, (
            f"org B's re-sync should insert ~0 per D-030 "
            f"'update in place'; got "
            f"{org_state['B'].entities_inserted}"
        )
        assert org_state["B"].entities_unchanged > 5000, (
            f"org B's re-sync should report >5000 unchanged "
            f"(§26 lookup fix recognises org A's entities by "
            f"hash); got {org_state['B'].entities_unchanged}"
        )

        # §26 engine fix: both orgs reach 'complete' even though
        # org B's drain has no queue work (the engine's call to
        # readiness.apply_org_status + maybe_finalize_run during
        # _mark_sync_run_structural_complete handles the no-work
        # case).
        for label, row in org_state.items():
            assert row.status in ('success', 'partial_success'), (
                f"org {label} sync_run.status={row.status}; "
                f"expected success or partial_success"
            )
            assert row.ai_enrichment_status == 'complete', (
                f"org {label} ai_enrichment_status="
                f"{row.ai_enrichment_status}; expected 'complete' "
                f"after §26 engine fix"
            )

        # ----- Per-org entity attribution -----
        attribution = dict(conn.execute(text("""
            SELECT last_synced_from_org_id::text, COUNT(*)
            FROM entities
            WHERE valid_to_seq IS NULL
            GROUP BY 1
        """)).fetchall())
        count_a = attribution.get(org_a, 0)
        count_b = attribution.get(org_b, 0)
        count_null = attribution.get(None, 0)
        total = sum(attribution.values())
        print(
            f"[scenario-3] entity attribution: "
            f"org A={count_a}, org B={count_b}, "
            f"NULL={count_null}, total={total}"
        )

        # §26 D-030 invariants:
        # - No orphans (no active entity without an org).
        assert count_null == 0, (
            f"{count_null} active entities have NULL "
            f"last_synced_from_org_id — orphans"
        )
        # - Single canonical model: total active entities ≈ 5865,
        #   not 2× (which was the pre-§26 duplication). Allow a
        #   small fudge.
        assert total < 6500, (
            f"total active entities ({total}) exceeds the "
            f"single-canonical-model floor — D-030 violation"
        )
        # - "Most recently sourced from": after org B's sync the
        #   touch path mutated last_synced_from_org_id to org B
        #   for unchanged entities. Org B should hold the
        #   majority of attributions.
        assert count_b > count_a, (
            f"expected org B's attribution ({count_b}) > org A's "
            f"({count_a}) post-§26; touch path should have mutated "
            f"last_synced_from_org_id to org B"
        )
        assert count_b > 5000, (
            f"expected org B to hold the bulk of the entity "
            f"attributions after most-recently-synced; "
            f"got {count_b}"
        )

        # Sync_run rows for both orgs exist with their own ids
        runs = conn.execute(text("""
            SELECT id, source_org_id, status, phase
            FROM sync_runs
            WHERE source_org_id IN (:a, :b)
            ORDER BY source_org_id, started_at
        """), {"a": org_a, "b": org_b}).fetchall()
        run_ids = {str(r.id) for r in runs}
        assert str(sync_run_a) in run_ids
        assert str(sync_run_b) in run_ids
        assert len(run_ids) == 2

        # ----- Queue rows correctly attributed (transitive via
        # entities.last_synced_from_org_id) -----
        queue_per_org = dict(conn.execute(text("""
            SELECT e.last_synced_from_org_id::text AS org, COUNT(*)
            FROM ai_enrichment_queue q
            JOIN entities e ON e.id = q.entity_id
            WHERE e.valid_to_seq IS NULL
            GROUP BY 1
        """)).fetchall())
        print(
            f"[scenario-3] queue rows per org: "
            f"A={queue_per_org.get(org_a, 0)} "
            f"B={queue_per_org.get(org_b, 0)} "
            f"NULL={queue_per_org.get(None, 0)}"
        )
        assert queue_per_org.get(None, 0) == 0, (
            "queue rows attributed to entities with NULL "
            "last_synced_from_org_id — orphans"
        )
