"""AUD-014 containment, DB-real on scratch: a decision requires a NON-EMPTY
graded scope. The two invariant attacks the audit landed — a release with no
scope, a release whose only declared target is inactive — now REFUSE through
the API and the web form, write no row, and name what is empty; the page's
card, the page's Evaluate button and the board card say the same sentence
(D-491: a list must not contradict the page it links to); the board's
currency verdict is the canonical readiness read's (bulk == single); and a
real scope grades exactly as before.

Runs against ``S3A3_TEST_DATABASE_URL`` (the scratch DB) with
``REPORT_PAGES=1``; plants its own world and removes it."""
from __future__ import annotations

import os
import re
from uuid import uuid4

import pytest
from sqlalchemy import text

DB = os.environ.get("S3A3_TEST_DATABASE_URL")
_ENABLED = os.environ.get("REPORT_PAGES") == "1" and bool(DB)
pytestmark = [pytest.mark.skipif(not _ENABLED, reason="set REPORT_PAGES=1 + S3A3_TEST_DATABASE_URL")]
if _ENABLED:
    os.environ["DATABASE_URL"] = DB

TENANT, ENV, ENV2, ADMIN = 1, 4, 5901, 15
MARK = "AUD14T"


def _client(role="admin", user="15"):
    import jwt as pyjwt
    from primeqa.app import app
    token = pyjwt.encode({"sub": user, "tenant_id": TENANT, "role": role, "email": "audit@x", "full_name": "audit"},
                         os.environ["JWT_SECRET"], algorithm="HS256")
    c = app.test_client(); c.set_cookie("access_token", token)
    return c, token


def _posting_client():
    c, token = _client()
    c.get("/releases")                        # mints csrf_token (double-submit)
    tok = None
    jar = getattr(c, "_cookies", {})
    for k, v in (jar.items() if hasattr(jar, "items") else []):
        if "csrf_token" in str(k):
            tok = getattr(v, "value", None) or str(v).split("=")[-1]
    return c, token, {"X-CSRF-Token": tok or ""}


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def _conn():
    from primeqa.semantic.connection import get_tenant_connection
    return get_tenant_connection(TENANT)


@pytest.fixture(scope="module")
def world():
    """Four releases: no scope; one requirement (3 test cases) with an INACTIVE
    declared target; a REAL scope (2 test cases, stamped current runs on env 4);
    a SPLIT scope (test case a ran on env 4 and 5901, b on env 4 only)."""
    import sys

    import primeqa.core.models  # noqa: F401 — registers the FK targets
    from primeqa import db as dbm
    from primeqa.test_representation import SemanticTransactionCoordinator
    sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
    from test_representation._fixtures import empty_conditions, make_value_claim
    if getattr(dbm, "engine", None) is None:
        dbm.init_db(DB)
    db = dbm.SessionLocal()
    tag = uuid4().hex[:6]
    W = {"tag": tag, "requirements": [], "releases": {}, "claims": [], "runs": [], "orgs": [], "versions": [],
         "sections": [], "environments": []}
    sid = db.execute(text("INSERT INTO sections (tenant_id, name, created_by) VALUES (1, :n, :u) RETURNING id"),
                     {"n": f"{MARK}-{tag}", "u": ADMIN}).scalar()
    W["sections"].append(sid)

    def req(key):
        rid = db.execute(text("INSERT INTO requirements (tenant_id, section_id, source, created_by, jira_summary, external_key) "
                              "VALUES (1, :s, 'manual', :u, :t, :k) RETURNING id"),
                         {"s": sid, "u": ADMIN, "t": f"{MARK} {key}", "k": key}).scalar()
        W["requirements"].append(rid); return rid

    def rel(name, req_ids):
        rid = db.execute(text("INSERT INTO releases (tenant_id, name, version_tag, status, created_by) "
                              "VALUES (1, :n, 'aud14', 'planning', :u) RETURNING id"), {"n": f"{MARK}-{name}-{tag}", "u": ADMIN}).scalar()
        for q in req_ids:
            db.execute(text("INSERT INTO release_requirements (release_id, requirement_id, added_by) VALUES (:r, :q, :u)"),
                       {"r": rid, "q": q, "u": ADMIN})
        W["releases"][name] = rid; return rid
    keys = {n: f"{MARK}-{n}-{tag}" for n in ("inactive", "real", "split")}
    r_inactive, r_real, r_split = req(keys["inactive"]), req(keys["real"]), req(keys["split"])
    env_inactive = db.execute(text(
        "INSERT INTO environments (tenant_id, name, env_type, sf_instance_url, sf_api_version, is_active, created_by) "
        "SELECT tenant_id, :n, env_type, sf_instance_url, sf_api_version, false, :u FROM environments WHERE id = :e RETURNING id"),
        {"n": f"{MARK}-inactive-{tag}", "u": ADMIN, "e": ENV}).scalar()
    W["environments"].append(env_inactive); W["env_inactive"] = env_inactive
    rel("noscope", []); rel("inactive", [r_inactive]); rel("real", [r_real]); rel("split", [r_split])
    db.commit(); db.close()
    W["keys"] = keys

    coord = SemanticTransactionCoordinator()
    try:
        _plant_tenant_side(W, coord, keys, env_inactive, tag, empty_conditions, make_value_claim)
    except BaseException:
        _remove(W)            # a half-planted world must not outlive its fixture
        raise
    yield W
    _remove(W)


