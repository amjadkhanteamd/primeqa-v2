"""Step 3 on SCRATCH (LLD_STEP_3_REQUIREMENT_SURFACE §g items 5, 7 and the
console): the parts that need the public tables the local harness lacks.

Everything runs inside ONE transaction that is rolled back at the end —
the declaration tables refuse DELETE by design, so no fixture may be left
behind; nothing here commits.

  - the approve hook: ``claim_sets.approve_claim_set`` re-materialises the
    ACTIVE declarations on the set's inventory version (add-only);
  - the read console: the card's rows, the substantive count line, the
    per-surface verdict split over a planted processing run, actor names;
  - declare / unlink through the console (the routes' seam);
  - the release-scope interim: ``_environments_with_evidence(tenant_id=)``
    enumerates ACTIVE environments only (an environment id without an
    active row is excluded; flipping the env inactive empties the scope).
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

DB = os.environ.get("S3A3_TEST_DATABASE_URL")
TENANT = 1
ENV = 5901
SITE = "step3-scope.example.my.site.com"

pytestmark = [
    pytest.mark.skipif(not DB, reason="set S3A3_TEST_DATABASE_URL (scratch, tenant_1 at 20260910_0010)"),
]


@pytest.fixture
def tx():
    """One connection, one transaction, rolled back — never committed."""
    engine = create_engine(DB)
    conn = engine.connect()
    trans = conn.begin()
    conn.execute(text(f'SET search_path TO "tenant_{TENANT}", public'))
    conn.execute(text("SELECT set_config('app.tenant_id', '1', false)"))
    session = Session(bind=conn)
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        conn.close()
        engine.dispose()


def _surface(path, **kw):
    from primeqa.test_representation.models.surface import SurfaceNaturalKey
    return SurfaceNaturalKey(site=SITE, path=path, persona_scope="customer", **kw)


def _member(surface, name):
    return {"site": surface.site, "path": surface.path,
            "persona_scope": surface.persona_scope,
            "record_context_ref": surface.record_context_ref,
            "viewport": surface.viewport, "display_name": name}


def _claim(session, coord, rule, surface):
    from primeqa.test_representation.models.claims.ui.conformance_claim import (
        ConformanceClaimBody,
    )
    from primeqa.test_representation.models.conditions import SemanticConditionsBody
    r = coord.write_claim(
        session, actor="s3", test_id=None, archetype="ui",
        claim_kind="conformance-claim",
        asserted_truth=ConformanceClaimBody(plimsol_rule_id=rule, surface=surface),
        semantic_conditions=SemanticConditionsBody())
    return str(r.test_id)


@pytest.fixture
def world(tx):
    from primeqa.test_representation import (
        SemanticTransactionCoordinator, claim_sets, identity,
    )
    from primeqa.test_representation.models.surface import canonical_surface_key
    s = tx
    coord = SemanticTransactionCoordinator()
    org = s.execute(text(
        "INSERT INTO connected_orgs (org_type, sf_instance_url, label, environment_id) "
        "VALUES ('sandbox', 'https://step3-scope.example', 'step3-scope-org', :e) "
        "RETURNING CAST(id AS text)"), {"e": ENV}).scalar()
    A, B = _surface("/s"), _surface("/s/topiccatalog")
    v = claim_sets.create_inventory_version(
        session=s, members=[_member(A, "Portal home"), _member(B, "Topic Catalog")],
        created_by=1, connected_org_id=org)
    a = [_claim(s, coord, f"PLM-A11Y-00{i}", A) for i in (1, 2, 3)]
    b = [_claim(s, coord, f"PLM-A11Y-00{i}", B) for i in (1, 2)]
    key = f"S3S-{uuid4().hex[:6]}"
    identity.establish(s, key, origin="manual", evidence={"test": "step 3 scope"},
                       established_by="test")
    return {"s": s, "coord": coord, "org": org, "v": v, "A": A, "B": B,
            "kA": canonical_surface_key(A), "kB": canonical_surface_key(B),
            "a": a, "b": b, "key": key}


def _draft_set(s, v, members):
    from primeqa.test_representation import claim_sets
    return claim_sets.create_claim_set(
        s, persona_scope="customer", inventory_version=v, catalogue_release_id=1,
        created_by=1, members=[{"test_id": t, "applicability": ap, "executable": True}
                               for t, ap in members])


def test_approve_claim_set_rematerialises_active_declarations(world):
    from primeqa.test_representation import claim_sets, surface_links as sl
    from primeqa.test_representation.coordinator import COVERAGE_LINK_KINDS
    w = world; s = w["s"]
    set1 = _draft_set(s, w["v"], [(t, "APPLICABLE") for t in w["a"][:2] + w["b"]])
    out = claim_sets.approve_claim_set(s, claim_set_id=set1, user_id=1, tenant_id=TENANT)
    assert out["surface_links_rematerialised"] == 0            # nothing declared yet
    res = sl.declare(s, requirement_key=w["key"], surface_key=w["kA"],
                     actor_user_id=1, tenant_id=TENANT)
    assert res.materialised == 2                                # the two A-claims in set1
    # a superseding set adds the third A-claim: approval follows the surface
    set2 = _draft_set(s, w["v"], [(t, "APPLICABLE") for t in w["a"] + w["b"]])
    out = claim_sets.approve_claim_set(s, claim_set_id=set2, user_id=1, tenant_id=TENANT)
    assert out["surface_links_rematerialised"] == 1
    linked = sorted(str(m.test_id) for m in w["coord"].list_tests_by_requirement(
        s, external_system="jira", external_key=w["key"], link_kind=COVERAGE_LINK_KINDS))
    assert linked == sorted(w["a"])
    # the audit rows name the acts
    acts = [r[0] for r in s.execute(text(
        "SELECT action FROM public.activity_log WHERE tenant_id = :t "
        "AND details->>'requirement_key' = :k ORDER BY id"), {"t": TENANT, "k": w["key"]}).fetchall()]
    assert acts == ["s2.surface_link.declare"]


def test_console_reads_rows_counts_and_the_verdict_split(world):
    from primeqa.intelligence.requirement_surface_console import (
        declare_surface, picker_candidates, read_requirement_surfaces, unlink_surface,
    )
    from primeqa.test_representation import claim_sets
    w = world; s = w["s"]
    set1 = _draft_set(s, w["v"], [(w["a"][0], "APPLICABLE"), (w["a"][1], "APPLICABLE"),
                                  (w["a"][2], "HUMAN_REVIEW")] + [(t, "APPLICABLE") for t in w["b"]])
    claim_sets.approve_claim_set(s, claim_set_id=set1, user_id=1, tenant_id=TENANT)
    # before: empty card, picker offers both surfaces
    r0 = read_requirement_surfaces(TENANT, w["key"], session=s)
    assert r0["available"] and r0["declared"] == [] and r0["counts"]["surfaces"] == 0
    assert r0["active_inventory_version"] == w["v"]
    p0 = picker_candidates(TENANT, w["key"], session=s)
    assert sorted(c["surface_key"] for c in p0["candidates"]) == sorted([w["kA"], w["kB"]])
    # declare A through the console (the routes' seam)
    d = declare_surface(TENANT, requirement_key=w["key"], surface_key=w["kA"],
                        user_id=7, session=s)
    assert d["ok"] and d["created"] and d["materialised"] == 3 and d["display_name"] == "Portal home"
    # a planted processing run over the active set: 2 PASS / 1 FAIL on A
    job = str(uuid4()); manifest = str(uuid4())
    s.execute(text("INSERT INTO s4_ui_run_manifests (id, payload) VALUES (CAST(:m AS uuid), '{}'::jsonb)"), {"m": manifest})
    s.execute(text("""
        INSERT INTO s6_ui_processing_runs (job_id, manifest_id, claim_set_id, engine, engine_version,
            bindings_hash, unmapped_engine_ids, surface_statuses, verdict_counts, no_verdict_members)
        VALUES (CAST(:j AS uuid), CAST(:m AS uuid), CAST(:c AS uuid), 'axe', '4.13.0', 'x',
            '[]'::jsonb, '{}'::jsonb, '{"PASS": 2, "FAIL": 1}'::jsonb, '[]'::jsonb)
    """), {"j": job, "m": manifest, "c": str(set1)})
    for tid, verdict in zip(w["a"], ("PASS", "PASS", "FAIL")):
        s.execute(text("""
            INSERT INTO s6_ui_verdicts (id, manifest_id, job_id, surface_key, claim_set_id, test_id,
                plimsol_rule_id, verdict, verdict_basis, ownership, evidence_state_at_write)
            VALUES (CAST(:i AS uuid), CAST(:m AS uuid), CAST(:j AS uuid), :k, CAST(:c AS uuid),
                CAST(:t AS uuid), 'PLM-A11Y-001', :v, '{}'::jsonb, 'CONFIRMED', 'REFERENCED')
        """), {"i": str(uuid4()), "m": manifest, "j": job, "k": w["kA"], "c": str(set1),
               "t": tid, "v": verdict})
    r1 = read_requirement_surfaces(TENANT, w["key"], session=s)
    assert r1["counts"] == {"surfaces": 1, "checks": 3, "failures": 1, "human_review": 1}
    row = r1["declared"][0]
    assert row["display_name"] == "Portal home" and row["source"] == "DECLARED"
    assert row["declared_by"] == 7 and row["declared_by_name"] == "AK Scratch"
    assert row["is_active_inventory"] and row["checks"] == 3 and row["human_review"] == 1
    assert row["split"] == {"pass": 2, "fail": 1, "not_determined": 0} and not row["no_run"]
    assert r1["latest_run"]["job_id"] == job
    # the picker now offers B only
    p1 = picker_candidates(TENANT, w["key"], session=s)
    assert [c["surface_key"] for c in p1["candidates"]] == [w["kB"]]
    # unlink through the console: provenance recorded, the card drops the row
    u = unlink_surface(TENANT, link_id=d["link_id"], requirement_key=w["key"],
                       user_id=7, reason="fixture", session=s)
    assert u["ok"] and u["removed_links"] == 3 and u["kept_links"] == 0
    r2 = read_requirement_surfaces(TENANT, w["key"], session=s)
    assert r2["declared"] == [] and r2["counts"]["surfaces"] == 0
    # a link of another requirement is refused as unknown
    x = unlink_surface(TENANT, link_id=d["link_id"], requirement_key="SOMEONE-ELSE",
                       user_id=7, reason="", session=s)
    assert x == {"ok": False, "reason": "unknown_link",
                 "sentence": "That surface link does not belong to this requirement."}


def test_release_scope_environments_are_active_only(world):
    from primeqa.intelligence.substrate_decision import _environments_with_evidence
    w = world; s = w["s"]
    tids = [w["a"][0]]
    for env in (ENV, 999999):                     # 999999: no environments row at all
        s.execute(text("""
            INSERT INTO s4_execution_runs (run_id, recipe_id, recipe_version_seq, claim_test_id,
                claim_version_seq, environment_id, outcome, started_at, finished_at, duration_ms, evidence)
            VALUES (CAST(:r AS uuid), CAST(:p AS uuid), 1, CAST(:t AS uuid), 1, :e, 'passed',
                now(), now(), 1, '{}'::jsonb)
        """), {"r": str(uuid4()), "p": str(uuid4()), "t": tids[0], "e": env})
    assert _environments_with_evidence(s, tids) == [ENV, 999999]                 # unfiltered read
    assert _environments_with_evidence(s, tids, tenant_id=TENANT) == [ENV]       # active only
    s.execute(text("UPDATE public.environments SET is_active = FALSE WHERE id = :e"), {"e": ENV})
    assert _environments_with_evidence(s, tids, tenant_id=TENANT) == []
    s.execute(text("UPDATE public.environments SET is_active = TRUE WHERE id = :e"), {"e": ENV})
