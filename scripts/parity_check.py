#!/usr/bin/env python
"""Cutover Step 4a (D-185): S1-vs-`meta_*` metadata read-parity runner.

Read-only. For each environment that has BOTH a v1 current metadata version and
a synced S1 org model, diff what the S1 reader returns against the v1 `meta_*`
repo (objects / fields / VRs) and print a per-env summary. Exits non-zero if any
env has a SHAPE divergence (a field both readers have, but a CRUD/type flag
differs — a reader bug). Membership divergence (a field in one source not the
other) is sync-time drift between the independent syncs — reported, not failed.

    python scripts/parity_check.py                 # all envs (uses DATABASE_URL)
    python scripts/parity_check.py --tenant-id 1   # one tenant
    python scripts/parity_check.py --verbose       # list the divergences

This is the Step-4 "clean parity window" instrument; the exit-gate is a clean
(zero shape-mismatch) run across the rollout tenants.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _init_db():
    from primeqa import db as dbmod
    url = os.getenv("DATABASE_URL")
    if not url:
        print("DATABASE_URL not set", file=sys.stderr)
        sys.exit(2)
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    dbmod.init_db(url)
    return dbmod.SessionLocal()


def run(tenant_id=None, verbose=False) -> int:
    from primeqa.core.models import Environment
    from primeqa.metadata.repository import MetadataRepository
    from primeqa.metadata.s1_reader import build_metadata_s1_reader
    from primeqa.metadata.parity import MetadataParityChecker

    db = _init_db()
    try:
        q = db.query(Environment).filter(
            Environment.current_meta_version_id.isnot(None))
        if tenant_id is not None:
            q = q.filter(Environment.tenant_id == tenant_id)
        envs = q.order_by(Environment.tenant_id, Environment.id).all()

        if not envs:
            print("No environments with a current meta version "
                  "(nothing to compare).")
            return 0

        checked = skipped = dirty = 0
        for env in envs:
            tag = f"tenant={env.tenant_id} env={env.id} ({env.name})"
            s1_reader = build_metadata_s1_reader(env.tenant_id)
            if s1_reader is None:
                print(f"  [SKIP] {tag} — no synced S1 org model")
                skipped += 1
                continue
            report = MetadataParityChecker(
                MetadataRepository(db), s1_reader).check(
                env.current_meta_version_id)
            s = report.summary()
            checked += 1
            status = "CLEAN" if report.clean else "DIVERGENT"
            if not report.clean:
                dirty += 1
            print(f"  [{status}] {tag} — "
                  f"objects(both={s['objects']['both']}, "
                  f"only_meta={s['objects']['only_meta']}, "
                  f"only_s1={s['objects']['only_s1']}, "
                  f"shape={s['objects']['shape_mismatch']}) "
                  f"fields(both={s['fields']['both']}, "
                  f"only_meta={s['fields']['only_meta']}, "
                  f"only_s1={s['fields']['only_s1']}, "
                  f"shape={s['fields']['shape_mismatch']}) "
                  f"vrs(both={s['vrs']['both']})")
            if verbose and not report.clean:
                for mm in report.objects["shape_mismatch"]:
                    print(f"      object {mm['api_name']}: {mm['attrs']}")
                for mm in report.fields["shape_mismatch"]:
                    print(f"      field  {mm['field']}: {mm['attrs']}")

        print(f"\nchecked={checked} skipped={skipped} divergent={dirty}")
        if dirty:
            print("PARITY FAILED — shape divergence found (reader bug); "
                  "investigate before the meta_* drop (Step 5).")
            return 1
        print("PARITY CLEAN — S1 reads match meta_* on the shape axis.")
        return 0
    finally:
        db.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tenant-id", type=int, default=None)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    return run(tenant_id=args.tenant_id, verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
