"""Re-run the static validator against stored test cases.

Use after pushing a validator logic change (e.g. tolerate $foo.Id) to
refresh the cached validation_report on existing TestCaseVersions.
Without this, the worker will keep blocking on the stale report even
though the new validator would pass.

Usage:

    # All TCs for tenant 1 in environment 24
    ./venv/bin/python scripts/revalidate_test_cases.py --tenant 1 --env 24

    # Just a specific batch of ids
    ./venv/bin/python scripts/revalidate_test_cases.py --tenant 1 --env 24 --ids 111,112,113,114,115,116

    # Dry run: show what would change without writing
    ./venv/bin/python scripts/revalidate_test_cases.py --tenant 1 --env 24 --dry-run

Writes updated validation_report into test_case_versions for each TC's
current_version_id. Idempotent.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import primeqa.db as db_mod
db_mod.init_db(os.environ["DATABASE_URL"])

from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from primeqa.test_management.models import TestCase, TestCaseVersion
from primeqa.core.models import Environment
from primeqa.metadata.repository import MetadataRepository
from primeqa.intelligence.validator import TestCaseValidator


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tenant", type=int, required=True)
    p.add_argument("--env", type=int, required=True,
                   help="Environment id \u2014 its current_meta_version_id drives validation")
    p.add_argument("--ids", type=str, default="",
                   help="Comma-separated test_case ids; omit to revalidate every TC in tenant")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    Session = sessionmaker(bind=db_mod.engine)
    sess = Session()
    try:
        env = sess.query(Environment).filter_by(id=args.env, tenant_id=args.tenant).first()
        if not env:
            print(f"ERROR: env {args.env} not found for tenant {args.tenant}")
            sys.exit(2)
        if not env.current_meta_version_id:
            print(f"ERROR: env {args.env} has no current_meta_version_id")
            sys.exit(2)

        metadata_repo = MetadataRepository(sess)
        validator = TestCaseValidator(metadata_repo, env.current_meta_version_id)

        q = sess.query(TestCase).filter(TestCase.tenant_id == args.tenant)
        if args.ids:
            ids = [int(x) for x in args.ids.split(",") if x.strip()]
            q = q.filter(TestCase.id.in_(ids))
        tcs = q.all()
        print(f"Revalidating {len(tcs)} TC(s) against meta_version {env.current_meta_version_id}...")

        unchanged = 0
        improved = 0
        worsened = 0
        for tc in tcs:
            if not tc.current_version_id:
                continue
            v = sess.query(TestCaseVersion).filter_by(id=tc.current_version_id).first()
            if not v or not v.steps:
                continue
            before = v.validation_report or {}
            before_status = before.get("status", "missing")
            after = validator.validate(v.steps)
            after_status = after.get("status", "ok")

            if before_status != after_status or len(before.get("issues", [])) != len(after.get("issues", [])):
                direction = "improved" if (
                    (before_status == "critical" and after_status != "critical") or
                    (len(after.get("issues", [])) < len(before.get("issues", [])))
                ) else "worsened"
                tag = "[DRY]" if args.dry_run else "      "
                print(f"  {tag} TC {tc.id}: {before_status} ({len(before.get('issues', []))} issues) "
                      f"\u2192 {after_status} ({len(after.get('issues', []))} issues) [{direction}]")
                if direction == "improved":
                    improved += 1
                else:
                    worsened += 1
                if not args.dry_run:
                    v.validation_report = after
                    flag_modified(v, "validation_report")
            else:
                unchanged += 1

        if not args.dry_run:
            sess.commit()
            print(f"\nCommitted.  improved={improved}  worsened={worsened}  unchanged={unchanged}")
        else:
            print(f"\nDRY RUN.  would improve={improved}  would worsen={worsened}  unchanged={unchanged}")
    finally:
        sess.close()


if __name__ == "__main__":
    main()
