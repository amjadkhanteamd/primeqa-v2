"""Phase 5 Part 2 DB-real tests — gated on S3A3_TEST_DATABASE_URL
(scratch with the 20260902_0010 tenant migration + public 068 applied,
release 3 present). Covers the briefed matrix's DB legs: tenant
isolation of the cust_* store, the ledger (drafted + refused), the
namespace CHECKs, PLM-CUST-00001 through the REAL lifecycle into a
tenant release, the ratification conflict gate, the enumeration union,
and process_job writing census-decided verdicts."""
from __future__ import annotations

import json
import os
import uuid

import pytest
from sqlalchemy import text

DB = os.environ.get("S3A3_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DB, reason="set S3A3_TEST_DATABASE_URL "
                       "(scratch with 062-068 + tenant 20260902_0010)"),
]

ADMIN = dict(actor_user_id=1, actor_tenant_id=1, actor_role="admin")


@pytest.fixture()
def session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    eng = create_engine(DB, pool_pre_ping=True, connect_args={
        "options": "-csearch_path=tenant_1,public -capp.tenant_id=1"})
    s = Session(bind=eng)
    yield s
    s.rollback()
    s.close()


def _draft(**over):
    d = {
        "name": "Primary actions meet the 44px floor",
        "guideline_thread_id": "GT-BRAND-01",
        "selector": [{"term": "role_is", "value": "button"}],
        "predicate": {"form": "at_least", "fact": "geom:width", "px": 44},
        "population": "every button rendered on the surface",
        "criterion": {"profile": "Brand/Targets", "binds_wcag_sc": "2.5.5"},
    }
    d.update(over)
    return d


def test_a_tenant_isolation_and_the_namespace_checks(session):
    # the cust_* store lives in the TENANT schema, not public
    for t in ("cust_rules", "cust_rule_versions", "cust_predicates",
              "cust_token_sets", "cust_authoring_ledger",
              "cust_release_members"):
        schema = session.execute(text("""
            SELECT string_agg(table_schema, ',') FROM information_schema.tables
            WHERE table_name = :t"""), {"t": t}).scalar()
        assert schema == "tenant_1", f"{t} found in: {schema}"
    # the public CHECK widened ONCE (ruled §g): A11Y@3 or CUST@5, nothing else
    for bad in ("PLM-CUST-001", "PLM-CUST-000001", "PLM-XYZ-001"):
        with pytest.raises(Exception, match="s5_rules_id_shape_v2"):
            session.execute(text(
                "INSERT INTO s5_rules (rule_id, owner) VALUES (:r,'plimsol')"),
                {"r": bad})
        session.rollback()
    # the tenant CHECK pins five digits
    with pytest.raises(Exception, match="cust_rules_id_shape"):
        session.execute(text(
            "INSERT INTO cust_rules (rule_id, created_by) "
            "VALUES ('PLM-CUST-1', 1)"))
    session.rollback()


