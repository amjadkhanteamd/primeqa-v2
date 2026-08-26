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

from primeqa.browser_worker import evidence as ev
from primeqa.browser_worker import queue as q
from primeqa.browser_worker.session import (
    CREDENTIAL_NOT_CONFIGURED, Credentials, LoginError, assert_session, login)
from primeqa.browser_worker.spike import scan_page

POLL_INTERVAL_S = 5
AUTH_MODE_TOTP_ENV = "totp_env"
AUTH_MODE_VAULT = "vault"


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


class _EvidenceSink:
    """Per-job evidence uploader. Builds the S3 client lazily (env read at
    first use); any failure is reported per surface as EVIDENCE_INCOMPLETE
    with the stage reached — never raised into the batch."""

    def __init__(self, session, manifest_id: str, job_id: str):
        self.session = session
        self.manifest_id = manifest_id
        self.job_id = job_id
        self._s3 = None
        self._bucket = None

    def _client(self):
        if self._s3 is None:
            self._s3 = ev.client()
            self._bucket = ev.bucket_name()
        return self._s3, self._bucket

    def upload(self, surface_key: str, attempt: int, png: bytes,
               observation: dict):
        """CAPTURED -> UPLOADED. Returns (evidence_dict_for_db, record|None)."""
        try:
            s3, bucket = self._client()
            keys = ev.build_keys(self.session, self.manifest_id,
                                 self.job_id, surface_key, attempt)
            rec = ev.put_evidence(self.session, s3, bucket, keys, png,
                                  observation)
            return rec.as_db(q.EVIDENCE_UPLOADED), rec
        except Exception as exc:  # noqa: BLE001 — custody failure, recorded
            return ({"state": q.EVIDENCE_INCOMPLETE,
                     "detail": {"reached": "CAPTURED",
                                "error": f"{type(exc).__name__}: {exc}"[:500]}},
                    None)

    def verify_and_reference(self, surface_key: str, rec) -> str:
        """UPLOADED -> VERIFIED -> REFERENCED, or the honest incomplete."""
        try:
            s3, bucket = self._client()
            ok, detail = ev.verify_evidence(s3, bucket, rec)
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, {"error": f"{type(exc).__name__}: {exc}"[:300]}
        if ok:
            q.mark_evidence_referenced(self.session, self.job_id, surface_key)
            return q.EVIDENCE_REFERENCED
        q.set_evidence_incomplete(self.session, self.job_id, surface_key,
                                  reached="UPLOADED", error=str(detail))
        return q.EVIDENCE_INCOMPLETE


def _run_surfaces(session, job_id: str, attempt: int, surfaces: list,
                  stabilisation: dict, *, manifest_id: str | None = None,
                  context=None, landed_check=None,
                  should_stop=None) -> bool:
    """Returns True when every surface ran; False when should_stop
    interrupted AFTER a finalized surface (the job stays leased for the
    reaper)."""
    sink = _EvidenceSink(session, manifest_id or "no-manifest", job_id)
    for i, surface in enumerate(surfaces):
        if should_stop is not None and i > 0 and should_stop():
            return False
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
        png = observation.pop("screenshot_png", None)   # bytes never reach JSON/DB

        # Evidence discipline: upload BEFORE the DB result write; the DB
        # write records keys+checksums+state; verify AFTER; REFERENCED only
        # on verification (LLD_EVIDENCE_STORE).
        evidence_db, rec = (sink.upload(key, attempt, png, observation)
                            if observation.get("status") == "OK" and png is not None
                            else ({"state": q.EVIDENCE_INCOMPLETE,
                                   "detail": {"reached": "NONE",
                                              "error": "no capture (scan not OK)"}},
                                  None))
        q.finalize_surface(session, job_id, key, attempt, observation,
                           evidence=evidence_db)
        ev_state = evidence_db["state"]
        if rec is not None:
            ev_state = sink.verify_and_reference(key, rec)
        print(f"  surface {key} -> {observation.get('status')} "
              f"evidence={ev_state}", flush=True)
    return True


