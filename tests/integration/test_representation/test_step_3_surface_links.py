"""Step 3 — the requirement → surface link (LLD_STEP_3_REQUIREMENT_SURFACE §g).

DB-real on the local-PG harness (per-test rollback):

  1. declare → the S2 ``verifies`` links appear exactly where the readers
     look (COVERAGE_LINK_KINDS via list_tests_by_requirement — what
     read_requirement_claims calls — and the engine's _claim_test_ids).
  2. declare is idempotent (no duplicate row, no duplicate link).
  3. unlink deactivates with provenance, removes ONLY the links this
     declaration created, leaves an independent verifies link, and the
     table refuses DELETE and identity UPDATE.
  4. DERIVED / VERIFIED_DERIVED cannot be written: the service refuses
     first; a direct INSERT trips the v1 write-guard CHECK.
  5. the link follows the surface: a superseding claim set's new claim on
     a declared surface is linked by rematerialise; a revoked member keeps
     its link (add-only).
  6. the picker excludes declared surfaces, record-context members, and
     every member of a non-active inventory; refusals name their reason.
     (7 — the active-environment filter — needs public.environments and
     runs on scratch: test_step_3_scope.py.)
"""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from primeqa.intelligence.substrate_decision import _claim_test_ids
from primeqa.test_representation import (
    SemanticTransactionCoordinator, claim_sets, identity,
)
from primeqa.test_representation import surface_links as sl
from primeqa.test_representation.coordinator import COVERAGE_LINK_KINDS
from primeqa.test_representation.models.claims.ui.conformance_claim import (
    ConformanceClaimBody,
)
from primeqa.test_representation.models.conditions import SemanticConditionsBody
from primeqa.test_representation.models.surface import (
    SurfaceNaturalKey, canonical_surface_key,
)

SITE = "step3.example.my.site.com"
KEY = "S3-REQ"


def _surface(path, **kw):
    return SurfaceNaturalKey(site=SITE, path=path, persona_scope="customer", **kw)


def _member(surface, display_name):
    return {"site": surface.site, "path": surface.path,
            "persona_scope": surface.persona_scope,
            "record_context_ref": surface.record_context_ref,
            "viewport": surface.viewport, "display_name": display_name}


def _org(session):
    session.execute(text("SELECT set_config('app.tenant_id', '1', false)"))
    return session.execute(text(
        "INSERT INTO connected_orgs (org_type, sf_instance_url, label, environment_id) "
        "VALUES ('sandbox', 'https://step3.example', 'step3-org', 59) "
        "RETURNING CAST(id AS text)")).scalar()


def _claim(session, coord, rule, surface):
    r = coord.write_claim(
        session, actor="s3", test_id=None, archetype="ui",
        claim_kind="conformance-claim",
        asserted_truth=ConformanceClaimBody(plimsol_rule_id=rule, surface=surface),
        semantic_conditions=SemanticConditionsBody())
    return str(r.test_id)


def _approved_set(session, version, members):
    """A claim set recorded through the real ``create_claim_set`` and marked
    approved directly — the approval act itself (and its Step 3 hook) is
    exercised on scratch, where public.activity_log exists."""
    set_id = claim_sets.create_claim_set(
        session, persona_scope="customer", inventory_version=version,
        catalogue_release_id=1, created_by=1,
        members=[{"test_id": t, "applicability": a, "executable": True}
                 for t, a in members])
    session.execute(text(
        "UPDATE claim_sets SET status = 'approved', approved_by = 1, "
        "approved_at = clock_timestamp(), member_count = :n WHERE id = CAST(:i AS uuid)"),
        {"n": len(members), "i": str(set_id)})
    return str(set_id)


def _reader_ids(session, coord, key=KEY):
    """What read_requirement_claims sees: COVERAGE_LINK_KINDS links."""
    return sorted({str(m.test_id) for m in coord.list_tests_by_requirement(
        session, external_system="jira", external_key=key,
        link_kind=COVERAGE_LINK_KINDS)})


def _engine_ids(session, key=KEY):
    tids, _ = _claim_test_ids(session, [key])
    return sorted(str(t) for t in tids)


