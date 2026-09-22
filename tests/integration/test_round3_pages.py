"""ROUND 3, Part A — what a pilot client hits first (AUD-007 / 015 / 022 / 017),
and THE GUARD for the class: every list page rendered over the hostile
tenant's zero, expired and all-deprecated shapes, with the count shown
asserted equal to the count planted.

The class: a page that reads an absence where there is a fact — no run
rendered as a 500 (a None compared to a number), a lapsed acceptance
rendered as "no waivers", a retired plan rendered as "no tests yet", a
missing stamp described as legacy. Each shape is PLANTED here, rendered
through the real routes as the real tiers, and removed (residue 0).

Runs against ``S3A3_TEST_DATABASE_URL`` with ``REPORT_PAGES=1`` (scratch;
tenant 1 with the audit users 13 viewer / 14 tester / 15 admin; tenant 2,
which has no run, with user 12).
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

DB = os.environ.get("S3A3_TEST_DATABASE_URL")
_ENABLED = os.environ.get("REPORT_PAGES") == "1" and bool(DB)
pytestmark = [pytest.mark.skipif(not _ENABLED, reason="set REPORT_PAGES=1 + S3A3_TEST_DATABASE_URL")]
if _ENABLED:
    os.environ["DATABASE_URL"] = DB

ADMIN, MEMBER, VIEWER = 15, 14, 13
T2_MEMBER = 12
KEY_DEP, KEY_MIX, KEY_RUN = "R3-ALLDEP", "R3-MIXED", "R3-UNSTAMPED"
ENV = 4
ENV_LEGACY = 5901       # the legacy run lives on its own (real) environment, so it IS that pair's latest run


def _client(user_id, role, tenant=1):
    import jwt as pyjwt
    from primeqa.app import app
    tok = pyjwt.encode({"sub": str(user_id), "tenant_id": tenant, "role": role, "email": f"u{user_id}@audit", "full_name": f"u{user_id}"},
                       os.environ["JWT_SECRET"], algorithm="HS256")
    c = app.test_client()
    c.set_cookie("access_token", tok)
    c.set_cookie("csrf_token", "gate-csrf-token")
    return c


def _get(user_id, role, path, tenant=1):
    r = _client(user_id, role, tenant).get(path)
    return r.status_code, r.get_data(as_text=True)


def _sweep_markers():
    """Round 3, part C: an aborted earlier run never reaches its remover, and
    its leftovers collide with the next plant. Remove this module's own markers
    before planting, so the module is runnable twice in one process."""
    from sqlalchemy import create_engine
    auto = create_engine(DB, isolation_level="AUTOCOMMIT")
    with auto.connect() as c:
        c.execute(text("SET session_replication_role = replica"))
        c.execute(text("SET search_path TO tenant_1, public"))
        for sql in (
            "DELETE FROM quality_waivers WHERE item_ref LIKE 'round3-%'",
            "DELETE FROM requirement_surface_links WHERE requirement_key LIKE 'R3-%'",
            "DELETE FROM test_requirement_links WHERE external_key LIKE 'R3-%'",
            "DELETE FROM requirement_identities WHERE external_key LIKE 'R3-%'",
            "DELETE FROM public.release_decisions WHERE release_id IN (SELECT id FROM public.releases WHERE name LIKE 'round3-%')",
            "DELETE FROM public.release_requirements WHERE release_id IN (SELECT id FROM public.releases WHERE name LIKE 'round3-%')",
            "DELETE FROM public.releases WHERE name LIKE 'round3-%'",
            "DELETE FROM public.requirements WHERE external_key LIKE 'R3-%'",
        ):
            try:
                c.execute(text(sql))
            except Exception as exc:  # noqa: BLE001 — named, never silent
                print("pre-plant sweep: %s -> %s" % (sql[:60], type(exc).__name__))


def plant_world() -> dict:
    """The hostile shapes, planted in tenant 1: five claims all deprecated on
    one requirement; two approved + one deprecated on another; one claim with
    a recipe and TWO unstamped runs (one dated now, one dated before Step 2's
    deploy); four waivers on a release (active, lapsed, revoked) and one
    tenant-wide active. ``remove_world`` takes them back under the replica
    role (residue asserted 0). Also driven by the screenshot session."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from primeqa.generation.emission import _inspection_recipe
    from primeqa.semantic.connection import get_tenant_connection
    from primeqa.test_representation import SemanticTransactionCoordinator
    from tests.integration.test_representation._fixtures import empty_conditions, make_value_claim

    _sweep_markers()                       # round 3: start from a clean slate
    eng = create_engine(DB)
    w: dict = {"claims": [], "runs": []}
    with eng.begin() as c:
        sec = c.execute(text("SELECT id FROM sections WHERE tenant_id = 1 AND deleted_at IS NULL ORDER BY id LIMIT 1")).scalar()
        for key, title in ((KEY_DEP, "round3: every claim deprecated"), (KEY_MIX, "round3: two live, one deprecated"), (KEY_RUN, "round3: unstamped runs")):
            rid = c.execute(text("INSERT INTO requirements (tenant_id, section_id, source, created_by, jira_summary, external_key) "
                                 "VALUES (1, :s, 'manual', :u, :t, :k) RETURNING id"), {"s": sec, "u": ADMIN, "t": title, "k": key}).scalar()
            w[key] = rid
        w["release"] = c.execute(text("INSERT INTO releases (tenant_id, name, version_tag, status, created_by) VALUES (1, 'round3-waivers', 'r3', 'planning', :u) RETURNING id"), {"u": ADMIN}).scalar()
    coord = SemanticTransactionCoordinator()
    with get_tenant_connection(1) as conn:
        s = Session(bind=conn)

        def claim(key, *, approve=True, deprecate=False, with_recipe=False):
            cr = coord.write_claim(s, actor="s3", test_id=None, archetype="data_behavior", claim_kind="value-claim",
                                   asserted_truth=make_value_claim(value="Tech"), semantic_conditions=empty_conditions())
            if with_recipe:
                t, r, e = _inspection_recipe(read_entity_type="Object", read_external_id="Lead", capture_field="APPLIES_TO", env_detail="round3")
                rr = coord.write_recipe(s, actor="s3", recipe_id=None, claim_test_id=cr.test_id, trigger_kind="inspection-trigger",
                                        recipe_kind="metadata-recipe", causal_initiation=t, observation_realization=r,
                                        execution_environment=e, claim_version_seq=cr.version_seq)
                coord.promote_recipe_to_approved(s, actor="human", recipe_id=rr.recipe_id, version_seq=rr.version_seq)
            if approve:
                coord.promote_claim_to_approved(s, actor="human", test_id=cr.test_id, version_seq=cr.version_seq)
            if deprecate:
                coord.deprecate_claim(s, actor="human", test_id=cr.test_id, version_seq=cr.version_seq, reason="round3: retired")
            coord.link_requirement(s, actor="s3", test_id=cr.test_id, external_system="jira", external_key=key, link_kind="generated_from")
            w["claims"].append(str(cr.test_id))
            return cr

        for _ in range(5):
            claim(KEY_DEP, deprecate=True)
        claim(KEY_MIX); claim(KEY_MIX); claim(KEY_MIX, deprecate=True)
        runner = claim(KEY_RUN, with_recipe=True)
        s.flush()
        w["run_claim"] = str(runner.test_id)
        for label, env, at in (("now", ENV, datetime.now(timezone.utc)), ("legacy", ENV_LEGACY, datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc))):
            rid = str(uuid.uuid4())
            conn.execute(text(
                "INSERT INTO s4_execution_runs (run_id, recipe_id, recipe_version_seq, claim_test_id, claim_version_seq, environment_id, outcome, started_at, finished_at, evidence) "
                "SELECT CAST(:r AS uuid), recipe_id, version_seq, claim_test_id, :cv, :e, CAST('passed' AS run_outcome), :at, :at, '{}'::jsonb "
                "FROM test_recipes WHERE claim_test_id = CAST(:c AS uuid) AND valid_to IS NULL LIMIT 1"),
                {"r": rid, "cv": runner.version_seq, "e": env, "c": str(runner.test_id), "at": at})
            w["run_" + label] = rid; w["runs"].append(rid)
        now = datetime.now(timezone.utc)
        for label, rel, expires, created, revoked in (
                ("active_rel", w["release"], now + timedelta(days=3), now, False),
                ("lapsed_rel", w["release"], now - timedelta(days=1), now - timedelta(days=3), False),
                ("revoked_rel", w["release"], now + timedelta(days=3), now - timedelta(days=2), True),
                ("active_tenant", None, now + timedelta(days=5), now, False)):
            wid = str(uuid.uuid4())
            conn.execute(text(
                "INSERT INTO quality_waivers (id, release_id, item_kind, item_ref, axis, reviewer_user_id, reason, expires_at, created_by, created_at) "
                "VALUES (CAST(:i AS uuid), :r, 'claim', :ref, 'functional', :rev, :why, :exp, :by, :cr)"),
                {"i": wid, "r": rel, "ref": "round3-" + label, "rev": ADMIN, "why": "round3 " + label, "exp": expires, "by": MEMBER, "cr": created})
            if revoked:
                conn.execute(text("UPDATE quality_waivers SET revoked_by = :u, revoked_at = now() - interval '1 day', revocation_reason = 'round3: no longer holds' WHERE id = CAST(:i AS uuid)"),
                             {"u": ADMIN, "i": wid})
            w["waiver_" + label] = wid
    return w