def consume_job(session, job: dict, should_stop=None) -> None:
    """Run one claimed job's surfaces to completion.

    ``should_stop`` (the SIGTERM lifecycle): consulted between surfaces —
    when it returns True the CURRENT surface finalizes and the job is
    LEFT in_progress for the reaper (claim-only attempts charging; the
    lease returns via the proven arm-B path)."""
    job_id = job["job_id"]
    attempt = job["attempts"]
    payload = job["payload"] or {}
    surfaces = payload.get("surfaces", [])
    stabilisation = payload.get("stabilisation") or {}
    auth = payload.get("auth") or {}
    auth_mode = auth.get("mode")
    print(f"job {job_id} attempt={attempt} surfaces={len(surfaces)} "
          f"auth={auth_mode or 'guest'}", flush=True)
    try:
        if auth_mode in (AUTH_MODE_TOTP_ENV, AUTH_MODE_VAULT):
            completed = _consume_authenticated(
                session, job_id, attempt, surfaces, stabilisation,
                manifest_id=job.get("manifest_id"), auth=auth,
                should_stop=should_stop)
        else:
            completed = _run_surfaces(
                session, job_id, attempt, surfaces, stabilisation,
                manifest_id=job.get("manifest_id"),
                should_stop=should_stop)
        if not completed:
            print(f"job {job_id} interrupted after current surface; "
                  f"lease left for the reaper (died_reason=SIGTERM)",
                  flush=True)
            return
        q.mark_succeeded(session, job_id)
        print(f"job {job_id} succeeded", flush=True)
    except LoginError as exc:
        from primeqa.browser_worker.audit import record_event
        record_event(session, action="ui.login_failed",
                     details={"class": exc.code, "job_id": str(job_id),
                              "persona": auth.get("persona"),
                              "auth_mode": auth_mode},
                     mandatory_log=True)
        status = q.mark_failed(session, job_id, f"{exc.code}: {exc.detail}",
                               attempt, retryable=exc.retryable)
        print(f"job {job_id} {status}: {exc.code} ({exc.detail})", flush=True)
    except Exception as exc:  # noqa: BLE001 — batch-level wall
        # Fail SAFE: an uncoded/unexpected exception is not known-transient,
        # so mark it PERMANENT (retryable=False) — never failed_retryable by
        # default, which would re-claim the job and resubmit credentials.
        # error_text carries the exception TYPE only (repr could echo secret
        # input); login() itself never reaches here (its catch-all belt
        # returns a coded LoginError, handled above).
        status = q.mark_failed(session, job_id, type(exc).__name__, attempt,
                               retryable=False)
        print(f"job {job_id} {status}: {type(exc).__name__}", flush=True)


def _resolve_auth_credentials(session, auth: dict) -> Credentials:
    """Per-mode credential resolution. totp_env is DEV-ONLY: refused
    under the production browser-worker role (the vault is the only
    production path). vault resolves from the tenant's portal_personas
    with job-scoped decrypt."""
    import os

    mode = auth.get("mode")
    if mode == AUTH_MODE_VAULT:
        from primeqa.browser_worker.vault import resolve_credentials
        return resolve_credentials(session, auth.get("persona") or "")
    if os.environ.get("PLIMSOL_SERVICE_ROLE") == "browser-worker":
        from primeqa.browser_worker.session import DEV_AUTH_MODE_REFUSED
        raise LoginError(
            DEV_AUTH_MODE_REFUSED,
            "totp_env is dev-only; the production browser-worker accepts "
            "vault personas only")
    return _read_credentials()


def _consume_authenticated(session, job_id, attempt, surfaces,
                           stabilisation, *, manifest_id=None,
                           auth=None, should_stop=None) -> bool:
    """One browser, one context, ONE login for the whole batch; every
    surface scanned in the authenticated context with the session-lost
    check. Credentials live only in this frame."""
    from playwright.sync_api import sync_playwright

    if not surfaces:
        return True
    creds = _resolve_auth_credentials(session, auth or {"mode": AUTH_MODE_TOTP_ENV})
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
            return _run_surfaces(session, job_id, attempt, surfaces,
                                 stabilisation, manifest_id=manifest_id,
                                 context=context,
                                 landed_check=assert_session,
                                 should_stop=should_stop)
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
