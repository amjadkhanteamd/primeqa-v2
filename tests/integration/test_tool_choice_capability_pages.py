"""D-504, DB-real on scratch through the REAL routes and services: the
forced-tool-choice capability as a recorded fact on the model catalog.

  * the picker REFUSES an override to a model whose capability is unknown
    (flash names the act; the row and the audit log are unchanged) and SAVES
    one whose capability is False (driven by instruction), audited as today;
  * the Models panel renders the fact per row and names tenants pinned to an
    unknown-capability model;
  * enable writes the fact with the row; a probe that cannot establish it
    writes nothing (through the real route, where scratch has no LLM
    connection to borrow a key from);
  * refresh probes every active row whose fact is NULL and stamps it, and a
    per-row probe failure leaves NULL without failing the refresh.

Runs against ``S3A3_TEST_DATABASE_URL`` with ``REPORT_PAGES=1``. Plants
catalog rows under the ``claude-d504-`` prefix and removes them; restores the
tenant's override; removes the audit rows it wrote. Residue 0.
"""
from __future__ import annotations

import os
import re

import pytest
from sqlalchemy import text

DB = os.environ.get("S3A3_TEST_DATABASE_URL")
_ENABLED = os.environ.get("REPORT_PAGES") == "1" and bool(DB)
pytestmark = [pytest.mark.skipif(not _ENABLED, reason="set REPORT_PAGES=1 + S3A3_TEST_DATABASE_URL")]
if _ENABLED:
    os.environ["DATABASE_URL"] = DB

TENANT = 1
SUPER = 15            # the scratch audit admin; the role rides the token (is_active is the DB read)
UNKNOWN = "claude-d504-unknown"
NOFORCE = "claude-d504-noforce"
PREFIX = "claude-d504-"


def _engine():
    from sqlalchemy import create_engine
    return create_engine(DB, isolation_level="AUTOCOMMIT")


def _posting_client(user_id: int, role: str):
    import jwt as pyjwt
    from primeqa.app import app
    token = pyjwt.encode({"sub": str(user_id), "tenant_id": TENANT, "role": role,
                          "email": f"u{user_id}@audit", "full_name": f"u{user_id}"},
                         os.environ["JWT_SECRET"], algorithm="HS256")
    c = app.test_client()
    c.set_cookie("access_token", token)
    c.get("/login")
    tok = None
    for k, v in getattr(c, "_cookies", {}).items():
        if "csrf_token" in str(k):
            tok = getattr(v, "value", None) or str(v).split("=")[-1]
    return c, {"X-CSRF-Token": tok or ""}


def _flash(c, location: str) -> str:
    html = c.get(location).get_data(as_text=True)
    m = re.search(r'__primeqaFlash = \[\["(?:error|success)", "((?:[^"\\]|\\.)*)"\]\]', html)
    return (m.group(1) if m else "").encode().decode("unicode_escape")


def _sweep():
    with _engine().connect() as c:
        c.execute(text("DELETE FROM public.activity_log WHERE entity_type IN "
                       "('llm_model_enabled','tenant_llm_model_override') "
                       "AND details::text LIKE :p"), {"p": f"%{PREFIX}%"})
        c.execute(text("DELETE FROM public.llm_models WHERE model_id LIKE :p"), {"p": f"{PREFIX}%"})


@pytest.fixture(scope="module")
def world():
    _sweep()
    eng = _engine()
    with eng.connect() as c:
        baseline = c.execute(text(
            "SELECT llm_model_override FROM public.tenant_agent_settings WHERE tenant_id = :t"),
            {"t": TENANT}).scalar()
        c.execute(text(
            "INSERT INTO public.llm_models (model_id, display_name, status, input_usd_per_mtok, "
            "output_usd_per_mtok, forced_tool_choice) VALUES "
            "(:u, 'D-504 unknown', 'active', 1.0, 5.0, NULL), "
            "(:n, 'D-504 no-force', 'active', 1.0, 5.0, FALSE)"), {"u": UNKNOWN, "n": NOFORCE})
    from primeqa.intelligence.llm import router
    router.selectable_model_ids(refresh=True)
    yield {"baseline": baseline}
    with eng.connect() as c:
        c.execute(text("UPDATE public.tenant_agent_settings SET llm_model_override = :m WHERE tenant_id = :t"),
                  {"m": baseline, "t": TENANT})
    _sweep()
    router.selectable_model_ids(refresh=True)
    with eng.connect() as c:
        assert c.execute(text("SELECT count(*) FROM public.llm_models WHERE model_id LIKE :p"),
                         {"p": f"{PREFIX}%"}).scalar() == 0
        assert c.execute(text("SELECT count(*) FROM public.activity_log WHERE details::text LIKE :p"),
                         {"p": f"%{PREFIX}%"}).scalar() == 0


