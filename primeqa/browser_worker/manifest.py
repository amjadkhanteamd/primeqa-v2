"""Run manifests for UI inspection (ui-s2.4).

A manifest pins everything a run means — surface set, artifact pins,
stabilisation policy, execution policy — and is IMMUTABLE by convention:
this module exposes create/get/enqueue only; no update or delete path
exists anywhere in primeqa/browser_worker/ (pinned by test).

Write discipline (the D-281 batch-manifest idiom lifted to run scope):
create_manifest commits in its own transaction BEFORE any job is
enqueued, and jobs.manifest_id is NOT NULL — a job can never exist
without its persisted manifest.

Design: docs/ui-testing/LLD_PHASE2_4_MANIFEST.md.
"""

from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.orm import Session


def create_manifest(session: Session, payload: dict) -> str:
    """Persist a manifest — own transaction, committed here, before any
    enqueue. Returns the manifest id."""
    row = session.execute(text("""
        INSERT INTO s4_ui_run_manifests (payload)
        VALUES (CAST(:payload AS JSONB))
        RETURNING id
    """), {"payload": json.dumps(payload)}).fetchone()
    session.commit()
    return str(row[0])


def get_manifest(session: Session, manifest_id: str) -> dict | None:
    row = session.execute(text("""
        SELECT id, payload, created_at
        FROM s4_ui_run_manifests WHERE id = :id
    """), {"id": manifest_id}).fetchone()
    if row is None:
        return None
    return {"manifest_id": str(row[0]), "payload": row[1],
            "created_at": row[2]}


def enqueue_for_manifest(session: Session, manifest_id: str) -> str:
    """Enqueue one pending job whose payload is built FROM the manifest —
    the worker executes the manifest, nothing else. Returns the job id."""
    manifest = get_manifest(session, manifest_id)
    if manifest is None:
        raise ValueError(f"manifest {manifest_id} not found")
    mp = manifest["payload"]
    job_payload = {
        "surfaces": mp.get("surfaces", []),
        "stabilisation": mp.get("stabilisation", {}),
    }
    row = session.execute(text("""
        INSERT INTO s4_ui_inspection_jobs (payload, manifest_id)
        VALUES (CAST(:payload AS JSONB), :manifest_id)
        RETURNING id
    """), {"payload": json.dumps(job_payload),
           "manifest_id": manifest_id}).fetchone()
    session.commit()
    return str(row[0])
