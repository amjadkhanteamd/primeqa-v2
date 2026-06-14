#!/usr/bin/env python
"""Flagship release seed (D-237, theme #1) — mint a named, point-in-time
GO/NO-GO decision record from env-59's LIVE substrate evidence.

The env-level dashboard already shows the live decision continuously; this seeds
a *durable, named* release-decision artifact (a ``ReleaseDecision`` row + the
per-release Decision tab) over the same evidence — the strongest single showcase
of the Release Intelligence Loop on a real org.

It writes prod rows (a Release + requirement links + one decision row), which is
why it ships as a script AK runs once rather than something the build does:

    python scripts/flagship_release_seed.py            # DRY RUN — resolves the
                                                       # corpus + PREVIEWS the
                                                       # decision, writes nothing
    python scripts/flagship_release_seed.py --commit   # actually create + record

Idempotent: re-running reuses the release (matched by name), adds only missing
requirement links, and appends a fresh decision row. Read-only until ``--commit``.
"""
from __future__ import annotations

import argparse
import os

from dotenv import load_dotenv
from sqlalchemy import text

RELEASE_NAME = "Flagship — env-59 live proof (D-237)"
VERSION_TAG = "flagship-2026-06-14"
TENANT_ID = 1
# The 8 requirements whose APPROVED claims run live against env-59 on the daily
# 06:00 UTC schedule. SQ-* resolve by jira_key; req-<id> are manual requirements
# whose external key is `req-<id>` (jira_key NULL) — resolved by id.
KEYS = ["SQ-205", "SQ-206", "SQ-207", "SQ-209", "SQ-211",
        "req-280", "req-282", "req-283"]


def _resolve_requirement_id(db, key: str):
    if key.startswith("req-"):
        rid = int(key.split("-", 1)[1])
        row = db.execute(text(
            "SELECT id FROM requirements WHERE tenant_id=:t AND id=:rid "
            "AND deleted_at IS NULL"), {"t": TENANT_ID, "rid": rid}).first()
    else:
        row = db.execute(text(
            "SELECT id FROM requirements WHERE tenant_id=:t AND jira_key=:k "
            "AND deleted_at IS NULL ORDER BY id LIMIT 1"),
            {"t": TENANT_ID, "k": key}).first()
    return row[0] if row else None


def _seed_user(db):
    row = db.execute(text(
        "SELECT id FROM users WHERE tenant_id=:t ORDER BY id LIMIT 1"),
        {"t": TENANT_ID}).first()
    return row[0] if row else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commit", action="store_true",
                    help="actually write (default: dry run / preview only)")
    args = ap.parse_args()

    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL not set (expected in .env). Aborting.")
        return 2

    from primeqa.db import init_db
    init_db(url)
    import primeqa.db as dbmod
    from primeqa.intelligence.substrate_decision import (
        get_release_substrate_decision,
    )

    db = dbmod.SessionLocal()
    try:
        # --- resolve the corpus -------------------------------------------
        resolved, missing = {}, []
        for key in KEYS:
            rid = _resolve_requirement_id(db, key)
            (resolved.__setitem__(key, rid) if rid else missing.append(key))
        print(f"Resolved {len(resolved)}/{len(KEYS)} requirements: "
              + ", ".join(f"{k}->#{v}" for k, v in resolved.items()))
        if missing:
            print(f"  (no Requirement row for: {', '.join(missing)} — skipped)")

        # --- preview the decision over the live evidence (read-only) ------
        d = get_release_substrate_decision(TENANT_ID, list(resolved.keys()))
        if d.get("applicable"):
            print(f"\nLive decision PREVIEW over {len(resolved)} requirements:")
            print(f"  recommendation : {d['recommendation'].upper()}  "
                  f"(confidence {int(d['confidence']*100)}%)")
            print(f"  risk           : {d['risk']['score']}/100 "
                  f"({d['risk']['level']})")
            print(f"  pass rate      : {d['metrics']['pass_rate']}%  "
                  f"({d['metrics']['passed']} passed / "
                  f"{d['metrics']['failed']} failed / "
                  f"{d['metrics']['errored']} errored)")
            for b in d.get("blocking", []):
                keys = ", ".join(b.get("external_keys") or []) or b["test_id"][:8]
                print(f"  BLOCKED BY {keys}: {b.get('cause')}")
        else:
            print("\nNo applicable substrate evidence — nothing to record.")
            return 1

        if not args.commit:
            print("\n[dry run] Pass --commit to create the release + record the "
                  "decision. Nothing was written.")
            return 0

        # --- write: release + links + one decision row --------------------
        from primeqa.release.repository import ReleaseRepository
        from primeqa.release.decision_composer import evaluate_and_record

        repo = ReleaseRepository(db)
        user_id = _seed_user(db)
        if user_id is None:
            print("No user found for tenant 1 — cannot set created_by. Aborting.")
            return 2

        existing = [r for r in repo.list_releases(TENANT_ID)
                    if r.name == RELEASE_NAME]
        release = existing[0] if existing else repo.create_release(
            TENANT_ID, RELEASE_NAME, user_id, version_tag=VERSION_TAG,
            description="Live end-to-end proof on env-59 'Prime QA NEW': the "
                        "daily-scheduled substrate corpus, with the explainable "
                        "GO/NO-GO over real org evidence (D-237).",
            decision_criteria={})            # defaults: 95% gate, advisory mode
        print(f"\nRelease #{release.id} '{release.name}' "
              + ("(reused)" if existing else "(created)"))

        for key, rid in resolved.items():
            repo.add_requirement(release.id, rid, user_id)
        print(f"Linked {len(resolved)} requirements.")

        envelope = evaluate_and_record(db, release, TENANT_ID, release_repo=repo)
        print(f"Recorded decision: {envelope['recommendation'].upper()} "
              f"(source={envelope['recommendation_source']}). "
              f"View at /releases/{release.id}?tab=decision")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