def _override_and_audit_count():
    with _engine().connect() as c:
        ov = c.execute(text("SELECT llm_model_override FROM public.tenant_agent_settings WHERE tenant_id = :t"),
                       {"t": TENANT}).scalar()
        n = c.execute(text("SELECT count(*) FROM public.activity_log WHERE entity_type = "
                           "'tenant_llm_model_override' AND details::text LIKE :p"),
                      {"p": f"%{PREFIX}%"}).scalar()
    return ov, n


def _tier():
    with _engine().connect() as c:
        return c.execute(text("SELECT llm_tier FROM public.tenant_agent_settings WHERE tenant_id = :t"),
                         {"t": TENANT}).scalar() or "starter"


# ---- the picker -------------------------------------------------------------------

def test_picker_refuses_an_unknown_capability_model(world):
    c, h = _posting_client(SUPER, "superadmin")
    before = _override_and_audit_count()
    r = c.post(f"/settings/tenant-tier/{TENANT}",
               data={"llm_tier": _tier(), "llm_model_override": UNKNOWN}, headers=h)
    assert r.status_code in (302, 303)
    msg = _flash(c, "/settings/llm-usage")
    assert UNKNOWN in msg and "Refresh models" in msg, msg
    assert _override_and_audit_count() == before          # no write, no audit row


def test_picker_saves_a_driven_by_instruction_model_and_audits(world):
    c, h = _posting_client(SUPER, "superadmin")
    _, audits_before = _override_and_audit_count()
    r = c.post(f"/settings/tenant-tier/{TENANT}",
               data={"llm_tier": _tier(), "llm_model_override": NOFORCE}, headers=h)
    assert r.status_code in (302, 303)
    ov, audits = _override_and_audit_count()
    assert ov == NOFORCE
    assert audits == audits_before + 1


# ---- the Models panel -------------------------------------------------------------

def test_panel_renders_the_fact_per_row(world):
    c, _ = _posting_client(SUPER, "superadmin")
    html = c.get("/settings/llm-usage").get_data(as_text=True)
    assert re.search(rf'data-testid="forced-tool-choice-{re.escape(UNKNOWN)}"[^<]*<span[^>]*>unknown', html)
    assert re.search(rf'data-testid="forced-tool-choice-{re.escape(NOFORCE)}"[^<]*<span[^>]*>no<', html)
    assert "driven by instruction" in html
    # A built-in model reads yes from the code table.
    from primeqa.intelligence.llm.router import SONNET_5
    assert re.search(rf'data-testid="forced-tool-choice-{re.escape(SONNET_5)}"[^<]*<span[^>]*>yes', html)


def test_panel_names_tenants_pinned_to_an_unknown_model(world):
    # The picker refuses such a pin, so the state can arise only from an
    # earlier pin whose row lost its fact — planted directly.
    with _engine().connect() as c:
        c.execute(text("UPDATE public.tenant_agent_settings SET llm_model_override = :m WHERE tenant_id = :t"),
                  {"m": UNKNOWN, "t": TENANT})
    try:
        c, _ = _posting_client(SUPER, "superadmin")
        html = c.get("/settings/llm-usage").get_data(as_text=True)
        band = re.search(r'data-testid="unknown-pinned".*?</div>', html, re.S)
        assert band and UNKNOWN in band.group(0) and f"tenant #{TENANT}" in band.group(0)
    finally:
        with _engine().connect() as c:
            c.execute(text("UPDATE public.tenant_agent_settings SET llm_model_override = :m WHERE tenant_id = :t"),
                      {"m": world["baseline"], "t": TENANT})


