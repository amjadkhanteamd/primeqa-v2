#!/usr/bin/env python3
"""PASS 3a/3d — a member of tenant 2 asks for every id-taking route with tenant-1
ids. Any 200 whose body carries a tenant-1 marker, and any 2xx on an act, is a
cross-tenant exposure. Markers are the hostile world's own strings."""
import json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from session import client_for, csrf_of
ids = json.load(open(sys.argv[1]))
MARKERS = ("AUD-", "one requirement with 79 claims", "AUD-release", "AUD-no-rules", "audit: expired", "AUD-group", "AUD-jira", "CIW-sbx", "gate sandbox", "Account.Industry")
from primeqa.app import create_app
app = create_app(); app.config["TESTING"] = True
out = []
rules = [r for r in app.url_map.iter_rules() if r.endpoint != "static" and re.search(r"<[^>]+>", r.rule)]
for r in sorted(rules, key=lambda x: x.rule):
    path = r.rule
    ok = True
    for _, name in re.findall(r"<(?:([a-z]+):)?([a-z_]+)>", r.rule):
        if name not in ids: ok = False; break
        path = re.sub(r"<(?:[a-z]+:)?%s>" % name, str(ids[name]), path)
    if not ok: continue
    for method in sorted(m for m in r.methods if m in ("GET", "POST", "PATCH", "DELETE")):
        c = client_for(app, "member_t2"); tok = csrf_of(c)
        kw = {"headers": {"X-CSRF-Token": tok}} if path.startswith("/api/") else {}
        data = None if method == "GET" else ({"csrf_token": tok, "reason": "x", "final_decision": "go", "environment_id": ids.get("env_id"), "name": "x"} if not path.startswith("/api/") else {"reason": "x"})
        resp = c.open(path, method=method, data=data if not path.startswith("/api/") else None, json=data if path.startswith("/api/") else None, follow_redirects=False, **kw)
        body = resp.get_data(as_text=True)
        leaked = [m for m in MARKERS if m in body]
        out.append({"rule": r.rule, "method": method, "status": resp.status_code, "location": resp.headers.get("Location", ""), "leak": leaked})
json.dump(out, sys.stdout, indent=1)