def remove_world(w: dict) -> None:
    from sqlalchemy import create_engine
    auto = create_engine(DB, isolation_level="AUTOCOMMIT")
    with auto.connect() as c:
        c.execute(text("SET session_replication_role = replica"))
        c.execute(text("SET search_path TO tenant_1, public"))
        for sql, p in (
            ("DELETE FROM quality_waivers WHERE item_ref LIKE 'round3-%'", {}),
            ("DELETE FROM s6_interpretations WHERE CAST(run_id AS text) = ANY(:r)", {"r": w["runs"]}),
            ("DELETE FROM s4_execution_runs WHERE CAST(run_id AS text) = ANY(:r)", {"r": w["runs"]}),
            ("DELETE FROM test_requirement_links WHERE external_key IN (:a, :b, :c)", {"a": KEY_DEP, "b": KEY_MIX, "c": KEY_RUN}),
            ("DELETE FROM requirement_identities WHERE external_key IN (:a, :b, :c)", {"a": KEY_DEP, "b": KEY_MIX, "c": KEY_RUN}),
            ("DELETE FROM s4_execution_jobs WHERE CAST(test_id AS text) = ANY(:c)", {"c": w["claims"]}),
            ("DELETE FROM test_claim_coverage WHERE CAST(claim_test_id AS text) = ANY(:c)", {"c": w["claims"]}),
            ("DELETE FROM test_recipes WHERE CAST(claim_test_id AS text) = ANY(:c)", {"c": w["claims"]}),
            ("DELETE FROM test_provenance WHERE CAST(claim_test_id AS text) = ANY(:c)", {"c": w["claims"]}),
            ("DELETE FROM s8_grounding_validity WHERE CAST(test_id AS text) = ANY(:c)", {"c": w["claims"]}),
            ("DELETE FROM test_claims WHERE CAST(test_id AS text) = ANY(:c)", {"c": w["claims"]}),
            ("DELETE FROM public.release_decisions WHERE release_id = :r", {"r": w["release"]}),
            ("DELETE FROM public.releases WHERE id = :r", {"r": w["release"]}),
            ("DELETE FROM public.requirements WHERE id IN (:a, :b, :c)", {"a": w[KEY_DEP], "b": w[KEY_MIX], "c": w[KEY_RUN]}),
        ):
            try:
                c.execute(text(sql), p)
            except Exception as exc:  # noqa: BLE001 — named, never silent
                print("remover: %s -> %s" % (sql[:60], type(exc).__name__))
        residue = c.execute(text(
            "SELECT (SELECT count(*) FROM quality_waivers WHERE item_ref LIKE 'round3-%') + (SELECT count(*) FROM public.requirements WHERE external_key LIKE 'R3-%') "
            "+ (SELECT count(*) FROM test_claims WHERE CAST(test_id AS text) = ANY(:c)) + (SELECT count(*) FROM s4_execution_runs WHERE CAST(run_id AS text) = ANY(:r))"),
            {"c": w["claims"], "r": w["runs"]}).scalar()
        assert residue == 0, f"round3 world left {residue} rows behind"


