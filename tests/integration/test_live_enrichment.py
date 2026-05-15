"""Live integration test for the §23 enrichment queue worker.

Runs a full structural sync (reusing test_live_sync_full's fixtures),
then drains ai_enrichment_queue by looping enrichment_tick until it
reports no work — and verifies:

  - the sync enqueued embedding rows for every entity type and summary
    rows ONLY for Flow + ValidationRule (the SUMMARY_ENABLED_ENTITY_
    TYPES filter)
  - the worker populated entities.embedding (vector(1024),
    embedding_model='voyage-3') for every currently-active entity
  - the worker populated flow_details / validation_rule_details
    summary_text + summary_embedding + summary_* metadata for every
    Flow / ValidationRule
  - the queue ends fully drained, zero failed_permanent
  - a second enrichment_tick after the drain is a no-op (idempotent)

Gated on @pytest.mark.sandbox. Requires SF_* env + LIVE_DATABASE_URL +
VOYAGE_API_KEY (embeddings) + ANTHROPIC_API_KEY (summaries). Self-
contained: the test_org fixture creates + tears down all tenant entity
data; the gateway_tables fixture provisions the primeqa-app LLM
plumbing the summary path needs (see its docstring).

Wall-clock: ~11 min sync + ~5-8 min enrichment. Cost per run is small
— ~46 Voyage batch calls for ~5,900 embeddings (~$0.02) + ~63
Anthropic Haiku summary calls + their embeddings (~$0.20).
"""
from __future__ import annotations

import pytest
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.orm import Session

load_dotenv(override=True)  # override=True: the parent shell may export
# ANTHROPIC_API_KEY="" (empty) — a no-op default would leave the empty
# value in place and the worker's `if not os.environ.get(...)` guard
# would skip the summary path. override=True lets the real key from
# .env take precedence over an empty/stale value already in os.environ.

# Reuse test_live_sync_full's fixtures + tenant constants + the
# live-DB-URL resolver. The imported names ARE used — pytest resolves
# fixtures by name from the test module's namespace.
from tests.integration.test_sync_object_phase_live import (  # noqa: F401
    TENANT_ID,
    TENANT_SCHEMA,
    _get_live_db_url,
    db_engine,
    live_sf_client,
    test_org,
)

pytestmark = pytest.mark.sandbox

MAX_TICKS = 250  # safety bound — ~5,900 embeddings / 128 ≈ 46 ticks


def _scope(conn) -> None:
    conn.execute(
        text(f'SET LOCAL search_path TO "{TENANT_SCHEMA}", public')
    )
    conn.execute(
        text("SET LOCAL app.tenant_id = :t"), {"t": str(TENANT_ID)}
    )


