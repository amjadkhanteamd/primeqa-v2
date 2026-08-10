"""Post-sync metadata-drift hook (D-438).

Fires after a sync job completes (``status ∈ {success, partial_success}``),
runs the D-434/D-437 drift detectors since the org's REVIEW WATERMARK, and
emits a compact, loud report through the log and the best-effort notify path.

The three rules this module exists to enforce:

* **THE HOOK CANNOT FAIL A SYNC.** It runs after ``store.complete``, on its
  own connection (``get_tenant_connection`` — it cannot touch the sync's
  transactions), and its entire body is wrapped: any exception logs the
  greppable marker ``S1-DRIFT-HOOK-FAILURE`` and returns. The consumer wraps
  the call a second time (defense in depth). But never silent — a failure is
  one loud, distinguishable log line, not a swallowed pass.
* **THE WATERMARK ADVANCES ONLY VIA THE EXPLICIT CLI REVIEW COMMAND**
  (``scripts/report_metadata_drift.py --ack``). This module never writes it
  — pinned by test. An event stays in every subsequent sync's emission until
  a human acknowledges it; the watermark silences through review, not time.
* **PARTIAL SUCCESS: run and LABEL, never skip.** The detectors walk full
  history, so a partial sync can make events MISSING (a phase that did not
  run captured nothing) but never WRONG; the emission carries an explicit
  derived-from-partial label instead of delaying detection (D-433).

Emission compactness contract: one summary line always; at most
``_MAX_EVENT_LINES`` per-event lines; a ``+K more`` pointer for the rest.
A never-reviewed backlog or a capture repair is a handful of lines, never a
wall (the D-437 suppression already makes a capture repair ONE event).
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import text

log = logging.getLogger(__name__)

# Greppable, dedicated failure marker — a hook failure must be loud and
# impossible to mistake for a clean no-event run (which logs its own line).
FAILURE_MARKER = "S1-DRIFT-HOOK-FAILURE"

_MAX_EVENT_LINES = 5
_VALUE_TRIM = 60

_REVIEW_CMD = ("python scripts/report_metadata_drift.py --since-watermark"
               "  (acknowledge with --ack)")


def read_watermark(conn, connected_org_id: str) -> Optional[int]:
    """``last_reviewed_seq``, or ``None`` when the org has NEVER been
    reviewed. Never-reviewed is the ABSENCE of the row — distinguishable by
    construction from a row with ``last_reviewed_seq = 0`` (reviewed at 0);
    the column is NOT NULL so no third state exists (D-438)."""
    row = conn.execute(text(
        "SELECT last_reviewed_seq FROM s1_drift_review_watermarks "
        "WHERE connected_org_id = CAST(:org AS uuid)"),
        {"org": str(connected_org_id)}).fetchone()
    return None if row is None else int(row[0])


def since_seq_for(last_reviewed_seq: Optional[int]) -> Optional[int]:
    """The detector's ``since_seq`` for a watermark: never-reviewed (None)
    means the FULL backlog; reviewed-at-N means events strictly after N."""
    return None if last_reviewed_seq is None else last_reviewed_seq + 1


def _trim(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    v = " ".join(str(value).split())
    return v if len(v) <= _VALUE_TRIM else v[:_VALUE_TRIM - 1] + "…"


def format_drift_lines(events, *, watermark: Optional[int], org_id: str,
                       sync_run_id, sync_status: str,
                       counts_by_type: dict) -> list[str]:
    """The emission, per the D-438 compactness contract. Pure — testable."""
    org8 = str(org_id)[:8]
    wm = "never-reviewed" if watermark is None else f"seq {watermark}"
    tail = f"[sync_run={sync_run_id} status={sync_status}]"
    if not events:
        return [f"s1-drift: org {org8} — no unreviewed drift events "
                f"(watermark={wm}) {tail}"]
    per_type = " ".join(f"{t}={n}" for t, n in counts_by_type.items() if n)
    lines = [f"s1-drift: org {org8} — {len(events)} UNREVIEWED drift "
             f"event(s) since watermark={wm} ({per_type}) {tail}"]
    for e in events[:_MAX_EVENT_LINES]:
        when = (e.at or "?")[:10]
        detail = ""
        if e.before is not None or e.after is not None:
            detail = f": {_trim(e.before)!r} -> {_trim(e.after)!r}"
        lines.append(
            f"s1-drift:   [{e.kind}] seq {e.seq} ({when}) {e.rule}{detail}")
    if len(events) > _MAX_EVENT_LINES:
        lines.append(f"s1-drift:   ... +{len(events) - _MAX_EVENT_LINES} "
                     f"more — review: {_REVIEW_CMD}")
    else:
        lines.append(f"s1-drift:   review: {_REVIEW_CMD}")
    if sync_status == "partial_success":
        lines.append(
            "s1-drift:   NOTE: derived from a PARTIAL sync — entity types "
            "not captured in this run may hold undetected changes")
    return lines


def collect_drift_events(conn, connected_org_id: str,
                         since_seq: Optional[int]):
    """All three detectors since ``since_seq``; returns (events,
    counts_by_type) with events seq-ordered across types."""
    from primeqa.semantic.metadata_drift import (
        detect_flow_drift, detect_picklist_drift, detect_vr_drift)
    per_type = {
        "vr": detect_vr_drift(conn, connected_org_id, since_seq=since_seq),
        "picklist": detect_picklist_drift(conn, connected_org_id,
                                          since_seq=since_seq),
        "flow": detect_flow_drift(conn, connected_org_id,
                                  since_seq=since_seq),
    }
    events = sorted((e for evs in per_type.values() for e in evs),
                    key=lambda e: (e.seq, e.rule, e.kind))
    return events, {t: len(evs) for t, evs in per_type.items()}


def run_post_sync_drift_hook(
    tenant_id: int, connected_org_id, sync_run_id, sync_status: str,
    *, _connect=None,
) -> None:
    """The post-sync entry point (called by ``sync/consumer.py`` after
    ``store.complete``). NEVER raises; NEVER writes the watermark.

    ``_connect`` is a test seam: a zero-arg callable returning a
    context-managed tenant-scoped connection (default:
    ``get_tenant_connection(tenant_id)``).
    """
    try:
        if sync_status not in ("success", "partial_success"):
            log.debug("s1-drift: hook skipped (sync_run=%s status=%s — only "
                      "success/partial_success fire)",
                      sync_run_id, sync_status)
            return
        if _connect is None:
            from primeqa.semantic.connection import get_tenant_connection
            def _connect():                                   # noqa: E306
                return get_tenant_connection(tenant_id)
        with _connect() as conn:
            watermark = read_watermark(conn, str(connected_org_id))
            events, counts = collect_drift_events(
                conn, str(connected_org_id), since_seq_for(watermark))
        lines = format_drift_lines(
            events, watermark=watermark, org_id=str(connected_org_id),
            sync_run_id=sync_run_id, sync_status=sync_status,
            counts_by_type=counts)
        for line in lines:
            log.info(line)
        if events:
            # Best-effort (never raises): loud log today; real alerting the
            # day NOTIFICATIONS_PROVIDER is configured (D-428 gates the
            # channel, not this hook).
            from primeqa.shared.notifications import notify_metadata_drift
            notify_metadata_drift(tenant_id, org_id=str(connected_org_id),
                                  subject_line=lines[0],
                                  body="\n".join(lines))
    except Exception:                     # noqa: BLE001 — the wall (D-438)
        log.exception(
            "%s: drift hook crashed; the sync's outcome is unaffected "
            "(org=%s sync_run=%s status=%s)",
            FAILURE_MARKER, connected_org_id, sync_run_id, sync_status)