# ---- enable: the fact is written with the row; no fact, no row ---------------------

def test_enable_writes_the_probed_fact(world, monkeypatch):
    from primeqa.intelligence.llm import catalog
    monkeypatch.setattr(catalog, "probe_forced_tool_choice", lambda key, mid, **kw: False)
    mid = PREFIX + "enabled"
    c, h = _posting_client(SUPER, "superadmin")
    r = c.post("/settings/llm-models/enable",
               data={"model_id": mid, "display_name": "D-504 enabled",
                     "input_usd_per_mtok": "2", "output_usd_per_mtok": "10"}, headers=h)
    assert r.status_code in (302, 303)
    with _engine().connect() as cn:
        row = cn.execute(text("SELECT status, forced_tool_choice, forced_tool_choice_probed_at "
                              "FROM public.llm_models WHERE model_id = :m"), {"m": mid}).first()
        audit = cn.execute(text("SELECT details FROM public.activity_log WHERE entity_type = "
                                "'llm_model_enabled' AND details::text LIKE :p ORDER BY id DESC LIMIT 1"),
                           {"p": f"%{mid}%"}).scalar()
    assert row is not None and row[0] == "active" and row[1] is False and row[2] is not None
    assert audit["forced_tool_choice"] is False


def test_enable_without_a_fact_writes_nothing(world):
    # Through the REAL route: scratch has no LLM connection, so there is no key
    # to probe with — the enable is refused and no row exists.
    mid = PREFIX + "refused"
    c, h = _posting_client(SUPER, "superadmin")
    r = c.post("/settings/llm-models/enable",
               data={"model_id": mid, "input_usd_per_mtok": "2", "output_usd_per_mtok": "10"}, headers=h)
    assert r.status_code in (302, 303)
    msg = _flash(c, "/settings/llm-usage")
    assert "Nothing was written" in msg, msg
    with _engine().connect() as cn:
        assert cn.execute(text("SELECT count(*) FROM public.llm_models WHERE model_id = :m"),
                          {"m": mid}).scalar() == 0


# ---- refresh: NULL active rows are probed; a failure leaves NULL -------------------

def test_refresh_probes_unknown_active_rows(world, monkeypatch):
    from primeqa.intelligence.llm import catalog
    mid_ok = PREFIX + "refresh-ok"
    mid_bad = PREFIX + "refresh-bad"
    with _engine().connect() as cn:
        cn.execute(text(
            "INSERT INTO public.llm_models (model_id, status, input_usd_per_mtok, output_usd_per_mtok) "
            "VALUES (:a, 'active', 1.0, 5.0), (:b, 'active', 1.0, 5.0)"), {"a": mid_ok, "b": mid_bad})
    # Upstream still lists every selectable id (nothing retires).
    from primeqa.intelligence.llm import router
    upstream = [{"id": m, "display_name": m} for m in sorted(router.selectable_model_ids(refresh=True))]
    monkeypatch.setattr(catalog, "fetch_upstream_models", lambda key: upstream)

    def probe(mid):
        if mid == mid_bad:
            raise catalog.CatalogProbeError("transport")
        return mid != UNKNOWN            # the planted unknown row becomes False

    result = catalog.refresh_model_catalog("k", actor_user_id=SUPER, actor_tenant_id=TENANT, probe=probe)
    assert result.probed.get(mid_ok) is True
    assert result.probed.get(UNKNOWN) is False
    assert mid_bad in result.probe_failed
    with _engine().connect() as cn:
        facts = dict(cn.execute(text(
            "SELECT model_id, forced_tool_choice FROM public.llm_models WHERE model_id LIKE :p"),
            {"p": f"{PREFIX}%"}).fetchall())
    assert facts[mid_ok] is True and facts[UNKNOWN] is False and facts[mid_bad] is None
    assert facts[NOFORCE] is False                       # an existing fact is not re-probed
