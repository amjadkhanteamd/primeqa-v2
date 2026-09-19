#!/usr/bin/env python3
"""RUN 2 PASS 2 — the NUMBERS, at all three tiers. A wrong-but-plausible count is
worse than a crash, so every check states the number the planted world makes
true, reads the page as viewer, member and admin, and records what each said.
Where the page is not for a tier (a redirect or a 403) that is recorded as
'not shown', never as a pass.

Usage: DATABASE_URL=scratch ... python scripts/audit/counts2.py ids.json manifest.json > counts.json
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from session import client_for  # noqa: E402

ids = json.load(open(sys.argv[1])); M = json.load(open(sys.argv[2]))
from primeqa.app import create_app  # noqa: E402
app = create_app(); app.config["TESTING"] = True
TIERS = ("viewer_t1", "member_t1", "admin_t1")
out = []


def txt(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S)))


def get(tier, path):
    c = client_for(app, tier)
    r = c.get(path, follow_redirects=False)
    if r.status_code in (301, 302, 303):
        return r.status_code, "", r.headers.get("Location", "")
    return r.status_code, r.get_data(as_text=True), ""


def check(name, path, expect, reader):
    """reader(html, text) -> (observed, ok)."""
    row = {"check": name, "path": path, "expected": expect, "tiers": {}}
    for tier in TIERS:
        st, html, loc = get(tier, path)
        if st != 200:
            row["tiers"][tier] = {"status": st, "observed": ("redirect " + loc) if loc else "not shown", "ok": None}
            continue
        try:
            observed, ok = reader(html, txt(html))
        except Exception as exc:  # noqa: BLE001
            observed, ok = "reader error: %s" % exc, False
        row["tiers"][tier] = {"status": st, "observed": observed, "ok": bool(ok)}
    out.append(row)


def num_after(t, pattern):
    m = re.search(pattern, t, re.I)
    return int(m.group(1)) if m else None


R = M["requirements"]
r79, rdep, rnocov, rsurf, r500, rident = R[0], R[1], R[2], R[3], R[4], R[5]
rel_noscope, rel_inactive, rel_ident = M["releases"][0], M["releases"][1], M["releases"][2]

# --- requirements -------------------------------------------------------------
check("AUD-79: the requirement page counts 79 test cases", "/requirements/%d" % r79, 79,
      lambda h, t: (re.findall(r"\b(\d+)\s+(?:test cases?|claims?|checks?)\b", t, re.I)[:4], "79" in re.findall(r"\b(\d+)\s+(?:test cases?|claims?|checks?)\b", t, re.I)))
check("AUD-DEP: 5 deprecated, 0 live — the page must not read 5 live", "/requirements/%d" % rdep, "0 live / 5 deprecated",
      lambda h, t: ((re.findall(r"\b(\d+)\s+(?:test cases?|claims?)\b", t, re.I)[:3], re.findall(r"\b(\d+)\s+deprecated", t, re.I)[:2], bool(re.search(r"never.?tested|no test case|nothing to test", t, re.I))),
                    ("5" not in re.findall(r"\b(\d+)\s+(?:test cases?|claims?)\b", t, re.I)[:1]) and "deprecated" in t.lower()))
check("AUD-500: 500 drafts counted as 500", "/requirements/%d" % r500, 500,
      lambda h, t: (re.findall(r"\b(\d+)\s+(?:draft|pending|test cases?|claims?)", t, re.I)[:4], "500" in t))
check("AUD-NOCOV: no coverage rows -> readiness CANNOT_DETERMINE, never CURRENT", "/requirements/%d" % rnocov, "CANNOT DETERMINE",
      lambda h, t: ([s.strip() for s in re.findall(r"[^.]*(?:cannot determine|never run|current)[^.]*", t, re.I)[:3]], bool(re.search(r"cannot.?determine", t, re.I)) and not re.search(r"\bcurrent\b(?! sequence)", t, re.I)))
check("AUD-SURF: a surface declared then unlinked reads 0 linked surfaces", "/requirements/%d" % rsurf, "0 surfaces linked (one unlinked, with reason)",
      lambda h, t: ([s.strip() for s in re.findall(r"[^.]*(?:surface|unlinked)[^.]*", t, re.I)[:3]], bool(re.search(r"(?:no|0) (?:linked )?surfaces?|unlinked", t, re.I))))
check("AUD-IDENT: decorated identity (external_key AUD-IDENT, jira_key AUD-JK-1) — the page counts the 2 linked test cases", "/requirements/%d" % rident, 2,
      lambda h, t: ((re.findall(r"\b(\d+)\s+(?:test cases?|claims?|checks?)\b", t, re.I)[:4], "AUD-IDENT" in t, "AUD-JK-1" in t), "2" in re.findall(r"\b(\d+)\s+(?:test cases?|claims?|checks?)\b", t, re.I)[:2]))
check("requirements list: the badge counts the 500 drafts", "/requirements", ">= 500",
      lambda h, t: (re.search(r'data-nav-badge="requirements">(\d+)<', h).group(1) if re.search(r'data-nav-badge="requirements">(\d+)<', h) else None,
                    bool(re.search(r'data-nav-badge="requirements">(\d+)<', h)) and int(re.search(r'data-nav-badge="requirements">(\d+)<', h).group(1)) >= 500))
check("requirements list: AUD-79 row shows 79", "/requirements?q=AUD-79", 79,
      lambda h, t: (re.findall(r"AUD-79[^A-Z]{0,200}?(\d+)", t)[:3], "79" in re.findall(r"AUD-79[^A-Z]{0,200}?(\d+)", t)[:3]))
check("requirements list: AUD-IDENT row shows 2 (not 0 under the Jira key)", "/requirements?q=AUD-IDENT", 2,
      lambda h, t: (re.findall(r"AUD-IDENT[^A-Z]{0,200}?(\d+)", t)[:3], "2" in re.findall(r"AUD-IDENT[^A-Z]{0,200}?(\d+)", t)[:3]))

# --- releases -----------------------------------------------------------------
def board_state(rid):
    def _r(h, t):
        m = re.search(r'data-release="%d"[^>]*data-state="([^"]+)"' % rid, h)
        i = h.find('data-release="%d"' % rid)
        return (m.group(1) if m else "card not found", txt(h[i:i + 2500])[:160] if i >= 0 else ""), bool(m)
    return _r
check("board: no-scope release reads 'will refuse — no requirement'", "/releases", "refuses / no requirement is in scope",
      lambda h, t: (board_state(rel_noscope)(h, t)[0], board_state(rel_noscope)(h, t)[0][0] == "refuses" and "no requirement is in scope" in board_state(rel_noscope)(h, t)[0][1]))
check("board: inactive-only release reads 'will refuse — every declared target inactive'", "/releases", "refuses / inactive",
      lambda h, t: (board_state(rel_inactive)(h, t)[0], board_state(rel_inactive)(h, t)[0][0] == "refuses" and "inactive" in board_state(rel_inactive)(h, t)[0][1]))
check("board: decorated-identity release counts its 2 test cases", "/releases", "2 functional checks in scope (never run)",
      lambda h, t: (board_state(rel_ident)(h, t)[0], "0 of 2" in board_state(rel_ident)(h, t)[0][1] or "2 items" in board_state(rel_ident)(h, t)[0][1]))
check("no-scope release decision tab: card refuses, no recommendation", "/releases/%d?tab=decision" % rel_noscope, "data-recommendation none + refusal",
      lambda h, t: ((re.search(r'data-recommendation="([^"]*)"', h).group(1) if re.search(r'data-recommendation="([^"]*)"', h) else None, re.search(r'data-testid="quality-refusal">([^<]+)<', h).group(1).strip() if re.search(r'data-testid="quality-refusal">([^<]+)<', h) else None),
                    'data-recommendation="none"' in h and "no requirement" in h))
check("inactive-only release decision tab: card refuses naming the inactive target", "/releases/%d?tab=decision" % rel_inactive, "every declared target environment is inactive (AUD-inactive)",
      lambda h, t: (re.search(r'data-testid="quality-refusal">([^<]+)<', h).group(1).strip() if re.search(r'data-testid="quality-refusal">([^<]+)<', h) else None, "inactive (AUD-inactive)" in h))
check("inactive-only release: the planted CANNOT DETERMINE decision row is shown as recorded", "/releases/%d?tab=decision" % rel_inactive, "CANNOT DETERMINE recorded",
      lambda h, t: ([s.strip() for s in re.findall(r"[^.]*(?:recorded|Recorded)[^.]*", t)[:2]], bool(re.search(r"cannot.?determine", t, re.I))))
check("decorated-identity release decision tab: scope = 2 test cases, never run", "/releases/%d?tab=decision" % rel_ident, "2 items not current / 2 in scope",
      lambda h, t: ((re.search(r'data-non-current="(\d+)"', h).group(1) if re.search(r'data-non-current="(\d+)"', h) else None, re.findall(r"(\d+) (?:functional )?check\(s\) in scope", t)[:2]),
                    (re.search(r'data-non-current="(\d+)"', h) or [None, None])[1] == "2" if re.search(r'data-non-current="(\d+)"', h) else False))

# --- settings and lists --------------------------------------------------------
check("waivers page: the expired waiver is shown as expired, not active", "/settings/waivers", "expired listed; 0 active",
      lambda h, t: ([s.strip() for s in re.findall(r"[^.]*(?:expired|active)[^.]*", t, re.I)[:4]], "expired" in t.lower() and "audit: expired" in t))
check("quality policy page: the zero-rule draft is listed with 0 rules", "/settings/quality-policy", "AUD-no-rules · 0 rules",
      lambda h, t: ([s.strip() for s in re.findall(r"[^.]*AUD-no-rules[^.]*", t)[:2]], "AUD-no-rules" in t))
check("claim sets: the revoked set reads revoked", "/settings/claim-sets", "revoked",
      lambda h, t: ([s.strip() for s in re.findall(r"[^.]*revoked[^.]*", t, re.I)[:2]], "revoked" in t.lower()))
check("inventories: the superseded version (313) is shown superseded by 314", "/settings/inventories", "313 superseded / 314 current",
      lambda h, t: ([s.strip() for s in re.findall(r"[^.]*(?:%s|%s|supersed)[^.]*" % (ids["inventory_superseded"], ids["inventory_current"]), t, re.I)[:4]],
                    str(ids["inventory_current"]) in t and (str(ids["inventory_superseded"]) in t or "supersed" in t.lower())))
check("runs list: the unstamped runs read unstamped/CANNOT DETERMINE, not CURRENT", "/runs?environment_id=%d" % ids["env_id"], "unstamped",
      lambda h, t: ([s.strip() for s in re.findall(r"[^.]*(?:cannot determine|unstamped|no stamp|freshness)[^.]*", t, re.I)[:3]], bool(re.search(r"cannot.?determine|unstamped|freshness unknown", t, re.I))))
check("plan never executed: reads not executed", "/plans/%s" % ids["plan_never"], "not executed / Run this plan",
      lambda h, t: ([s.strip() for s in re.findall(r"[^.]*(?:executed|Run this plan|never)[^.]*", t)[:3]], bool(re.search(r"not (?:yet )?executed|never executed|Run this plan", t, re.I))))
check("plan executed twice: reads executed ONCE (the second refused)", "/plans/%s" % ids["plan_twice"], "executed once",
      lambda h, t: ([s.strip() for s in re.findall(r"[^.]*(?:executed|twice|again)[^.]*", t)[:3]], bool(re.search(r"executed", t, re.I)) and not re.search(r"twice|2 executions", t, re.I)))
check("repairs panel: the orphan-run proposal (SPECULATIVE, run does not exist) is shown honestly", "/runs/substrate", "SPECULATIVE; run unknown",
      lambda h, t: ([s.strip() for s in re.findall(r"[^.]*(?:SPECULATIVE|orphan|unknown run|no run)[^.]*", t)[:3]], "SPECULATIVE" in t))
check("run detail for the ORPHAN run id: not found, never a 200 shell", "/runs/%s" % ids["orphan_run_id"], "404",
      lambda h, t: (t[:120], False))   # a 200 here is the AUD-016 shape; a 404 records as 'not shown'
check("claim page for the no-coverage claim: no coverage stated", "/claims/%s" % ids["test_id"], "no coverage / CANNOT DETERMINE",
      lambda h, t: ([s.strip() for s in re.findall(r"[^.]*(?:coverage|cannot determine)[^.]*", t, re.I)[:3]], bool(re.search(r"no coverage|cannot.?determine|not (?:yet )?covered", t, re.I))))

json.dump(out, sys.stdout, indent=1)