@pytest.fixture(scope="module")
def world():
    w = plant_world()
    yield w
    remove_world(w)


# --- AUD-007: the landing page over a tenant with no run ---------------------------------

def test_aud_007_a_tenant_with_no_run_lands_on_a_page_not_a_500():
    from sqlalchemy import create_engine
    with create_engine(DB).connect() as c:
        assert c.execute(text("SELECT count(*) FROM tenant_2.s4_execution_runs")).scalar() == 0, "tenant 2 must hold no run for this proof"
    status, html = _get(T2_MEMBER, "tester", "/", tenant=2)
    assert status == 200, status
    assert 'data-testid="pass-rate-empty"' in html and "no run yet" in html
    assert "Something went wrong" not in html


def test_aud_007_a_tenant_with_runs_still_shows_its_rate():
    status, html = _get(MEMBER, "tester", "/")
    assert status == 200
    assert 'data-testid="pass-rate"' in html or 'data-testid="pass-rate-empty"' in html


# --- AUD-015: the register lists every acceptance, and a lapse is a fact -------------------

def _rows(html, state):
    return len(re.findall(r'data-testid="waiver-row" data-state="%s"' % state, html))


def test_aud_015_every_waiver_is_listed_by_state_with_who_and_until(world):
    status, html = _get(ADMIN, "admin", "/settings/waivers")
    assert status == 200
    assert 'data-testid="no-waivers"' not in html
    # the counts shown equal the counts planted (+ whatever else the tenant holds — asserted as >=, exact on the planted refs)
    for ref in ("round3-active_rel", "round3-active_tenant", "round3-lapsed_rel", "round3-revoked_rel"):
        assert ref in html, f"{ref} is not on the register"
    assert _rows(html, "active") >= 2 and _rows(html, "expired") >= 1 and _rows(html, "revoked") >= 1
    assert 'data-testid="waivers-expired"' in html and "Lapsed" in html
    lapsed = html[html.index('data-testid="waivers-expired"'):]
    lapsed = lapsed[:lapsed.index("</section>")]
    assert "round3-lapsed_rel" in lapsed and "not counted" in lapsed
    assert "u15" in lapsed or "admin" in lapsed.lower(), "who gave the acceptance must be on the lapsed row"
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    assert yesterday in lapsed, "the lapse date must be on the row"


