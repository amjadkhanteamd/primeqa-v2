"""Notification dispatch \u2014 email.

REAL providers ship today (D-200): :func:`send_email` selects ``log`` (default)
/ ``smtp`` / ``sendgrid`` via ``NOTIFICATIONS_PROVIDER``. ``log`` is the safe
default (logs + returns True); ``smtp``/``sendgrid`` send for real once their
env config is set, and degrade to a logged warning (never an exception) when it
isn't. The ``notify_*`` entry points are stable, so a caller's wiring doesn't
change as the provider is switched.

Wired callers: :func:`notify_release_decision` (from ``decision_composer``) and
:func:`notify_substrate_run_failed` (from the S4 execution consumer, D-234). The
older v1-shaped ``notify_run_failed`` / ``notify_agent_fix_applied`` /
``notify_dms_silent`` stubs were removed in D-238 (orphaned; their
``pipeline_runs``-era models retire with migration 053).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Iterable, List, Optional

log = logging.getLogger(__name__)


@dataclass
class Notification:
    kind: str                # 'run_failed' | 'scheduled_fire' | 'agent_fix_applied' | 'dms_silent'
    subject: str
    body: str
    recipients: List[str]   # email addresses; empty \u2192 no-op
    tenant_id: Optional[int] = None
    extras: Optional[dict] = None


def send_email(notification: Notification) -> bool:
    """Dispatch an email (product theme #5 \u2014 real providers, D-200).

    Provider via NOTIFICATIONS_PROVIDER:
      - unset / 'log' \u2192 log and return True (the safe default)
      - 'smtp'        \u2192 stdlib SMTP: SMTP_HOST (+ SMTP_PORT=587, SMTP_USERNAME,
                        SMTP_PASSWORD, SMTP_FROM, SMTP_STARTTLS=true)
      - 'sendgrid'    \u2192 the SendGrid v3 API: SENDGRID_API_KEY (+ SMTP_FROM)
      - otherwise     \u2192 warn + False

    All failures are swallowed to `log.warning` rather than raised so notifier
    calls in hot paths (run completion, scheduler fire) never break core flows.
    """
    if not notification.recipients:
        return False
    provider = (os.getenv("NOTIFICATIONS_PROVIDER") or "log").lower()
    try:
        if provider == "log":
            log.info("[notify:%s] subject=%r recipients=%s",
                     notification.kind, notification.subject, notification.recipients)
            return True
        elif provider == "smtp":
            return _send_smtp(notification)
        elif provider == "sendgrid":
            return _send_sendgrid(notification)
        else:
            log.warning("Unknown NOTIFICATIONS_PROVIDER=%s; skipping", provider)
            return False
    except Exception as e:
        log.exception("email dispatch failed: %s", e)
        return False


def _send_smtp(notification: Notification) -> bool:
    """Real send via stdlib smtplib. Config from env; missing SMTP_HOST \u2192 warn +
    False (mirrors the fail-closed webhook discipline)."""
    import smtplib
    from email.message import EmailMessage

    host = os.getenv("SMTP_HOST", "")
    if not host:
        log.warning("NOTIFICATIONS_PROVIDER=smtp but SMTP_HOST unset; skipping")
        return False
    port = int(os.getenv("SMTP_PORT", "587"))
    sender = os.getenv("SMTP_FROM", "primeqa@localhost")

    msg = EmailMessage()
    msg["Subject"] = notification.subject
    msg["From"] = sender
    msg["To"] = ", ".join(notification.recipients)
    msg.set_content(notification.body)

    with smtplib.SMTP(host, port, timeout=15) as smtp:
        if (os.getenv("SMTP_STARTTLS", "true").lower() in ("1", "true", "yes")):
            smtp.starttls()
        user, pw = os.getenv("SMTP_USERNAME", ""), os.getenv("SMTP_PASSWORD", "")
        if user:
            smtp.login(user, pw)
        smtp.send_message(msg)
    log.info("[notify:%s] sent via smtp to %s",
             notification.kind, notification.recipients)
    return True


def _send_sendgrid(notification: Notification) -> bool:
    """Real send via the SendGrid v3 mail API (no SDK dependency)."""
    import requests

    api_key = os.getenv("SENDGRID_API_KEY", "")
    if not api_key:
        log.warning("NOTIFICATIONS_PROVIDER=sendgrid but SENDGRID_API_KEY unset; skipping")
        return False
    sender = os.getenv("SMTP_FROM", "primeqa@localhost")
    resp = requests.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        json={
            "personalizations": [
                {"to": [{"email": r} for r in notification.recipients]}],
            "from": {"email": sender},
            "subject": notification.subject,
            "content": [{"type": "text/plain", "value": notification.body}],
        },
        timeout=15,
    )
    ok = 200 <= resp.status_code < 300
    if not ok:
        log.warning("sendgrid send failed (%s): %s",
                    resp.status_code, resp.text[:200])
    return ok


# ---- Helpers that build + send common notifications ------------------------

# D-238 (drop-readiness): the v1-shaped notify_run_failed / notify_agent_fix_applied
# stubs were removed — orphaned (zero callers), and their pipeline_runs / agent
# tables retire with migration 053. notify_substrate_run_failed below is the live
# D-234 new-engine replacement.

def notify_release_decision(db, tenant_id: int, release_name: str,
                            envelope: dict) -> None:
    """D-200: email tenant admins when an evaluated release decision is NOT a
    clean go — a no_go / conditional_go, or a substrate-gate degrade. The
    decision card carries the detail; the email is the heads-up."""
    rec = envelope.get("recommendation")
    source = envelope.get("recommendation_source")
    if rec == "go" and source != "substrate_gate":
        return                                           # clean go — no noise
    recipients = _admin_emails(db, tenant_id)
    sub = (envelope.get("substrate") or {})
    sub_line = ""
    if isinstance(sub, dict) and sub.get("applicable"):
        risk = sub.get("risk") or {}
        sub_line = (f" Substrate: {sub.get('recommendation')} "
                    f"(risk {risk.get('score')}/100 {risk.get('level')}).")
    send_email(Notification(
        kind="release_decision",
        subject=f"[PrimeQA] Release '{release_name}': "
                f"{str(rec).replace('_', ' ').upper()}",
        body=(f"The evaluated recommendation for release '{release_name}' is "
              f"{rec} (source: {source}).{sub_line} "
              f"Review the Decision tab for the full reasoning."),
        recipients=recipients,
        tenant_id=tenant_id,
        extras={"recommendation": rec, "source": source},
    ))


def notify_substrate_run_failed(tenant_id: int, *, run_id, test_id,
                                environment_id, outcome: str,
                                error_message: Optional[str] = None) -> None:
    """D-234: email tenant admins when a SUBSTRATE (S4) execution run did not pass
    (``outcome ∈ {failed, errored}``) — the new-engine counterpart to the v1
    ``notify_run_failed`` (which read the retired ``pipeline_runs``).

    Fired from the execution consumer for UNATTENDED runs only (scheduled /
    auto-enqueued / CI); live UI runs are watched, so they do not come through the
    queue. Best-effort throughout — NEVER raises (a notify must not affect the run
    or the job). A QUARANTINED claim (D-232) is skipped: its failures are the noise
    the operator set it aside to silence. Opens its own brief public session for
    recipient resolution, closes it, THEN sends — no DB connection across the
    email I/O."""
    try:
        from primeqa.intelligence import quarantine
        if quarantine.is_quarantined(tenant_id, test_id):
            return                                   # operator set this one aside
    except Exception:                                # pragma: no cover
        pass                                         # a quarantine-read blip ≠ block

    try:
        from primeqa.db import get_db
        db = next(get_db())
        try:
            recipients = _admin_emails(db, tenant_id)
        finally:
            db.close()
        if not recipients:
            return
        short = str(run_id)[:8]
        body = (f"A test run on environment #{environment_id} finished "
                f"{outcome}.\n\nRun: {run_id}\n")
        if error_message:
            body += f"Error: {error_message}\n"
        body += f"\nReview it at /runs/{run_id}"
        send_email(Notification(
            kind="substrate_run_failed",
            subject=f"[PrimeQA] A test run {outcome} (run {short})",
            body=body,
            recipients=recipients,
            tenant_id=tenant_id,
            extras={"run_id": str(run_id), "test_id": str(test_id),
                    "outcome": outcome},
        ))
    except Exception as e:                           # pragma: no cover
        log.warning("notify_substrate_run_failed failed for tenant %s run %s: %s",
                    tenant_id, run_id, e)


def _admin_emails(db, tenant_id: int) -> List[str]:
    from primeqa.core.models import User
    rows = db.query(User.email).filter(
        User.tenant_id == tenant_id,
        User.is_active.is_(True),
        User.role.in_(("admin", "superadmin")),
    ).all()
    return [r[0] for r in rows]


def _superadmin_emails(db) -> List[str]:
    from primeqa.core.models import User
    rows = db.query(User.email).filter(
        User.is_active.is_(True),
        User.role == "superadmin",
    ).all()
    return [r[0] for r in rows]
