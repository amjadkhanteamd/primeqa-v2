"""Scheduled UI-conformance runs (SDLC v3 item 3, D-474) — the D-214
cadence pattern applied to the browser plane.

WHAT A SCHEDULE IS: a recorded authorisation to enqueue RUNS of one
APPROVED claim set on a cron cadence. It automates the enqueue and
nothing else — never approval, never authoring, never a policy bypass:
every fire goes through ``enqueue_ui_run`` (the D-245 boundary, the
D-461 manifest invariant, the D6 mode table), building a FRESH manifest
each time so the census and run-set pins are current at fire time.

ACTOR SEMANTICS (the semantics-bearing bit): ``created_by`` is the
authorising human. The D-245 boundary check runs against the CREATOR's
authority at schedule creation; each tick RE-CHECKS the creator's
CURRENT authority — an inactive or demoted-below-MEMBER creator
deactivates the schedule loudly (``dead_authority`` + audit), never a
silent run on dead authority. The enqueue itself is system-as-actor
with the schedule reference (`trigger` on the audit + the manifest's
execution mode = "scheduled"), attributed "scheduled by schedule <id>,
authorised by user <id>".

OVERLAP: if the schedule's previous job is still queued/running, the
tick SKIPS with an audit event and a recorded skip counter — never
stacks. FAILURE: a failed enqueue is a recorded ``error_state`` + audit
event — never a silent tick.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from primeqa.core.authz import AuthorizationError, Tier, authorize, rank
from primeqa.execution_engine.schedules import is_due

log = logging.getLogger("primeqa.ui_schedules")

_AUTH_MODES = ("vault", "totp_env")


class UiScheduleError(ValueError):
    """A refused schedule operation — the message names the cause."""


def _session(tenant_id: int):
    from primeqa.semantic.connection import get_tenant_connection
    return get_tenant_connection(tenant_id)


def _audit(session, action: str, details: dict,
           user_id: int | None, tenant_id: int) -> None:
    from primeqa.browser_worker.audit import record_event
    record_event(session, action=action, details=details,
                 user_id=user_id, tenant_id=tenant_id, mandatory_log=True)


def _validate_cron(cron_expr: str) -> None:
    from croniter import croniter
    try:
        croniter(cron_expr, datetime.now(timezone.utc))
    except Exception as exc:
        raise UiScheduleError(f"invalid cron expression {cron_expr!r}: {exc}")


def _validate_auth(auth: dict | None) -> dict | None:
    if auth is None:
        return None
    if auth.get("mode") not in _AUTH_MODES:
        raise UiScheduleError(
            f"auth mode {auth.get('mode')!r} is not schedulable; one of "
            f"{_AUTH_MODES} (descriptor only — never a credential)")
    if auth["mode"] == "vault" and not auth.get("persona"):
        raise UiScheduleError("auth mode 'vault' requires a persona key")
    out = {"mode": auth["mode"]}
    if auth.get("persona"):
        out["persona"] = auth["persona"]
    return out


# ---------------------------------------------------------------------------
# CRUD — real actors, the boundary checked at creation
# ---------------------------------------------------------------------------

def create_ui_run_schedule(tenant_id: int, *, subject, claim_set_id,
                           cron_expr: str, auth: dict | None = None,
                           note: str = "") -> dict:
    allow, reason = authorize(subject, Tier.MEMBER)
    if not allow:
        raise AuthorizationError(reason)
    _validate_cron(cron_expr)
    auth = _validate_auth(auth)
    user_id = (subject.get("user_id") or subject.get("id")
               if isinstance(subject, dict) else subject.user_id)
    with _session(tenant_id) as conn:
        s = Session(bind=conn)
        status = s.execute(text(
            "SELECT status FROM claim_sets WHERE id = :i"),
            {"i": str(claim_set_id)}).scalar()
        if status is None:
            raise UiScheduleError(f"no such claim set {claim_set_id}")
        if status != "approved":
            raise UiScheduleError(
                f"claim set {claim_set_id} is {status!r} — scheduling "
                "automates RUNS of an approved set, never approval")
        row = s.execute(text("""
            INSERT INTO ui_run_schedules
                (claim_set_id, cron_expr, auth, created_by, note)
            VALUES (:cs, :cron, CAST(:a AS JSONB), :u, :n)
            RETURNING id
        """), {"cs": str(claim_set_id), "cron": cron_expr,
               "a": json.dumps(auth) if auth else None,
               "u": user_id, "n": note}).fetchone()
        _audit(s, "ui.schedule_created",
               {"schedule_id": row[0], "claim_set_id": str(claim_set_id),
                "cron_expr": cron_expr,
                "auth_mode": (auth or {}).get("mode", "guest")},
               user_id, tenant_id)
        s.flush()
        return {"schedule_id": int(row[0]), "authorized": reason}


def deactivate_ui_run_schedule(tenant_id: int, *, subject,
                               schedule_id: int, reason: str) -> None:
    allow, why = authorize(subject, Tier.MEMBER)
    if not allow:
        raise AuthorizationError(why)
    user_id = (subject.get("user_id") or subject.get("id")
               if isinstance(subject, dict) else subject.user_id)
    with _session(tenant_id) as conn:
        s = Session(bind=conn)
        n = s.execute(text("""
            UPDATE ui_run_schedules
            SET active = FALSE, deactivated_reason = :r,
                deactivated_at = NOW()
            WHERE id = :i AND active
        """), {"r": reason, "i": schedule_id}).rowcount
        if not n:
            raise UiScheduleError(
                f"schedule {schedule_id} not found or already inactive")
        _audit(s, "ui.schedule_deactivated",
               {"schedule_id": schedule_id, "reason": reason},
               user_id, tenant_id)
        s.flush()


def list_ui_run_schedules(tenant_id: int) -> list:
    with _session(tenant_id) as conn:
        rows = conn.execute(text("""
            SELECT id, claim_set_id, cron_expr, auth, active, created_by,
                   created_at, last_enqueued_at, last_job_id,
                   skips_since_last_run, error_state, last_error,
                   deactivated_reason
            FROM ui_run_schedules ORDER BY id
        """)).fetchall()
        return [dict(r._mapping) for r in rows]


# ---------------------------------------------------------------------------
# The tick
# ---------------------------------------------------------------------------

def fire_due_ui_schedules(tenant_id: int, now: datetime | None = None,
                          scheme: str = "https") -> dict:
    """One pass over the tenant's active schedules. Every outcome is a
    recorded, audited state — nothing here is silent, and one bad
    schedule never poisons the rest."""
    from primeqa.execution_engine.ui_manifest import enqueue_ui_run

    now = now or datetime.now(timezone.utc)
    out = {"checked": 0, "fired": [], "skipped_overlap": [],
           "deactivated_dead_authority": [], "failed": []}
    with _session(tenant_id) as conn:
        s = Session(bind=conn)
        schedules = s.execute(text("""
            SELECT id, claim_set_id, cron_expr, auth, created_by,
                   created_at, last_enqueued_at, last_job_id
            FROM ui_run_schedules WHERE active ORDER BY id
        """)).fetchall()
        for row in schedules:
            (sid, claim_set_id, cron_expr, auth, created_by,
             created_at, last_enqueued_at, last_job_id) = row
            out["checked"] += 1
            if not is_due(cron_expr, last_enqueued_at, created_at, now):
                continue

            # -- dead authority: the creator's CURRENT standing decides
            creator = s.execute(text("""
                SELECT role, is_active FROM public.users WHERE id = :u
            """), {"u": created_by}).fetchone()
            alive = (creator is not None and creator[1]
                     and rank(creator[0]) >= Tier.MEMBER)
            if not alive:
                detail = ("creator missing" if creator is None else
                          "creator inactive" if not creator[1] else
                          f"creator role {creator[0]!r} below Member")
                s.execute(text("""
                    UPDATE ui_run_schedules
                    SET active = FALSE, error_state = 'dead_authority',
                        deactivated_reason = :d, deactivated_at = NOW()
                    WHERE id = :i
                """), {"d": detail, "i": sid})
                _audit(s, "ui.schedule_dead_authority",
                       {"schedule_id": sid, "authorised_by": created_by,
                        "detail": detail,
                        "disposition": "deactivated — a schedule never "
                                       "runs on dead authority"},
                       None, tenant_id)
                out["deactivated_dead_authority"].append(sid)
                continue

            # -- overlap: the previous scheduled run must be finished
            if last_job_id is not None:
                prev = s.execute(text("""
                    SELECT status FROM s4_ui_inspection_jobs WHERE id = :j
                """), {"j": str(last_job_id)}).scalar()
                if prev in ("pending", "in_progress"):
                    s.execute(text("""
                        UPDATE ui_run_schedules
                        SET last_skipped_at = NOW(),
                            skips_since_last_run = skips_since_last_run + 1
                        WHERE id = :i
                    """), {"i": sid})
                    _audit(s, "ui.schedule_overlap_skipped",
                           {"schedule_id": sid,
                            "previous_job_id": str(last_job_id),
                            "previous_status": prev,
                            "disposition": "skipped — scheduled runs "
                                           "never stack"},
                           None, tenant_id)
                    out["skipped_overlap"].append(sid)
                    continue

            # -- enqueue: a fresh manifest every fire (census + run set
            # pinned at fire time); system-as-actor with the schedule
            # reference, authorised by the creator
            subject = {"user_id": created_by, "tenant_id": tenant_id,
                       "role": creator[0]}
            try:
                enq = enqueue_ui_run(
                    s, subject=subject, claim_set_id=UUID(str(claim_set_id)),
                    scheme=scheme, auth=auth,
                    trigger={"scheduled_by_schedule": sid,
                             "authorised_by_user": created_by})
                s.execute(text("""
                    UPDATE ui_run_schedules
                    SET last_enqueued_at = :n, last_job_id = :j,
                        skips_since_last_run = 0,
                        error_state = NULL, last_error = NULL
                    WHERE id = :i
                """), {"n": now, "j": enq["job_id"], "i": sid})
                out["fired"].append({"schedule_id": sid,
                                     "job_id": enq["job_id"]})
            except AuthorizationError as exc:
                # demoted between the SELECT above and the boundary —
                # same disposition as dead authority, loudly
                s.execute(text("""
                    UPDATE ui_run_schedules
                    SET active = FALSE, error_state = 'dead_authority',
                        deactivated_reason = :d, deactivated_at = NOW()
                    WHERE id = :i
                """), {"d": str(exc)[:500], "i": sid})
                _audit(s, "ui.schedule_dead_authority",
                       {"schedule_id": sid, "authorised_by": created_by,
                        "detail": str(exc)[:200]},
                       None, tenant_id)
                out["deactivated_dead_authority"].append(sid)
            except Exception as exc:  # noqa: BLE001 — recorded, never silent
                s.execute(text("""
                    UPDATE ui_run_schedules
                    SET error_state = 'enqueue_failed', last_error = :e
                    WHERE id = :i
                """), {"e": str(exc)[:500], "i": sid})
                _audit(s, "ui.schedule_enqueue_failed",
                       {"schedule_id": sid, "error": str(exc)[:200],
                        "disposition": "error_state recorded — a failed "
                                       "scheduled enqueue is never a "
                                       "silent tick"},
                       None, tenant_id)
                out["failed"].append(sid)
        s.flush()
    return out


# ---------------------------------------------------------------------------
# CLI — non-secret argv only
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m primeqa.execution_engine.ui_schedules")
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--tenant-id", type=int, required=True)
    c.add_argument("--claim-set", required=True)
    c.add_argument("--cron", required=True)
    c.add_argument("--user-id", type=int, required=True)
    c.add_argument("--role", required=True)
    c.add_argument("--auth-mode", choices=_AUTH_MODES)
    c.add_argument("--persona")
    c.add_argument("--note", default="")
    ls = sub.add_parser("list")
    ls.add_argument("--tenant-id", type=int, required=True)
    d = sub.add_parser("deactivate")
    d.add_argument("--tenant-id", type=int, required=True)
    d.add_argument("--id", type=int, required=True)
    d.add_argument("--user-id", type=int, required=True)
    d.add_argument("--role", required=True)
    d.add_argument("--reason", required=True)
    args = parser.parse_args(argv)

    if args.cmd == "create":
        auth = ({"mode": args.auth_mode, "persona": args.persona}
                if args.auth_mode else None)
        out = create_ui_run_schedule(
            args.tenant_id,
            subject={"user_id": args.user_id, "tenant_id": args.tenant_id,
                     "role": args.role},
            claim_set_id=args.claim_set, cron_expr=args.cron,
            auth=auth, note=args.note)
        print(json.dumps(out))
    elif args.cmd == "list":
        print(json.dumps(list_ui_run_schedules(args.tenant_id),
                         default=str, indent=2))
    else:
        deactivate_ui_run_schedule(
            args.tenant_id,
            subject={"user_id": args.user_id, "tenant_id": args.tenant_id,
                     "role": args.role},
            schedule_id=args.id, reason=args.reason)
        print(json.dumps({"deactivated": args.id}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
