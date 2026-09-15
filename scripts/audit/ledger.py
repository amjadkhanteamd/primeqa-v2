#!/usr/bin/env python3
"""Findings ledger for the 2026-09 audit. Append-only during the sweep; the
final `docs/audit/findings.json` is written from it. Every finding carries the
fields AK's brief names, and CLASS groups by shared cause, not symptom.

    python scripts/audit/ledger.py add  <json-object-on-stdin>
    python scripts/audit/ledger.py list
    python scripts/audit/ledger.py write docs/audit/findings.json
"""
from __future__ import annotations

import datetime
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LEDGER = os.path.join(REPO, "docs", "audit", ".ledger.jsonl")
REQUIRED = ("title", "class", "severity", "pass", "surface", "reproduction",
            "evidence", "expected", "observed", "proposed_fix")


def _load():
    if not os.path.exists(LEDGER):
        return []
    return [json.loads(l) for l in open(LEDGER) if l.strip()]


def add(obj: dict):
    missing = [k for k in REQUIRED if k not in obj]
    assert not missing, "missing %s" % missing
    assert obj["severity"] in ("critical", "high", "medium", "low"), obj["severity"]
    assert obj["pass"] in (1, 2, 3, 4), obj["pass"]
    rows = _load()
    obj = {"id": "AUD-%03d" % (len(rows) + 1), **obj, "status": "open",
           "fix_plan_ref": None,
           "discovered_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "a") as f:
        f.write(json.dumps(obj) + "\n")
    print(obj["id"], obj["severity"].upper(), obj["title"])


def main():
    cmd = sys.argv[1]
    if cmd == "add":
        add(json.load(sys.stdin))
    elif cmd == "list":
        for r in _load():
            print("%s %-8s p%s %-38s %s" % (r["id"], r["severity"], r["pass"], r["class"][:38], r["title"]))
    elif cmd == "write":
        rows = _load()
        json.dump(rows, open(sys.argv[2], "w"), indent=1)
        print("wrote", len(rows), "findings to", sys.argv[2])


if __name__ == "__main__":
    main()
