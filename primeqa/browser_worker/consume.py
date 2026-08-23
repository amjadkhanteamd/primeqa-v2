"""Manual consumer for the UI inspection queue (ui-s2.3 + session substrate).

    python -m primeqa.browser_worker.consume --tenant <id> [--once]

Claim one job -> (auth mode: ONE login for the batch) -> for each surface:
heartbeat, scan_page, finalize_surface (UPSERT) -> mark the batch
terminal. Per-surface failure is recorded in the result row and does not
abort the batch; a login-phase failure writes ZERO result rows and marks
the job failed_* with error_text = the taxonomy class. Manual invocation
only — the Railway service CMD stays `sleep infinity`.

Secrets: PORTAL_USERNAME / PORTAL_PASSWORD / PORTAL_TOTP_SEED are read
from env here, at login time, into an in-memory Credentials value, and
never stored, logged, or printed.
"""

from __future__ import annotations

import argparse
import os
import time
from urllib.parse import urlsplit, urlunsplit

from primeqa.browser_worker import queue as q
from primeqa.browser_worker.session import (
    CREDENTIAL_NOT_CONFIGURED, Credentials, LoginError, assert_session, login)
from primeqa.browser_worker.spike import scan_page

POLL_INTERVAL_S = 5
AUTH_MODE_TOTP_ENV = "totp_env"


def _scan_kwargs(surface: dict, stabilisation: dict) -> dict:
    kw = {}
    if surface.get("viewport"):
        kw["viewport"] = (surface["viewport"]["width"],
                          surface["viewport"]["height"])
    if surface.get("locale"):
        kw["locale"] = surface["locale"]
    if stabilisation.get("max_wait_s"):
        kw["max_wait_s"] = stabilisation["max_wait_s"]
    if stabilisation.get("quiet_ms"):
        kw["quiet_ms"] = stabilisation["quiet_ms"]
    return kw


def _read_credentials() -> Credentials:
    user = os.environ.get("PORTAL_USERNAME")
    password = os.environ.get("PORTAL_PASSWORD")
    seed = os.environ.get("PORTAL_TOTP_SEED")
    if not user or not password:
        raise LoginError(CREDENTIAL_NOT_CONFIGURED,
                         "PORTAL_USERNAME/PORTAL_PASSWORD unset")
    return Credentials(user, password, seed or None)


def _split_start(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    base = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    start = urlunsplit(("", "", parts.path or "/", parts.query, ""))
    return base, start


def _run_surfaces(session, job_id: str, attempt: int, surfaces: list,
                  stabilisation: dict, *, context=None,
                  landed_check=None) -> None:
    for surface in surfaces:
        key, url = surface["key"], surface["url"]
        q.heartbeat(session, job_id)
        try:
            observation = scan_page(url, context=context,
                                    landed_check=landed_check,
                                    **_scan_kwargs(surface, stabilisation))
        except LoginError:
            raise                      # SESSION_LOST: fail the job here
        except Exception as exc:  # noqa: BLE001 — surface-level wall
            observation = {"status": "ERROR", "error": repr(exc)[:2000]}
        q.finalize_surface(session, job_id, key, attempt, observation)
        print(f"  surface {key} -> {observation.get('status')}", flush=True)


def consume_job(session, job: dict) -> None:
    """Run one claimed job's surfaces to completion."""
    job_id = job["job_id"]
    attempt = job["attempts"]
    payload = job["payload"] or {}
    surfaces = payload.get("surfaces", [])
    stabilisation = payload.get("stabilisation") or {}
    auth_mode = (payload.get("auth") or {}).get("mode")
    print(f"job {job_id} attempt={attempt} surfaces={len(surfaces)} "
          f"auth={auth_mode or 'guest'}", flush=True)
    try:
        if auth_mode == AUTH_MODE_TOTP_ENV:
            _consume_authenticated(session, job_id, attempt, surfaces,
                                   stabilisation)
        else:
            _run_surfaces(session, job_id, attempt, surfaces, stabilisation)
        q.mark_succeeded(session, job_id)
        print(f"job {job_id} succeeded", flush=True)
    except LoginError as exc:
        status = q.mark_failed(session, job_id, f"{exc.code}: {exc.detail}",
                               attempt, retryable=exc.retryable)
        print(f"job {job_id} {status}: {exc.code} ({exc.detail})", flush=True)
    except Exception as exc:  # noqa: BLE001 — batch-level wall
        status = q.mark_failed(session, job_id, repr(exc), attempt)
        print(f"job {job_id} {status}: {exc!r}", flush=True)


def _consume_authenticated(session, job_id, attempt, surfaces,
                           stabilisation) -> None:
    """One browser, one context, ONE login for the whole batch; every
    surface scanned in the authenticated context with the session-lost
    check. Credentials live only in this frame."""
    from playwright.sync_api import sync_playwright

    if not surfaces:
        return
    creds = _read_credentials()
    base_url, start_path = _split_start(surfaces[0]["url"])
    first = surfaces[0]
    ctx_kwargs = {}
    if first.get("viewport"):
        ctx_kwargs["viewport"] = {"width": first["viewport"]["width"],
                                  "height": first["viewport"]["height"]}
    if first.get("locale"):
        ctx_kwargs["locale"] = first["locale"]
    max_wait_s = stabilisation.get("max_wait_s") or 30

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(**ctx_kwargs)
        try:
            q.heartbeat(session, job_id)
            outcome = login(context, base_url, start_path, creds,
                            max_wait_s=max_wait_s)
            print(f"  login events: {outcome.events}", flush=True)
            _run_surfaces(session, job_id, attempt, surfaces, stabilisation,
                          context=context, landed_check=assert_session)
        finally:
            context.close()
            browser.close()


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
