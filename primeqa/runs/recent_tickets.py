"""Recent-ticket tracking for the /run Tickets picker.

Every time a user views a requirement detail page, runs a single
ticket, or selects a ticket in any picker, we upsert a row into
`user_recent_tickets` keyed on (user_id, environment_id, jira_key).
The picker reads the top-10 rows ordered by viewed_at DESC.

Retention: last 20 per (user, environment). Old rows are pruned in
the write path so the table stays small without a background job.

All functions are best-effort: a DB failure while recording a view
must not break the page the user was visiting. Callers should
swallow exceptions.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from primeqa.core.models import UserRecentTicket


MAX_RECENT_PER_ENV = 20


def record_view(db: Session, user_id: int, environment_id: Optional[int],
                jira_key: Optional[str], jira_summary: Optional[str] = None
                ) -> None:
    """Upsert a (user, env, jira_key) row and prune to MAX_RECENT_PER_ENV.

    No-ops when environment_id is None (the user has no active env yet)
    or jira_key is empty. Idempotent on repeated calls — viewing the
    same ticket twice just bumps viewed_at.
    """
    if not user_id or not environment_id or not jira_key:
        return
    jira_key = jira_key.strip()
    if not jira_key:
        return
    try:
        # Upsert: bump viewed_at + refresh summary on repeat views so
        # the title reflects the latest Jira state if the user's
        # re-navigating through the app.
        db.execute(text("""
            INSERT INTO user_recent_tickets
                (user_id, environment_id, jira_key, jira_summary, viewed_at)
            VALUES (:uid, :eid, :key, :summary, NOW())
            ON CONFLICT (user_id, environment_id, jira_key) DO UPDATE
                SET viewed_at = NOW(),
                    jira_summary = COALESCE(
                        EXCLUDED.jira_summary, user_recent_tickets.jira_summary)
        """), {"uid": user_id, "eid": environment_id,
               "key": jira_key, "summary": jira_summary})
        # Prune older rows beyond the retention cap. Subquery gets the
        # cutoff viewed_at; anything older in this (user, env) scope
        # gets deleted. Runs in the same transaction as the insert.
        db.execute(text("""
            DELETE FROM user_recent_tickets
            WHERE user_id = :uid AND environment_id = :eid
              AND viewed_at < (
                SELECT COALESCE(MIN(viewed_at), NOW())
                FROM (
                    SELECT viewed_at FROM user_recent_tickets
                    WHERE user_id = :uid AND environment_id = :eid
                    ORDER BY viewed_at DESC
                    LIMIT :cap
                ) AS keep
              )
        """), {"uid": user_id, "eid": environment_id,
               "cap": MAX_RECENT_PER_ENV})
        db.commit()
    except Exception:
        db.rollback()
        # best-effort — never escape


def list_recent(db: Session, user_id: int, environment_id: int,
                limit: int = 10) -> list[dict]:
    """Return the last `limit` viewed tickets for (user_id, environment_id).

    Each dict: {jira_key, jira_summary, viewed_at (iso)}. Empty list if
    nothing tracked.
    """
    if not user_id or not environment_id:
        return []
    limit = max(1, min(int(limit or 10), MAX_RECENT_PER_ENV))
    rows = (db.query(UserRecentTicket)
            .filter(UserRecentTicket.user_id == user_id,
                    UserRecentTicket.environment_id == environment_id)
            .order_by(UserRecentTicket.viewed_at.desc())
            .limit(limit)
            .all())
    return [{
        "jira_key": r.jira_key,
        "jira_summary": r.jira_summary or "",
        "viewed_at": r.viewed_at.isoformat() if r.viewed_at else "",
    } for r in rows]


__all__ = ["record_view", "list_recent", "MAX_RECENT_PER_ENV"]