def test_aud_015_the_revoke_form_carries_a_reason_field_on_both_doors(world):
    _, html = _get(ADMIN, "admin", "/settings/waivers")
    forms = re.findall(r'<form[^>]*data-testid="revoke-waiver"[^>]*>(.*?)</form>', html, re.S)
    assert forms, "no revoke form on the register"
    assert all('name="reason"' in f and 'type="hidden" name="reason"' not in f and "required" in f for f in forms)
    _, page = _get(ADMIN, "admin", f"/releases/{world['release']}?tab=decision")
    forms = re.findall(r'<form[^>]*data-testid="revoke-waiver"[^>]*>(.*?)</form>', page, re.S)
    assert forms, "no revoke form on the decision tab"
    assert all('name="reason"' in f and 'type="hidden" name="reason"' not in f for f in forms)


def test_aud_015_a_revocation_through_the_register_lands_with_its_reason(world):
    from sqlalchemy import create_engine
    c = _client(ADMIN, "admin")
    r = c.post(f"/settings/waivers/{world['waiver_active_tenant']}/revoke", data={"csrf_token": "gate-csrf-token", "reason": "round3: proven through the form"},
               headers={"X-CSRF-Token": "gate-csrf-token"})
    assert r.status_code == 302, r.status_code
    with create_engine(DB).connect() as cx:
        cx.execute(text("SET search_path TO tenant_1, public"))
        row = cx.execute(text("SELECT revoked_by, revocation_reason FROM quality_waivers WHERE id = CAST(:i AS uuid)"), {"i": world["waiver_active_tenant"]}).fetchone()
    assert row[0] == ADMIN and row[1] == "round3: proven through the form"


# --- AUD-022: all-retired is a fact, not an absence ----------------------------------------

