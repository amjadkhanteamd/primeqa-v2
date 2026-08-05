#!/usr/bin/env python3
"""Flow restore-verifier — open-snapshot baseline (the corrected P6 design).

The 2026-08-02 finding (FLOW_PERTURBATION_PLAN.md §4.1 correction): a
hand-authored fixture file and a Metadata API retrieval are DIFFERENT
SERIALISATIONS of the same logic — element order, whitespace, and the org
eliding default-valued elements (`storeOutputAutomatically=false`) — so
byte-diff against committed source can never verify a restore. The byte-stable
baseline is a **window-open retrieve snapshot**: the same serialiser produces
both sides, so a diff is real.

Modes
-----
  snapshot [flows...]   retrieve each flow (throwaway temp project — never
                        this repo's tree) and store the bytes as the
                        window-open baseline under --snapshot-dir
                        (default ~/.primeqa/flow_snapshots/), named
                        <flow>__<UTC-timestamp>Z__<label>.flow-meta.xml —
                        unambiguous about which flow, which window, and when.
  verify   [flows...]   retrieve again and byte-diff against the flow's most
                        recent snapshot (or --snapshot-file for an explicit
                        one). The snapshot used is always printed.

Outcomes per flow — five, NEVER collapsed (a failed or empty retrieve is
UNVERIFIED, never "restored"):

    IDENTICAL        retrieve succeeded AND org bytes == snapshot bytes
    DIVERGENT        retrieve succeeded, bytes differ (unified diff shown)
    RETRIEVE_FAILED  the retrieve itself failed (CLI / auth / network)
    RETRIEVE_EMPTY   retrieve reported success but returned no flow file
    NO_SNAPSHOT      no window-open snapshot exists for this flow

Secondary signal — clearly separated, and it does NOT gate restoration: a
SEMANTIC comparison against the committed fixture source (parse both XMLs,
strip whitespace, sort children recursively). That answers "has the org's flow
LOGIC drifted from the repo?", which is a different question from "was the
window restored?". It is semantic rather than byte-level precisely because of
the serialisation finding above; and it is best-effort (an untracked fixture —
sandbox_fixtures/home_loan/ today — reports repo-signal: no-committed-baseline
without affecting the restore verdict). S1's version_number rides along as a
tertiary drift hint (S1 captures no flow logic; it can never verify anything).

Exit code: 0 only when EVERY requested flow is IDENTICAL (verify mode) or
snapshotted (snapshot mode); 2 when any retrieve failed/was empty
(unverifiable — the worst outcome); 1 otherwise (DIVERGENT / NO_SNAPSHOT).

READ PATH ONLY: never deploys, never writes to the repo tree, never mutates
the org. The perturb/deploy half of a P6 window is deliberately NOT here.
"""
from __future__ import annotations

import argparse
import difflib
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SNAPSHOT_DIR = os.path.expanduser("~/.primeqa/flow_snapshots")

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
SEVERITY = {"IDENTICAL": 0, "SNAPSHOTTED": 0, "NO_SNAPSHOT": 1,
            "DIVERGENT": 1, "RETRIEVE_EMPTY": 2, "RETRIEVE_FAILED": 2}

PROTOCOL = os.path.join(REPO, "docs/architecture/perturb_and_restore_protocol.md")
CAMPAIGN = os.path.join(REPO, "docs/architecture/FEATURE_CAMPAIGN.md")

# Per-section authorisation registry (D-432). The original parser read §6 to
# END OF FILE and substring-matched status text — correct with one section,
# nearly disabled when the §7/§8 drafts were appended beneath it (their
# UNSIGNED banners would have flipped P6's report). Every section now parses
# ONLY its own slice, delimited at the next `## ` heading. Keys double as
# snapshot-label prefixes: a `p3-*` snapshot checks §7's status and only §7's.
_SECTIONS = {
    "p6": {"label": "P6", "heading": r"^## 6\. ", "marker": "P6 REVOCATION MET"},
    "p3": {"label": "P3'", "heading": r"^## 7\. ", "marker": "P3 REVOCATION MET"},
    "p2": {"label": "P2'", "heading": r"^## 8\. ", "marker": "P2 REVOCATION MET"},
}
# Both the historical draft-banner wording and the 2026-08-05 interim wording
# are recognized, so no future draft can regress the parse by picking either.
_UNSIGNED_BANNERS = ("STATUS: DRAFT, UNSIGNED", "STATUS: UNSIGNED DRAFT")
# Back-compat name (pre-D-432 callers).
P6_REVOCATION_MARKER = _SECTIONS["p6"]["marker"]