@pytest.fixture
def world(session):
    coord = SemanticTransactionCoordinator()
    org = _org(session)
    A, B = _surface("/s"), _surface("/s/topiccatalog")
    C = _surface("/s/case/Case/Default", record_context_ref="Case:001")
    v1 = claim_sets.create_inventory_version(
        session, members=[_member(A, "Portal home"), _member(B, "Topic Catalog"),
                          _member(C, "A case (record context)")],
        created_by=1, connected_org_id=org)
    a_claims = [_claim(session, coord, f"PLM-A11Y-00{i}", A) for i in (1, 2, 3)]
    b_claims = [_claim(session, coord, f"PLM-A11Y-00{i}", B) for i in (1, 2)]
    c_claims = [_claim(session, coord, "PLM-A11Y-001", C)]
    members = ([(t, "APPLICABLE") for t in a_claims[:2]]
               + [(a_claims[2], "HUMAN_REVIEW")]
               + [(t, "APPLICABLE") for t in b_claims + c_claims])
    set1 = _approved_set(session, v1, members)
    identity.establish(session, KEY, origin="manual", evidence={"test": "step 3"},
                       established_by="test")
    return {"coord": coord, "org": org, "v1": v1, "set1": set1,
            "A": A, "B": B, "C": C, "kA": canonical_surface_key(A),
            "kB": canonical_surface_key(B), "kC": canonical_surface_key(C),
            "a": a_claims, "b": b_claims, "c": c_claims}


# 1 ---------------------------------------------------------------------------

def test_declare_materialises_where_the_readers_look(session, world):
    w = world
    assert _reader_ids(session, w["coord"]) == []          # zero links before
    res = sl.declare(session, requirement_key=KEY, surface_key=w["kA"],
                     actor_user_id=1)
    assert res.created and res.materialised == 3 and res.already_linked == 0
    # what read_requirement_claims sees == the three claims ON surface A
    assert _reader_ids(session, w["coord"]) == sorted(w["a"])
    # the engine's own read (Area 5 / release scope) sees the same
    assert _engine_ids(session) == sorted(w["a"])
    # every link is the verifies kind, written by the human actor
    kinds = session.execute(text(
        "SELECT link_kind, linked_by FROM test_requirement_links "
        "WHERE external_key = :k"), {"k": KEY}).fetchall()
    assert {tuple(map(str, r)) for r in kinds} == {("verifies", "human")}
    # the declaration row is DECLARED, active, with actor + time
    row = session.execute(text(
        "SELECT source, active, declared_by, declared_at IS NOT NULL "
        "FROM requirement_surface_links WHERE id = CAST(:i AS uuid)"),
        {"i": res.link_id}).one()
    assert tuple(row) == ("DECLARED", True, 1, True)
    # surface_claims counts every applicability (the HUMAN_REVIEW one too)
    on_a = sl.surface_claims(session, claim_set_id=w["set1"], surface_key=w["kA"])
    assert sorted(c.test_id for c in on_a) == sorted(w["a"])
    assert sum(1 for c in on_a if c.applicability == "HUMAN_REVIEW") == 1


# 2 ---------------------------------------------------------------------------

def test_declare_is_idempotent(session, world):
    w = world
    first = sl.declare(session, requirement_key=KEY, surface_key=w["kA"], actor_user_id=1)
    second = sl.declare(session, requirement_key=KEY, surface_key=w["kA"], actor_user_id=2)
    assert second.link_id == first.link_id
    assert (second.created, second.materialised, second.already_linked) == (False, 0, 3)
    assert session.execute(text(
        "SELECT count(*) FROM requirement_surface_links WHERE active "
        "AND requirement_key = :k"), {"k": KEY}).scalar() == 1
    assert session.execute(text(
        "SELECT count(*) FROM requirement_surface_link_claims "
        "WHERE link_id = CAST(:i AS uuid)"), {"i": first.link_id}).scalar() == 3
    assert _reader_ids(session, w["coord"]) == sorted(w["a"])


# 3 ---------------------------------------------------------------------------

