"""Backfill summary_input_hash with the summary MODEL folded in (Phase-1 close-out #5).

The summary hash formula gained a model component: ``enrichment_gate.summary_input_hash``
now folds ``summary_model_tag()`` alongside ``prompt_version`` and the entity
context, mirroring ``embed_input_hash``'s model_tag fold. Without a backfill, every
existing Flow/ValidationRule detail row's STORED hash (old formula, no model) would
mismatch the new formula on the next sync, needlessly re-summarizing rows that did
not change.

This recomputes the stored ``summary_input_hash`` for every detail row that has
one, using the SAME new formula + the CURRENT summary model (Haiku by default —
exactly what those summaries were actually made with). Post-backfill, an unchanged
row at the default model produces a hash that MATCHES what the next sync's gate
computes → no re-summarization. A later SUMMARY_MODEL swap will then correctly
mismatch and re-summarize.

Idempotent: re-running recomputes the identical hash and the UPDATE is a no-op
(``IS DISTINCT FROM`` guard). Per-tenant, across all active tenants. Run AFTER the
new code is deployed (it imports the new ``summary_input_hash`` signature) and
BEFORE the next sync, so the first post-deploy sync sees matching hashes.

Run:
    source venv/bin/activate && railway run python scripts/backfill_summary_model_hash.py
"""
import os
import sys


def _fix_db_url() -> None:
    """Local convenience: railway.internal won't resolve outside Railway's network,
    so fall back to the .env public proxy when running via `railway run` locally.
    No-op in the Railway runtime (where the internal URL resolves) and in CI."""
    url = os.environ.get("DATABASE_URL", "")
    if "railway.internal" in url or not url:
        try:
            from dotenv import dotenv_values
            local = dotenv_values(".env")
            if local.get("DATABASE_URL"):
                os.environ["DATABASE_URL"] = local["DATABASE_URL"]
        except Exception:
            pass


_DETAIL_TABLES = (
    ("Flow", "flow_details"),
    ("ValidationRule", "validation_rule_details"),
)


def _backfill_tenant(conn, tenant_id) -> int:
    """Recompute summary_input_hash for one tenant's Flow/VR detail rows.

    ``conn`` is a tenant-scoped connection (search_path = tenant schema) inside a
    transaction that commits on clean context exit — so the UPDATEs persist."""
    from sqlalchemy import text
    from primeqa.sync.enrichment_gate import (
        summary_input_hash,
        summary_model_tag,
        summary_prompt_version,
    )

    model_tag = summary_model_tag()
    updated = 0
    for entity_type, detail_table in _DETAIL_TABLES:
        prompt_version = summary_prompt_version(entity_type)
        rows = conn.execute(text(
            f"SELECT entity_id FROM {detail_table} "
            f"WHERE summary_input_hash IS NOT NULL"
        )).fetchall()
        for (eid,) in rows:
            h = summary_input_hash(
                conn, entity_type, str(eid), prompt_version, model_tag)
            if h is None:
                continue
            res = conn.execute(text(
                f"UPDATE {detail_table} SET summary_input_hash = :h "
                f"WHERE entity_id = :id AND summary_input_hash IS DISTINCT FROM :h"
            ), {"h": h, "id": str(eid)})
            updated += res.rowcount or 0
    print(f"  tenant {tenant_id}: model={model_tag} rows_rehashed={updated}")
    return updated


def main() -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    _fix_db_url()
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL is not set — aborting", file=sys.stderr)
        return 2

    from primeqa import db as dbmod
    dbmod.init_db(os.environ["DATABASE_URL"])
    from primeqa.semantic.connection import admin_iterate_all_tenants

    print("=== backfill summary_input_hash (fold the summary model) ===")
    results = admin_iterate_all_tenants(_backfill_tenant)
    print(f"=== done: {sum(results)} rows re-hashed across {len(results)} tenants ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
