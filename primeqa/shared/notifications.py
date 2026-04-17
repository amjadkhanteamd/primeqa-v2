"""Notification dispatch \u2014 email / slack / webhook.

v1 is a log-only stub per Q4 deferral; provider (SendGrid / SES / SMTP) is
selected before R6 ships notifications for real. The entry points here are
stable so the wiring in callers (run failure, scheduled fire, agent apply)
doesn't need to change when we plug a real provider in.
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
    """Dispatch an email.

    Behaviour:
      - If NOTIFICATIONS_PROVIDER env var is unset or 'log' \u2192 log and return True
      - If 'sendgrid' \u2192 TODO in R6.1 when Q4 is decided
      - Otherwise \u2192 log warning and return False

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
        elif provider == "sendgrid":
            log.warning("SendGrid provider not wired yet; stubbing send")
            return False
        else:
            log.warning("Unknown NOTIFICATIONS_PROVIDER=%s; skipping", provider)
            return False
    except Exception as e:
        log.exception("email dispatch failed: %s", e)
        return False


# ---- Helpers that build + send common notifications ------------------------

def notify_run_failed(db, run) -> None:
    """Email tenant admins + superadmins when a run fails."""
    recipients = _admin_emails(db, run.tenant_id)
    send_email(Notification(
        kind="run_failed",
        subject=f"[PrimeQA] Run #{run.id} failed",
        body=(f"Run #{run.id} against environment #{run.environment_id} failed. "
              f"{run.failed}/{run.total_tests} tests failed. "
              f"Error: {run.error_message or '(no message)'}"),
        recipients=recipients,
        tenant_id=run.tenant_id,
        extras={"run_id": run.id},
    ))


def notify_agent_fix_applied(db, fix_attempt) -> None:
    """Email super admins when an agent auto-applies a fix on sandbox."""
    recipients = _superadmin_emails(db)
    send_email(Notification(
        kind="agent_fix_applied",
        subject=f"[PrimeQA] Agent auto-applied a fix (TC #{fix_attempt.test_case_id})",
        body=(f"Agent applied {fix_attempt.proposed_fix_type} at confidence "
              f"{fix_attempt.confidence} ({fix_attempt.trust_band}). "
              f"Rerun: {fix_attempt.rerun_run_id} "
              f"Cause: {fix_attempt.root_cause_summary}"),
        recipients=recipients,
        extras={"fix_attempt_id": fix_attempt.id},
    ))


def notify_dms_silent(db, schedule) -> None:
    """Email super admins when a schedule blows past its dead-man's-switch."""
    recipients = _superadmin_emails(db)
    send_email(Notification(
        kind="dms_silent",
        subject=f"[PrimeQA] Scheduled run #{schedule.id} silent",
        body=(f"Schedule #{schedule.id} (suite #{schedule.suite_id}) was "
              f"expected to fire within {schedule.max_silence_hours}h but "
              f"hasn't. Check the scheduler process."),
        recipients=recipients,
        extras={"schedule_id": schedule.id},
    ))


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
