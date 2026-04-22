"""One-shot cleanup: delete all orphaned PQA_* records from every
sandbox-type environment for a tenant.

Safety:
  - `team` + `sandbox` / `uat` / `staging` envs only. `production`
    envs are SKIPPED unconditionally; if you want to clean a prod
    org, do it by hand.
  - Scans the canonical object list (Account, Contact, Opportunity,
    Lead, Case, Task) plus any custom types passed via --objects.
  - Dry-run by default. Pass --execute to actually delete.

Usage:
  python scripts/cleanup_orphan_pqa_records.py --tenant 1
  python scripts/cleanup_orphan_pqa_records.py --tenant 1 --execute
  python scripts/cleanup_orphan_pqa_records.py --tenant 1 \\
      --objects Account,Contact,Opportunity,Lead,Case,Task,Contract
"""

from __future__ import annotations

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from primeqa.app import app  # noqa: F401 — loads engine
from primeqa.db import SessionLocal
from primeqa.core.models import Environment
from primeqa.core.repository import ConnectionRepository
from primeqa.execution.cleanup import CleanupEngine
from primeqa.execution.executor import SalesforceExecutionClient
from primeqa.metadata.worker_runner import _oauth_token


SAFE_ENV_TYPES = {"sandbox", "uat", "staging"}


def _sf_client_for_env(db, env):
    """Build a SalesforceExecutionClient for the env, or return None
    if credentials are missing / OAuth fails. Mirrors the worker's
    pattern: fetch connection config, run OAuth, build client."""
    if not env.connection_id:
        return None
    conn = ConnectionRepository(db).get_connection_decrypted(
        env.connection_id, env.tenant_id)
    if not conn:
        return None
    cfg = conn["config"]
    try:
        token = _oauth_token(env, cfg)
    except Exception as e:
        print(f"    WARN: OAuth failed for env {env.id}: {e}")
        return None
    try:
        return SalesforceExecutionClient(
            instance_url=env.sf_instance_url,
            api_version=env.sf_api_version,
            access_token=token,
        )
    except Exception as e:
        print(f"    WARN: could not build SF client for env {env.id}: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", type=int, required=True,
                        help="Tenant id to scope the cleanup to.")
    parser.add_argument("--objects", type=str, default=None,
                        help="Comma-separated SObject list (overrides default).")
    parser.add_argument("--execute", action="store_true",
                        help="Actually delete. Default is dry-run.")
    parser.add_argument("--env-id", type=int, default=None,
                        help="Run only against this env id.")
    args = parser.parse_args()

    object_types = ([s.strip() for s in args.objects.split(",") if s.strip()]
                    if args.objects else None)

    db = SessionLocal()
    try:
        q = (db.query(Environment)
             .filter(Environment.tenant_id == args.tenant,
                     Environment.is_active.is_(True),
                     Environment.env_type.in_(SAFE_ENV_TYPES)))
        if args.env_id:
            q = q.filter(Environment.id == args.env_id)
        envs = q.order_by(Environment.name.asc()).all()
    finally:
        db.close()

    if not envs:
        print("No eligible environments for tenant", args.tenant)
        return 0

    print(f"Scanning {len(envs)} env(s) for orphan PQA_* records "
          f"(execute={args.execute})")
    print("-" * 70)

    grand = {"deleted": 0, "failed": 0, "scanned_envs": 0}
    for env in envs:
        print(f"\nEnv {env.id}: {env.name} ({env.env_type})")
        if env.is_production:
            print("  SKIP: marked is_production=True")
            continue
        db = SessionLocal()
        try:
            sf = _sf_client_for_env(db, env)
            if sf is None:
                print("  SKIP: no usable SF connection")
                continue
            engine = CleanupEngine(
                None,  # attempt repo not needed for emergency sweep
                sf=sf,
                entity_repo=None,
            )
            if args.execute:
                result = engine.emergency_cleanup(
                    environment=env, sobject_types=object_types)
                print(f"  deleted={result['deleted']} failed={result['failed']}")
                grand["deleted"] += result["deleted"]
                grand["failed"] += result["failed"]
            else:
                # Dry-run: count only, do not delete.
                types = (object_types
                         or ["Account", "Contact", "Opportunity",
                             "Lead", "Case", "Task"])
                dry_total = 0
                for sobj in types:
                    qres = sf.query(
                        f"SELECT Id FROM {sobj} WHERE Name LIKE 'PQA_%'")
                    if qres.get("success"):
                        n = len(qres["api_response"]["body"].get("records", []))
                        if n:
                            print(f"  {sobj}: {n} candidate(s)")
                            dry_total += n
                print(f"  dry-run total: {dry_total}")
            grand["scanned_envs"] += 1
        finally:
            db.close()

    print("\n" + "=" * 70)
    print(f"Scanned {grand['scanned_envs']} env(s). "
          f"Deleted={grand['deleted']} Failed={grand['failed']}")
    if not args.execute:
        print("Dry-run only — re-run with --execute to actually delete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
