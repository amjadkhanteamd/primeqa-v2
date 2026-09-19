#!/usr/bin/env python3
"""RUN 2 PASS 3d — IDOR: a member (14) who can SEE another user's (15's) object
tries to ACT on it with a VALID body, through the real route; the row is read
before and after so the answer is the database's, not the status code's.
Also: the viewer (13) on the same acts (must be denied at the gate), and the
tenant-2 member (12) on tenant-1 ids (must be not-found).

Usage: DATABASE_URL=scratch ... python scripts/audit/idor.py ids.json manifest.json > idor.json
"""
from __future__ import annotations

import json
import os
import re
import sys
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sqlalchemy import text  # noqa: E402

from session import client_for, post  # noqa: E402

ids = json.load(open(sys.argv[1])); M = json.load(open(sys.argv[2]))
from primeqa.app import create_app  # noqa: E402
from primeqa.semantic.connection import get_tenant_connection  # noqa: E402
app = create_app(); app.config["TESTING"] = True
T = 1; OWNER = 15
out = []


def q(sql, params=None, tenant=True):
    if tenant:
        with get_tenant_connection(T) as conn:
            return conn.execute(text(sql), params or {}).first()
    import primeqa.db as dbm
    with dbm.engine.connect() as c:
        return c.execute(text(sql), params or {}).first()


def act(name, session, method, path, data, before_sql, before_params=None, *, api=False, expect):
    before = q(before_sql, before_params)
    c = client_for(app, session)
    if api:
        from session import csrf_of
        tok = csrf_of(c); c.set_cookie("csrf_token", tok, domain="localhost")
        r = c.open(path, method=method, json=data, headers={"X-CSRF-Token": tok})
    else:
        r = post(c, path, data)
    after = q(before_sql, before_params)
    body = r.get_data(as_text=True)
    flash = ""
    if r.status_code in (301, 302, 303) and r.headers.get("Location"):
        h = c.get(r.headers["Location"]).get_data(as_text=True)
        m = re.search(r'__primeqaFlash = \[\["(?:error|success)", "((?:[^"\\]|\\.)*)"\]\]', h)
        flash = (m.group(1) if m else "").encode().decode("unicode_escape")
    changed = tuple(before or ()) != tuple(after or ())
    verdict = "ACTED" if changed else "refused"
    out.append({"name": name, "session": session, "method": method, "path": path, "status": r.status_code,
                "answer": (flash or re.sub(r"\s+", " ", body)[:160]), "before": [str(x) for x in (before or ())],
                "after": [str(x) for x in (after or ())], "row_changed": changed, "expected": expect,
                "finding": (verdict == "ACTED") != (expect == "acts")})
    return changed


rel = ids["release_id"]; dec = ids["decision_id"]; req = ids["req_id"]
# --- fixtures owned by 15 that a member must not undo ------------------------------
with get_tenant_connection(T) as conn:
    # leftovers of an earlier, interrupted run
    conn.execute(text("SET LOCAL session_replication_role = replica"))
    conn.execute(text("DELETE FROM s3_generation_jobs WHERE requirement_key = 'AUD-79' AND created_by = :u"), {"u": OWNER})
    conn.execute(text("DELETE FROM requirement_surface_links WHERE requirement_key = 'AUD-SURF' AND active"))
with get_tenant_connection(T) as conn:
    # a schedule-free, fresh plan by 15 exists (plan_never); a generation job by 15:
    seq = conn.execute(text("SELECT MAX(version_seq) FROM logical_versions")).scalar()
    job = conn.execute(text("INSERT INTO s3_generation_jobs (requirement_key, s1_version_seq, status, created_by, created_at, updated_at, environment_id) "
                            "VALUES ('AUD-79', :s, 'queued', :u, now(), now(), :e) RETURNING id"), {"s": seq, "u": OWNER, "e": ids["env_id"]}).scalar()
    # a surface link declared by 15 (the planted one was unlinked): declare again through the console
from primeqa.intelligence.requirement_surface_console import declare_surface
with get_tenant_connection(T) as conn:
    surf = conn.execute(text("SELECT surface_key FROM ui_surface_inventory_members WHERE inventory_version = (SELECT MAX(inventory_version) FROM claim_sets WHERE status='approved') LIMIT 1")).scalar()
d = declare_surface(T, requirement_key="AUD-SURF", surface_key=surf, user_id=OWNER)
link = (d.get("link") or {}).get("link_id") or d.get("link_id")
rsurf = M["requirements"][3]

# --- the acts, as MEMBER 14 ----------------------------------------------------------
act("mint a status token on 15's release (AUD-012)", "member_t1", "POST", f"/api/releases/{rel}/status-token", {},
    "SELECT status_poll_token_hash IS NOT NULL FROM public.releases WHERE id = :r", {"r": rel}, api=True, expect="refused")