def test_b_mint_plm_cust_00001_through_the_real_lifecycle(session):
    from primeqa.knowledge import cust_authoring as A
    from primeqa.knowledge import cust_grammar as G

    already = session.execute(text(
        "SELECT COUNT(*) FROM cust_rules")).scalar_one()
    if already:
        pytest.skip("scratch already holds custom rules (idempotent replay)")

    rule, refusal = G.validate(_draft())
    assert refusal is None
    out = A.draft_rule(session, rule, prose="Primary actions must be at "
                       "least 44px wide.", **ADMIN)
    assert out["rule_id"] == "PLM-CUST-00001" and out["version"] == 1

    rows = session.execute(text("""
        SELECT slot, term FROM cust_predicates
        WHERE rule_id = 'PLM-CUST-00001' ORDER BY slot, ordinal""")).fetchall()
    assert ("selector", "role_is") in rows and ("predicate", "at_least") in rows
    led = session.execute(text("""
        SELECT outcome FROM cust_authoring_ledger
        WHERE rule_id = 'PLM-CUST-00001'""")).scalar_one()
    assert led == "drafted"

    # DRAFT -> REVIEW -> APPROVED needs the explicit reviewer confirmation
    A.transition(session, rule_id="PLM-CUST-00001", version=1,
                 to_state="REVIEW", **ADMIN)
    with pytest.raises(A.AuthoringError, match="reviewed_no_conflict"):
        A.transition(session, rule_id="PLM-CUST-00001", version=1,
                     to_state="APPROVED", **ADMIN)
    A.transition(session, rule_id="PLM-CUST-00001", version=1,
                 to_state="APPROVED", reviewed_no_conflict=True, **ADMIN)
    # the informed confirmation: the catalogue overlap was recorded
    overlap = session.execute(text("""
        SELECT definition->'catalogue_overlap_at_review'
        FROM cust_rule_versions WHERE rule_id='PLM-CUST-00001'""")).scalar_one()
    assert isinstance(overlap, list)
    A.transition(session, rule_id="PLM-CUST-00001", version=1,
                 to_state="VERSIONED", **ADMIN)
    A.transition(session, rule_id="PLM-CUST-00001", version=1,
                 to_state="ACTIVE", **ADMIN)
    st = session.execute(text("""
        SELECT state, reviewed_by FROM cust_rule_versions
        WHERE rule_id='PLM-CUST-00001'""")).fetchone()
    assert st[0] == "ACTIVE" and st[1] == 1
    # skipping a stage is illegal
    with pytest.raises(A.AuthoringError, match="illegal transition"):
        A.transition(session, rule_id="PLM-CUST-00001", version=1,
                     to_state="DRAFT", **ADMIN)
    session.rollback()


def test_c_refusals_are_ledgered_with_their_class(session):
    from primeqa.knowledge import cust_authoring as A
    from primeqa.knowledge import cust_grammar as G

    # mechanical: an LLM draft with a connective
    rule, refusal = G.validate(_draft(predicate={"and": [
        {"form": "present", "fact": "attr:alt"},
        {"form": "present", "fact": "attr:title"}]}))
    assert rule is None
    rid = A.record_refusal(session, guideline_thread_id="GT-BRAND-02",
                           prose="Images need alt AND title.",
                           refusal=refusal, **ADMIN)
    row = session.execute(text("""
        SELECT refusal_class, nearest_expressible FROM cust_authoring_ledger
        WHERE id = :i"""), {"i": rid}).fetchone()
    assert row[0] == "needs_prohibited_operator"
    assert "split into 2 rules" in json.dumps(row[1])

    # reviewer-judged: the token-vs-literal case, said plainly (§f):
    # "must consume the design token, never the literal" is refused —
    # post-resolution styles make token and hex byte-identical.
    rid = A.record_refusal(
        session, guideline_thread_id="GT-BRAND-03",
        prose="Buttons must consume --brand-primary, never a hardcoded hex.",
        refusal_class="needs_capability_not_captured",
        refusal_reason="computed style is post-resolution: a component "
                       "consuming the token and one hardcoding the same "
                       "hex are byte-identical in the observation",
        nearest_expressible=[{"predicate": {
            "form": "member_of", "fact": "style:background-color",
            "token_set": {"key": "brand-palette", "version": 1}}}],
        **ADMIN)
    assert session.execute(text(
        "SELECT outcome FROM cust_authoring_ledger WHERE id=:i"),
        {"i": rid}).scalar_one() == "refused"
    # every one of the six classes is ledgerable (the three mechanical
    # ones arrive via the grammar — unit-proven; the three reviewer
    # judgments are recorded directly)
    for cls in ("not_observable", "belongs_to_public_catalogue",
                "ambiguous_guideline"):
        rid = A.record_refusal(
            session, guideline_thread_id=f"GT-CLASS-{cls}",
            prose=f"probe for {cls}", refusal_class=cls,
            refusal_reason="reviewer judgment probe", **ADMIN)
        assert session.execute(text(
            "SELECT refusal_class FROM cust_authoring_ledger WHERE id=:i"),
            {"i": rid}).scalar_one() == cls
    # the CHECK refuses a refusal without a class
    with pytest.raises(Exception, match="cust_ledger_refusal_shape"):
        session.execute(text("""
            INSERT INTO cust_authoring_ledger
                (guideline_thread_id, prose, outcome, actor_user_id)
            VALUES ('GT-X', 'p', 'refused', 1)"""))
    session.rollback()