def _section_slice(text: str, heading_re: str):
    """The section's OWN text: its heading up to the next `## ` heading (any)
    or EOF. None when the heading is absent — the caller reports UNKNOWN."""
    m = re.search(heading_re, text, re.M)
    if not m:
        return None
    rest = text[m.end():]
    nxt = re.search(r"^## ", rest, re.M)
    return text[m.start():m.end() + (nxt.start() if nxt else len(rest))]


def section_status(key: str, today=None, protocol_text=None,
                   campaign_text=None):
    """One section's live status, parsed from the protocol text itself so the
    report can never drift from what was signed (clause (e)).

    Returns (status, detail) with status one of:
        SIGNED | EXPIRED | REVOKED | UNSIGNED | UNKNOWN
    Parse discipline unchanged from the original p6_status: the banner must
    match the strict SIGNED/EXPIRES shapes or the status is UNKNOWN — a
    malformed or missing section NEVER reads as SIGNED.
    Precedence: UNSIGNED > UNKNOWN(parse) > REVOKED > EXPIRED > SIGNED.
    ``protocol_text``/``campaign_text`` exist for posture tests only.
    """
    from datetime import date
    spec = _SECTIONS[key]
    today = today or date.today()
    try:
        text = (protocol_text if protocol_text is not None
                else open(PROTOCOL, encoding="utf-8").read())
        section = _section_slice(text, spec["heading"])
        if section is None:
            return ("UNKNOWN", f"protocol has no {spec['label']} section — "
                    f"cannot infer authorisation")
        if any(b in section for b in _UNSIGNED_BANNERS):
            return ("UNSIGNED",
                    f"{spec['label']} is a draft; nothing may run under it")
        sig = re.search(
            r"STATUS: SIGNED — (\w+), (\d{4}-\d{2}-\d{2})", section)
        exp = re.search(r"EXPIRES (\d{4}-\d{2}-\d{2})", section)
        if not sig or not exp:
            return ("UNKNOWN", f"{spec['label']} banner unrecognized — "
                    f"refusing to infer SIGNED from malformed text")
        expiry = date.fromisoformat(exp.group(1))
        signed_on = f"signed by {sig.group(1)} on {sig.group(2)}"
        try:
            camp = (campaign_text if campaign_text is not None
                    else open(CAMPAIGN, encoding="utf-8").read())
        except OSError:
            return ("UNKNOWN", f"cannot read campaign ledger to evaluate the "
                    f"revocation condition ({signed_on})")
        if spec["marker"] in camp:
            return ("REVOKED", f"the revocation marker '{spec['marker']}' is "
                    f"recorded in the campaign ledger — revocation clause: "
                    f"authorisation VOID ({signed_on})")
        if today > expiry:
            return ("EXPIRED", f"hard expiry {expiry} passed — no "
                    f"{spec['label']} window may be opened; re-authorisation "
                    f"needs a new signature ({signed_on})")
        return ("SIGNED", f"{signed_on}; expires {expiry}; revocation "
                f"condition not met")
    except Exception as e:  # noqa: BLE001 — any surprise is UNKNOWN, loudly
        return "UNKNOWN", f"status parse failed ({e.__class__.__name__}: {e})"


def p6_status(today=None):
    """Back-compat wrapper (pre-D-432 name): §6's status from its own slice."""
    return section_status("p6", today=today)


