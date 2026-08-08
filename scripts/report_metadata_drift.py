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
    args = p.parse_args()

    for line in open(os.path.join(REPO, ".env")):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k, v)

    from sqlalchemy import create_engine, text
    from primeqa.semantic.metadata_drift import detect_vr_drift

    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as conn:
        conn.execute(text(f"SET search_path TO {args.schema}, public"))
        events = detect_vr_drift(conn, args.org, since_seq=args.since_seq)

    if not events:
        print("no drift events")
        return 0
    print(f"{len(events)} drift event(s):")
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
