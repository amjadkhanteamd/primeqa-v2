"""Step 2 (§c) DB-real on SCRATCH — gated on S3A3_TEST_DATABASE_URL (tenant_1
at 20260909_0010). The release SCOPE's readiness through the real tenant
connection: a never-run claim names itself; a stamped CURRENT run clears it;
``readiness_for_pairs`` (the list/detail pages' read) sees the same states.
Every row this suite plants is removed at teardown.
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

DB = os.environ.get("S3A3_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DB, reason="set S3A3_TEST_DATABASE_URL (scratch, tenant_1 at 20260909_0010)"),
]
TENANT = 1
# Step 3 §d (the release-scope interim): the scope enumerates ACTIVE environments
# only — 5901 is scratch's one active environment; an id with no environments row
# is excluded by design now (test_step_3_scope.py proves the filter itself).
ENV = 5901
MARK = "s2scope"


def _conn():
    from primeqa.semantic.connection import get_tenant_connection
    return get_tenant_connection(TENANT)


@pytest.fixture(scope="module")
def world():
    from primeqa import db as dbm
    if os.environ.get("DATABASE_URL", "") != DB:
        pytest.skip("DATABASE_URL must point at the scratch DB for this suite")
    if getattr(dbm, "engine", None) is None:
        dbm.init_db(DB)
    from primeqa.test_representation import SemanticTransactionCoordinator
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
    from test_representation._fixtures import empty_conditions, make_value_claim
    key = f"{MARK}-{uuid4().hex[:6]}"
    with _conn() as conn:
        conn.execute(text("SELECT set_config('app.tenant_id', '1', false)"))
        org = conn.execute(text(
            "INSERT INTO connected_orgs (org_type, sf_instance_url, label, environment_id) "
            "VALUES ('sandbox', 'https://s2.example', :l, :e) RETURNING CAST(id AS text)"),
            {"l": f"{MARK}-org", "e": ENV}).scalar()
        seq = conn.execute(text(
            "INSERT INTO logical_versions (version_name, version_type, connected_org_id) "
            "VALUES (:n, 'sync_run', CAST(:o AS uuid)) RETURNING version_seq"),
            {"n": f"{MARK}-v", "o": org}).scalar()
        s = Session(bind=conn)
        try:
            coord = SemanticTransactionCoordinator()
            claims = []
            for _ in range(2):
                cr = coord.write_claim(s, actor="s3", test_id=None, archetype="data_behavior",
                                       claim_kind="value-claim",
                                       asserted_truth=make_value_claim(value="Tech"),
                                       semantic_conditions=empty_conditions())
                coord.link_requirement(s, actor="s3", test_id=cr.test_id, external_system="jira",
                                       external_key=key, link_kind="generated_from")
                coord.promote_claim_to_approved(s, actor="human", test_id=cr.test_id,
                                                version_seq=cr.version_seq)
                claims.append((str(cr.test_id), cr.version_seq))
            s.commit()
        finally:
            s.close()
    yield {"key": key, "org": org, "seq": seq, "claims": claims}
    ids = [c[0] for c in world_ids(claims)] if False else [c[0] for c in claims]
    with _conn() as conn:
        conn.execute(text("DELETE FROM s4_execution_runs WHERE claim_test_id = ANY(CAST(:i AS uuid[]))"), {"i": ids})
        conn.execute(text("DELETE FROM test_requirement_links WHERE test_id = ANY(CAST(:i AS uuid[]))"), {"i": ids})
        conn.execute(text("DELETE FROM requirement_identities WHERE external_key = :k"), {"k": key})
        conn.execute(text("DELETE FROM test_claim_coverage WHERE claim_test_id = ANY(CAST(:i AS uuid[]))"), {"i": ids})
        conn.execute(text("DELETE FROM test_provenance WHERE claim_test_id = ANY(CAST(:i AS uuid[]))"), {"i": ids})
        conn.execute(text("DELETE FROM test_claims WHERE test_id = ANY(CAST(:i AS uuid[]))"), {"i": ids})
        conn.execute(text("DELETE FROM logical_versions WHERE version_name = :n"), {"n": f"{MARK}-v"})
        conn.execute(text("DELETE FROM connected_orgs WHERE label = :l"), {"l": f"{MARK}-org"})


def world_ids(claims):
    return claims


def _run(claim, *, stamped, seq=None, org=None):
    with _conn() as conn:
        conn.execute(text(
            "INSERT INTO s4_execution_runs (run_id, recipe_id, recipe_version_seq, claim_test_id, "
            "claim_version_seq, environment_id, outcome, started_at, finished_at, evidence, "
            "connected_org_id, org_version_seq) VALUES (CAST(:r AS uuid), CAST(:rec AS uuid), 1, "
            "CAST(:t AS uuid), :cvs, :e, 'passed', now(), now(), '{}'::jsonb, "
            "CAST(:o AS uuid), :s)"),
            {"r": str(uuid4()), "rec": str(uuid4()), "t": claim[0], "cvs": claim[1], "e": ENV,
             "o": (org if stamped else None), "s": (seq if stamped else None)})


def test_scope_names_the_never_run_claims_then_clears_on_a_current_run(world):
    from primeqa.intelligence.substrate_decision import (
        readiness_for_pairs, release_scope_readiness,
    )
    key, org, seq, (c1, c2) = world["key"], world["org"], world["seq"], world["claims"]
    # zero evidence: every claim NEVER_RUN once, environment None
    sc = release_scope_readiness(TENANT, [key])
    assert sc["available"] and sc["claim_count"] == 2 and sc["environments"] == []
    assert sc["non_current"] == 2
    assert {i["state"] for i in sc["items"]} == {"NEVER_RUN"}
    assert all(i["external_keys"] == [key] for i in sc["items"])

    # one claim runs, STAMPED and current → it clears; the other is still named
    _run(c1, stamped=True, seq=seq, org=org)
    sc = release_scope_readiness(TENANT, [key])
    assert sc["environments"] == [ENV] and sc["non_current"] == 1
    by = {i["test_id"]: i for i in sc["items"]}
    assert by[c1[0]]["state"] == "CURRENT" and by[c1[0]]["stamp_seq"] == seq
    assert by[c2[0]]["state"] == "NEVER_RUN" and by[c2[0]]["sentence"] == "No run in this environment."

    # an UNSTAMPED run (the legacy shape) reads CANNOT_DETERMINE with the TA sentence
    _run(c2, stamped=False)
    sc = release_scope_readiness(TENANT, [key])
    assert by_state(sc)[c2[0]] == "CANNOT_DETERMINE"
    item = [i for i in sc["items"] if i["test_id"] == c2[0]][0]
    assert item["reason"] == "unstamped" and item["sentence"].startswith("Freshness unknown")
    assert sc["non_current"] == 1

    # the pages' read agrees
    pairs = readiness_for_pairs(TENANT, [(c1[0], ENV), (c2[0], ENV)])["map"]
    assert pairs[(c1[0], ENV)]["state"] == "CURRENT"
    assert pairs[(c2[0], ENV)]["state"] == "CANNOT_DETERMINE"


def by_state(sc):
    return {i["test_id"]: i["state"] for i in sc["items"]}