def print_section_statuses() -> dict:
    """Session-start report per clause (e): every authorisation section's
    status, each parsed from its OWN slice. The loud banner is reserved for
    the alarming states (REVOKED / EXPIRED / UNKNOWN); SIGNED and UNSIGNED
    are expected states and print one line each — with three sections,
    bannering every unsigned draft would bury the alarm that matters."""
    statuses: dict = {}
    for key, spec in _SECTIONS.items():
        status, detail = section_status(key)
        statuses[key] = status
        if status in ("SIGNED", "UNSIGNED"):
            print(f"{spec['label']} status: {status} — {detail}")
        else:
            bang = "!" * 66
            print(bang)
            print(f"!!  {spec['label']} status: {status} — {detail}")
            print(f"!!  NO {spec['label']} WINDOW MAY BE OPENED "
                  f"under this status.")
            print(bang)
    return statuses


def print_p6_status() -> str:
    """Back-compat wrapper (pre-D-432 callers): P6's line only."""
    status, detail = section_status("p6")
    if status == "SIGNED":
        print(f"P6 status: SIGNED — {detail}")
    else:
        bang = "!" * 66
        print(bang)
        print(f"!!  P6 status: {status} — {detail}")
        print("!!  NO P6 WINDOW MAY BE OPENED under this status.")
        print(bang)
    return status


# ---------------------------------------------------------------------------
# Retrieval (one org call for N flows; throwaway temp project = no source
# tracking, so the dogfood log's 'Unchanged' silent no-op cannot occur)
# ---------------------------------------------------------------------------

def retrieve_flows(flows: list[str], org: str, tmpdir: str,
                   timeout: int) -> dict[str, tuple[str, bytes | None, str]]:
    """{flow: (status, bytes|None, detail)} — status RETRIEVED /
    RETRIEVE_EMPTY / RETRIEVE_FAILED, decided PER FLOW, never collapsed."""
    proj = os.path.join(tmpdir, "verify_proj")
    os.makedirs(os.path.join(proj, "force-app"), exist_ok=True)
    with open(os.path.join(proj, "sfdx-project.json"), "w") as f:
        json.dump({"packageDirectories": [{"path": "force-app",
                                           "default": True}],
                   "sourceApiVersion": "59.0"}, f)
    cmd = ["sf", "project", "retrieve", "start", "--target-org", org, "--json"]
    for fl in flows:
        cmd += ["--metadata", f"Flow:{fl}"]
    try:
        r = subprocess.run(cmd, cwd=proj, capture_output=True, text=True,
                           timeout=timeout)
    except FileNotFoundError:
        return {f: ("RETRIEVE_FAILED", None, "sf CLI not found on PATH")
                for f in flows}
    except subprocess.TimeoutExpired:
        return {f: ("RETRIEVE_FAILED", None, f"retrieve timed out ({timeout}s)")
                for f in flows}
    try:
        payload = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        payload = {}
    if r.returncode != 0 or payload.get("status", 1) != 0:
        msg = (payload.get("message")
               or (r.stderr or r.stdout or "").strip()[:400]
               or f"exit {r.returncode}")
        return {f: ("RETRIEVE_FAILED", None, msg) for f in flows}
    out: dict[str, tuple[str, bytes | None, str]] = {}
    for fl in flows:
        path = os.path.join(proj, "force-app", "main", "default", "flows",
                            f"{fl}.flow-meta.xml")
        if not os.path.exists(path):
            out[fl] = ("RETRIEVE_EMPTY", None,
                       "retrieve succeeded but no flow file landed")
            continue
        with open(path, "rb") as fh:
            out[fl] = ("RETRIEVED", fh.read(), "")
    return out


# ---------------------------------------------------------------------------
# Snapshot store
# ---------------------------------------------------------------------------

def snapshot_path(sdir: str, flow: str, label: str, stamp: str) -> str:
    return os.path.join(sdir, f"{flow}__{stamp}__{label}.flow-meta.xml")


def latest_snapshot(sdir: str, flow: str) -> str | None:
    hits = sorted(glob.glob(os.path.join(sdir, f"{flow}__*.flow-meta.xml")))
    return hits[-1] if hits else None


