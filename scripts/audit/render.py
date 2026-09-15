#!/usr/bin/env python3
"""PASS 1c — render every GET route for real, per tier, and assert the page is
a page: status as expected for the tier, a <main> with real text, the nav hooks,
and never the 500 page. A 200 with an empty body is a finding (the /plans/<id>
lesson: the link was proven, the page was not).

Usage: DATABASE_URL=scratch JWT_SECRET=... CREDENTIAL_ENCRYPTION_KEY=... \
       python scripts/audit/render.py ids.json > render.json
ids.json maps parameter names to a value that exists on the target database.
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from session import client_for  # noqa: E402

SESSIONS = tuple((os.environ.get("AUDIT_SESSIONS") or "viewer_t1,member_t1,admin_t1,member_t2").split(","))
ERROR_PAGE = "Something went wrong"
MAIN_RX = re.compile(r"<main[^>]*>(.*?)</main>", re.S)
TAG_RX = re.compile(r"<[^>]+>")
SCRIPT_RX = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S)


def _fill(rule: str, ids: dict):
    out = rule
    for conv, name in re.findall(r"<(?:([a-z]+):)?([a-z_]+)>", rule):
        key = name
        if key not in ids:
            return None
        out = re.sub(r"<(?:[a-z]+:)?%s>" % name, str(ids[key]), out)
    return out


def main():
    ids = json.load(open(sys.argv[1]))
    from primeqa.app import create_app
    app = create_app()
    app.config["TESTING"] = True
    rules = [r for r in app.url_map.iter_rules()
             if r.endpoint != "static" and "GET" in r.methods]
    results = []
    for label in SESSIONS:
        for r in sorted(rules, key=lambda x: x.rule):
            # A fresh client per request: /logout clears the cookie and would
            # otherwise poison every later render into the login page.
            c = client_for(app, label)
            path = _fill(r.rule, ids)
            if path is None:
                results.append({"session": label, "rule": r.rule, "skipped": "no id for a parameter"})
                continue
            try:
                resp = c.get(path, follow_redirects=True)
            except Exception as exc:                          # noqa: BLE001 — the point is to catch it
                results.append({"session": label, "rule": r.rule, "path": path,
                                "status": "EXC", "exception": "%s: %s" % (type(exc).__name__, str(exc)[:200])})
                continue
            body = resp.get_data(as_text=True)
            final = resp.request.path if resp.request else path
            if os.environ.get("AUDIT_SAVE_DIR"):
                _fn = os.path.join(os.environ["AUDIT_SAVE_DIR"], "%s__%s.html" % (label, re.sub(r"[^A-Za-z0-9]+", "_", path)[:120]))
                open(_fn, "w").write(body)
            is_json = resp.mimetype == "application/json"
            m = MAIN_RX.search(body)
            main_text = TAG_RX.sub(" ", SCRIPT_RX.sub("", m.group(1))) if m else ""
            main_text = re.sub(r"\s+", " ", main_text).strip()
            results.append({
                "session": label, "rule": r.rule, "path": path, "status": resp.status_code,
                "final_path": final, "redirected": final != path,
                "json": is_json, "bytes": len(body),
                "has_main": bool(m), "main_chars": len(main_text),
                "has_nav": 'data-nav-item="' in body,
                "error_page": ERROR_PAGE in body,
                "title": (re.search(r"<title>(.*?)</title>", body, re.S) or [None, ""])[1].strip()[:60],
                "main_head": main_text[:120],
            })
    json.dump(results, sys.stdout, indent=1)


if __name__ == "__main__":
    main()
