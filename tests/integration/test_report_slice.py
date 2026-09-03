"""Report-slice bridge tests — gated on S3A3_TEST_DATABASE_URL (scratch
with P-1 + B-1 + the Phase 5 state). READ-ONLY by construction: the
bridge computes nothing and persists nothing; these tests assert the
reads against the known scratch facts (B-1's decomposition, the stored
P-1→B-1 comparison, the Part 1 denominators, the Part 3 refusals)."""
from __future__ import annotations

import os

import pytest

DB = os.environ.get("S3A3_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DB, reason="set S3A3_TEST_DATABASE_URL"),
]
if DB:
    # the bridge resolves tenants through the S1 connection singleton,
    # which binds DATABASE_URL on first use — point it at scratch BEFORE
    # any bridge call. If something already bound it elsewhere, skip
    # loudly rather than read the wrong database.
    os.environ.setdefault("DATABASE_URL", DB)

P1 = "c70fa8e6-888d-4a6f-9087-f18fd8ef3196"
B1 = "471a9c35-13d6-466a-bd7b-38b809f9aac6"


@pytest.fixture(autouse=True)
def _bound_to_scratch():
    from primeqa.semantic import connection as C
    eng = C.get_engine()
    if DB.rsplit("/", 1)[-1] not in str(eng.url):
        pytest.skip("S1 engine already bound to a different database")


def test_a_runs_list_carries_both_recorded_runs():
    from primeqa.intelligence.ui_report_console import list_processing_runs
    out = list_processing_runs(1)
    assert out["available"] is True
    by_id = {r["job_id"]: r for r in out["runs"]}
    # B-1 is recent and must list; P-1 may sit outside the 50-row window
    # on a scratch carrying ~136 planted suite worlds — its presence is
    # asserted through the comparison read (test_d), not this window
    assert B1 in by_id
    b1 = by_id[B1]
    assert b1["auth_mode"] == "vault" and b1["surfaces"] == 2
    assert b1["catalogue_release_id"] == "3"
    assert b1["verdict_counts"] == {"FAIL": 3, "PASS": 66,
                                    "NOT_DETERMINED": 79}
    # newest first
    assert out["runs"][0]["processed_at"] >= out["runs"][-1]["processed_at"]


def test_b_run_report_header_filters_and_pagination():
    from primeqa.intelligence.ui_report_console import run_report
    out = run_report(1, B1, standard="WCAG22")
    assert out["available"] and out["header"] is not None
    h = out["header"]
    assert h["denominator_provenance"] == "ratified_catalogue"
    assert h["denominator_complete"] is True
    assert h["engine_run_set_size"] == 74
    assert out["denominator"] == {"size": 55, "covered": 21,
                                  "complete": True}
    assert set(out["standards"]) >= {"WCAG22", "EN301549", "SECTION508",
                                     "CUSTOM:acme"}
    assert len(out["surfaces"]) == 2
    # FAIL filter: B-1's three FAILs, FAIL-first ordering, titles joined
    fails = run_report(1, B1, standard="WCAG22", verdict="FAIL")
    assert fails["total"] == 3
    assert {v["rule_id"] for v in fails["verdicts"]} == \
        {"PLM-A11Y-030", "PLM-A11Y-071"}
    assert all(v["rule_title"] for v in fails["verdicts"])
    assert all(v["has_evidence"] and v["evidence_state"] == "REFERENCED"
               for v in fails["verdicts"])
    # surface filter halves the run
    one = run_report(1, B1, standard="WCAG22", surface=out["surfaces"][0])
    assert 0 < one["total"] < out["total"]
    # pagination: per_page caps at 50 and pages partition the total
    p1 = run_report(1, B1, standard="WCAG22", page=1)
    p2 = run_report(1, B1, standard="WCAG22", page=2)
    assert len(p1["verdicts"]) == 50 and p1["total"] == out["total"]
    assert {v["test_id"] for v in p1["verdicts"]}.isdisjoint(
        {v["test_id"] for v in p2["verdicts"]})


def test_c_custom_profile_projection_in_the_run_view():
    from primeqa.intelligence.ui_report_console import run_report
    out = run_report(1, B1, standard="CUSTOM:acme")
    assert out["available"]
    assert out["header"]["denominator_provenance"] == "ratified_profile"
    # B-1 predates the custom rules: the projection is honestly empty
    assert out["total"] == 0 and out["verdicts"] == []
    # an unknown profile carries the refusal in header_error, not a 500
    ghost = run_report(1, B1, standard="CUSTOM:ghost")
    assert ghost["available"] and ghost["header"] is None
    assert "no ACTIVE profile set" in ghost["header_error"]


