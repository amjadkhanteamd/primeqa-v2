#!/usr/bin/env python3
"""PASS 1d + 3b — POST every write route with hostile input, per session.

For each POST/PATCH/DELETE rule and each session, five bodies: empty, maximal,
wrong-type, missing-CSRF, missing-required (= the empty form WITH a valid CSRF
token, which is what a browser sends when a user submits nothing). A 500 or a
traceback in the body is a finding; so is a viewer reaching an act route.

Usage: DATABASE_URL=scratch ... AUDIT_SESSIONS=member_t1,viewer_t1 \
       python scripts/audit/post_all.py ids.json > posts.json
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from session import client_for, csrf_of  # noqa: E402

ERROR_PAGE = "Something went wrong"
BIG = "A" * 20000
BODIES = {
    "empty": {},
    "maximal": {"name": BIG, "title": BIG, "reason": BIG, "note": BIG, "description": BIG,
                "email": BIG + "@x", "jira_key": BIG, "external_key": BIG, "q": BIG,
                "reviewer": BIG, "expires_at": "9999-12-31", "environment_id": "9" * 30,
                "release_id": "9" * 30, "version": "9" * 30, "page": "9" * 30, "per_page": "9" * 30},
    "wrong_type": {"environment_id": "not-an-int", "release_id": "x", "requirement_id": "x",
                   "expires_at": "not-a-date", "per_page": "x", "page": "x", "version": "x",
                   "test_id": "not-a-uuid", "plan_id": "not-a-uuid", "active": "maybe",
                   "email": "not-an-email", "role": "emperor", "cron": "garbage",
                   "reviewer": 123, "reason": 123},
}
SKIP = ("/api/auth/login", "/api/auth/refresh", "/api/webhooks/", "/login", "/logout", "/api/auth/logout")


def _fill(rule, ids):
    out = rule
    for _, name in re.findall(r"<(?:([a-z]+):)?([a-z_]+)>", rule):
        if name not in ids:
            return None
        out = re.sub(r"<(?:[a-z]+:)?%s>" % name, str(ids[name]), out)
    return out


def main():
    ids = json.load(open(sys.argv[1]))
    sessions = (os.environ.get("AUDIT_SESSIONS") or "member_t1,viewer_t1").split(",")
    from primeqa.app import create_app
    app = create_app()
    app.config["TESTING"] = True
    rules = [r for r in app.url_map.iter_rules()
             if r.endpoint != "static" and ({"POST", "PATCH", "DELETE", "PUT"} & r.methods)]
    out = []
    for label in sessions:
        for r in sorted(rules, key=lambda x: x.rule):
            if r.rule.startswith(SKIP):
                continue
            path = _fill(r.rule, ids)
            if path is None:
                out.append({"session": label, "rule": r.rule, "skipped": "no id"})
                continue
            for method in sorted({"POST", "PATCH", "DELETE", "PUT"} & r.methods):
                for variant in ("empty", "maximal", "wrong_type", "missing_csrf", "missing_required"):
                    c = client_for(app, label)
                    body = dict(BODIES.get(variant, {}))
                    is_api = r.rule.startswith("/api/")
                    kw = {}
                    if variant != "missing_csrf":
                        tok = csrf_of(c)
                        c.set_cookie("csrf_token", tok, domain="localhost")
                        if is_api:
                            kw["headers"] = {"X-CSRF-Token": tok}
                        else:
                            body["csrf_token"] = tok
                    try:
                        if is_api:
                            resp = c.open(path, method=method, json=body if body else {}, **kw)
                        else:
                            resp = c.open(path, method=method, data=body, **kw)
                    except Exception as exc:                        # noqa: BLE001
                        out.append({"session": label, "rule": r.rule, "method": method, "variant": variant,
                                    "status": "EXC", "exception": "%s: %s" % (type(exc).__name__, str(exc)[:160])})
                        continue
                    text = resp.get_data(as_text=True)
                    out.append({"session": label, "rule": r.rule, "method": method, "variant": variant,
                                "status": resp.status_code,
                                "location": resp.headers.get("Location", ""),
                                "error_page": ERROR_PAGE in text or "Traceback" in text,
                                "body_head": re.sub(r"\s+", " ", text)[:160]})
    json.dump(out, sys.stdout, indent=1)


if __name__ == "__main__":
    main()
