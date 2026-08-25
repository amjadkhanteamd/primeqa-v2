"""3A-1 DB-real tests — gated on S5_TEST_DATABASE_URL (a NON-production
scratch DB with migrations 062+063 applied). Skipped entirely otherwise;
the merge gate is the pure suite in tests/unit/test_s5_registry_gate.py.
"""
from __future__ import annotations

import json
import os

import pytest

S5_DB = os.environ.get("S5_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not S5_DB, reason="set S5_TEST_DATABASE_URL (non-prod, 062+063 applied)"),
]

SUPER = {"actor_user_id": 7, "actor_tenant_id": 1, "actor_role": "superadmin"}


@pytest.fixture()
def session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    eng = create_engine(S5_DB, pool_pre_ping=True)
    s = Session(bind=eng)
    yield s
    s.rollback()
    # cleanup any test-created rules (PLM-A11Y-9xx test range)
    from sqlalchemy import text
    for tbl in ("s5_catalogue_release_members", "s5_standard_maps",
                "s5_engine_bindings", "s5_rule_versions"):
        s.execute(text(f"DELETE FROM {tbl} WHERE rule_id LIKE 'PLM-A11Y-9%'"))
    s.execute(text("DELETE FROM s5_rules WHERE rule_id LIKE 'PLM-A11Y-9%'"))
    s.commit()
    s.close()


def test_seed_counts_and_acc05_autoset(session):
    from sqlalchemy import text
    n_rules = session.execute(text(
        "SELECT count(*) FROM s5_rules")).scalar()
    n_active = session.execute(text(
        "SELECT count(*) FROM s5_rule_versions WHERE state='ACTIVE'")).scalar()
    n_auto = session.execute(text(
        "SELECT count(*) FROM s5_rule_versions "
        "WHERE state='ACTIVE' AND automation_capability='AUTO'")).scalar()
    assert n_rules >= 60 and n_active == n_rules
    # ACC-05 mapping (LLD §3): the automated list == the seeded AUTO set
    assert n_auto == n_active


def test_lifecycle_legal_chain_with_actor_recorded(session):
    from sqlalchemy import text
    from primeqa.knowledge import rule_lifecycle as lc
    lc.create_rule(session, rule_id="PLM-A11Y-901", name="t", description="t",
                   automation_capability="AUTO", human_review_required=False,
                   **SUPER)
    for st in ("REVIEW", "APPROVED", "VERSIONED", "ACTIVE"):
        lc.transition(session, rule_id="PLM-A11Y-901", version=1,
                      to_state=st, **SUPER)
    row = session.execute(text(
        "SELECT state, created_by, state_changed_by FROM s5_rule_versions "
        "WHERE rule_id='PLM-A11Y-901' AND version=1")).fetchone()
    assert row[0] == "ACTIVE" and row[1] == 7 and row[2] == 7   # real actor


def test_illegal_transition_refused(session):
    from primeqa.knowledge import rule_lifecycle as lc
    lc.create_rule(session, rule_id="PLM-A11Y-902", name="t", description="t",
                   automation_capability="AUTO", human_review_required=False,
                   **SUPER)
    with pytest.raises(lc.LifecycleError) as ei:
        lc.transition(session, rule_id="PLM-A11Y-902", version=1,
                      to_state="ACTIVE", **SUPER)          # DRAFT -/-> ACTIVE
    assert "illegal transition" in str(ei.value)


def test_single_active_enforced_at_db_level(session):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    from primeqa.knowledge import rule_lifecycle as lc
    lc.create_rule(session, rule_id="PLM-A11Y-903", name="t", description="t",
                   automation_capability="AUTO", human_review_required=False,
                   **SUPER)
    for st in ("REVIEW", "APPROVED", "VERSIONED", "ACTIVE"):
        lc.transition(session, rule_id="PLM-A11Y-903", version=1,
                      to_state=st, **SUPER)
    # bypass the service and try to write a second ACTIVE row raw: the
    # partial unique index must reject it
    with pytest.raises(IntegrityError):
        session.execute(text("""
            INSERT INTO s5_rule_versions (rule_id, version, name, description,
                automation_capability, human_review_required, state)
            VALUES ('PLM-A11Y-903', 2, 't', 't', 'AUTO', FALSE, 'ACTIVE')
        """))
    session.rollback()


def test_activation_retires_predecessor_atomically(session):
    from sqlalchemy import text
    from primeqa.knowledge import rule_lifecycle as lc
    lc.create_rule(session, rule_id="PLM-A11Y-904", name="t", description="t",
                   automation_capability="AUTO", human_review_required=False,
                   **SUPER)
    for st in ("REVIEW", "APPROVED", "VERSIONED", "ACTIVE"):
        lc.transition(session, rule_id="PLM-A11Y-904", version=1, to_state=st, **SUPER)
    lc.new_draft_version(session, rule_id="PLM-A11Y-904", name="t2",
                         description="t2", automation_capability="AUTO",
                         human_review_required=False, **SUPER)
    for st in ("REVIEW", "APPROVED", "VERSIONED", "ACTIVE"):
        lc.transition(session, rule_id="PLM-A11Y-904", version=2, to_state=st, **SUPER)
    rows = dict(session.execute(text(
        "SELECT version, state FROM s5_rule_versions "
        "WHERE rule_id='PLM-A11Y-904'")).fetchall())
    assert rows == {1: "RETIRED", 2: "ACTIVE"}


def test_registry_reads_and_unmapped_surfacing(session):
    from primeqa.knowledge import rule_registry as rr
    rules = rr.active_rules_for_profile(session, "WCAG22")
    assert len(rules) >= 60
    assert all(r.criteria for r in rules)
    one = rr.rule(session, rules[0].rule_id)
    assert one is not None and one.state == "ACTIVE"
    bindings = rr.bindings_for_engine(session, "axe-core", "4.13.0")
    assert "image-alt" in bindings                       # a known axe rule
    out = rr.resolve_engine_rules(session, "axe-core", "4.13.0",
                                  ["image-alt", "totally-fake-engine-rule"])
    assert "image-alt" in out["mapped"]
    assert out["unmapped"] == ["totally-fake-engine-rule"]   # surfaced, not dropped


def test_release_membership_recorded(session):
    from primeqa.knowledge import rule_registry as rr
    rel = rr.release(session, 1)                         # the seed release
    assert rel is not None and len(rel["members"]) >= 60
    assert len(rel["content_hash"]) == 64
