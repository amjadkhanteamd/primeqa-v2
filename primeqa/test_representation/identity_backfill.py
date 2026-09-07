"""Step 1 — the origin backfill (LLD_STEP_1_PROVENANCE §b).

Establishes one ``requirement_identities`` row per identity the tenant
already has, classifying origin BY EVIDENCE. Idempotent: a second
``--apply`` inserts nothing and reports the same counts.

**The re-key guard is the script's own precondition** (TA invariant 6):
the identity key set it is about to write must EQUAL the key set the
tenant already has (link keys ∪ live decorating-row keys). On any
difference it refuses to commit and prints the symmetric difference —
a backfill that would invent or drop a key is a re-key, and no report is
worth one.

    python -m primeqa.test_representation.identity_backfill \
        --tenant-id 1 [--apply]

Dry run by default: reads only, prints the per-origin table and the
referential gaps.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from sqlalchemy import text

from primeqa.test_representation.identity import (
    CANNOT_CLASSIFY,
    CLASSIFIER_VERSION,
    ORIGINS,
    classify_origin,
    establish,
    link_key_census,
)

log = logging.getLogger(__name__)

BACKFILL_ACTOR = "backfill@v1"


class RekeyRefused(RuntimeError):
    """The identity set the backfill would write differs from the one the
    tenant has. Refusing: that is a re-key, not a backfill."""


def _live_requirement_rows(conn, tenant_id: int) -> list:
    """The tenant's LIVE requirement rows, each with the identity key it
    decorates: ``external_key`` post-072, else the pre-Step-1 derivation —
    byte-identical, so this reads THE SAME SET either way. The column is
    probed rather than assumed so the dry run is runnable at merge
    pre-flight, before 072 has been applied."""
    has_col = conn.execute(text(
        "SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
        "AND table_name = 'requirements' AND column_name = 'external_key'")).scalar()
    key_expr = ("COALESCE(external_key, jira_key, 'req-' || id)" if has_col
                else "COALESCE(jira_key, 'req-' || id)")
    cols = "id, source, jira_key, " + ("external_key, " if has_col else "NULL AS external_key, ")
    return [dict(r) for r in conn.execute(text(
        f"SELECT {cols} {key_expr} AS key "                      # noqa: S608
        "FROM public.requirements "
        "WHERE tenant_id = :t AND deleted_at IS NULL ORDER BY id"),
        {"t": tenant_id}).mappings().all()]


def _established_keys(conn) -> set:
    """The identities already established — an EMPTY set when the table does
    not exist yet, so the dry run is runnable at merge pre-flight (before the
    tenant migration) and the counts can be reviewed before the switch."""
    present = conn.execute(text(
        "SELECT to_regclass(current_schema || '.requirement_identities')")).scalar()
    if present is None:
        return set()
    return {r[0] for r in conn.execute(text(
        "SELECT external_key FROM requirement_identities")).all()}


def plan(conn, tenant_id: int) -> dict:
    """PURE-ish (reads only): the identity set, each with its evidence-
    classified origin, its claim counts and whether a live row decorates
    it. Returns ``{identities, counts, gaps, existing}``."""
    census = link_key_census(conn)
    rows_by_key = {r["key"]: r for r in _live_requirement_rows(conn, tenant_id)}
    existing = _established_keys(conn)

    identities = []
    for key in sorted(set(census) | set(rows_by_key)):
        row = rows_by_key.get(key)
        cls = classify_origin(key, requirement_row=row)
        counts = census.get(key, {"claims": 0, "approved_claims": 0})
        identities.append({
            "key": key, "origin": cls.origin, "evidence": cls.evidence,
            "claims": counts["claims"],
            "approved_claims": counts["approved_claims"],
            "decorated": row is not None,
            "requirement_id": (row or {}).get("id"),
            "already_established": key in existing,
        })
    counts = {o: sum(1 for i in identities if i["origin"] == o) for o in ORIGINS}
    gaps = [i for i in identities if not i["decorated"]]
    return {"identities": identities, "counts": counts, "gaps": gaps,
            "existing": existing}


def _assert_no_rekey(before_keys: set, planned_keys: set) -> None:
    if before_keys != planned_keys:
        missing = sorted(before_keys - planned_keys)
        invented = sorted(planned_keys - before_keys)
        raise RekeyRefused(
            f"identity key set differs from the tenant's — refusing to "
            f"commit. dropped={missing} invented={invented}")


def run(tenant_id: int, *, apply: bool = False,
        actor_user_id: Optional[int] = None) -> dict:
    """Dry-run (default) or apply. Returns the report."""
    from primeqa.semantic.connection import get_tenant_connection

    with get_tenant_connection(tenant_id) as conn:
        p = plan(conn, tenant_id)
        # The key set the tenant HAS, computed independently of the plan.
        census = link_key_census(conn)
        row_keys = {r["key"] for r in _live_requirement_rows(conn, tenant_id)}
        before_keys = set(census) | row_keys
        planned_keys = {i["key"] for i in p["identities"]}
        _assert_no_rekey(before_keys, planned_keys)

        report = {
            "tenant_id": tenant_id, "applied": apply,
            "identities": len(p["identities"]),
            "counts": p["counts"],
            "gaps": [{"key": g["key"], "origin": g["origin"],
                      "claims": g["claims"]} for g in p["gaps"]],
            "already_established": sum(1 for i in p["identities"]
                                       if i["already_established"]),
            "classifier_version": CLASSIFIER_VERSION,
        }
        if not apply:
            report["would_insert"] = sum(1 for i in p["identities"]
                                         if not i["already_established"])
            return report

        established_before = _established_keys(conn)
        inserted = 0
        for i in p["identities"]:
            if establish(conn, i["key"], origin=i["origin"],
                         evidence=i["evidence"], established_by=BACKFILL_ACTOR):
                inserted += 1
        # Read back: the run added EXACTLY the planned keys and dropped
        # nothing. An identity that was already established but is no longer
        # in the tenant's current key set is NOT a re-key — that is a
        # purged row's identity surviving as a gap, which is the design.
        after = _established_keys(conn)
        _assert_no_rekey(established_before | planned_keys, after)
        report["inserted"] = inserted
        report["identity_rows_after"] = len(after)
        conn.execute(text(
            "INSERT INTO public.activity_log "
            "(tenant_id, user_id, action, entity_type, entity_id, details) "
            "VALUES (:t, :u, 'identity.backfill', 'requirement_identity', "
            "        NULL, CAST(:d AS JSONB))"),
            {"t": tenant_id, "u": actor_user_id,
             "d": json.dumps({"inserted": inserted, "counts": p["counts"],
                              "identities": len(p["identities"]),
                              "gaps": len(p["gaps"]),
                              "classifier_version": CLASSIFIER_VERSION})})
        return report


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Step 1: establish requirement identities with an "
                    "evidence-classified origin (dry run unless --apply)")
    parser.add_argument("--tenant-id", type=int, required=True)
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    report = run(args.tenant_id, apply=args.apply, actor_user_id=args.user_id)
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":                                # pragma: no cover
    raise SystemExit(main())
