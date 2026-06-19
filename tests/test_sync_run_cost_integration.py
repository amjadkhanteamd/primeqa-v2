"""Integration: get_sync_run_cost over real mixed llm_usage_log rows (1d).

Exercises the SQL conditional-bucketing in
``primeqa.intelligence.llm.usage.get_sync_run_cost`` against the real DB:
inserts an embedding row, a summary-generation row, and a tokens_missing
embedding row under one random sync_run_id, asserts the per-bucket roll-up,
then cleans up.

SELF-SKIPS when (a) no DATABASE_URL / DB unreachable, or (b) migration 058
(``llm_usage_log.sync_run_id``) has not been applied yet — so the suite stays
green before AK applies the migration. Run it after 058 lands on the DB:

    python tests/test_sync_run_cost_integration.py
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text

from primeqa.intelligence.llm import usage
from primeqa.intelligence.llm.limits import EMBEDDING_TASK


def _column_exists(engine) -> bool:
    with engine.connect() as conn:
        return conn.execute(text("""
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'llm_usage_log' AND column_name = 'sync_run_id'
        """)).first() is not None


def _a_tenant_id(engine):
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT id FROM tenants ORDER BY id LIMIT 1")).scalar()


def run() -> bool:
    print("\n=== get_sync_run_cost integration (1d) ===\n")
    try:
        from primeqa.app import app  # noqa: F401 — triggers init_db()
        from primeqa.db import engine
    except Exception as e:                      # pragma: no cover
        print(f"  SKIP  no engine ({e})")
        return True

    try:
        if not _column_exists(engine):
            print("  SKIP  migration 058 not applied "
                  "(llm_usage_log.sync_run_id missing) — apply it, then re-run")
            return True
        tenant_id = _a_tenant_id(engine)
        if tenant_id is None:
            print("  SKIP  no tenant rows to attribute usage to")
            return True
    except Exception as e:                      # pragma: no cover
        print(f"  SKIP  DB unreachable ({e})")
        return True

    srid = str(uuid.uuid4())
    ok = True
    try:
        # 3 embedding rows (2 measured + 1 tokens_missing) + 2 summary rows.
        usage.record(tenant_id=tenant_id, task=EMBEDDING_TASK, model="voyage-3",
                     prompt_version="voyage-3", input_tokens=1000,
                     cost_usd=0.0001, sync_run_id=srid,
                     context={"primitive": "embedding"})
        usage.record(tenant_id=tenant_id, task=EMBEDDING_TASK, model="voyage-3",
                     prompt_version="voyage-3", input_tokens=500,
                     cost_usd=0.00005, sync_run_id=srid,
                     context={"primitive": "summary_embedding",
                              "rate_provisional": True})
        usage.record(tenant_id=tenant_id, task=EMBEDDING_TASK, model="voyage-3",
                     prompt_version="voyage-3", input_tokens=0, cost_usd=0.0,
                     sync_run_id=srid, context={"tokens_missing": True})
        usage.record(tenant_id=tenant_id, task="entity_summary_flow",
                     model="claude-haiku-4-5-20251001",
                     prompt_version="entity_summary_flow@v1",
                     input_tokens=2000, cost_usd=0.012, sync_run_id=srid,
                     context={})
        usage.record(tenant_id=tenant_id, task="entity_summary_validation_rule",
                     model="claude-haiku-4-5-20251001",
                     prompt_version="entity_summary_validation_rule@v1",
                     input_tokens=800, cost_usd=0.005, sync_run_id=srid,
                     context={})

        roll = usage.get_sync_run_cost(srid)
        print("  roll-up:", roll)

        def check(name, got, want):
            nonlocal ok
            if got != want:
                ok = False
                print(f"  FAIL  {name}: got {got!r}, want {want!r}")
            else:
                print(f"  PASS  {name} == {want!r}")

        # embedding bucket: 3 rows, 1500 measured tokens, $0.00015
        check("embedding.rows", roll["embedding"]["rows"], 3)
        check("embedding.tokens", roll["embedding"]["tokens"], 1500)
        check("embedding.cost_usd", round(roll["embedding"]["cost_usd"], 6),
              0.00015)
        # summary bucket: 2 rows, 2800 tokens, $0.017
        check("summary.rows", roll["summary"]["rows"], 2)
        check("summary.tokens", roll["summary"]["tokens"], 2800)
        check("summary.cost_usd", round(roll["summary"]["cost_usd"], 6), 0.017)
        # total + missing + provisional surfaced
        check("total_cost_usd", round(roll["total_cost_usd"], 6), 0.01715)
        check("tokens_missing_rows", roll["tokens_missing_rows"], 1)
        check("rate_provisional_rows", roll["rate_provisional_rows"], 1)
    finally:
        # cleanup — remove exactly the rows we inserted
        try:
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM llm_usage_log "
                                  "WHERE sync_run_id = :s"), {"s": srid})
        except Exception as e:                  # pragma: no cover
            print(f"  WARN  cleanup failed for {srid}: {e}")

    print("\n  RESULT:", "PASS" if ok else "FAIL", "\n")
    return ok


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
