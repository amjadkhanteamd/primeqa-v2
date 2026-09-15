#!/usr/bin/env python3
"""AUD-013 verification — re-run pass-4 attack #8 through EVERY path and assert
the refusal names the plan; then prove the planner's own path still executes.

Scratch only (it writes). Usage: DATABASE_URL=scratch JWT_SECRET=... \
  CREDENTIAL_ENCRYPTION_KEY=... WEBHOOK_SECRET=audit-hook FLASK_ENV=production \
  python scripts/audit/attack8.py <planted_executable_claim_id> <ui_only_claim_id>
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import jwt  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

_USERS = {"member_t1": (14, 1, "tester"), "admin_t1": (15, 1, "admin")}   # planted on scratch by the audit


def client_for(app, label):
    uid, tid, role = _USERS[label]
    now = datetime.now(timezone.utc)
    tok = jwt.encode({"sub": str(uid), "tenant_id": tid, "role": role, "email": label + "@audit",
                      "full_name": "audit " + role, "iat": now, "exp": now + timedelta(minutes=30)},
                     os.environ["JWT_SECRET"], algorithm="HS256")
    c = app.test_client(); c.set_cookie("access_token", tok, domain="localhost"); return c


def csrf_of(client):
    """GET a page so the double-submit cookie lands in the jar; return its value
    (the jar now carries the cookie, so the POST needs only the field/header)."""
    r = client.get("/requirements", follow_redirects=True)
    for h in r.headers.getlist("Set-Cookie"):
        m = re.match(r"csrf_token=([^;]+)", h)
        if m:
            return m.group(1)
    m = re.search(r'name="csrf_token" value="([^"]+)"', r.get_data(as_text=True))
    return m.group(1) if m else ""

EXEC, UIONLY = sys.argv[1], sys.argv[2]
NAMES_PLAN = re.compile(r"no plan.*recorded plan.*D-486", re.S)
out = {}


def _text(r):
    return " ".join(r.get_data(as_text=True).split())


def main():
    from primeqa.app import create_app
    app = create_app(); app.config["TESTING"] = True

    def admin():
        c = client_for(app, "admin_t1"); tok = csrf_of(c); return c, tok

    def member():
        c = client_for(app, "member_t1"); tok = csrf_of(c); return c, tok

    # a. the jobs API, admin, executable claim — the attack that succeeded
    c, tok = admin()
    r = c.post("/api/s4-execution-jobs", json={"test_id": EXEC, "environment_id": 4}, headers={"X-CSRF-Token": tok})
    out["a_api_admin"] = (r.status_code, r.get_json())
    # b. run-async
    r = c.post("/claims/%s/run-async" % EXEC, data={"environment_id": "4", "csrf_token": tok})
    out["b_run_async"] = (r.status_code, _text(r)[:200])
    # c. the sync run (flash + redirect, followed)
    r = c.post("/claims/%s/run" % EXEC, data={"environment_id": "4", "csrf_token": tok}, follow_redirects=True)
    out["c_sync_run"] = (r.status_code, bool(NAMES_PLAN.search(_text(r))))
    # d. member WITHOUT env access — the first refusal the audit saw; must now name the plan
    c2, tok2 = member()
    r = c2.post("/api/s4-execution-jobs", json={"test_id": EXEC, "environment_id": 4}, headers={"X-CSRF-Token": tok2})
    out["d_api_member_no_env_access"] = (r.status_code, r.get_json())
    # e. the ui-only claim — the second refusal the audit saw; must now name the plan
    c, tok = admin()
    r = c.post("/api/s4-execution-jobs", json={"test_id": UIONLY, "environment_id": 4}, headers={"X-CSRF-Token": tok})
    out["e_api_ui_only_claim"] = (r.status_code, r.get_json())
    # f. the CI webhook, correctly signed
    body = json.dumps({"release_id": 58, "environment_id": 4}).encode()
    sig = hmac.new(os.environ["WEBHOOK_SECRET"].encode(), body, hashlib.sha256).hexdigest()
    r = app.test_client().post("/api/webhooks/ci-trigger", data=body,
                               headers={"Content-Type": "application/json", "X-PrimeQA-Signature": sig})
    out["f_webhook"] = (r.status_code, r.get_json())
    # i. the release Run button and the requirement run-substrate form
    r = c.post("/releases/58/run", data={"environment_id": "4", "csrf_token": tok}, follow_redirects=True)
    out["i_release_run"] = (r.status_code, bool(NAMES_PLAN.search(_text(r))), r.request.path + ("?" + r.request.query_string.decode() if r.request.query_string else ""))
    # g. the planner's own path still executes, with the plan on every job
    from primeqa.intelligence.run_plan_console import create_plan, run_plan
    plan = create_plan(1, scope={"scope_kind": "tenant", "environment_id": 4}, user_id=15, user_role="admin")
    out["g_create_plan"] = {k: plan.get(k) for k in ("ok", "plan_id", "reason", "sentence")}
    if plan.get("ok"):
        rec = run_plan(1, plan_id=plan["plan_id"], user_id=15, user_role="admin")
        receipt = rec.get("receipt") or {}
        out["g_run_plan"] = {"ok": rec.get("ok"), "s4_jobs": len(receipt.get("s4_jobs", [])),
                             "refusals": [x.get("error", "")[:60] for x in receipt.get("refusals", [])][:3],
                             "plan_required_in_refusals": any("no plan" in json.dumps(x) for x in receipt.get("refusals", []))}
        from sqlalchemy import text
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(1) as conn:
            rows = conn.execute(text("SELECT id, CAST(plan_id AS text) FROM s4_execution_jobs "
                                     "WHERE CAST(plan_id AS text) = :p"), {"p": plan["plan_id"]}).all()
        out["g_jobs_under_plan"] = len(rows)
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
