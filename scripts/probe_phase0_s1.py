#!/usr/bin/env python
"""Phase-0 (S1 breadth-unblock) live-sandbox probe — the merge exit-gate for
D-118 / D-119 / D-120.

Verify-only: run an S1 sync against a connected sandbox org FIRST (on the
`phase-8-substrate-1-tier2` branch, so the D-118 content-match runs during the
field phase), THEN run this probe against the resulting S1 state:

    python scripts/probe_phase0_s1.py --tenant-id 1
    # or: PRIMEQA_PROBE_TENANT_ID=1 python scripts/probe_phase0_s1.py

It checks, against the REAL synced data, what the deterministic unit tests
cannot:
  D-118  real standard picklist fields actually content-match their
         StandardValueSets (the HAS_PICKLIST_VALUES edge count moved 0 → N,
         and Account.Industry links to its SVS)
  D-119  get_picklist_values reads that value set
  D-120  permission-claim + config grounding work over real grant / detail data
         (get_related edge properties, get_entities, get_entity_details)

Prints PASS / FAIL per check; exits non-zero on any failure. A 0 edge count or
an unlinked Account.Industry is a real finding (exact-match found nothing on
this org's data), not a probe bug — that is exactly what this gate exists to
catch before merge.
"""
from __future__ import annotations

import argparse
import os
import sys

# Repo root on sys.path so `python scripts/probe_phase0_s1.py` resolves `primeqa`
# (matches the other scripts/ probes; also runnable via `railway run`).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from primeqa.semantic.connection import get_tenant_connection  # noqa: E402
from primeqa.semantic.query import SemanticOrgModel  # noqa: E402


class _Probe:
    def __init__(self) -> None:
        self.failures = 0

    def check(self, label, fn) -> bool:
        try:
            ok, detail = fn()
        except Exception as e:  # defensive: a raising check is a failure, not a crash
            ok, detail = False, f"raised {type(e).__name__}: {e}"
        if not ok:
            self.failures += 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
        return ok


def run(tenant_id: int) -> int:
    p = _Probe()
    print(f"Phase-0 S1 live probe — tenant {tenant_id}\n")
    with get_tenant_connection(tenant_id) as conn:
        s1 = SemanticOrgModel(conn)
        at = s1.current_version_seq()
        print(f"  current_version_seq = {at}\n")

        industry_pvs = {"id": None}

        # --- D-118: standard-field → SVS HAS_PICKLIST_VALUES edges ---
        print("D-118 — standard-field → SVS edges (content-match):")

        def _edge_count():
            n = conn.execute(text(
                "SELECT COUNT(*) FROM edges WHERE edge_type = 'HAS_PICKLIST_VALUES' "
                "AND valid_to_seq IS NULL")).scalar() or 0
            return n > 0, f"{n} HAS_PICKLIST_VALUES edges (expected > 0; ~0 pre-D-118)"
        p.check("edge count moved above 0", _edge_count)

        def _industry_linked():
            fields = s1.get_entities("Field", at_seq=at,
                                     filters={"sf_api_name": "Account.Industry"})
            if not fields:
                return False, "Account.Industry not found (org-specific — spot-check skipped)"
            rel = s1.get_related(fields[0].id, ["HAS_PICKLIST_VALUES"], "outbound", at_seq=at)
            if not rel:
                return False, "Account.Industry has NO HAS_PICKLIST_VALUES edge (match missed it)"
            industry_pvs["id"] = rel[0].entity.id
            return True, f"Account.Industry → {rel[0].entity.sf_api_name}"
        p.check("Account.Industry links to its SVS", _industry_linked)

        # --- D-119: get_picklist_values reads the value set ---
        print("\nD-119 — get_picklist_values read:")

        def _read_values():
            pvs_id = industry_pvs["id"]
            if pvs_id is None:  # fall back to any value set with values
                pvs_id = conn.execute(text(
                    "SELECT target_entity_id FROM edges WHERE edge_type = "
                    "'HAS_PICKLIST_VALUES' AND valid_to_seq IS NULL LIMIT 1")).scalar()
                if pvs_id is None:
                    return False, "no HAS_PICKLIST_VALUES edge to read from"
            vals = s1.get_picklist_values(pvs_id, at_seq=at)
            sample = [v["value_api_name"] for v in vals if v["is_active"]][:6]
            return len(vals) > 0, f"{len(vals)} values, e.g. {sample}"
        p.check("get_picklist_values returns the value set", _read_values)

        # --- D-120: permission + config grounding over real data ---
        print("\nD-120 — permission + config grounding (existing primitives):")

        def _config_existence():
            objs = s1.get_entities("Object", at_seq=at, filters={"sf_api_name": "Account"})
            return len(objs) == 1, f"get_entities(Object, Account) → {len(objs)}"
        p.check("config existence (get_entities)", _config_existence)

        def _config_property():
            fields = s1.get_entities("Field", at_seq=at,
                                     filters={"sf_api_name": "Account.Industry"})
            if not fields:
                return False, "Account.Industry not found (skipped)"
            d = s1.get_entity_details(fields[0].id, at_seq=at)
            return d is not None and "field_type" in d, \
                f"get_entity_details → field_type={d.get('field_type') if d else None}"
        p.check("config property (get_entity_details)", _config_property)

        def _permission_grant():
            profiles = s1.get_entities("Profile", at_seq=at)
            if not profiles:
                return False, "no Profile entities synced"
            for prof in profiles[:25]:
                rel = s1.get_related(prof.id, ["GRANTS_FIELD_ACCESS"], "outbound", at_seq=at)
                if rel:
                    props = rel[0].properties
                    return ("can_read" in props or "can_edit" in props), \
                        f"{prof.sf_api_name} grants {len(rel)} fields, props={list(props)[:4]}"
            return False, "no Profile with GRANTS_FIELD_ACCESS edges (check the permission sync)"
        p.check("permission grant read (get_related properties)", _permission_grant)

    print()
    if p.failures:
        print(f"PROBE FAILED — {p.failures} check(s) failed.")
        return 1
    print("PROBE PASSED — Phase 0 verified on live data.")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase-0 S1 live-sandbox probe (D-118/119/120).")
    ap.add_argument("--tenant-id", default=os.environ.get("PRIMEQA_PROBE_TENANT_ID"),
                    help="tenant id to probe (or set PRIMEQA_PROBE_TENANT_ID)")
    args = ap.parse_args()
    if args.tenant_id is None:
        ap.error("--tenant-id is required (or set PRIMEQA_PROBE_TENANT_ID)")
    sys.exit(run(int(args.tenant_id)))


if __name__ == "__main__":
    main()
