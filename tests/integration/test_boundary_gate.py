"""The BOUNDARY GATE (triage round 2, batch 2 — AUD-011 / AUD-021 / AUD-035):
a hostile body never reaches the database.

The class the audit named: "the database is the only validator" — a 20,000-
character name, a requirement_id of "x", an empty form, a purge of a section
that still holds requirements; each an unhandled DataError / IntegrityError
and the themed 500 page. The rule now: a create/update service validates
type, emptiness and length against the COLUMN before any row is built
(``primeqa/shared/validation.py``, the ``create_section`` shape lifted), and
a route answers 4xx naming the field.

THE GUARD, generated — never listed — from the live url_map: every write rule
(POST / PATCH / PUT) whose view reads a body (``request.form`` /
``get_json`` / ``request.json`` / ``request.values`` / ``request.files``)
receives three bodies as the minimum tier the route-authority table declares
for it: EMPTY, MAXIMAL (every field any route names, 20,000 characters; every
id 10**30) and WRONG-TYPE (numbers where strings go, strings where ids and
dates and booleans go, a NUL byte inside a string). Every answer must be
below 500 with no error page; every /api/* answer to MAXIMAL and WRONG-TYPE
must be a 4xx. A route added tomorrow is swept the day it appears; a field
it names joins the bodies. The world the ids point at is planted here and
removed here (residue proven 0).

Runs against ``S3A3_TEST_DATABASE_URL`` with ``REPORT_PAGES=1`` (scratch).
"""
from __future__ import annotations

import inspect
import os
import re
import uuid

import pytest
from sqlalchemy import text

DB = os.environ.get("S3A3_TEST_DATABASE_URL")
_ENABLED = os.environ.get("REPORT_PAGES") == "1" and bool(DB)
pytestmark = [pytest.mark.skipif(not _ENABLED, reason="set REPORT_PAGES=1 + S3A3_TEST_DATABASE_URL")]
if _ENABLED:
    os.environ["DATABASE_URL"] = DB

T = 1
ADMIN, SUPER = 15, 1                       # the scratch audit admin; the scratch superadmin
BIG = "GATE-BOUNDARY-" + "A" * 20000       # findable residue, if any ever lands
HUGE = 10 ** 30
ERROR_PAGE = ("Something went wrong", "Traceback (most recent call last)")
# auth and signature routes are not body boundaries (login / refresh / the HMAC webhook)
SKIP = ("/api/auth/login", "/api/auth/refresh", "/api/auth/logout", "/login", "/logout", "/api/webhooks/")

BODY_READ = re.compile(r"request\.(form|get_json|json|values|files)\b")
FIELD = re.compile(r"""(?:request\.form|request\.values|request\.json|data|payload|body)(?:\.get\(|\[)\s*['"]([a-zA-Z_][a-zA-Z0-9_]*)['"]""")


# --- the world -------------------------------------------------------------------------