@pytest.fixture
def gateway_tables(db_engine):
    """Provision the primeqa-app LLM gateway plumbing the §23 summary
    subtick needs — the substrate-1 dev database carries the tenant
    entity schema but NOT the primeqa-app gateway tables.

    `_summary_subtick` calls `llm_call()`, whose gateway flow touches
    `limits.load_tenant_config` → `limits.check` → `usage.record`:

      - load_tenant_config + check fail OPEN when
        `tenant_agent_settings` is absent (the rate-limit/tier layer
        is governance, not the work itself) — nothing to provision.
      - usage.record needs a real `llm_usage_log` table to land a
        row, AND it writes through the GLOBAL `primeqa.db.engine`,
        which stays None until `init_db()` binds it.

    So this fixture (a) binds the global engine to the live test DB
    via `init_db()`, and (b) `CREATE TABLE IF NOT EXISTS` the
    `llm_usage_log` table — no FK constraints, since tenants / users /
    pipeline_runs / etc. don't exist in this DB's public schema. Rows
    for this tenant are cleared at setup so a re-run's Anthropic-cost
    sum is scoped to just that run. The table itself is left in place
    (idempotent create) — it lives in `public` and is untouched by
    the `test_org` teardown, so cost stays queryable after the test.
    """
    from primeqa.db import init_db

    init_db(_get_live_db_url())

    # Pre-register every model module whose tables LLMUsageLog
    # foreign-keys into. `usage.record` only imports
    # `primeqa.intelligence.models` (where LLMUsageLog itself lives);
    # without these companions in the shared Base.metadata,
    # SQLAlchemy raises NoReferencedTableError on first INSERT trying
    # to resolve ForeignKey("requirements.id") / ("users.id") /
    # ("pipeline_runs.id") / ("test_cases.id"). The substrate-1 sync
    # engine doesn't otherwise need these modules, so importing them
    # here (test-only) keeps the production import graph clean.
    from primeqa.core import models as _core_models  # noqa: F401
    from primeqa.test_management import models as _tm_models  # noqa: F401
    from primeqa.execution import models as _ex_models  # noqa: F401

    with db_engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.llm_usage_log (
                id BIGSERIAL PRIMARY KEY,
                ts TIMESTAMPTZ NOT NULL DEFAULT now(),
                tenant_id INTEGER NOT NULL,
                user_id INTEGER,
                task VARCHAR(40) NOT NULL,
                model VARCHAR(100) NOT NULL,
                prompt_version VARCHAR(60) NOT NULL,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                cached_input_tokens INTEGER NOT NULL DEFAULT 0,
                cache_write_tokens INTEGER NOT NULL DEFAULT 0,
                cost_usd NUMERIC(10, 6) NOT NULL DEFAULT 0,
                latency_ms INTEGER,
                status VARCHAR(20) NOT NULL DEFAULT 'ok',
                complexity VARCHAR(10),
                escalated BOOLEAN NOT NULL DEFAULT false,
                request_id VARCHAR(80),
                run_id INTEGER,
                requirement_id INTEGER,
                test_case_id INTEGER,
                generation_batch_id BIGINT,
                context JSONB NOT NULL DEFAULT '{}'
            )
        """))
        conn.execute(
            text("DELETE FROM public.llm_usage_log WHERE tenant_id = :t"),
            {"t": TENANT_ID},
        )
    yield


def test_live_enrichment_after_sync(
    live_sf_client, db_engine, test_org, gateway_tables,
):
    """Full sync → drain ai_enrichment_queue via enrichment_tick →
    verify entities.embedding + Flow/VR summary_* are populated."""
    from primeqa.sync.engine import SyncEngine
    from primeqa.worker import enrichment_tick

    # ===== Setup: run a full structural sync =====
    engine = SyncEngine(
        engine_db=db_engine,
        sf_client=live_sf_client,
        tenant_schema=TENANT_SCHEMA,
    )
    sync_run_id = engine.run_sync(connected_org_id=test_org)
    assert sync_run_id is not None

    # ===== Verify the queue state the sync produced =====
    with db_engine.begin() as conn:
        _scope(conn)

        emb_pending = conn.execute(text("""
            SELECT COUNT(*) FROM ai_enrichment_queue
            WHERE primitive_type = 'embedding' AND status = 'pending'
        """)).scalar()
        assert emb_pending > 5000, (
            f"expected >5000 pending embedding rows after a full sync; "
            f"got {emb_pending}"
        )

        # Summary rows exist ONLY for the SUMMARY_ENABLED_ENTITY_TYPES
        # (Flow + ValidationRule) — the §23 sync-engine filter.
        summary_types = {
            r[0] for r in conn.execute(text("""
                SELECT DISTINCT entity_type FROM ai_enrichment_queue
                WHERE primitive_type = 'summary'
            """)).fetchall()
        }
        assert summary_types == {"Flow", "ValidationRule"}, (
            f"summary rows should exist only for Flow + ValidationRule; "
            f"got {summary_types}"
        )

        # And embedding rows exist for many more entity types than that.
        emb_types = {
            r[0] for r in conn.execute(text("""
                SELECT DISTINCT entity_type FROM ai_enrichment_queue
                WHERE primitive_type = 'embedding'
            """)).fetchall()
        }
        assert len(emb_types) >= 8, (
            f"embedding should be enqueued for every entity type; "
            f"got only {sorted(emb_types)}"
        )

    # ===== Drain the queue =====
    def _factory():
        return Session(bind=db_engine)

    ticks_used = 0
    for ticks_used in range(1, MAX_TICKS + 1):
        if not enrichment_tick(db_factory=_factory):
            break
    else:
        pytest.fail(
            f"enrichment_tick still finding work after {MAX_TICKS} ticks"
        )

    # ===== Verify the worker's results =====
    with db_engine.begin() as conn:
        _scope(conn)

        # Queue fully drained — no pending / in_progress left, and
        # crucially zero failed_permanent.
        status_counts = dict(conn.execute(text("""
            SELECT status, COUNT(*) FROM ai_enrichment_queue
            GROUP BY status
        """)).fetchall())
        assert status_counts.get("failed_permanent", 0) == 0, (
            f"queue has failed_permanent rows: {status_counts}"
        )
        assert status_counts.get("pending", 0) == 0, (
            f"queue still has pending rows: {status_counts}"
        )
        assert status_counts.get("in_progress", 0) == 0, (
            f"queue still has in_progress rows: {status_counts}"
        )
        assert status_counts.get("succeeded", 0) > 5000, (
            f"expected >5000 succeeded queue rows; got {status_counts}"
        )

        # entities.embedding populated for every currently-active
        # entity, stamped with the model tag.
        unpopulated = conn.execute(text("""
            SELECT COUNT(*) FROM entities
            WHERE valid_to_seq IS NULL AND embedding IS NULL
        """)).scalar()
        assert unpopulated == 0, (
            f"{unpopulated} active entities still have a NULL embedding"
        )
        bad_model = conn.execute(text("""
            SELECT COUNT(*) FROM entities
            WHERE valid_to_seq IS NULL
              AND embedding IS NOT NULL
              AND (embedding_model != 'voyage-3'
                   OR embedding_generated_at IS NULL)
        """)).scalar()
        assert bad_model == 0, (
            f"{bad_model} entities have an embedding but wrong/missing "
            f"model tag or generated_at"
        )

        # Embedding dimension is 1024 (the voyage-3 / migration dim).
        sample_dim = conn.execute(text("""
            SELECT vector_dims(embedding) FROM entities
            WHERE valid_to_seq IS NULL AND embedding IS NOT NULL
            LIMIT 1
        """)).scalar()
        assert sample_dim == 1024, (
            f"embedding dimension is {sample_dim}, expected 1024"
        )

        # Flow summaries: every active Flow has summary_text +
        # summary_embedding + the summary_* metadata.
        flow_total = conn.execute(text("""
            SELECT COUNT(*) FROM entities
            WHERE entity_type = 'Flow' AND valid_to_seq IS NULL
        """)).scalar()
        flow_summarised = conn.execute(text("""
            SELECT COUNT(*) FROM flow_details fd
            JOIN entities e ON e.id = fd.entity_id
            WHERE e.entity_type = 'Flow' AND e.valid_to_seq IS NULL
              AND fd.summary_text IS NOT NULL
              AND fd.summary_embedding IS NOT NULL
              AND fd.summary_model IS NOT NULL
              AND fd.summary_prompt_version IS NOT NULL
              AND fd.summary_generated_at IS NOT NULL
        """)).scalar()
        assert flow_total > 0, "no Flow entities in this sync"
        assert flow_summarised == flow_total, (
            f"only {flow_summarised}/{flow_total} Flow detail rows have "
            f"a complete summary"
        )

        # ValidationRule summaries: same invariant.
        vr_total = conn.execute(text("""
            SELECT COUNT(*) FROM entities
            WHERE entity_type = 'ValidationRule' AND valid_to_seq IS NULL
        """)).scalar()
        vr_summarised = conn.execute(text("""
            SELECT COUNT(*) FROM validation_rule_details vrd
            JOIN entities e ON e.id = vrd.entity_id
            WHERE e.entity_type = 'ValidationRule'
              AND e.valid_to_seq IS NULL
              AND vrd.summary_text IS NOT NULL
              AND vrd.summary_embedding IS NOT NULL
              AND vrd.summary_model IS NOT NULL
              AND vrd.summary_prompt_version IS NOT NULL
              AND vrd.summary_generated_at IS NOT NULL
        """)).scalar()
        assert vr_total > 0, "no ValidationRule entities in this sync"
        assert vr_summarised == vr_total, (
            f"only {vr_summarised}/{vr_total} ValidationRule detail rows "
            f"have a complete summary"
        )

        # A summary embedding is also 1024-dim.
        summ_dim = conn.execute(text("""
            SELECT vector_dims(summary_embedding) FROM flow_details
            WHERE summary_embedding IS NOT NULL LIMIT 1
        """)).scalar()
        assert summ_dim == 1024, (
            f"summary embedding dimension is {summ_dim}, expected 1024"
        )

    # ===== Report block — captured BEFORE the test_org teardown
    # wipes tenant_1 entity data. The queue end-state breakdown +
    # sample summaries only live in the tenant schema; llm_usage_log
    # is in `public` and survives teardown, so cost is queried
    # separately after the run. =====
    with db_engine.begin() as conn:
        _scope(conn)

        print("\n===== §23 ENRICHMENT REPORT =====")
        print(f"ticks_used (incl. final no-op): {ticks_used}")

        breakdown = conn.execute(text("""
            SELECT entity_type, primitive_type, status, COUNT(*)
            FROM ai_enrichment_queue
            GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
        """)).fetchall()
        print("queue end-state (entity_type / primitive / status / count):")
        for et, pt, st, ct in breakdown:
            print(f"  {et:22s} {pt:10s} {st:18s} {ct}")

        flow_sample = conn.execute(text("""
            SELECT e.sf_api_name, fd.summary_text, fd.summary_model,
                   fd.summary_prompt_version
            FROM flow_details fd
            JOIN entities e ON e.id = fd.entity_id
            WHERE e.entity_type = 'Flow' AND e.valid_to_seq IS NULL
              AND fd.summary_text IS NOT NULL
            ORDER BY e.sf_api_name
            LIMIT 1
        """)).fetchone()
        if flow_sample:
            print(
                f"\nSAMPLE FLOW SUMMARY [{flow_sample[0]}] "
                f"(model={flow_sample[2]}, prompt={flow_sample[3]}):"
            )
            print(flow_sample[1])

        vr_sample = conn.execute(text("""
            SELECT e.sf_api_name, vrd.summary_text, vrd.summary_model,
                   vrd.summary_prompt_version
            FROM validation_rule_details vrd
            JOIN entities e ON e.id = vrd.entity_id
            WHERE e.entity_type = 'ValidationRule' AND e.valid_to_seq IS NULL
              AND vrd.summary_text IS NOT NULL
            ORDER BY e.sf_api_name
            LIMIT 1
        """)).fetchone()
        if vr_sample:
            print(
                f"\nSAMPLE VALIDATIONRULE SUMMARY [{vr_sample[0]}] "
                f"(model={vr_sample[2]}, prompt={vr_sample[3]}):"
            )
            print(vr_sample[1])
        print("===== END REPORT =====\n")

    # ===== Idempotency: a second tick after the drain is a no-op =====
    assert enrichment_tick(db_factory=_factory) is False, (
        "enrichment_tick reported work after the queue was already "
        "drained — not idempotent"
    )
