"""Manual consumer for the UI inspection queue (ui-s2.3, dormant-first).

    python -m primeqa.browser_worker.consume --tenant <id> [--once]

Claim one job -> for each surface: heartbeat, scan_page, finalize_surface
(UPSERT) -> mark the batch terminal. Per-surface failure is recorded in
the result row and does not abort the batch. Manual invocation only —
the Railway service CMD stays `sleep infinity`.
"""

from __future__ import annotations

import argparse
import time

from primeqa.browser_worker import queue as q
from primeqa.browser_worker.spike import scan_page

POLL_INTERVAL_S = 5


def consume_job(session, job: dict) -> None:
    """Run one claimed job's surfaces to completion."""
    job_id = job["job_id"]
    attempt = job["attempts"]
    surfaces = (job["payload"] or {}).get("surfaces", [])
    print(f"job {job_id} attempt={attempt} surfaces={len(surfaces)}", flush=True)
    try:
        for surface in surfaces:
            key, url = surface["key"], surface["url"]
            q.heartbeat(session, job_id)
            try:
                observation = scan_page(url)
            except Exception as exc:  # noqa: BLE001 — surface-level wall
                observation = {"status": "ERROR", "error": repr(exc)[:2000]}
            q.finalize_surface(session, job_id, key, attempt, observation)
            print(f"  surface {key} -> {observation.get('status')}", flush=True)
        q.mark_succeeded(session, job_id)
        print(f"job {job_id} succeeded", flush=True)
    except Exception as exc:  # noqa: BLE001 — batch-level wall
        status = q.mark_failed(session, job_id, repr(exc), attempt)
        print(f"job {job_id} {status}: {exc!r}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m primeqa.browser_worker.consume")
    parser.add_argument("--tenant", type=int, required=True)
    parser.add_argument("--once", action="store_true",
                        help="exit after at most one job (or if none pending)")
    args = parser.parse_args()

    session = q.open_tenant_session(args.tenant)
    try:
        while True:
            job = q.claim_one(session)
            if job is not None:
                consume_job(session, job)
            if args.once:
                if job is None:
                    print("no pending job", flush=True)
                return 0
            if job is None:
                time.sleep(POLL_INTERVAL_S)
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