def _sweep_markers():
    """Round 3, part C: remove anything an EARLIER aborted run of this module
    left behind, before planting. A fixture whose setup raises never reaches its
    remover, and the leftovers then collide with the next run's plant
    (uq_requirements_tenant_external_key) — a red that re-creates itself and
    made the whole tree unrunnable in one process. The markers are this
    module's own; nothing else writes them."""
    from sqlalchemy import create_engine
    auto = create_engine(DB, isolation_level="AUTOCOMMIT")
    with auto.connect() as c:
        c.execute(text("SET session_replication_role = replica"))
        c.execute(text("SET search_path TO tenant_1, public"))
        for sql in (
            "DELETE FROM quality_waivers WHERE reason = 'gate'",
            "DELETE FROM requirement_surface_link_claims WHERE link_id IN (SELECT id FROM requirement_surface_links WHERE requirement_key = 'GATE-BOUNDARY')",
            "DELETE FROM requirement_surface_links WHERE requirement_key = 'GATE-BOUNDARY'",
            "DELETE FROM test_requirement_links WHERE external_key = 'GATE-BOUNDARY'",
            "DELETE FROM requirement_identities WHERE external_key = 'GATE-BOUNDARY'",
            "WITH g AS (DELETE FROM repair_proposals WHERE proposal_kind = 'gate' RETURNING run_id) "
            "DELETE FROM s4_execution_runs WHERE run_id IN (SELECT run_id FROM g)",
            "DELETE FROM quality_policies WHERE name = 'gate-boundary'",
            "DELETE FROM public.shared_dashboard_links WHERE environment_id IN (SELECT id FROM public.environments WHERE name LIKE 'gate-boundary%')",
            "DELETE FROM public.release_decisions WHERE release_id IN (SELECT id FROM public.releases WHERE name LIKE 'gate-boundary%' OR name LIKE 'GATE-BOUNDARY%')",
            "DELETE FROM public.release_requirements WHERE release_id IN (SELECT id FROM public.releases WHERE name LIKE 'gate-boundary%' OR name LIKE 'GATE-BOUNDARY%')",
            "DELETE FROM release_targets WHERE release_id IN (SELECT id FROM public.releases WHERE name LIKE 'gate-boundary%' OR name LIKE 'GATE-BOUNDARY%')",
            "DELETE FROM public.releases WHERE name LIKE 'gate-boundary%' OR name LIKE 'GATE-BOUNDARY%'",
            "DELETE FROM public.requirements WHERE external_key = 'GATE-BOUNDARY'",
            "DELETE FROM public.requirements WHERE section_id IN (SELECT id FROM public.sections WHERE name = 'gate-boundary')",
            "DELETE FROM public.sections WHERE name = 'gate-boundary'",
            "DELETE FROM s4_run_schedules WHERE environment_id IN (SELECT id FROM public.environments WHERE name LIKE 'gate-boundary%')",
            "DELETE FROM public.environment_credentials WHERE environment_id IN (SELECT id FROM public.environments WHERE name LIKE 'gate-boundary%')",
            "DELETE FROM public.environments WHERE name LIKE 'gate-boundary%'",
            "DELETE FROM public.group_environments WHERE group_id IN (SELECT id FROM public.groups WHERE name LIKE 'gate-boundary%')",
            "DELETE FROM public.group_members WHERE group_id IN (SELECT id FROM public.groups WHERE name LIKE 'gate-boundary%')",
            "DELETE FROM public.groups WHERE name LIKE 'gate-boundary%'",
            "DELETE FROM public.connections WHERE name LIKE 'gate-boundary%'",
            "DELETE FROM public.milestones WHERE name LIKE 'GATE-BOUNDARY%'",
            "DELETE FROM public.refresh_tokens WHERE user_id IN (SELECT id FROM public.users WHERE email = 'gate-boundary@audit')",
            "DELETE FROM public.activity_log WHERE user_id IN (SELECT id FROM public.users WHERE email = 'gate-boundary@audit')",
            "DELETE FROM public.users WHERE email = 'gate-boundary@audit'",
        ):
            try:
                c.execute(text(sql))
            except Exception as exc:  # noqa: BLE001 — named, never silent
                print("pre-plant sweep: %s -> %s" % (sql[:60], type(exc).__name__))