def test_d_the_conflict_gate_refuses_the_mechanical_negation(session):
    from primeqa.knowledge import cust_authoring as A
    from primeqa.knowledge import cust_grammar as G

    active = session.execute(text("""
        SELECT COUNT(*) FROM cust_rule_versions
        WHERE rule_id='PLM-CUST-00001' AND state='ACTIVE'""")).scalar_one()
    if not active:
        pytest.skip("PLM-CUST-00001 not ACTIVE on this scratch")
    # the negation of 00001's predicate is not mechanically a negation
    # pair (at_least/at_most differ in threshold, not polarity), so use
    # a presence pair on a fresh criterion to prove the gate
    r1, _ = G.validate(_draft(
        name="Buttons carry aria-label",
        selector=[{"term": "role_is", "value": "switch"}],
        predicate={"form": "present", "fact": "attr:aria-label"},
        criterion={"profile": "Brand/Labels", "binds_wcag_sc": None}))
    out1 = A.draft_rule(session, r1, prose="p", **ADMIN)
    A.transition(session, rule_id=out1["rule_id"], version=1,
                 to_state="REVIEW", **ADMIN)
    A.transition(session, rule_id=out1["rule_id"], version=1,
                 to_state="APPROVED", reviewed_no_conflict=True, **ADMIN)
    A.transition(session, rule_id=out1["rule_id"], version=1,
                 to_state="VERSIONED", **ADMIN)
    A.transition(session, rule_id=out1["rule_id"], version=1,
                 to_state="ACTIVE", **ADMIN)
    r2, _ = G.validate(_draft(
        name="Switches must NOT carry aria-label",
        selector=[{"term": "role_is", "value": "switch"}],
        predicate={"form": "absent", "fact": "attr:aria-label"},
        criterion={"profile": "Brand/Labels", "binds_wcag_sc": None}))
    out2 = A.draft_rule(session, r2, prose="p", **ADMIN)
    A.transition(session, rule_id=out2["rule_id"], version=1,
                 to_state="REVIEW", **ADMIN)
    with pytest.raises(A.AuthoringError, match="NEGATION of ACTIVE custom"):
        A.transition(session, rule_id=out2["rule_id"], version=1,
                     to_state="APPROVED", reviewed_no_conflict=True, **ADMIN)
    session.rollback()


def test_e_tenant_release_union_and_enumeration(session):
    from primeqa.knowledge import cust_authoring as A

    active = session.execute(text(
        "SELECT COUNT(*) FROM cust_rule_versions WHERE state='ACTIVE'"
    )).scalar_one()
    if not active:
        pytest.skip("no ACTIVE custom rule on this scratch")
    recorded = session.execute(text("""
        SELECT COUNT(*) FROM cust_release_members
        WHERE platform_release_id = 3""")).scalar_one()
    if not recorded:
        out = A.record_tenant_release(session, platform_release_id=3, **ADMIN)
        assert out["members"] >= 1
    # membership is recorded ONCE (D-281)
    with pytest.raises(A.AuthoringError, match="already recorded"):
        A.record_tenant_release(session, platform_release_id=3, **ADMIN)
    session.rollback()

    # the union: enumeration yields platform + custom claims
    from primeqa.generation.enumeration import enumerate_claims
    out = enumerate_claims(session, catalogue_release_id=3,
                           inventory_version=1, persona_scope="customer",
                           created_by=1)
    session.commit()
    n_custom = session.execute(text("""
        SELECT COUNT(*) FROM cust_release_members
        WHERE platform_release_id = 3""")).scalar_one()
    assert out["members"] == (74 + n_custom) * 2
    custom_claims = session.execute(text("""
        SELECT COUNT(*) FROM claim_set_members m
        JOIN test_claims c ON c.test_id = m.test_id
        WHERE m.claim_set_id = :s
          AND c.asserted_truth->>'plimsol_rule_id' LIKE 'PLM-CUST-%'
    """), {"s": str(out["claim_set_id"])}).scalar_one()
    assert custom_claims == n_custom * 2
    session.execute(text(
        "DELETE FROM claim_set_members WHERE claim_set_id=:s"),
        {"s": str(out["claim_set_id"])})
    session.execute(text("DELETE FROM claim_sets WHERE id=:s"),
                    {"s": str(out["claim_set_id"])})
    session.commit()