def test_unlink_deactivates_with_provenance_and_removes_only_its_own_links(session, world):
    w = world
    coord = w["coord"]
    # an INDEPENDENT human verifies link on one A-claim, made before the declaration
    from uuid import UUID
    coord.link_requirement(session, actor="human", test_id=UUID(w["a"][0]),
                           external_system="jira", external_key=KEY,
                           link_kind="verifies")
    res = sl.declare(session, requirement_key=KEY, surface_key=w["kA"], actor_user_id=1)
    assert res.materialised == 3                      # ledgered for all three…
    created = dict(session.execute(text(
        "SELECT CAST(test_id AS text), created_link FROM requirement_surface_link_claims "
        "WHERE link_id = CAST(:i AS uuid)"), {"i": res.link_id}).fetchall())
    assert created[w["a"][0]] is False                # …but this one pre-existed
    assert created[w["a"][1]] is True and created[w["a"][2]] is True

    out = sl.unlink(session, link_id=res.link_id, actor_user_id=7,
                    reason="declared on the wrong surface")
    assert (out.removed_links, out.kept_links, out.already_inactive) == (2, 1, False)
    row = session.execute(text(
        "SELECT active, deactivated_by, deactivated_at IS NOT NULL, deactivation_reason "
        "FROM requirement_surface_links WHERE id = CAST(:i AS uuid)"),
        {"i": res.link_id}).one()
    assert tuple(row) == (False, 7, True, "declared on the wrong surface")
    assert session.execute(text(
        "SELECT count(*) FROM requirement_surface_link_claims "
        "WHERE link_id = CAST(:i AS uuid) AND deactivated_by = 7 "
        "AND deactivated_at IS NOT NULL"), {"i": res.link_id}).scalar() == 3
    # the readers drop the declaration's claims; the independent link stays
    assert _reader_ids(session, coord) == [w["a"][0]]
    assert _engine_ids(session) == [w["a"][0]]
    # idempotent on an inactive link
    again = sl.unlink(session, link_id=res.link_id, actor_user_id=7, reason="")
    assert again.already_inactive and again.removed_links == 0
    # the tables refuse DELETE and identity UPDATE
    for stmt in ("DELETE FROM requirement_surface_links WHERE id = CAST(:i AS uuid)",
                 "DELETE FROM requirement_surface_link_claims WHERE link_id = CAST(:i AS uuid)",
                 "UPDATE requirement_surface_links SET surface_key = 'x' WHERE id = CAST(:i AS uuid)",
                 "UPDATE requirement_surface_links SET requirement_key = 'OTHER' WHERE id = CAST(:i AS uuid)"):
        with pytest.raises(DBAPIError):
            with session.begin_nested():
                session.execute(text(stmt), {"i": res.link_id})
    # the row is still there, still inactive
    assert session.execute(text(
        "SELECT active FROM requirement_surface_links WHERE id = CAST(:i AS uuid)"),
        {"i": res.link_id}).scalar() is False


# 4 ---------------------------------------------------------------------------

def test_derived_cannot_be_written(session, world):
    w = world
    for src in (sl.SOURCE_DERIVED, sl.SOURCE_VERIFIED_DERIVED):
        with pytest.raises(sl.SurfaceLinkError) as e:
            sl.declare(session, requirement_key=KEY, surface_key=w["kA"],
                       actor_user_id=1, source=src)
        assert e.value.reason == sl.REASON_SOURCE_NOT_WRITABLE
    ins = ("INSERT INTO requirement_surface_links "
           "(requirement_key, surface_key, inventory_version, source, declared_by) "
           "VALUES (:k, :sk, :v, :src, 1)")
    for src in ("DERIVED", "VERIFIED_DERIVED"):
        with pytest.raises(DBAPIError) as e:
            with session.begin_nested():
                session.execute(text(ins), {"k": KEY, "sk": w["kA"], "v": w["v1"], "src": src})
        assert "requirement_surface_links_v1_declared_only" in str(e.value)
    with pytest.raises(DBAPIError) as e:               # outside the vocabulary too
        with session.begin_nested():
            session.execute(text(ins), {"k": KEY, "sk": w["kA"], "v": w["v1"], "src": "GUESSED"})
    # DECLARED passes both CHECKs
    with session.begin_nested():
        session.execute(text(ins), {"k": KEY, "sk": w["kA"], "v": w["v1"], "src": "DECLARED"})
    assert session.execute(text(
        "SELECT count(*) FROM requirement_surface_links WHERE requirement_key = :k"),
        {"k": KEY}).scalar() == 1