def test_aud_022_the_board_row_says_no_live_test_and_the_deprecated_count(world):
    status, html = _get(MEMBER, "tester", "/requirements?q=round3&per_page=50")
    assert status == 200
    row = html[html.index(f'/requirements/{world[KEY_DEP]}"'):]
    row = row[:row.index("</tr>")] if "</tr>" in row else row[:4000]
    assert 'data-testid="all-deprecated"' in row and "5 deprecated" in row and "no tests yet" not in row
    mixed = html[html.index(f'/requirements/{world[KEY_MIX]}"'):]
    mixed = mixed[:mixed.index("</tr>")] if "</tr>" in mixed else mixed[:4000]
    assert "2 approved" in mixed and 'data-testid="deprecated-chip"' in mixed and "1 deprecated" in mixed


def test_aud_022_the_page_header_carries_the_deprecated_count(world):
    status, html = _get(MEMBER, "tester", f"/requirements/{world[KEY_DEP]}")
    assert status == 200
    assert 'data-testid="deprecated-count"' in html and "5 deprecated" in html
    assert 'data-testid="all-deprecated"' in html and "No live test case" in html
    status, html = _get(MEMBER, "tester", f"/requirements/{world[KEY_MIX]}")
    assert "(2 tests" in html and "1 deprecated" in html


# --- AUD-017: the unstamped sentence states the fact ---------------------------------------

def test_aud_017_an_unstamped_run_made_today_is_not_called_legacy(world):
    from sqlalchemy.orm import Session
    from primeqa.semantic.connection import get_tenant_connection
    from primeqa.sync import readiness as R
    with get_tenant_connection(1) as conn:
        s = Session(bind=conn)
        # the latest run per pair is the one dated now (finished_at DESC)
        r = R.resolve_run_readiness(s, claim_test_id=world["run_claim"], environment_id=ENV)
        assert r.state == R.READY_CANNOT_DETERMINE and r.reason == R.REASON_UNSTAMPED
        assert r.sentence == R.SENTENCE_UNSTAMPED and not r.predates_stamping
        assert "predates" not in r.sentence
        bulk = R.resolve_run_readiness_bulk(s, [(world["run_claim"], ENV)])
        assert bulk[(world["run_claim"], ENV)].sentence == R.SENTENCE_UNSTAMPED
    status, html = _get(MEMBER, "tester", f"/runs/{world['run_now']}")
    assert status == 200 and "carries no environment stamp" in html and "predates" not in html


def test_aud_017_a_run_dated_before_step_2_carries_the_legacy_clause(world):
    from sqlalchemy.orm import Session
    from primeqa.semantic.connection import get_tenant_connection
    from primeqa.sync import readiness as R
    with get_tenant_connection(1) as conn:
        s = Session(bind=conn)
        r = R.resolve_run_readiness(s, claim_test_id=world["run_claim"], environment_id=ENV_LEGACY)
        assert r.state == R.READY_CANNOT_DETERMINE and r.reason == R.REASON_UNSTAMPED
        assert r.predates_stamping and r.sentence == R.SENTENCE_UNSTAMPED_LEGACY, r.as_dict()
    status, html = _get(MEMBER, "tester", f"/runs/{world['run_legacy']}")
    assert status == 200
    assert "predates run-level stamping" in html, "the legacy clause belongs on a run dated before Step 2's deploy"


# --- Part E: the two observations only PRODUCTION data reached, planted here ----------------

@pytest.fixture(scope="module")
def relative_date_claim(world):
    """A claim whose asserted value carries a ``$relative_date`` object — the
    shape that made a production landing-page title read
    ``Automatically sets PLS FB Order SLA Deadline to {'$relative_date':
    {'anchor': 'RUN_DATE', 'offset_days': 5}}`` (AUD-043). It existed only on
    production, so the audit could not reproduce it; it exists on scratch now."""
    from sqlalchemy.orm import Session
    from primeqa.semantic.connection import get_tenant_connection
    from primeqa.test_representation import SemanticTransactionCoordinator
    from tests.integration.test_representation._fixtures import empty_conditions, make_value_claim

    coord = SemanticTransactionCoordinator()
    with get_tenant_connection(1) as conn:
        s = Session(bind=conn)
        truth = make_value_claim(value={"$relative_date": {"anchor": "RUN_DATE", "offset_days": 5}})
        cr = coord.write_claim(s, actor="s3", test_id=None, archetype="data_behavior",
                               claim_kind="value-claim", asserted_truth=truth,
                               semantic_conditions=empty_conditions())
        coord.promote_claim_to_approved(s, actor="human", test_id=cr.test_id, version_seq=cr.version_seq)
        coord.link_requirement(s, actor="s3", test_id=cr.test_id, external_system="jira",
                               external_key=KEY_MIX, link_kind="generated_from")
        s.flush()
        world["claims"].append(str(cr.test_id))
    return str(cr.test_id)