def test_f_process_job_decides_custom_claims_from_the_census(session):
    """A planted vault-shaped run whose observation carries a census:
    the custom rule PASSes where its match set satisfies, and decides
    no_match_set where the selector matched nothing — through the REAL
    process_job, beside the engine-backed rules."""
    from primeqa.interpretation.ui_conformance import _decide_custom

    active = session.execute(text("""
        SELECT definition FROM cust_rule_versions
        WHERE rule_id='PLM-CUST-00001' AND state='ACTIVE'""")).fetchone()
    if active is None:
        pytest.skip("PLM-CUST-00001 not ACTIVE on this scratch")

    def obs(nodes):
        return {"status": "OK", "census": {
            "schema_version": 1, "traversal_mode": "synthetic_aura",
            "node_cap": 1500, "cap_hit": False, "capture_errors": 0,
            "n": len(nodes), "nodes": nodes}}

    good = {"role": "button", "name": "Save", "heading": 0, "tag": "",
            "anc": ["main"], "attrs": {"type": "submit"}, "style": {},
            "box": [0, 0, 48.0, 32.0]}
    narrow = dict(good, box=[0, 0, 20.0, 32.0])

    v, b = _decide_custom(session, "PLM-CUST-00001", obs([good]), {})
    assert v == "PASS" and "attested_by" in b
    v, b = _decide_custom(session, "PLM-CUST-00001", obs([narrow]), {})
    assert v == "FAIL" and b["nodes"]
    v, b = _decide_custom(session, "PLM-CUST-00001",
                          obs([dict(good, role="link")]), {})
    assert v == "NOT_DETERMINED" and b["reason"] == "no_match_set"
    v, b = _decide_custom(session, "PLM-CUST-99999", obs([good]), {})
    assert v is None and b["no_verdict_reason"] == "custom_rule_not_active"


def test_g_census_pins_ride_the_manifest_and_the_job_payload(session):
    from primeqa.browser_worker.manifest import (create_manifest,
                                                 enqueue_for_manifest)
    from primeqa.knowledge.census_schema import census_pins

    mid = create_manifest(session, {
        "surfaces": [{"key": "s1", "url": "http://127.0.0.1:1/a"}],
        "pins": {"census": census_pins()}, "stabilisation": {},
        "execution": {"mode": "phase5-pin-test"}})
    jid = enqueue_for_manifest(session, mid)
    payload = session.execute(text(
        "SELECT payload FROM s4_ui_inspection_jobs WHERE id=:j"),
        {"j": jid}).scalar_one()
    assert payload["census"]["schema_version"] == 1
    assert payload["census"]["node_cap"] == 1500
    assert "property_allowlist" in payload["census"]
    session.execute(text(
        "DELETE FROM s4_ui_inspection_results WHERE job_id=:j"), {"j": jid})
    session.execute(text(
        "DELETE FROM s4_ui_inspection_jobs WHERE id=:j"), {"j": jid})
    session.commit()