# ---------------------------------------------------------------------------
# Secondary signal: SEMANTIC comparison vs committed fixture (repo logic
# drift — a different question; never gates the restore verdict)
# ---------------------------------------------------------------------------

# Elements the Metadata API ELIDES when they hold their default value — a
# hand-authored fixture may spell them out. Observed live 2026-08-02 (the
# only content-level delta across all 8 candidates). Extend ONLY with
# observed elisions; anything not listed here reports as logic drift.
_DEFAULT_ELISIONS = {("storeOutputAutomatically", "false")}


def _canon(elem):
    tag = elem.tag.split("}")[-1]
    text = (elem.text or "").strip()
    kids = tuple(sorted(
        _canon(c) for c in elem
        if (c.tag.split("}")[-1], (c.text or "").strip())
        not in _DEFAULT_ELISIONS))
    return (tag, text, kids)


def repo_logic_signal(flow: str, org_bytes: bytes | None) -> str:
    if org_bytes is None:
        return "repo-signal: n/a (no retrieval)"
    hits = glob.glob(os.path.join(
        REPO, "sandbox_fixtures", "*", "force-app", "main", "default",
        "flows", f"{flow}.flow-meta.xml"))
    if not hits:
        return "repo-signal: no fixture file"
    rel = os.path.relpath(hits[0], REPO)
    r = subprocess.run(["git", "-C", REPO, "show", f"HEAD:{rel}"],
                       capture_output=True)
    if r.returncode != 0:
        return ("repo-signal: no-committed-baseline (fixture untracked — "
                "does not affect the restore verdict)")
    try:
        same = (_canon(ET.fromstring(r.stdout))
                == _canon(ET.fromstring(org_bytes)))
    except ET.ParseError as e:
        return f"repo-signal: unparseable ({e})"
    return ("repo-signal: logic matches committed fixture" if same
            else "repo-signal: LOGIC DRIFT vs committed fixture — "
                 "investigate separately (does not gate restoration)")