def test_d_comparison_report_reads_the_stored_run_only():
    from primeqa.intelligence.ui_report_console import comparison_report
    out = comparison_report(1, P1, B1)
    assert out["available"] and out["found"]
    assert out["transition_counts"] == {"NEW_CLAIM": 4,
                                        "NOT_COMPARABLE": 142,
                                        "STILL_FAILING": 2}
    assert set(out["tool_drift"]) == {"catalogue_release_id",
                                      "catalogue_content_hash",
                                      "bindings_hash"}
    assert out["env_delta"]["not_captured"] == {"baseline": True,
                                                "candidate": True}
    # NOT_COMPARABLE rows carry their reason, never hidden
    nc = out["groups"]["NOT_COMPARABLE"]
    assert len(nc) == 142
    assert all(r["not_comparable_reason"] for r in nc)
    sf = out["groups"]["STILL_FAILING"]
    assert {r["rule_id"] for r in sf} == {"PLM-A11Y-071"}
    # the unstored direction is an honest empty, not a computation
    rev = comparison_report(1, B1, P1)
    assert rev["available"] and rev["found"] is False
    assert "read-only" in rev["note"]


def test_e_coverage_report_renders_every_standard_with_refusals():
    from primeqa.intelligence.ui_report_console import coverage_report
    out = coverage_report(1, B1)
    assert out["available"]
    by = {s["standard"]: s for s in out["standards"]}
    assert set(by) >= {"WCAG22", "EN301549", "SECTION508", "CUSTOM:acme"}
    assert by["WCAG22"]["denominator"] == {"size": 55, "covered": 21,
                                           "complete": True}
    assert len(by["WCAG22"]["not_covered"]) == 34
    assert by["EN301549"]["denominator"]["size"] == 50
    assert by["SECTION508"]["denominator"]["size"] == 38
    for s in ("WCAG22", "CUSTOM:acme"):
        ref = by[s]["refusals"]
        assert ref["available"] and ref["count"] >= 1
        assert any(r["refusal_class"] == "needs_capability_not_captured"
                   for r in ref["rows"])
    acme = by["CUSTOM:acme"]
    assert acme["denominator"]["size"] == 3
    # data-driven: uncovered = ratified headings minus the profiles the
    # ACTIVE custom rules on THIS scratch actually map (Part 2's probe
    # suite legitimately activates rules beyond PLM-CUST-00001)
    from sqlalchemy import create_engine, text
    eng = create_engine(DB, connect_args={
        "options": "-csearch_path=tenant_1,public -capp.tenant_id=1"})
    with eng.connect() as c0:
        covered = {r[0] for r in c0.execute(text(
            "SELECT DISTINCT definition->'criterion'->>'profile' "
            "FROM cust_rule_versions WHERE state='ACTIVE'"))}
    expected = {"Brand/Targets", "Brand/Labels", "Brand/Contrast"} - covered
    assert {c["criterion"] for c in acme["not_covered"]} == expected
    assert "Brand/Contrast" in expected            # never covered by any probe


def test_f_evidence_links_degrade_honestly_without_the_store():
    """The web tier may not carry the store's credentials; the read then
    says so instead of 500ing — and when the store IS configured, the
    signing path is evidence.sign_url, whose tenant-scope refusal is
    covered by the browser_worker suites."""
    from primeqa.intelligence.ui_report_console import evidence_links
    surface = ("orgfarm-4399654d2d-dev-ed.develop.my.site.com"
               "|/s|customer|-|-")
    out = evidence_links(1, B1, surface)
    assert out["available"] and out["found"]
    if out["links"]:
        assert {l["kind"] for l in out["links"]} == {"screenshot",
                                                     "observation"}
        assert all(l["url"].startswith("http") for l in out["links"])
    else:
        assert "not configured" in out["note"]
    missing = evidence_links(1, B1, "no|such|surface|-|-")
    assert missing["available"] and missing["found"] is False


def test_f2_bridge_sessions_carry_the_de19_tenant_stamp():
    """Regression (found live 2026-09-03): sign_url derives the tenant
    key prefix from session.info['tenant_schema'] (DE-19) and REFUSES a
    session without it. The bridge's sessions must carry the same stamp
    the queue's session factory applies — without it, evidence links
    could never mint on ANY tier once the store was configured (the
    prod symptom: every mint died 'session carries no tenant_schema'
    into the available:false catch)."""
    from primeqa.browser_worker.evidence import key_prefix
    from primeqa.intelligence.ui_report_console import _best_effort

    seen = {}

    def probe(session):
        seen["info"] = dict(session.info)
        seen["prefix"] = key_prefix(session)   # raises pre-fix
        return {}

    out = _best_effort(1, probe, "de19_stamp_probe")
    assert out["available"] is True
    assert seen["info"]["tenant_schema"] == "tenant_1"
    assert seen["info"]["tenant_id"] == 1
    assert seen["prefix"] == "tenant_1"


def test_g_the_bridge_never_logs_a_minted_url():
    """Structural: no logging call in the bridge takes a URL argument —
    the bearer rule's 'never logged' leg, checkable."""
    import ast
    import inspect

    from primeqa.intelligence import ui_report_console as M
    tree = ast.parse(inspect.getsource(M))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if (isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "log"):
                src = ast.unparse(node)
                assert "url" not in src.lower(), f"log call touches a URL: {src}"