# 5 ---------------------------------------------------------------------------

def test_the_link_follows_the_surface_add_only(session, world):
    w = world
    coord = w["coord"]
    res = sl.declare(session, requirement_key=KEY, surface_key=w["kA"], actor_user_id=1)
    assert _reader_ids(session, coord) == sorted(w["a"])
    # a superseding claim set on the SAME inventory version: one new rule on A
    new_a = _claim(session, coord, "PLM-A11Y-004", w["A"])
    set2 = _approved_set(session, w["v1"],
                         [(t, "APPLICABLE") for t in w["a"] + [new_a] + w["b"]])
    assert sl.active_claim_set(session, w["v1"]) == set2
    n = sl.rematerialise(session, actor_user_id=1, inventory_version=w["v1"])
    assert n == 1
    assert _reader_ids(session, coord) == sorted(w["a"] + [new_a])
    ledger_sets = {r[0] for r in session.execute(text(
        "SELECT CAST(claim_set_id AS text) FROM requirement_surface_link_claims "
        "WHERE link_id = CAST(:i AS uuid)"), {"i": res.link_id}).fetchall()}
    assert ledger_sets == {w["set1"], set2}           # provenance names the set each came from
    # a member revoked from the active set keeps its link (add-only)
    session.execute(text(
        "UPDATE claim_set_members SET revoked_at = now(), revoked_by = 1 "
        "WHERE claim_set_id = CAST(:s AS uuid) AND test_id = CAST(:t AS uuid)"),
        {"s": set2, "t": w["a"][0]})
    assert sl.rematerialise(session, actor_user_id=1, inventory_version=w["v1"]) == 0
    assert _reader_ids(session, coord) == sorted(w["a"] + [new_a])


# 6 ---------------------------------------------------------------------------

def test_picker_excludes_declared_record_context_and_inactive_inventory(session, world):
    w = world
    v, cands = sl.picker_candidates(session, requirement_key=KEY)
    assert v == w["v1"]
    assert sorted(c.surface_key for c in cands) == sorted([w["kA"], w["kB"]])   # C: record context
    with pytest.raises(sl.SurfaceLinkError) as e:
        sl.declare(session, requirement_key=KEY, surface_key=w["kC"], actor_user_id=1)
    assert e.value.reason == sl.REASON_RECORD_CONTEXT
    with pytest.raises(sl.SurfaceLinkError) as e:
        sl.declare(session, requirement_key="NO-IDENTITY", surface_key=w["kA"], actor_user_id=1)
    assert e.value.reason == sl.REASON_NO_IDENTITY
    sl.declare(session, requirement_key=KEY, surface_key=w["kA"], actor_user_id=1)
    _, cands = sl.picker_candidates(session, requirement_key=KEY)
    assert [c.surface_key for c in cands] == [w["kB"]]                        # A declared
    # a NEW inventory version with an approved set becomes the active one
    D = _surface("/s/contactsupport")
    v2 = claim_sets.create_inventory_version(
        session, members=[_member(D, "Contact Support")], created_by=1,
        connected_org_id=w["org"])
    d_claim = _claim(session, w["coord"], "PLM-A11Y-001", D)
    _approved_set(session, v2, [(d_claim, "APPLICABLE")])
    assert sl.active_inventory_version(session) == v2
    v, cands = sl.picker_candidates(session, requirement_key=KEY)
    assert v == v2 and [c.surface_key for c in cands] == [canonical_surface_key(D)]
    with pytest.raises(sl.SurfaceLinkError) as e:                             # B is v1-only now
        sl.declare(session, requirement_key=KEY, surface_key=w["kB"], actor_user_id=1)
    assert e.value.reason == sl.REASON_NOT_IN_ACTIVE_INVENTORY
    # the v1 declaration stays, active, labelled by its version
    decl = sl.declared_surfaces(session, requirement_key=KEY)
    assert [(d.surface_key, d.inventory_version, d.active) for d in decl] == [(w["kA"], w["v1"], True)]
    # an inventory with NO approved set is never active
    session.execute(text("UPDATE claim_sets SET status = 'revoked' WHERE inventory_version = :v"), {"v": v2})
    assert sl.active_inventory_version(session) == w["v1"]
