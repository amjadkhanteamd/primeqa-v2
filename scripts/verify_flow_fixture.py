#!/usr/bin/env python3
"""Flow fixture restore-verifier — the READ half of the P6 protocol row.

Retrieves a flow's CURRENT org metadata (Metadata API, via the sf CLI, into a
throwaway temp project — never into this repo's tree) and byte-diffs it against
the COMMITTED fixture source (`git show HEAD:<fixture path>`).

Why this exists (FLOW_PERTURBATION_PLAN.md §4.2/§4.3): S1 captures no flow
logic (`flow_details.parsed_logic` is NULL for every candidate), so the
perturb-and-restore protocol's "synced S1 state matches baseline" check is
VACUOUS for flow-logic edits; and `sf project deploy` has been observed
silently no-opping under source tracking (dogfood log, P1 window 1), so a
deploy's own success message is not evidence the org changed.

**The byte-diff against committed fixture bytes is the AUTHORITATIVE restore
check.** The S1 `version_number` shown alongside is a cheap secondary drift
signal only — it can prove *something* was deployed, never *what*.

Outcomes per flow — three distinct failure surfaces, NEVER collapsed
(a failed retrieve must not read as verified):

    IDENTICAL             retrieve succeeded AND org bytes == committed bytes
    DIVERGENT             retrieve succeeded, bytes differ (unified diff shown)
    RETRIEVE_EMPTY        retrieve reported success but returned no flow file
    RETRIEVE_FAILED       the retrieve itself failed (CLI error / auth / net)
    NO_COMMITTED_BASELINE the fixture file is not tracked by git — there is
                          no committed baseline to verify against (the org
                          state is still shown vs the WORKING-TREE file, as
                          information only; the status stays unverified)

Exit code: 0 only when EVERY requested flow is IDENTICAL.
           2 when any flow is RETRIEVE_FAILED / RETRIEVE_EMPTY (unverifiable —
             the worst outcome: nothing may be claimed either way).
           1 otherwise (DIVERGENT and/or NO_COMMITTED_BASELINE).

READ PATH ONLY. This script never deploys, never writes to the repo tree, and
never mutates the org. The perturb/deploy half of a P6 window is deliberately
NOT here.
"""
from __future__ import annotations

import argparse
import difflib
import glob
import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The FLOW_PERTURBATION_PLAN.md candidate set (F1–F8, deduplicated).
CANDIDATES = [
    "PLS_FB_FL07_Order_Rollup",
    "PLS_FB_FL04_Confirmation_Task",
    "PLS_FB_FL05_Cancellation_Sync",
    "PLS_FB_FL09_Reopen_Guard",
    "SQ205_Create_Case_SLA",
    "SQ205_Escalation_Effects",
    "HL_Auto_Risk_Rating",
    "HL_High_Risk_Task",
]

ENV59_ORG = "902850e3-89c0-4d74-9141-66084045f439"

SEVERITY = {"IDENTICAL": 0, "NO_COMMITTED_BASELINE": 1, "DIVERGENT": 1,
            "RETRIEVE_EMPTY": 2, "RETRIEVE_FAILED": 2}


def _fixture_path(flow: str) -> str | None:
    hits = glob.glob(os.path.join(
        REPO, "sandbox_fixtures", "*", "force-app", "main", "default",
        "flows", f"{flow}.flow-meta.xml"))
    return hits[0] if len(hits) == 1 else (hits[0] if hits else None)


def _committed_bytes(fixture_abs: str) -> bytes | None:
    """The COMMITTED baseline — `git show HEAD:<path>`. None = not tracked."""
    rel = os.path.relpath(fixture_abs, REPO)
    r = subprocess.run(["git", "-C", REPO, "show", f"HEAD:{rel}"],
                       capture_output=True)
    return r.stdout if r.returncode == 0 else None


