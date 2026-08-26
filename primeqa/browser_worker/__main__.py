"""The browser-worker entrypoint (LLD_PRODUCTIONISATION §c).

Default (no subcommand): the CONSUMER LOOP — ``sleep infinity`` dies.
Composed from the proven pieces: fail-closed boot (validate_boot_secrets
under the service role), the egress-IP print on every boot (P-2/D9
evidence, observed), a per-tenant tick over DISCOVERED schemas (the
stale-tenant lesson: a tenant row without a provisioned schema is
skipped loudly-once, never a traceback per tick), reap → claim →
consume per tenant, and the SIGTERM lifecycle (finish the current
surface; the lease returns via the reaper with claim-only attempts
charging; died_reason on the exit line).

``probe``: the Phase 2.1 manual scan CLI, unchanged behavior.

Usage:
    python -m primeqa.browser_worker                 # the consumer loop
    python -m primeqa.browser_worker probe --url …   # the spike probe
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import urllib.request

from primeqa.browser_worker.spike import scan_page

_TICK_SLEEP_S = float(os.environ.get("PLIMSOL_UI_TICK_SLEEP_S", "5"))


def _egress_ip() -> str:
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=5) as resp:
            return resp.read().decode("ascii", errors="replace").strip()
    except Exception:  # noqa: BLE001 — diagnostics only; never crash the run
        return "UNKNOWN"


# ---------------------------------------------------------------------------
# The consumer loop
# ---------------------------------------------------------------------------

def _discover_tenant_ids(database_url: str | None) -> list[int]:
    """Active tenant rows INTERSECT existing tenant schemas. A row
    without a schema is skipped LOUDLY-ONCE per process (the recorded
    stale-tenant FIX-PLAN posture, applied from birth)."""
    from sqlalchemy import create_engine, text

    eng = create_engine(database_url or os.environ["DATABASE_URL"],
                        pool_pre_ping=True)
    try:
        with eng.connect() as conn:
            rows = conn.execute(text("""
                SELECT t.id,
                       EXISTS (SELECT 1 FROM information_schema.schemata s
                               WHERE s.schema_name = 'tenant_' || t.id)
                FROM tenants t WHERE t.status = 'active' ORDER BY t.id
            """)).fetchall()
    finally:
        eng.dispose()
    ids = []
    for tid, has_schema in rows:
        if has_schema:
            ids.append(int(tid))
        elif tid not in _WARNED_SCHEMALESS:
            _WARNED_SCHEMALESS.add(tid)
            print(f"tenant {tid} is active but has no provisioned schema — "
                  f"skipped (warned once per process)", flush=True)
    return ids


_WARNED_SCHEMALESS: set = set()


def run_loop(database_url: str | None = None, *, once: bool = False) -> int:
    """The production consumer. Fail-closed boot, then tick forever
    (or one pass with ``once`` — the test/manual entry)."""
    from primeqa.core.secrets import validate_boot_secrets

    validate_boot_secrets()
    print(f"EGRESS_IP={_egress_ip()}", flush=True)
    print(f"browser-worker consumer starting "
          f"(role={os.environ.get('PLIMSOL_SERVICE_ROLE') or 'untagged'})",
          flush=True)

    stop = {"flag": False}

    def _sigterm(_signum, _frame):
        stop["flag"] = True
        print("SIGTERM received — finishing the current surface",
              flush=True)

    signal.signal(signal.SIGTERM, _sigterm)

    from primeqa.browser_worker import queue as q
    from primeqa.browser_worker.consume import consume_job

    while True:
        did_work = False
        for tid in _discover_tenant_ids(database_url):
            if stop["flag"]:
                break
            session = q.open_tenant_session(tid, database_url)
            try:
                reaped = q.reap_stalled(session)
                if reaped:
                    print(f"tenant {tid}: reaped {reaped} stalled job(s)",
                          flush=True)
                job = q.claim_one(session)
                if job is not None:
                    did_work = True
                    consume_job(session, job,
                                should_stop=lambda: stop["flag"])
            finally:
                session.close()
        if stop["flag"]:
            print("browser-worker exiting (died_reason=SIGTERM)", flush=True)
            return 0
        if once:
            return 0
        if not did_work:
            # interruptible idle sleep
            deadline = time.monotonic() + _TICK_SLEEP_S
            while time.monotonic() < deadline and not stop["flag"]:
                time.sleep(0.2)


# ---------------------------------------------------------------------------
# The probe (the Phase 2.1 spike CLI, unchanged behavior)
# ---------------------------------------------------------------------------

def run_probe(args) -> int:
    print(f"EGRESS_IP={_egress_ip()}")

    results = []
    for url in args.url:
        result = scan_page(url)
        result.pop("screenshot_png", None)   # bytes; the size stays in screenshot_bytes
        results.append(result)
        total_ms = round(sum(result["timings_ms"].values()), 1)
        obs = result.get("engine_observations")
        if obs is not None:
            counts = (
                f"violations={obs['violations_count']} "
                f"passes={obs['passes_count']} "
                f"incomplete={obs['incomplete_count']}"
            )
        else:
            counts = "observations=none"
        print(f"{url} status={result['status']} total_ms={total_ms} {counts}")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=2)
        print(f"wrote {args.json_out}")

    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "probe":
        parser = argparse.ArgumentParser(
            prog="python -m primeqa.browser_worker probe")
        parser.add_argument("--url", action="append", required=True,
                            help="page to scan; repeatable")
        parser.add_argument("--json-out", metavar="PATH", default=None,
                            help="write the full JSON results here")
        return run_probe(parser.parse_args(argv[1:]))
    parser = argparse.ArgumentParser(
        prog="python -m primeqa.browser_worker",
        description="the consumer loop (default) — see 'probe' for the "
                    "manual scan CLI")
    parser.add_argument("--once", action="store_true",
                        help="one tick then exit (manual/test entry)")
    args = parser.parse_args(argv)
    return run_loop(once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