act("revoke the status token on 15's release (AUD-012)", "member_t1", "DELETE", f"/api/releases/{rel}/status-token", {},
    "SELECT status_poll_token_hash IS NOT NULL FROM public.releases WHERE id = :r", {"r": rel}, api=True, expect="refused")
act("finalize 15's release decision (recommendation CANNOT DETERMINE) as NO GO — not more permissive", "member_t1", "POST", f"/releases/{rel}/decisions/{dec}/final",
    {"final_decision": "no_go", "reason": "member says no"}, "SELECT final_decision, decided_by FROM public.release_decisions WHERE id = :d", {"d": dec}, expect="acts?")
act("cancel 15's generation job", "member_t1", "POST", f"/api/s3-generation-jobs/{job}/cancel", {},
    "SELECT status FROM s3_generation_jobs WHERE id = :j", {"j": job}, api=True, expect="refused")
if link:
    act("unlink a surface 15 declared", "member_t1", "POST", f"/requirements/{rsurf}/surfaces/{link}/unlink", {"reason": "member unlinks"},
        "SELECT active FROM requirement_surface_links WHERE CAST(id AS text) = :l", {"l": str(link)}, expect="refused?")
act("run 15's plan (never executed)", "member_t1", "POST", f"/plans/{ids['plan_never']}/run", {},
    "SELECT executed_at IS NOT NULL, executed_by FROM run_plans WHERE CAST(id AS text) = :p", {"p": ids["plan_never"]}, expect="acts?")
act("edit 15's requirement summary", "member_t1", "POST", f"/requirements/{req}/edit", {"jira_summary": "edited by member 14", "acceptance_criteria": ""},
    "SELECT jira_summary FROM public.requirements WHERE id = :q", {"q": req}, expect="acts")
act("PATCH 15's release via the API", "member_t1", "PATCH", f"/api/releases/{rel}", {"name": "renamed by member 14"},
    "SELECT name FROM public.releases WHERE id = :r", {"r": rel}, api=True, expect="acts")
act("remove a requirement from 15's release via the API", "member_t1", "DELETE", f"/api/releases/{rel}/requirements/{req}", {},
    "SELECT count(*) FROM public.release_requirements WHERE release_id = :r AND requirement_id = :q", {"r": rel, "q": req}, api=True, expect="acts")
act("approve 15's claim set (the revoked one)", "member_t1", "POST", f"/claim-sets/{ids['claim_set_id']}/approve", {},
    "SELECT status FROM claim_sets WHERE CAST(id AS text) = :c", {"c": ids["claim_set_id"]}, expect="refused")
# --- the VIEWER on the same acts: denied at the gate ------------------------------------
act("viewer: finalize a decision", "viewer_t1", "POST", f"/releases/{rel}/decisions/{dec}/final", {"final_decision": "go", "reason": "x"},
    "SELECT final_decision FROM public.release_decisions WHERE id = :d", {"d": dec}, expect="refused")
act("viewer: mint a status token", "viewer_t1", "POST", f"/api/releases/{rel}/status-token", {},
    "SELECT status_poll_token_hash IS NOT NULL FROM public.releases WHERE id = :r", {"r": rel}, api=True, expect="refused")
act("viewer: edit a requirement", "viewer_t1", "POST", f"/requirements/{req}/edit", {"jira_summary": "viewer edit"},
    "SELECT jira_summary FROM public.requirements WHERE id = :q", {"q": req}, expect="refused")
# --- tenant-2 member on tenant-1 ids: not found ---------------------------------------
act("tenant-2 member: finalize tenant-1's decision", "member_t2", "POST", f"/releases/{rel}/decisions/{dec}/final", {"final_decision": "go", "reason": "x"},
    "SELECT final_decision FROM public.release_decisions WHERE id = :d", {"d": dec}, expect="refused")
act("tenant-2 member: mint a token on tenant-1's release", "member_t2", "POST", f"/api/releases/{rel}/status-token", {},
    "SELECT status_poll_token_hash IS NOT NULL FROM public.releases WHERE id = :r", {"r": rel}, api=True, expect="refused")
act("tenant-2 member: edit tenant-1's requirement", "member_t2", "POST", f"/requirements/{req}/edit", {"jira_summary": "t2 edit"},
    "SELECT jira_summary FROM public.requirements WHERE id = :q", {"q": req}, expect="refused")
# cleanup of the fixtures this script added
with get_tenant_connection(T) as conn:
    conn.execute(text("SET LOCAL session_replication_role = replica"))
    conn.execute(text("DELETE FROM s3_generation_jobs WHERE id = :j"), {"j": job})
    if link:
        conn.execute(text("DELETE FROM requirement_surface_links WHERE CAST(id AS text) = :l"), {"l": str(link)})
json.dump(out, sys.stdout, indent=1)