def _retrieve(flow: str, org: str, tmpdir: str,
              timeout: int) -> tuple[str, bytes | None, str]:
    """Retrieve the flow into a throwaway sfdx project under tmpdir.

    A FRESH temp project has no source tracking, so the dogfood log's
    'Unchanged' silent no-op hazard cannot occur here. Returns
    (status, org_bytes, detail) where status is RETRIEVED / RETRIEVE_EMPTY /
    RETRIEVE_FAILED.
    """
    proj = os.path.join(tmpdir, "verify_proj")
    os.makedirs(os.path.join(proj, "force-app"), exist_ok=True)
    with open(os.path.join(proj, "sfdx-project.json"), "w") as f:
        json.dump({"packageDirectories": [{"path": "force-app",
                                           "default": True}],
                   "sourceApiVersion": "59.0"}, f)
    try:
        r = subprocess.run(
            ["sf", "project", "retrieve", "start",
             "--metadata", f"Flow:{flow}", "--target-org", org, "--json"],
            cwd=proj, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return "RETRIEVE_FAILED", None, "sf CLI not found on PATH"
    except subprocess.TimeoutExpired:
        return "RETRIEVE_FAILED", None, f"retrieve timed out ({timeout}s)"
    try:
        payload = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        payload = {}
    if r.returncode != 0 or payload.get("status", 1) != 0:
        msg = (payload.get("message")
               or (r.stderr or r.stdout or "").strip()[:400]
               or f"exit {r.returncode}")
        return "RETRIEVE_FAILED", None, msg
    out = os.path.join(proj, "force-app", "main", "default", "flows",
                       f"{flow}.flow-meta.xml")
    if not os.path.exists(out):
        # CLI said success but produced nothing — the flow does not exist in
        # the org under this name, or the retrieve was silently empty. This
        # is NOT verification either way.
        files = payload.get("result", {}).get("files", [])
        return ("RETRIEVE_EMPTY", None,
                f"retrieve succeeded but no flow file landed "
                f"(files in result: {len(files)})")
    with open(out, "rb") as f:
        return "RETRIEVED", f.read(), ""


def _s1_signal(flows: list[str]) -> dict[str, str]:
    """SECONDARY signal only: S1's version_number + is_active per flow.

    S1 captures no flow logic, so it can never verify a restore — a changed
    version_number proves a deploy happened; an unchanged one proves nothing.
    Best-effort: DB unreachable → 'S1: unavailable' (which must not, and does
    not, affect the byte-diff verdict).
    """
    try:
        env = {}
        with open(os.path.join(REPO, ".env")) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env.setdefault(k, v)
        url = os.environ.get("DATABASE_URL") or env.get("DATABASE_URL")
        if not url:
            return {f: "S1: unavailable (no DATABASE_URL)" for f in flows}
        from sqlalchemy import create_engine, text
        eng = create_engine(url)
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT e.sf_api_name, fd.version_number, fd.is_active "
                "FROM tenant_1.entities e "
                "JOIN tenant_1.flow_details fd ON fd.entity_id = e.id "
                "WHERE e.entity_type = 'Flow' "
                "  AND e.connected_org_id = CAST(:o AS uuid) "
                "  AND e.valid_to_seq IS NULL "
                "  AND e.sf_api_name = ANY(:names)"),
                {"o": ENV59_ORG, "names": flows}).all()
        by = {r[0]: f"S1: version_number={r[1]} is_active={r[2]}"
              for r in rows}
        return {f: by.get(f, "S1: flow not in current org model") for f in flows}
    except Exception as e:  # noqa: BLE001 — secondary signal degrades loudly
        return {f: f"S1: unavailable ({e.__class__.__name__})" for f in flows}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("flows", nargs="*",
                   help="flow API names (default: the plan's candidate set)")
    p.add_argument("--target-org", default="primeqa-sandbox")
    p.add_argument("--timeout", type=int, default=120)
    p.add_argument("--show-diff-lines", type=int, default=40)
    args = p.parse_args()
    flows = args.flows or CANDIDATES

    s1 = _s1_signal(flows)
    worst = 0
    print(f"flow fixture verifier — org={args.target_org} "
          f"(byte-diff vs committed fixture bytes is AUTHORITATIVE; "
          f"S1 is a secondary drift signal only)\n")
    for flow in flows:
        fixture = _fixture_path(flow)
        if fixture is None:
            print(f"[RETRIEVE_FAILED] {flow}: no fixture file found under "
                  f"sandbox_fixtures/**/flows/ — cannot verify")
            worst = max(worst, 2)
            continue
        committed = _committed_bytes(fixture)
        with tempfile.TemporaryDirectory(prefix="flow_verify_") as tmp:
            status, org_bytes, detail = _retrieve(
                flow, args.target_org, tmp, args.timeout)
        if status != "RETRIEVED":
            print(f"[{status}] {flow}: {detail}  ({s1[flow]})")
            print("           -> UNVERIFIED. A failed/empty retrieve is "
                  "never evidence of a restored (or unrestored) org.")
            worst = max(worst, SEVERITY[status])
            continue
        baseline, base_label = committed, "committed"
        if committed is None:
            baseline, base_label = open(fixture, "rb").read(), \
                "WORKING TREE (file is NOT tracked by git — no committed " \
                "baseline exists)"
        if org_bytes == baseline:
            if committed is None:
                print(f"[NO_COMMITTED_BASELINE] {flow}: org matches the "
                      f"untracked working-tree file byte-for-byte, but there "
                      f"is no committed baseline to verify against. "
                      f"({s1[flow]})")
                worst = max(worst, 1)
            else:
                print(f"[IDENTICAL] {flow}: org == committed fixture bytes "
                      f"({len(org_bytes)} bytes). ({s1[flow]})")
        else:
            tag = ("NO_COMMITTED_BASELINE"
                   if committed is None else "DIVERGENT")
            print(f"[{tag}] {flow}: org differs from {base_label} "
                  f"({s1[flow]})")
            diff = list(difflib.unified_diff(
                baseline.decode("utf-8", "replace").splitlines(),
                org_bytes.decode("utf-8", "replace").splitlines(),
                fromfile=f"{base_label}:{os.path.relpath(fixture, REPO)}",
                tofile=f"org:{flow}", lineterm=""))
            for line in diff[:args.show_diff_lines]:
                print(f"    {line}")
            if len(diff) > args.show_diff_lines:
                print(f"    … {len(diff) - args.show_diff_lines} more "
                      f"diff lines")
            worst = max(worst, SEVERITY[tag])
    return worst


if __name__ == "__main__":
    sys.exit(main())