def _plant_tenant_side(W, coord, keys, env_inactive, tag, empty_conditions, make_value_claim):
    from sqlalchemy.orm import Session
    with _conn() as conn:
        conn.execute(text("SELECT set_config('app.tenant_id', '1', false)"))
        s = Session(bind=conn)
        orgs, seqs = {}, {}
        for e in (ENV, ENV2):
            # one connected org per environment (uq_connected_orgs_environment_id):
            # reuse the environment's org when one exists, plant one otherwise
            orgs[e] = conn.execute(text("SELECT CAST(id AS text) FROM connected_orgs WHERE environment_id = :e"), {"e": e}).scalar()
            if orgs[e] is None:
                orgs[e] = conn.execute(text("INSERT INTO connected_orgs (org_type, sf_instance_url, label, environment_id) "
                                            "VALUES ('sandbox', 'https://aud14.example', :l, :e) RETURNING CAST(id AS text)"),
                                       {"l": f"{MARK}-org-{e}-{tag}", "e": e}).scalar()
                W["orgs"].append(orgs[e])
            seqs[e] = conn.execute(text("INSERT INTO logical_versions (version_name, version_type, connected_org_id) "
                                        "VALUES (:n, 'sync_run', CAST(:o AS uuid)) RETURNING version_seq"),
                                   {"n": f"{MARK}-v-{e}-{tag}", "o": orgs[e]}).scalar()
            W["versions"].append(seqs[e])

        def claim(key):
            cr = coord.write_claim(s, actor="s3", test_id=None, archetype="data_behavior", claim_kind="value-claim",
                                   asserted_truth=make_value_claim(value="Tech"), semantic_conditions=empty_conditions())
            coord.promote_claim_to_approved(s, actor="human", test_id=cr.test_id, version_seq=cr.version_seq)
            coord.link_requirement(s, actor="s3", test_id=cr.test_id, external_system="jira", external_key=key, link_kind="generated_from")
            W["claims"].append(str(cr.test_id)); return cr

        def run(cr, e):
            rid = str(uuid4())
            conn.execute(text("INSERT INTO s4_execution_runs (run_id, recipe_id, recipe_version_seq, claim_test_id, claim_version_seq, "
                              "environment_id, outcome, started_at, finished_at, evidence, connected_org_id, org_version_seq) "
                              "VALUES (CAST(:r AS uuid), CAST(:rec AS uuid), 1, CAST(:t AS uuid), :cvs, :e, 'passed', now(), now(), "
                              "'{}'::jsonb, CAST(:o AS uuid), :s)"),
                         {"r": rid, "rec": str(uuid4()), "t": str(cr.test_id), "cvs": cr.version_seq, "e": e, "o": orgs[e], "s": seqs[e]})
            W["runs"].append(rid)
        for _ in range(3):
            claim(keys["inactive"])
        for _ in range(2):
            run(claim(keys["real"]), ENV)
        a, b = claim(keys["split"]), claim(keys["split"])
        s.flush()
        run(a, ENV); run(a, ENV2); run(b, ENV)
        conn.execute(text("INSERT INTO release_targets (release_id, environment_id, declared_by, declared_at, active) "
                          "VALUES (:r, :e, :u, now(), true)"), {"r": W["releases"]["inactive"], "e": env_inactive, "u": ADMIN})
        s.close()


