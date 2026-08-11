#!/usr/bin/env python3
"""S1 metadata-drift report — VR slice (D-434). READ PATH ONLY.

Runs the S1 version-diff detector over one org's ValidationRule history and
prints the events, one line per event, oldest first. Consumes S1 versions —
never calls the org. Delivery/notification is deliberately NOT here (D-428
NOTIFICATIONS_PROVIDER precondition).

    python scripts/report_metadata_drift.py                 # env-59, all history
    python scripts/report_metadata_drift.py --since-seq 52  # from a seq
"""
from __future__ import annotations

import argparse
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

ENV59_ORG = "902850e3-89c0-4d74-9141-66084045f439"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--org", default=ENV59_ORG,
                   help="connected_org_id (default: env-59)")
    p.add_argument("--schema", default="tenant_1",
                   help="tenant schema (default: tenant_1)")
    p.add_argument("--since-seq", type=int, default=None,
                   help="only report events at or after this version seq")
    p.add_argument("--type", dest="types", default="all",
                   choices=["vr", "picklist", "flow", "related", "all"],
                   help="artifact type to diff (default: all)")
    p.add_argument("--since-watermark", action="store_true",
                   help="list only events after the org's review watermark "
                        "(the unreviewed backlog; full history when the org "
                        "has never been reviewed)")
    p.add_argument("--ack", action="store_true",
                   help="after listing, advance the review watermark to the "
                        "org's latest version seq — THE ONLY writer of the "
                        "watermark (D-438); implies --since-watermark")
    args = p.parse_args()

    for line in open(os.path.join(REPO, ".env")):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k, v)

    from sqlalchemy import create_engine, text
    from primeqa.semantic.metadata_drift import (
        detect_flow_drift, detect_picklist_drift,
        detect_related_entity_drift, detect_vr_drift)

    detectors = {"vr": detect_vr_drift, "picklist": detect_picklist_drift,
                 "flow": detect_flow_drift,
                 "related": detect_related_entity_drift}
    wanted = list(detectors) if args.types == "all" else [args.types]

    from primeqa.sync.drift_hook import read_watermark, since_seq_for

    engine = create_engine(os.environ["DATABASE_URL"])
    total = 0
    with engine.connect() as conn:
        conn.execute(text(f"SET search_path TO {args.schema}, public"))
        since = args.since_seq
        if args.since_watermark or args.ack:
            wm = read_watermark(conn, args.org)
            since = since_seq_for(wm)
            print(f"review watermark: "
                  f"{'never-reviewed' if wm is None else f'seq {wm}'}")
        for t in wanted:
            events = detectors[t](conn, args.org, since_seq=since)
            total += len(events)
            print(f"== {t}: {len(events)} drift event(s) ==")
            for e in events:
                when = (e.at or "?")[:10]
                print(f"[{e.kind}] seq {e.seq} ({when}) {e.rule}")
                if e.kind == "FORMULA":
                    print(f"    before: {e.before!r}")
                    print(f"    after:  {e.after!r}")
                elif e.before is not None or e.after is not None:
                    print(f"    {e.before!r} -> {e.after!r}")
                if e.note:
                    print(f"    NOTE: {e.note}")
        if args.ack:
            # THE only watermark writer (D-438): explicit human review.
            row = conn.execute(text(
                "SELECT MAX(version_seq) FROM logical_versions "
                "WHERE connected_org_id = CAST(:org AS uuid)"),
                {"org": args.org}).fetchone()
            latest = row[0] if row and row[0] is not None else None
            if latest is None:
                print("ACK refused: org has no versions to acknowledge")
                return 1
            reviewer = os.environ.get("USER") or "unknown"
            conn.execute(text(
                "INSERT INTO s1_drift_review_watermarks "
                " (connected_org_id, last_reviewed_seq, reviewed_at, "
                "  reviewed_by) "
                "VALUES (CAST(:org AS uuid), :seq, now(), :by) "
                "ON CONFLICT (connected_org_id) DO UPDATE SET "
                " last_reviewed_seq = EXCLUDED.last_reviewed_seq, "
                " reviewed_at = EXCLUDED.reviewed_at, "
                " reviewed_by = EXCLUDED.reviewed_by"),
                {"org": args.org, "seq": int(latest), "by": reviewer})
            conn.commit()
            print(f"ACKNOWLEDGED: watermark advanced to seq {latest} "
                  f"(the {total} event(s) listed above are now reviewed; "
                  f"reviewed_by={reviewer})")
    if total == 0:
        print("no drift events")
    return 0


if __name__ == "__main__":
    sys.exit(main())