@pytest.fixture(scope="module")
def world():
    """Disposable rows in tenant 1, all owned by the audit admin, so every id a
    body-reading route takes resolves to something the hostile body can try to
    damage — and the remover takes them back (residue 0)."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from primeqa.semantic.connection import get_tenant_connection
    from primeqa.test_representation import SemanticTransactionCoordinator
    from tests.integration.test_representation._fixtures import empty_conditions, make_value_claim

    key = "GATE-BOUNDARY"
    _sweep_markers()                       # round 3: start from a clean slate
    eng = create_engine(DB)
    ids = {"tenant_id": T, "user_id": None}
    with eng.begin() as c:
        ids["section_id"] = c.execute(text("INSERT INTO sections (tenant_id, name, created_by) VALUES (1, 'gate-boundary', :u) RETURNING id"), {"u": ADMIN}).scalar()
        ids["req_id"] = c.execute(text("INSERT INTO requirements (tenant_id, section_id, source, created_by, jira_summary, external_key) "
                                       "VALUES (1, :s, 'manual', :u, 'gate boundary', :k) RETURNING id"), {"s": ids["section_id"], "u": ADMIN, "k": key}).scalar()
        ids["env_id"] = c.execute(text("INSERT INTO environments (tenant_id, name, env_type, sf_instance_url, sf_api_version, is_active, created_by) "
                                       "VALUES (1, 'gate-boundary-env', 'sandbox', 'https://gate.my.salesforce.com', '59.0', true, :u) RETURNING id"), {"u": ADMIN}).scalar()
        ids["environment_id"] = ids["env_id"]
        ids["group_id"] = c.execute(text("INSERT INTO groups (tenant_id, name) VALUES (1, 'gate-boundary-group') RETURNING id")).scalar()
        ids["conn_id"] = c.execute(text("INSERT INTO connections (tenant_id, connection_type, name, config, created_by) "
                                        "VALUES (1, 'jira', 'gate-boundary-jira', '{\"base_url\": \"https://example.invalid\"}'::jsonb, :u) RETURNING id"), {"u": ADMIN}).scalar()
        ids["release_id"] = c.execute(text("INSERT INTO releases (tenant_id, name, version_tag, status, created_by) "
                                           "VALUES (1, 'gate-boundary-release', 'gate', 'planning', :u) RETURNING id"), {"u": ADMIN}).scalar()
        ids["decision_id"] = c.execute(text("INSERT INTO release_decisions (release_id, recommendation, confidence, reasoning, criteria_met, recommended_by) "
                                            "VALUES (:r, 'cannot_determine', 0, '\"gate\"'::jsonb, '[]'::jsonb, 'ai') RETURNING id"), {"r": ids["release_id"]}).scalar()
        ids["user_id"] = c.execute(text("INSERT INTO users (tenant_id, email, password_hash, full_name, role, is_active) "
                                        "VALUES (1, 'gate-boundary@audit', 'x', 'gate boundary', 'tester', true) RETURNING id")).scalar()
    coord = SemanticTransactionCoordinator()
    with get_tenant_connection(T) as conn:
        s = Session(bind=conn)
        cr = coord.write_claim(s, actor="s3", test_id=None, archetype="data_behavior", claim_kind="value-claim",
                               asserted_truth=make_value_claim(value="Tech"), semantic_conditions=empty_conditions())
        coord.promote_claim_to_approved(s, actor="human", test_id=cr.test_id, version_seq=cr.version_seq)
        coord.link_requirement(s, actor="s3", test_id=cr.test_id, external_system="jira", external_key=key, link_kind="generated_from")
        s.flush()
        ids["test_id"] = str(cr.test_id)
        ids["waiver_id"] = conn.execute(text(
            "INSERT INTO quality_waivers (release_id, item_kind, item_ref, axis, reviewer_user_id, reason, expires_at, created_by, created_at) "
            "VALUES (:r, 'claim', :t, 'functional', :u, 'gate', now() + interval '1 day', :u, now()) RETURNING CAST(id AS text)"),
            {"r": ids["release_id"], "t": ids["test_id"], "u": ADMIN}).scalar()
        conn.execute(text("INSERT INTO release_targets (release_id, environment_id, declared_by, declared_at, active) VALUES (:r, :e, :u, now(), true)"),
                     {"r": ids["release_id"], "e": ids["env_id"], "u": ADMIN})
        ids["schedule_id"] = conn.execute(text(
            "INSERT INTO s4_run_schedules (environment_id, cron_expr, enabled, created_by) VALUES (:e, '0 6 * * *', false, :u) RETURNING id"),
            {"e": ids["env_id"], "u": ADMIN}).scalar()
        # round 4 (AUD-028): a proposal names a run that EXISTS (fk_repair_proposals_run) — plant the run first
        ids["run_id"] = str(uuid.uuid4())
        conn.execute(text(
            "INSERT INTO s4_execution_runs (run_id, recipe_id, recipe_version_seq, claim_test_id, claim_version_seq, environment_id, "
            "outcome, started_at, finished_at, evidence) VALUES (CAST(:r AS uuid), gen_random_uuid(), 1, CAST(:c AS uuid), NULL, :e, "
            "'failed', now(), now(), '{}'::jsonb)"), {"r": ids["run_id"], "c": ids["test_id"], "e": ids["env_id"]})
        ids["proposal_id"] = conn.execute(text(
            "INSERT INTO repair_proposals (run_id, claim_test_id, environment_id, verdict, proposal_kind, payload, status, proposed_payload, auto_applied) "
            "VALUES (CAST(:r AS uuid), CAST(:c AS uuid), :e, 'failed', 'gate', '{}'::jsonb, 'proposed', '{}'::jsonb, false) RETURNING id"),
            {"r": ids["run_id"], "c": ids["test_id"], "e": ids["env_id"]}).scalar()
        ids["policy_id"] = conn.execute(text("INSERT INTO quality_policies (name, version, status, note) VALUES ('gate-boundary', 1, 'draft', 'gate') RETURNING CAST(id AS text)")).scalar()
        # a declared surface link (the unlink route's id): an established identity + an active-inventory member
        from primeqa.test_representation.identity import establish_for_key
        establish_for_key(conn, key, established_by=ADMIN)
        member = conn.execute(text("SELECT surface_key FROM ui_surface_inventory_members WHERE inventory_version = "
                                   "(SELECT MAX(inventory_version) FROM claim_sets WHERE status = 'approved') LIMIT 1")).scalar()
    from primeqa.intelligence.requirement_surface_console import declare_surface
    d = declare_surface(T, requirement_key=key, surface_key=member, user_id=ADMIN)
    ids["link_id"] = str((d.get("link") or {}).get("link_id") or d.get("link_id") or "") or None
    assert ids["link_id"], d
    yield ids
    # --- the remover: statement by statement in AUTOCOMMIT under the replica role (the
    # hostile_world shape — one failed statement never poisons the rest); residue proven --
    auto = create_engine(DB, isolation_level="AUTOCOMMIT")
    with auto.connect() as c:
        c.execute(text("SET session_replication_role = replica"))
        c.execute(text("SET search_path TO tenant_%d, public" % T))
        extra = [str(r) for r in ids.get("extra_releases", [])]
        for sql, p in (
            ("DELETE FROM run_plans WHERE scope_ref = :r AND scope_kind = 'release'", {"r": str(ids["release_id"])}),
            ("DELETE FROM run_plans WHERE scope_kind = 'release' AND scope_ref = ANY(:x)", {"x": extra}),
            ("DELETE FROM run_plans WHERE scope_ref = :k", {"k": key}),
            ("DELETE FROM quality_waivers WHERE release_id = :r", {"r": ids["release_id"]}),
            ("DELETE FROM quality_policy_rules WHERE policy_id = CAST(:p AS uuid)", {"p": ids["policy_id"]}),
            ("DELETE FROM quality_policies WHERE id = CAST(:p AS uuid)", {"p": ids["policy_id"]}),
            ("DELETE FROM release_targets WHERE release_id = :r", {"r": ids["release_id"]}),
            ("DELETE FROM s4_run_schedules WHERE id = :s", {"s": ids["schedule_id"]}),
            ("DELETE FROM repair_proposals WHERE id = :p", {"p": ids["proposal_id"]}),
            ("DELETE FROM s4_execution_runs WHERE CAST(run_id AS text) = :r", {"r": ids.get("run_id", "")}),
            ("DELETE FROM requirement_surface_link_claims WHERE link_id IN (SELECT id FROM requirement_surface_links WHERE requirement_key = :k)", {"k": key}),
            ("DELETE FROM requirement_surface_links WHERE requirement_key = :k", {"k": key}),
            ("DELETE FROM test_requirement_links WHERE external_key = :k", {"k": key}),
            ("DELETE FROM requirement_identities WHERE external_key = :k", {"k": key}),
            ("DELETE FROM s4_execution_jobs WHERE CAST(test_id AS text) = :c", {"c": ids["test_id"]}),
            ("DELETE FROM test_claim_coverage WHERE CAST(claim_test_id AS text) = :c", {"c": ids["test_id"]}),
            ("DELETE FROM test_recipes WHERE CAST(claim_test_id AS text) = :c", {"c": ids["test_id"]}),
            ("DELETE FROM test_provenance WHERE CAST(claim_test_id AS text) = :c", {"c": ids["test_id"]}),
            ("DELETE FROM s8_grounding_validity WHERE CAST(test_id AS text) = :c", {"c": ids["test_id"]}),
            ("DELETE FROM test_claims WHERE CAST(test_id AS text) = :c", {"c": ids["test_id"]}),
            ("DELETE FROM public.shared_dashboard_links WHERE environment_id = :e", {"e": ids["env_id"]}),
            ("DELETE FROM public.release_decisions WHERE release_id = :r", {"r": ids["release_id"]}),
            ("DELETE FROM public.release_requirements WHERE release_id = :r", {"r": ids["release_id"]}),
            ("DELETE FROM public.releases WHERE id = :r OR (tenant_id = 1 AND name LIKE 'GATE-BOUNDARY-%')", {"r": ids["release_id"]}),
            ("DELETE FROM public.requirements WHERE id = :q OR (tenant_id = 1 AND section_id = :s)", {"q": ids["req_id"], "s": ids["section_id"]}),
            ("DELETE FROM public.sections WHERE id = :s OR (tenant_id = 1 AND name LIKE 'GATE-BOUNDARY-%')", {"s": ids["section_id"]}),
            ("DELETE FROM public.group_environments WHERE group_id = :g", {"g": ids["group_id"]}),
            ("DELETE FROM public.group_members WHERE group_id = :g", {"g": ids["group_id"]}),
            ("DELETE FROM public.groups WHERE id = :g OR (tenant_id = 1 AND name LIKE 'GATE-BOUNDARY-%')", {"g": ids["group_id"]}),
            ("DELETE FROM public.environment_credentials WHERE environment_id = :e", {"e": ids["env_id"]}),
            ("DELETE FROM public.environments WHERE id = :e OR (tenant_id = 1 AND name LIKE 'GATE-BOUNDARY-%')", {"e": ids["env_id"]}),
            ("DELETE FROM public.connections WHERE id = :c OR (tenant_id = 1 AND name LIKE 'GATE-BOUNDARY-%')", {"c": ids["conn_id"]}),
            ("DELETE FROM public.milestones WHERE tenant_id = 1 AND name LIKE 'GATE-BOUNDARY-%'", {}),
            ("DELETE FROM public.refresh_tokens WHERE user_id = :u", {"u": ids["user_id"]}),
            ("DELETE FROM public.users WHERE id = :u OR (tenant_id = 1 AND (email LIKE 'GATE-BOUNDARY-%' OR full_name LIKE 'GATE-BOUNDARY-%'))", {"u": ids["user_id"]}),
            ("UPDATE public.users SET is_active = true WHERE id IN (:a, :b)", {"a": ADMIN, "b": SUPER}),
        ):
            try:
                c.execute(text(sql), p)
            except Exception as exc:  # noqa: BLE001 — one failed statement never stops the sweep; it is named
                print("remover: %s -> %s" % (sql[:70], type(exc).__name__))
        residue = c.execute(text(
            "SELECT (SELECT count(*) FROM public.releases WHERE name LIKE 'GATE-BOUNDARY-%') + (SELECT count(*) FROM public.sections WHERE name LIKE 'GATE-BOUNDARY-%') "
            "+ (SELECT count(*) FROM public.groups WHERE name LIKE 'GATE-BOUNDARY-%') + (SELECT count(*) FROM public.environments WHERE name LIKE 'GATE-BOUNDARY-%') "
            "+ (SELECT count(*) FROM public.users WHERE full_name LIKE 'GATE-BOUNDARY-%') + (SELECT count(*) FROM public.milestones WHERE name LIKE 'GATE-BOUNDARY-%') "
            "+ (SELECT count(*) FROM public.environments WHERE id = :e) + (SELECT count(*) FROM public.releases WHERE id = :r) "
            "+ (SELECT count(*) FROM test_claims WHERE CAST(test_id AS text) = :c) + (SELECT count(*) FROM run_plans WHERE scope_ref IN (:k, :rs))"),
            {"e": ids["env_id"], "r": ids["release_id"], "c": ids["test_id"], "k": key, "rs": str(ids["release_id"])}).scalar()
        assert residue == 0, f"the boundary world left {residue} rows behind"


# --- the generated matrix ---------------------------------------------------------------

def _source_of(f) -> str:
    src, g, seen = "", f, set()
    while g is not None and id(g) not in seen:
        seen.add(id(g))
        try:
            src += inspect.getsource(g)
        except (OSError, TypeError):
            pass
        g = getattr(g, "__wrapped__", None)
    return src


def _body_routes():
    """Every (method, rule, endpoint, fields) of the live url_map whose view reads a body."""
    from primeqa.app import app
    out = []
    for r in app.url_map.iter_rules():
        if r.endpoint == "static" or r.rule.startswith(SKIP):
            continue
        methods = sorted({"POST", "PATCH", "PUT"} & r.methods)
        if not methods:
            continue
        src = _source_of(app.view_functions[r.endpoint])
        if not BODY_READ.search(src):
            continue
        for m in methods:
            out.append((m, r.rule, r.endpoint, sorted(set(FIELD.findall(src)))))
    return sorted(out, key=lambda x: (x[1], x[0]))


def _all_fields(routes):
    return sorted({f for *_, fields in routes for f in fields})


ID_LIKE = re.compile(r"(_id|_ids|_seq|_days|_slots|_attempts_per_run|_per_mtok|_rate|_percent|^position$|^version$|^page$|^per_page$|^tenant_id$)")
DATE_LIKE = re.compile(r"(date|expires_at|due)")
BOOL_LIKE = re.compile(r"^(is_|allow_|confirm_|include_|cleanup_|require_|repair_|agent_enabled|llm_enable_)")


def _kind(field):
    if BOOL_LIKE.search(field):
        return "bool"
    if DATE_LIKE.search(field):
        return "date"
    if ID_LIKE.search(field):
        return "id"
    return "text"


def _body(variant, fields, api):
    if variant == "empty":
        return {}
    body = {}
    for f in fields:
        k = _kind(f)
        if variant == "maximal":
            body[f] = {"text": BIG, "id": HUGE if api else str(HUGE), "date": "9999-12-31", "bool": BIG}[k]
        else:  # wrong_type
            body[f] = {"text": 12345 if api else "nul\x00byte", "id": "not-an-int", "date": "not-a-date", "bool": "maybe"}[k]
            if f.endswith("_ids") or f.endswith("_keys"):
                body[f] = "not-a-list"
    return body


def _path(rule, ids):
    out = rule
    for _, name in re.findall(r"<(?:([a-z]+):)?([a-z_]+)>", rule):
        if ids.get(name) is None:
            return None
        out = re.sub(r"<(?:[a-z]+:)?%s>" % name, str(ids[name]), out)
    return out


def _role_for(method, rule):
    from primeqa.core.authz import Tier
    from tests.unit.test_route_authority_table import AUTHORITY
    tier = AUTHORITY.get((method, rule), (Tier.ADMIN, ""))[0]
    return ("superadmin", SUPER) if tier >= Tier.SUPERADMIN else ("admin", ADMIN)


def _client(role, user_id):
    import jwt as pyjwt
    from primeqa.app import app
    tok = pyjwt.encode({"sub": str(user_id), "tenant_id": T, "role": role, "email": f"u{user_id}@audit", "full_name": f"u{user_id}"},
                       os.environ["JWT_SECRET"], algorithm="HS256")
    c = app.test_client()
    c.set_cookie("access_token", tok)
    c.set_cookie("csrf_token", "gate-csrf-token")
    return c


def _send(c, method, path, body, api):
    headers = {"Referer": "http://localhost/from-the-gate", "X-CSRF-Token": "gate-csrf-token"}
    if api:
        return c.open(path, method=method, json=body, headers=headers)
    form = {k: v for k, v in body.items()}
    form["csrf_token"] = "gate-csrf-token"
    return c.open(path, method=method, data=form, headers=headers)


@pytest.fixture(scope="module")
def matrix(world):
    """Every body-reading write route x {empty, maximal, wrong_type} — one run, shared by the assertions."""
    routes = _body_routes()
    assert len(routes) >= 60, f"the census collected {len(routes)} body-reading write routes — it is not reading the url_map"
    fields = _all_fields(routes)
    rows = []
    for method, rule, endpoint, own_fields in routes:
        path = _path(rule, world)
        if path is None:
            rows.append({"method": method, "rule": rule, "variant": "-", "status": "no id", "api": rule.startswith("/api/")})
            continue
        api = rule.startswith("/api/")
        role, uid = _role_for(method, rule)
        for variant in ("empty", "maximal", "wrong_type"):
            # the route's OWN fields first, then every field any route names (a field it ignores is harmless)
            body = _body(variant, own_fields or fields, api)
            resp = _send(_client(role, uid), method, path, body, api)
            txt = resp.get_data(as_text=True)
            rows.append({"method": method, "rule": rule, "variant": variant, "status": resp.status_code, "api": api,
                         "error_page": any(m in txt for m in ERROR_PAGE),
                         "location": resp.headers.get("Location", ""), "head": re.sub(r"\s+", " ", txt)[:140]})
    return rows


def _fmt(r):
    return f"{r['method']:6s} {r['rule']:66s} {r['variant']:10s} -> {r['status']} {r.get('location', '')[:40]} {r.get('head', '')[:80]}"


def test_no_hostile_body_reaches_the_database(matrix):
    """THE CLASS: never a 500, never the error page — for every body-reading write route and every body."""
    swept = [r for r in matrix if r["variant"] != "-"]
    print(f"\nboundary sweep: {len(swept)} requests over {len({r['rule'] for r in swept})} routes "
          f"({sum(1 for r in matrix if r['status'] == 'no id')} routes had no id to resolve)")
    for r in swept:
        print("  " + _fmt(r))
    bad = [r for r in swept if r["status"] == 500 or r.get("error_page")]
    assert bad == [], "the database was the validator on:\n  " + "\n  ".join(_fmt(r) for r in bad)


def test_every_api_route_refuses_an_oversize_or_mistyped_body(matrix):
    """The /api/* half of the class answers a 4xx to MAXIMAL and WRONG-TYPE — a
    boundary body is refused, never acted on, never 500."""
    rows = [r for r in matrix if r["api"] and r["variant"] in ("maximal", "wrong_type")]
    assert rows, "no /api/* body-reading route was swept"
    bad = [r for r in rows if not (400 <= int(r["status"]) < 500)]
    assert bad == [], "an /api/* route accepted (or died on) a boundary body:\n  " + "\n  ".join(_fmt(r) for r in bad)


def test_every_route_was_reached_with_a_real_id(matrix):
    """The world resolves every id a body-reading route takes — a route swept
    with no id would be a 404, not a boundary test."""
    unresolved = [r for r in matrix if r["status"] == "no id"]
    assert unresolved == [], "routes with no id in the world (plant one):\n  " + "\n  ".join(f"{r['method']} {r['rule']}" for r in unresolved)


# --- the named members (exact answers) ----------------------------------------------------

def _api(role="admin", uid=ADMIN):
    return _client(role, uid)


def _err(resp):
    j = resp.get_json(silent=True) or {}
    return (j.get("error") or {}).get("message", "") if isinstance(j.get("error"), dict) else str(j)


@pytest.mark.parametrize("body,field", [
    ({"name": BIG}, "name"), ({"name": 123}, "name"), ({"name": "   "}, "name"),
    ({"name": "ok", "version_tag": BIG}, "version_tag"), ({"name": "ok", "target_date": "not-a-date"}, "target_date"),
    ({"name": "ok", "description": 12345}, "description"), ({"name": "ok", "decision_criteria": "not-a-dict"}, "decision_criteria"),
])
def test_aud_011_release_create_refuses_naming_the_field(world, body, field):
    r = _send(_api(), "POST", "/api/releases", body, True)
    assert r.status_code == 400 and field in _err(r), (r.status_code, _err(r))


@pytest.mark.parametrize("body,field", [({"name": BIG}, "name"), ({"name": ""}, "name"), ({"version_tag": BIG}, "version_tag"),
                                        ({"target_date": "x"}, "target_date"), ({"status": "nope"}, "status")])
def test_aud_011_release_update_refuses_naming_the_field(world, body, field):
    r = _send(_api(), "PATCH", f"/api/releases/{world['release_id']}", body, True)
    assert r.status_code == 400 and field in _err(r), (r.status_code, _err(r))


def test_aud_011_the_web_release_form_re_renders_with_a_400(world):
    r = _send(_api(), "POST", "/releases", {"name": BIG}, False)
    txt = r.get_data(as_text=True)
    assert r.status_code == 400 and "name" in txt and not any(m in txt for m in ERROR_PAGE)


@pytest.mark.parametrize("rid", ["x", "9" * 30, 0, -1, True, [1]])
def test_aud_011_add_requirement_refuses_a_bad_id(world, rid):
    r = _send(_api(), "POST", f"/api/releases/{world['release_id']}/requirements", {"requirement_id": rid}, True)
    assert r.status_code == 400 and "requirement_id" in _err(r), (r.status_code, _err(r))


@pytest.mark.parametrize("body,field", [({"name": BIG}, "name"), ({"name": ""}, "name"), ({"env_type": "moon"}, "env_type"),
                                        ({"is_active": "maybe"}, "is_active"), ({"max_execution_slots": "x"}, "max_execution_slots"),
                                        ({"sf_api_version": BIG}, "sf_api_version")])
def test_aud_011_environment_update_refuses_naming_the_field(world, body, field):
    r = _send(_api(), "PATCH", f"/api/environments/{world['env_id']}", body, True)
    assert r.status_code == 400 and field in _err(r), (r.status_code, _err(r))


@pytest.mark.parametrize("body,field", [({"name": BIG}, "name"), ({"name": ""}, "name"), ({"position": "x"}, "position"),
                                        ({"parent_id": "x"}, "parent_id"), ({"description": 5}, "description")])
def test_aud_011_section_update_refuses_naming_the_field(world, body, field):
    r = _send(_api(), "PATCH", f"/api/sections/{world['section_id']}", body, True)
    assert r.status_code == 400 and field in _err(r), (r.status_code, _err(r))


@pytest.mark.parametrize("body,field", [({"jira_summary": BIG}, "jira_summary"), ({"jira_key": BIG}, "jira_key"),
                                        ({"is_stale": "maybe"}, "is_stale"), ({"section_id": "x"}, "section_id"),
                                        ({"source": BIG}, "source")])
def test_aud_011_requirement_update_refuses_naming_the_field(world, body, field):
    r = _send(_api(), "PATCH", f"/api/requirements/{world['req_id']}", body, True)
    assert r.status_code == 400 and field in _err(r), (r.status_code, _err(r))


def test_aud_011_a_valid_update_still_lands(world):
    r = _send(_api(), "PATCH", f"/api/releases/{world['release_id']}", {"name": "  gate-boundary-release renamed  ", "version_tag": ""}, True)
    assert r.status_code == 200, r.get_data(as_text=True)[:200]
    j = r.get_json()
    assert j["name"] == "gate-boundary-release renamed" and j["version_tag"] is None       # trimmed; an empty optional is NULL


def test_aud_035_a_section_that_still_holds_requirements_is_not_purged(world):
    from sqlalchemy import create_engine
    r = _send(_api(), "POST", f"/api/sections/{world['section_id']}/purge", {}, True)
    assert r.status_code == 409, (r.status_code, r.get_data(as_text=True)[:200])
    assert "1 requirement" in _err(r), _err(r)
    with create_engine(DB).connect() as c:
        assert c.execute(text("SELECT count(*) FROM sections WHERE id = :s"), {"s": world["section_id"]}).scalar() == 1


def test_aud_021_a_release_with_nothing_to_target_records_no_plan(world):
    """A release whose targets and evidence resolve to NO environment is refused
    by the planner before any row is written; the empty tenant-wide and
    requirement forms are refused at the route (no environment)."""
    from sqlalchemy import create_engine
    from primeqa.semantic.connection import get_tenant_connection
    eng = create_engine(DB)
    with eng.begin() as c:
        rid = c.execute(text("INSERT INTO releases (tenant_id, name, version_tag, status, created_by) VALUES (1, 'GATE-BOUNDARY-notarget', 'gate', 'planning', :u) RETURNING id"), {"u": ADMIN}).scalar()
    world.setdefault("extra_releases", []).append(rid)          # the remover takes its plans too
    with get_tenant_connection(T) as conn:
        before = conn.execute(text("SELECT count(*) FROM run_plans")).scalar()
    c = _api()
    r = c.post(f"/releases/{rid}/plan", data={"csrf_token": "gate-csrf-token"}, headers={"X-CSRF-Token": "gate-csrf-token"})
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/releases/{rid}?tab=decision"), (r.status_code, r.headers.get("Location"))
    page = c.get(f"/releases/{rid}?tab=decision").get_data(as_text=True)
    assert "target environment" in page, "the refusal sentence is not on the page"
    r2 = c.post("/plans", data={"csrf_token": "gate-csrf-token"}, headers={"X-CSRF-Token": "gate-csrf-token"})
    r3 = c.post(f"/requirements/{world['req_id']}/plan", data={"csrf_token": "gate-csrf-token"}, headers={"X-CSRF-Token": "gate-csrf-token"})
    # round 4 (AUD-033): the tenant-wide plan route is RETIRED — 410, no row, the successor named
    assert r2.status_code == 410 and "retired" in r2.get_data(as_text=True).lower()
    assert r3.status_code == 302 and r3.headers["Location"].endswith(f"/requirements/{world['req_id']}")
    with get_tenant_connection(T) as conn:
        after = conn.execute(text("SELECT count(*) FROM run_plans")).scalar()
    assert after == before, f"{after - before} plan row(s) recorded on nothing"