def _remove(W):
    """Remove the world, statement by statement under the replica role
    (never-delete triggers) in AUTOCOMMIT — one failure never poisons the rest."""
    from sqlalchemy import create_engine
    eng = create_engine(DB, isolation_level="AUTOCOMMIT")
    rels = list(W["releases"].values())
    with eng.connect() as c:
        c.execute(text("SET session_replication_role = replica"))
        c.execute(text("SET search_path TO tenant_1, public"))
        for sql, p in (
            ("DELETE FROM release_decisions WHERE release_id = ANY(:r)", {"r": rels}),
            ("DELETE FROM release_targets WHERE release_id = ANY(:r)", {"r": rels}),
            ("DELETE FROM s4_execution_runs WHERE CAST(run_id AS text) = ANY(:x)", {"x": W["runs"]}),
            ("DELETE FROM test_requirement_links WHERE CAST(test_id AS text) = ANY(:x)", {"x": W["claims"]}),
            ("DELETE FROM test_claims WHERE CAST(test_id AS text) = ANY(:x)", {"x": W["claims"]}),
            ("DELETE FROM logical_versions WHERE version_seq = ANY(:x)", {"x": W["versions"]}),
            ("DELETE FROM connected_orgs WHERE CAST(id AS text) = ANY(:x)", {"x": W["orgs"]}),
            ("DELETE FROM release_requirements WHERE release_id = ANY(:r)", {"r": rels}),
            ("DELETE FROM releases WHERE id = ANY(:r)", {"r": rels}),
            ("DELETE FROM requirements WHERE id = ANY(:x)", {"x": W["requirements"]}),
            ("DELETE FROM environments WHERE id = ANY(:x)", {"x": W["environments"]}),
            ("DELETE FROM sections WHERE id = ANY(:x)", {"x": W["sections"]}),
        ):
            try:
                c.execute(text(sql), p)
            except Exception:  # noqa: BLE001 — autocommit: one failure never poisons the rest
                pass


def _decisions(rid):
    with _conn() as conn:
        return conn.execute(text("SELECT count(*) FROM release_decisions WHERE release_id = :r"), {"r": rid}).scalar()


def _board_sentence(c, rid):
    html = c.get("/releases").get_data(as_text=True)
    m = re.search(r'data-release="%d"[^>]*data-state="([^"]*)"' % rid, html)
    return m.group(1) if m else None, html


# --- attack #9: a release with NO scope -------------------------------------------

def test_a_release_with_no_scope_refuses_through_the_api_and_writes_nothing(world):
    rid = world["releases"]["noscope"]
    c, token = _client()
    before = _decisions(rid)
    r = c.post(f"/api/releases/{rid}/evaluate-decision", headers={"Authorization": "Bearer " + token}, json={})
    assert r.status_code == 409, r.get_data(as_text=True)
    body = r.get_json()
    assert body["error"]["code"] == "SCOPE_EMPTY"
    assert body["error"]["message"] == "Evaluate will refuse — no requirement is in scope — nothing to grade"
    assert body["error"]["details"]["empty"]["reason"] == "no_requirement"
    assert _decisions(rid) == before == 0


def test_a_release_with_no_scope_refuses_through_the_web_form_and_names_it(world):
    rid = world["releases"]["noscope"]
    c, token, hdr = _posting_client()
    r = c.post(f"/releases/{rid}/evaluate-decision", headers=hdr, data={})
    assert r.status_code == 302 and r.headers["Location"] == f"/releases/{rid}?tab=decision"
    html = c.get(r.headers["Location"]).get_data(as_text=True)
    # the flash rides the toast payload (JSON-escaped em dashes)
    assert "Evaluate refused \\u2014 no requirement is in scope \\u2014 nothing to grade" in html
    assert _decisions(rid) == 0


# --- attack #2: a release scoped ONLY to an inactive environment ------------------

def test_an_inactive_only_target_refuses_naming_the_environment_not_the_currency(world):
    rid = world["releases"]["inactive"]
    c, token = _client()
    r = c.post(f"/api/releases/{rid}/evaluate-decision", headers={"Authorization": "Bearer " + token}, json={})
    assert r.status_code == 409
    body = r.get_json()
    # the TRUE reason — not "3 items not current" (which is also true, and second)
    assert body["error"]["code"] == "SCOPE_EMPTY"
    assert body["error"]["details"]["empty"]["reason"] == "no_active_target"
    assert f"every declared target environment is inactive ({MARK}-inactive-{world['tag']})" in body["error"]["message"]
    assert body["error"]["details"]["scope"]["targets"] == [world["env_inactive"]]
    assert body["error"]["details"]["scope"]["active_targets"] == []
    assert _decisions(rid) == 0


