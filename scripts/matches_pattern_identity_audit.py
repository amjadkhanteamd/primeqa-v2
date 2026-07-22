#!/usr/bin/env python3
"""matches_pattern identity audit (D-384) — read-only identity replay.

For every persisted claim whose ``semantic_conditions`` carry a
``matches_pattern`` clause: (1) deserialize both bodies through the live
body registry, (2) sanity-replay the STORED ``identity_hash`` through the
live canonicalization (proving the harness computes real identity, not an
approximation), (3) recompute the hash under the D-384 normalization
(``matches_pattern`` value → None), (4) report rows → distinct identities
before/after, the collapse groups with their invented spellings, and the
non-deprecated current rows the deprecate-then-regen law (D-353/D-383)
applies to.

Read-only by construction: one SELECT; nothing persists.

Usage:
  python scripts/matches_pattern_identity_audit.py [--tenant 1] [--current-only]
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import create_engine, text                 # noqa: E402

from primeqa.test_representation.identity_hash import (    # noqa: E402
    compute_identity_hash,
)
from primeqa.test_representation.models.registry import (  # noqa: E402
    get_body_model,
)


def _deserialize(jsonb_dict: dict):
    """Registry dispatch, the coordinator's `_deserialize_body` idiom."""
    cls = get_body_model(jsonb_dict["kind"], jsonb_dict["body_schema_version"])
    return cls.model_validate(jsonb_dict)


def _normalize_conditions(body):
    """The D-384 normalization: every matches_pattern clause's value → None.

    Clones via ``model_construct`` so the audit is coupling-agnostic — it
    yields the identical canonical form whether it runs before or after the
    S2 validator change (construct bypasses validation; canonicalization
    walks fields either way).
    """
    changed = False
    clauses = []
    for c in body.conditions:
        if c.predicate == "matches_pattern" and c.value is not None:
            clauses.append(type(c).model_construct(
                **{**{k: getattr(c, k) for k in type(c).model_fields},
                   "value": None}))
            changed = True
        else:
            clauses.append(c)
    if not changed:
        return body, ()
    spellings = tuple(
        repr(c.value) for c in body.conditions
        if c.predicate == "matches_pattern" and c.value is not None)
    return type(body).model_construct(
        **{**{k: getattr(body, k) for k in type(body).model_fields},
           "conditions": clauses}), spellings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", type=int, default=1)
    ap.add_argument("--current-only", action="store_true",
                    help="only current versions (valid_to IS NULL); "
                         "default audits every persisted version row")
    args = ap.parse_args()

    engine = create_engine(os.environ["DATABASE_URL"])
    where_current = "AND valid_to IS NULL" if args.current_only else ""
    sql = text(f"""
        SELECT test_id, version_seq, valid_to, archetype, claim_kind,
               asserted_truth, semantic_conditions, identity_hash, status
        FROM tenant_{args.tenant}.test_claims
        WHERE semantic_conditions::text LIKE '%matches_pattern%'
          {where_current}
        ORDER BY test_id, version_seq
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql).mappings().all()

    sanity_fail = 0
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        at = _deserialize(r["asserted_truth"])
        sc = _deserialize(r["semantic_conditions"])
        replayed = compute_identity_hash(
            r["archetype"], r["claim_kind"], at, sc)
        if replayed != r["identity_hash"]:
            sanity_fail += 1
            print(f"  SANITY FAIL {r['test_id']} v{r['version_seq']}: "
                  f"stored {r['identity_hash'][:8]} != replayed {replayed[:8]}")
            continue
        norm_sc, spellings = _normalize_conditions(sc)
        norm_hash = compute_identity_hash(
            r["archetype"], r["claim_kind"], at, norm_sc)
        subject = (r["asserted_truth"].get("target") or {}).get("external_id")
        groups[(norm_hash,)].append({
            "test_id": str(r["test_id"]), "version_seq": r["version_seq"],
            "current": r["valid_to"] is None, "status": r["status"],
            "stored": r["identity_hash"], "spellings": spellings,
            "subject": subject, "operation": r["asserted_truth"].get("operation"),
        })

    scope = "current versions" if args.current_only else "all version rows"
    total = sum(len(v) for v in groups.values())
    before = len({m["stored"] for v in groups.values() for m in v})
    after = len(groups)
    print(f"\n=== matches_pattern identity audit (D-384) — tenant_{args.tenant}, {scope} ===")
    print(f"rows carrying a matches_pattern clause : {total}"
          f"   (sanity replay: {total} ok, {sanity_fail} failed)")
    print(f"distinct identities under STORED hashes: {before}")
    print(f"distinct identities under NORMALIZATION: {after}")
    print(f"identities collapsed                   : {before - after}")

    print("\n--- collapse groups (normalized identity <- stored spellings) ---")
    for (nh,), members in sorted(groups.items(),
                                 key=lambda kv: -len(kv[1])):
        stored = sorted({m["stored"] for m in members})
        if len(stored) < 2:
            continue
        m0 = members[0]
        print(f"{nh[:8]}  {m0['subject']}  op={m0['operation']}  "
              f"({len(members)} rows, {len(stored)} stored identities)")
        by_stored: dict[str, list] = defaultdict(list)
        for m in members:
            by_stored[m["stored"]].append(m)
        for sh, ms in sorted(by_stored.items()):
            sp = sorted({s for m in ms for s in m["spellings"]})
            st = sorted({m["status"] for m in ms})
            print(f"    {sh[:8]}  {'/'.join(st):<10}  {', '.join(sp)}")

    targets = sorted(
        {(m["subject"], m["test_id"], m["status"])
         for v in groups.values() for m in v
         if m["current"] and m["status"] != "deprecated"})
    print("\n--- deprecate-then-regen targets (current, non-deprecated) ---")
    for subject, tid, status in targets:
        print(f"  {subject:<22} {tid[:8]}  {status}")
    print(f"  ({len(targets)} claims)")
    return 1 if sanity_fail else 0


if __name__ == "__main__":
    sys.exit(main())