def test_aud_043_a_relative_date_value_renders_as_a_date_not_a_dict(relative_date_claim):
    """Round 4: the marker is gone — a RelativeDate value reads in words."""
    status, html = _get(MEMBER, "tester", f"/claims/{relative_date_claim}")
    assert status == 200
    assert "$relative_date" not in html and "offset_days" not in html, \
        "the claim's title/value shows the value object's repr"


def test_aud_043_the_shape_is_really_planted(relative_date_claim):
    """The plant itself is proven, so the xfail above is evidence about the
    RENDER and not about a fixture that quietly did nothing."""
    from sqlalchemy import create_engine
    with create_engine(DB).connect() as c:
        c.execute(text("SET search_path TO tenant_1, public"))
        truth = c.execute(text("SELECT CAST(asserted_truth AS text) FROM test_claims WHERE CAST(test_id AS text) = :t AND valid_to IS NULL"),
                          {"t": relative_date_claim}).scalar()
    assert "$relative_date" in (truth or ""), truth


# --- THE GUARD for the class: every list page over the hostile shapes, counts shown == planted --

@pytest.mark.parametrize("path,user,role,tenant", [
    ("/", T2_MEMBER, "tester", 2), ("/requirements", T2_MEMBER, "tester", 2), ("/runs/substrate", T2_MEMBER, "tester", 2),
    ("/releases", T2_MEMBER, "tester", 2), ("/settings/waivers", T2_MEMBER, "tester", 2),
    ("/", MEMBER, "tester", 1), ("/requirements?q=round3&per_page=50", MEMBER, "tester", 1), ("/runs/substrate", MEMBER, "tester", 1),
    ("/releases", MEMBER, "tester", 1), ("/settings/waivers", ADMIN, "admin", 1), ("/settings/claim-sets", ADMIN, "admin", 1),
])
def test_every_list_page_renders_over_the_hostile_shapes(world, path, user, role, tenant):
    status, html = _get(user, role, path, tenant=tenant)
    assert status == 200, (path, status)
    assert "Something went wrong" not in html and "Traceback" not in html


def test_the_counts_shown_equal_the_counts_planted(world):
    # the register: every planted waiver, grouped by its REAL state at this moment (the revoke proof above moved one)
    from sqlalchemy import create_engine
    with create_engine(DB).connect() as c:
        c.execute(text("SET search_path TO tenant_1, public"))
        planted = dict(c.execute(text(
            "SELECT CASE WHEN revoked_at IS NOT NULL THEN 'revoked' WHEN expires_at <= now() THEN 'expired' ELSE 'active' END, count(*) "
            "FROM quality_waivers WHERE item_ref LIKE 'round3-%' GROUP BY 1")).fetchall())
    assert sum(planted.values()) == 4, planted
    _, html = _get(ADMIN, "admin", "/settings/waivers")
    for state, n in planted.items():
        shown = len(re.findall(r'data-testid="waiver-row" data-state="%s"[^>]*>(?:(?!</tr>).)*round3-' % state, html, re.S))
        assert shown == n, (state, shown, n)
    # the board: the retired count per requirement
    _, html = _get(MEMBER, "tester", "/requirements?q=round3&per_page=50")
    assert html.count("5 deprecated") == 1 and html.count("1 deprecated") == 1
    # the run list carries both unstamped runs of the planted claim (the legacy one sits outside the
    # default 24-hour window, so it is asked for by its environment and an open window)
    _, html = _get(MEMBER, "tester", "/runs/substrate?group=runs&per_page=50")
    assert world["run_now"][:8] in html
    # (as the admin: the member's group does not reach the second environment)
    _, html = _get(ADMIN, "admin", f"/runs/substrate?group=runs&env={ENV_LEGACY}&since=2026-08-01&per_page=50")
    assert world["run_legacy"][:8] in html, "the legacy unstamped run is not listed for its environment"