def s1_signal(flows: list[str]) -> dict[str, str]:
    """Tertiary: S1 version_number/is_active. S1 captures no flow logic, so
    this can prove a deploy happened, never verify a restore."""
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
            return {f: "S1: unavailable" for f in flows}
        from sqlalchemy import create_engine, text
        with create_engine(url).connect() as conn:
            rows = conn.execute(text(
                "SELECT e.sf_api_name, fd.version_number, fd.is_active "
                "FROM tenant_1.entities e "
                "JOIN tenant_1.flow_details fd ON fd.entity_id = e.id "
                "WHERE e.entity_type = 'Flow' "
                "  AND e.connected_org_id = CAST(:o AS uuid) "
                "  AND e.valid_to_seq IS NULL "
                "  AND e.sf_api_name = ANY(:names)"),
                {"o": ENV59_ORG, "names": flows}).all()
        by = {r[0]: f"S1: v{r[1]} active={r[2]}" for r in rows}
        return {f: by.get(f, "S1: not in current model") for f in flows}
    except Exception as e:  # noqa: BLE001 — tertiary signal degrades loudly
        return {f: f"S1: unavailable ({e.__class__.__name__})" for f in flows}


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("mode", choices=["snapshot", "verify"])
    p.add_argument("flows", nargs="*",
                   help="flow API names (default: the plan's candidate set)")
    p.add_argument("--target-org", default="primeqa-sandbox")
    p.add_argument("--snapshot-dir", default=DEFAULT_SNAPSHOT_DIR)
    p.add_argument("--snapshot-file",
                   help="verify against THIS snapshot (single-flow only)")
    p.add_argument("--label", default="window",
                   help="snapshot label, e.g. p6-f1-open (snapshot mode)")
    p.add_argument("--timeout", type=int, default=300)
    p.add_argument("--show-diff-lines", type=int, default=40)
    args = p.parse_args()
    flows = args.flows or CANDIDATES
    if args.snapshot_file and len(flows) != 1:
        p.error("--snapshot-file requires exactly one flow")

    # Clause (e), generalized per D-432: the session-start check reports
    # EVERY authorisation section's live status, each parsed from its own
    # bounded slice. A snapshot taken to OPEN a window (label 'p6-*' /
    # 'p3-*' / 'p2-*') is refused outright unless THAT label's section is
    # SIGNED — an unsigned section refuses that label's windows and only
    # that label's.
    statuses = print_section_statuses()
    if args.mode == "snapshot":
        lbl = args.label.lower()
        gate_key = next((k for k in _SECTIONS if lbl.startswith(k)), None)
        if gate_key and statuses[gate_key] != "SIGNED":
            print(f"REFUSED: cannot open a {_SECTIONS[gate_key]['label']} "
                  f"window (label {args.label!r}) while its status is "
                  f"{statuses[gate_key]}.")
            return 2

    s1 = s1_signal(flows)
    with tempfile.TemporaryDirectory(prefix="flow_verify_") as tmp:
        retrieved = retrieve_flows(flows, args.target_org, tmp, args.timeout)

    worst = 0
    if args.mode == "snapshot":
        os.makedirs(args.snapshot_dir, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        print(f"snapshot mode — org={args.target_org} label={args.label} "
              f"stamp={stamp}\nstore: {args.snapshot_dir}\n")
        for fl in flows:
            status, data, detail = retrieved[fl]
            if status != "RETRIEVED":
                print(f"[{status}] {fl}: {detail}  ({s1[fl]}) — NO snapshot "
                      f"written; the window must not open on a failed read")
                worst = max(worst, SEVERITY[status])
                continue
            path = snapshot_path(args.snapshot_dir, fl, args.label, stamp)
            with open(path, "wb") as fh:
                fh.write(data)
            print(f"[SNAPSHOTTED] {fl}: {len(data)} bytes -> {path}  "
                  f"({s1[fl]}; {repo_logic_signal(fl, data)})")
        return worst

    print(f"verify mode — org={args.target_org} "
          f"(byte-diff vs the WINDOW-OPEN SNAPSHOT is authoritative; the "
          f"repo-logic and S1 signals never gate restoration)\n")
    for fl in flows:
        status, data, detail = retrieved[fl]
        if status != "RETRIEVED":
            print(f"[{status}] {fl}: {detail}  ({s1[fl]})")
            print("           -> UNVERIFIED. A failed/empty retrieve is "
                  "never evidence of a restored (or unrestored) org.")
            worst = max(worst, SEVERITY[status])
            continue
        snap = args.snapshot_file or latest_snapshot(args.snapshot_dir, fl)
        if snap is None:
            print(f"[NO_SNAPSHOT] {fl}: no window-open snapshot under "
                  f"{args.snapshot_dir} — nothing to verify against. "
                  f"({s1[fl]}; {repo_logic_signal(fl, data)})")
            worst = max(worst, 1)
            continue
        with open(snap, "rb") as fh:
            base = fh.read()
        if data == base:
            print(f"[IDENTICAL] {fl}: org == snapshot "
                  f"{os.path.basename(snap)} ({len(data)} bytes). "
                  f"({s1[fl]}; {repo_logic_signal(fl, data)})")
        else:
            print(f"[DIVERGENT] {fl}: org differs from snapshot "
                  f"{os.path.basename(snap)}  ({s1[fl]}; "
                  f"{repo_logic_signal(fl, data)})")
            diff = list(difflib.unified_diff(
                base.decode("utf-8", "replace").splitlines(),
                data.decode("utf-8", "replace").splitlines(),
                fromfile=f"snapshot:{os.path.basename(snap)}",
                tofile=f"org:{fl}", lineterm=""))
            for line in diff[:args.show_diff_lines]:
                print(f"    {line}")
            if len(diff) > args.show_diff_lines:
                print(f"    … {len(diff) - args.show_diff_lines} more lines")
            worst = max(worst, 1)
    return worst


if __name__ == "__main__":
    sys.exit(main())
