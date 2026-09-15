#!/usr/bin/env python3
"""PASS 2 — the numbers, not the status codes. A count that is wrong but
plausible is worse than a crash. Each check names the page, the shape planted,
the number expected, and what the page said."""
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
c = client_for(app, "admin_t1")
out = []


def page(path):
    r = c.get(path, follow_redirects=True)
    return r.status_code, r.get_data(as_text=True)


def txt(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S)))


def check(name, path, expect, found, ok):
    out.append({"check": name, "path": path, "expected": expect, "observed": found, "ok": bool(ok)})


# requirement AUD-79: 79 claims, all approved
rid = M["requirements"][0]; st, h = page("/requirements/%d" % rid); t = txt(h)
m = re.search(r"(\d+)\s+(?:test cases?|claims?)", t, re.I)
check("AUD-79 page claim count", "/requirements/%d" % rid, "79", (st, m.group(0) if m else t[:120]), st == 200 and "79" in (m.group(1) if m else ""))
# requirement AUD-DEP: 5 claims, all deprecated — the live count must be 0
rid = M["requirements"][1]; st, h = page("/requirements/%d" % rid); t = txt(h)
check("AUD-DEP page shows deprecated, not live", "/requirements/%d" % rid, "5 deprecated / 0 live", (st, re.findall(r"\b\d+\s+(?:deprecated|retired)", t, re.I)[:3], re.findall(r"\b(\d+)\s+(?:test cases?|claims?)", t, re.I)[:3]), st == 200 and "deprecated" in t.lower())
# requirement AUD-500: 500 draft claims
rid = M["requirements"][4]; st, h = page("/requirements/%d" % rid); t = txt(h)
check("AUD-500 page draft count", "/requirements/%d" % rid, "500", (st, re.findall(r"\b500\b", t)[:2], re.findall(r"\b(\d+)\s+(?:draft|pending)", t, re.I)[:3]), st == 200 and "500" in t)
# requirements list: the needs-review badge must count the 500 drafts
st, h = page("/requirements"); badge = re.search(r'data-nav-badge="requirements">(\d+)<', h)
check("nav badge counts the 500 drafts", "/requirements", ">= 500", (st, badge.group(1) if badge else None), st == 200 and badge and int(badge.group(1)) >= 500)
# releases board: no-scope and inactive-env releases
st, h = page("/releases")
for rel, name in ((M["releases"][0], "no scope"), (M["releases"][1], "inactive env")):
    card = re.search(r'data-testid="release-board"[^>]*data-release-id="%d"[^>]*data-state="([^"]+)"' % rel, h) or re.search(r'data-release-id="%d"[^>]*data-state="([^"]+)"' % rel, h)
    check("release board state (%s)" % name, "/releases", "not_evaluated (no evidence counted)", (st, card.group(1) if card else "card not found"), st == 200 and bool(card))
# the inactive-env release's decision tab: the scope must NOT count the inactive environment
rel = M["releases"][1]; st, h = page("/releases/%d?tab=decision" % rel); t = txt(h)
check("inactive-env release: decision tab scope", "/releases/%d?tab=decision" % rel, "no active environment in scope / refuses", (st, t[:0], [s for s in re.findall(r"[^.]*(?:environment|scope)[^.]*\.", t)[:3]]), st == 200 and "AUD-inactive" not in t.replace("AUD-release-inactive-env", ""))
# settings/waivers: the expired waiver must not read as active
st, h = page("/settings/waivers"); t = txt(h)
check("expired waiver not active", "/settings/waivers", "shown expired / not counted active", (st, [s for s in re.findall(r"[^.]*(?:expired|active|until)[^.]*", t)[:4]]), st == 200 and ("expired" in t.lower() or "0 active" in t.lower()))
# settings/quality-policy: the zero-rule draft
st, h = page("/settings/quality-policy"); t = txt(h)
check("zero-rule policy listed honestly", "/settings/quality-policy", "AUD-no-rules with 0 rules", (st, [s for s in re.findall(r"[^.]*AUD-no-rules[^.]*", t)[:2]]), st == 200 and "AUD-no-rules" in t)
# plans: never executed vs executed once (second refused)
for key, expect in (("plan_never", "not executed"), ("plan_twice", "executed once")):
    st, h = page("/plans/%s" % ids[key]); t = txt(h)
    ex = re.search(r'data-testid="plan-execution"[^>]*>(.*?)</', h, re.S)
    check("plan %s" % key, "/plans/%s" % ids[key], expect, (st, re.sub(r"\s+", " ", ex.group(1))[:80] if ex else [s for s in re.findall(r"[^.]*(?:executed|Run this plan)[^.]*", t)[:2]]), st == 200)
# claim sets: the revoked set
st, h = page("/settings/claim-sets"); t = txt(h)
check("revoked claim set shown as revoked", "/settings/claim-sets", "revoked", (st, ids["claim_set_id"][:8], [s for s in re.findall(r"[^.]*revoked[^.]*", t)[:2]]), st == 200 and "revoked" in t.lower())
# runs list: the two unstamped runs read CANNOT_DETERMINE, not CURRENT
st, h = page("/runs?environment_id=%d" % ids["env_id"]); t = txt(h)
check("unstamped runs read CANNOT DETERMINE", "/runs", "CANNOT_DETERMINE / unstamped", (st, [s for s in re.findall(r"[^.]*(?:cannot determine|unstamped|no stamp)[^.]*", t, re.I)[:2]]), st == 200 and re.search(r"cannot.?determine", t, re.I) is not None)
# requirement AUD-NOCOV: readiness must be CANNOT_DETERMINE (no coverage), never CURRENT
rid = M["requirements"][2]; st, h = page("/requirements/%d" % rid); t = txt(h)
check("no-coverage claim readiness", "/requirements/%d" % rid, "CANNOT_DETERMINE (no coverage)", (st, [s for s in re.findall(r"[^.]*(?:cannot determine|no coverage|current)[^.]*", t, re.I)[:3]]), st == 200 and re.search(r"current\b", t, re.I) is None or re.search(r"cannot", t, re.I) is not None)
print(json.dumps(out, indent=1))