# --- the page and the board say the same words (D-491) ----------------------------

@pytest.mark.parametrize("name", ["noscope", "inactive"])
def test_the_page_card_the_button_and_the_board_card_carry_one_sentence(world, name):
    rid = world["releases"][name]
    c, _ = _client()
    page = c.get(f"/releases/{rid}?tab=decision").get_data(as_text=True)
    assert 'data-recommendation="none"' in page                    # the card previews NO recommendation
    m = re.search(r'data-testid="quality-refusal">([^<]+)<', page)
    assert m, "the quality card must carry the refusal"
    card = m.group(1).strip()
    assert card.startswith("Evaluate will refuse — ")
    mb = re.search(r'title="Refuses: ([^"]+)"[^>]*>Evaluate GO/NO-GO', page)
    assert mb and card == "Evaluate will refuse — " + mb.group(1)
    state, board_html = _board_sentence(c, rid)
    assert state == "refuses"
    # the board card's sentence, verbatim
    i = board_html.find('data-release="%d"' % rid)
    assert card in _text(board_html[i:i + 4000])


def test_the_board_counts_currency_like_the_page_does(world):
    # SPLIT: test case a ran on env 4 and 5901, b on env 4 only -> the page names
    # one NEVER_RUN item (b on 5901); before this slice the board counted 0
    rid = world["releases"]["split"]
    c, _ = _client()
    page = c.get(f"/releases/{rid}?tab=decision").get_data(as_text=True)
    m = re.search(r'data-testid="scope-refusal" data-non-current="(\d+)"', page)
    assert m and m.group(1) == "1"
    state, board_html = _board_sentence(c, rid)
    assert state == "refuses"
    i = board_html.find('data-release="%d"' % rid)
    assert "Evaluate will refuse — 1 item in scope is not current" in _text(board_html[i:i + 4000])


def test_bulk_readiness_is_the_single_read_entry_by_entry(world):
    from sqlalchemy.orm import Session

    from primeqa.intelligence.substrate_decision import (
        release_scope_readiness, release_scope_readiness_bulk,
    )
    keys = {world["releases"][n]: [world["keys"][n]] for n in ("inactive", "real", "split")}
    keys[world["releases"]["noscope"]] = []
    with _conn() as conn:
        s = Session(bind=conn)
        bulk = release_scope_readiness_bulk(s, TENANT, keys)
        s.close()
    for rid, ks in keys.items():
        assert bulk[rid] == release_scope_readiness(TENANT, ks), rid
    assert bulk[world["releases"]["split"]]["non_current"] == 1
    assert bulk[world["releases"]["real"]]["non_current"] == 0
    assert bulk[world["releases"]["inactive"]]["non_current"] == 3


# --- the page's keys are the composer's keys (defect 2) ---------------------------

def test_the_page_previews_over_the_same_keys_the_act_evaluates(world):
    # a MANUAL requirement (no jira_key): before this slice the page derived
    # "req-<id>" and previewed GO over an empty scope the act refused
    from primeqa.release.decision_composer import external_keys_for_requirements
    from primeqa.release.repository import ReleaseRepository
    from primeqa.release.service import ReleaseService
    from primeqa import db as dbm
    db = dbm.SessionLocal()
    try:
        detail = ReleaseService(ReleaseRepository(db)).get_release_detail(world["releases"]["real"], TENANT)
    finally:
        db.close()
    assert external_keys_for_requirements(detail["requirements"]) == [world["keys"]["real"]]
    c, _ = _client()
    page = c.get(f"/releases/{world['releases']['real']}?tab=decision").get_data(as_text=True)
    assert "2 functional check(s) in scope" in _text(page)


# --- a real scope still grades ---------------------------------------------------

def test_a_real_scope_is_graded_not_refused(world):
    from primeqa.intelligence.quality_decision_console import preview_release_decision
    q = preview_release_decision(TENANT, world["releases"]["real"], [world["keys"]["real"]])
    assert q["available"] and q["ok"] is True
    assert q["evidence"]["scope"] == {"claim_count": 2, "functional": 2, "conformance": 0}
    assert q["decision"]["recommendation"] in ("go", "conditional_go", "no_go", "cannot_determine")
