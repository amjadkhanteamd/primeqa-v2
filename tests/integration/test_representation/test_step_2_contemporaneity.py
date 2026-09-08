"""Step 2 (LLD_STEP_2_CONTEMPORANEITY §h) — DB-real on the local-PG rollback
session (tenant_1 at the substrate head, which includes 20260909_0010).

  1. THE STAMP equals the executor's value: the select bracket's prep tuple
     carries OrgStamp(org, seq) == the Step B resolver's current sequence,
     and the persisted run row carries both columns; an environment with no
     org yields NO stamp (NULLs), never a number from nowhere.
  2. TARGETING: a run stamped at S; close an UNCOVERED entity → CURRENT;
     close a COVERED read → STALE for that claim only, the read named; a
     second claim that does not read it stays CURRENT.
  3. THE VOCABULARY: NEVER_RUN, the TA's unstamped sentence verbatim, and
     no_coverage each carry their sentence.
  4. THE ENGINE refuses a scope with a never-run claim (cannot_determine,
     the readiness line naming it) — through _decide_for_session.
  5. D9: a graded blocker beside an ungraded input → NO GO stands; only
     ungraded → cannot_determine; STALE → conditional_go at best.
  6. THE LEGACY shape: an unstamped run reads CANNOT_DETERMINE / unstamped
     and NOTHING relabels it (the column stays NULL; the module holds no
     UPDATE of it).
  7. THE MINT: materialize_surface_entities with an org writes an ORG-BOUND
     checkpoint; the Surface entity stays org-less by S1's own CHECK
     (entities_org_only_for_sync); the org-less call refuses.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

from primeqa.evolution import persist_grounding_validity
from primeqa.execution_engine.evidence import RunEvidence
from primeqa.execution_engine.result_store import persist_run_evidence
from primeqa.execution_engine.run import OrgStamp
from primeqa.intelligence.substrate_decision import (
    _assemble_claim_evidence, _decide_for_session, compute_substrate_decision,
)
from primeqa.sync import readiness as R
from primeqa.sync.readiness import resolve_current_sequence, resolve_run_readiness
from primeqa.test_representation import SemanticTransactionCoordinator

from .test_substrate_decision_evidence import _approved_claim, _gv, _seed_run, _seed_s1_version

ENV_A, ENV_B = 59, 78


def _tenant(session):
    session.execute(text("SELECT set_config('app.tenant_id', '1', false)"))


def _org(session, env, label):
    return session.execute(text(
        "INSERT INTO connected_orgs (org_type, sf_instance_url, label, environment_id) "
        "VALUES ('sandbox', :u, :l, :e) RETURNING CAST(id AS text)"),
        {"u": f"https://{label}.example", "l": label, "e": env}).scalar()


def _version(session, org):
    return session.execute(text(
        "INSERT INTO logical_versions (version_name, version_type, connected_org_id) "
        "VALUES (:n, 'sync_run', CAST(:o AS uuid)) RETURNING version_seq"),
        {"n": f"v-{uuid4().hex[:6]}", "o": org}).scalar()


def _covered_ids(session, claim_test_id):
    return [(r[0], r[1]) for r in session.execute(text(
        "SELECT entity_id, entity_type FROM test_claim_coverage "
        "WHERE claim_test_id = CAST(:t AS uuid)"), {"t": str(claim_test_id)}).all()]


def _materialise_entity(session, eid, etype, org, *, valid_from, valid_to=None):
    session.execute(text(
        "INSERT INTO entities (id, entity_type, sf_api_name, display_name, attributes, "
        "valid_from_seq, valid_to_seq, tenant_id, connected_org_id, created_at, last_synced_at) "
        "VALUES (CAST(:i AS uuid), :t, :n, :n, '{}'::jsonb, :f, :to, 1, CAST(:o AS uuid), now(), now()) "
        "ON CONFLICT (id) DO UPDATE SET valid_from_seq = :f, valid_to_seq = :to, "
        "connected_org_id = CAST(:o AS uuid)"),
        {"i": str(eid), "t": etype, "n": f"read-{str(eid)[:8]}", "f": valid_from,
         "to": valid_to, "o": org})


def _close(session, eid, at_seq):
    session.execute(text("UPDATE entities SET valid_to_seq = :s WHERE id = CAST(:i AS uuid)"),
                    {"s": at_seq, "i": str(eid)})


def _evidence(claim, env, outcome="passed"):
    now = datetime.now(timezone.utc)
    return RunEvidence(run_id=uuid4(), recipe_id=uuid4(), recipe_version_seq=1,
                       claim_test_id=claim.test_id, claim_version_seq=claim.version_seq,
                       environment_id=env, api_choice="n/a", outcome=outcome,
                       started_at=now, finished_at=now, steps=())


class _Recipe:
    recipe_kind = "metadata-recipe"
    recipe_id = None


# ---- 1. the stamp --------------------------------------------------------

def test_1_the_bracket_stamp_equals_the_resolver_and_persists(session):
    from primeqa.execution_engine.run import _prepare_async_execute, _stamp_evidence
    _tenant(session)
    org = _org(session, ENV_A, "A")
    seq = _version(session, org)
    coord = SemanticTransactionCoordinator()
    cr = _approved_claim(session, coord, key="S2-1")

    prep = _prepare_async_execute(_Recipe(), session, ENV_A, object())
    assert len(prep) == 5
    stamp = prep[4]
    assert isinstance(stamp, OrgStamp)
    assert stamp.connected_org_id == org
    assert stamp.org_version_seq == seq == resolve_current_sequence(
        session, connected_org_id=org).current_seq

    ev = _stamp_evidence(_evidence(cr, ENV_A), stamp)
    assert (ev.connected_org_id, ev.org_version_seq) == (org, seq)
    run_id = persist_run_evidence(session, ev)
    row = session.execute(text(
        "SELECT CAST(connected_org_id AS text), org_version_seq FROM s4_execution_runs "
        "WHERE run_id = CAST(:r AS uuid)"), {"r": str(run_id)}).first()
    assert row == (org, seq)

    # an environment with no org → no stamp, the run written with NULLs
    prep2 = _prepare_async_execute(_Recipe(), session, 9001, object())
    assert prep2[4] is None
    ev2 = _stamp_evidence(_evidence(cr, 9001), prep2[4])
    assert ev2.org_version_seq is None
    run2 = persist_run_evidence(session, ev2)
    assert session.execute(text(
        "SELECT org_version_seq FROM s4_execution_runs WHERE run_id = CAST(:r AS uuid)"),
        {"r": str(run2)}).scalar() is None


# ---- 2. targeting ---------------------------------------------------------

def test_2_only_a_changed_covered_read_moves_the_claim(session):
    _tenant(session)
    org = _org(session, ENV_A, "A")
    s1 = _version(session, org)
    coord = SemanticTransactionCoordinator()
    c1 = _approved_claim(session, coord, key="S2-2A")
    c2 = _approved_claim(session, coord, key="S2-2B")
    for c in (c1, c2):
        for eid, et in _covered_ids(session, c.test_id):
            _materialise_entity(session, eid, et, org, valid_from=s1)
        _seed_run(session, claim_test_id=c.test_id, outcome="passed",
                  finished_at="2026-09-06T10:00:00+00:00", claim_version_seq=c.version_seq,
                  environment_id=ENV_A, connected_org_id=org, org_version_seq=s1)
    # an UNCOVERED entity of the same org changes at S+1 → both stay CURRENT
    s2 = _version(session, org)
    other = uuid4()
    _materialise_entity(session, other, "Field", org, valid_from=s1, valid_to=s2)
    session.flush()
    for c in (c1, c2):
        r = resolve_run_readiness(session, claim_test_id=c.test_id, environment_id=ENV_A)
        assert r.state == R.READY_CURRENT, (c.test_id, r)
        assert r.stamp_seq == s1 and r.current_seq == s2
    # a COVERED read of c1 closes at S+2 → c1 STALE (named); c2 stays CURRENT
    s3 = _version(session, org)
    for eid, _ in _covered_ids(session, c1.test_id):
        _close(session, eid, s3)
    session.flush()
    r1 = resolve_run_readiness(session, claim_test_id=c1.test_id, environment_id=ENV_A)
    r2 = resolve_run_readiness(session, claim_test_id=c2.test_id, environment_id=ENV_A)
    assert r1.state == R.READY_STALE and r1.changed_reads
    assert r1.changed_reads[0][2] == "entity_closed"
    assert "thing(s) this test reads changed" in r1.sentence
    assert r2.state == R.READY_CURRENT
    # and the engine's grounding staleness uses the SAME predicate (R-ground)
    persist_grounding_validity(session, connected_org_id=org, test_id=c1.test_id,
                               version_seq=c1.version_seq, evaluated_at_version_seq=s1,
                               validity=_gv(overall="intact"))
    persist_grounding_validity(session, connected_org_id=org, test_id=c2.test_id,
                               version_seq=c2.version_seq, evaluated_at_version_seq=s1,
                               validity=_gv(overall="intact"))
    session.flush()
    rows = {r["test_id"]: r for r in _assemble_claim_evidence(
        session, ["S2-2A", "S2-2B"], environment_id=ENV_A, connected_org_id=org)}
    assert rows[str(c1.test_id)]["grounding"]["stale"] is True
    assert rows[str(c2.test_id)]["grounding"]["stale"] is False


# ---- 3. the vocabulary ----------------------------------------------------

def test_3_the_vocabulary_carries_its_sentences(session):
    _tenant(session)
    org = _org(session, ENV_A, "A")
    s1 = _version(session, org)
    coord = SemanticTransactionCoordinator()
    never = _approved_claim(session, coord, key="S2-3N")
    unst = _approved_claim(session, coord, key="S2-3U")
    nocov = _approved_claim(session, coord, key="S2-3C")
    _seed_run(session, claim_test_id=unst.test_id, outcome="passed",
              finished_at="2026-09-06T10:00:00+00:00", claim_version_seq=unst.version_seq,
              environment_id=ENV_A)                                   # UNSTAMPED
    _seed_run(session, claim_test_id=nocov.test_id, outcome="passed",
              finished_at="2026-09-06T10:00:00+00:00", claim_version_seq=nocov.version_seq,
              environment_id=ENV_A, connected_org_id=org, org_version_seq=s1)
    session.execute(text("DELETE FROM test_claim_coverage WHERE claim_test_id = CAST(:t AS uuid)"),
                    {"t": str(nocov.test_id)})
    session.flush()
    r = resolve_run_readiness(session, claim_test_id=never.test_id, environment_id=ENV_A)
    assert r.state == R.READY_NEVER_RUN and r.sentence == "No run in this environment."
    r = resolve_run_readiness(session, claim_test_id=unst.test_id, environment_id=ENV_A)
    assert r.state == R.READY_CANNOT_DETERMINE and r.reason == "unstamped"
    assert r.sentence == ("Freshness unknown — this run predates run-level environment "
                          "stamping. Run again to establish current readiness.")
    r = resolve_run_readiness(session, claim_test_id=nocov.test_id, environment_id=ENV_A)
    assert r.state == R.READY_CANNOT_DETERMINE and r.reason == "no_coverage"
    assert "reads are not recorded" in r.sentence


# ---- 4. the engine refuses a never-run scope ------------------------------

def test_4_a_never_run_claim_in_scope_is_ungraded_and_named(session):
    _tenant(session)
    org = _org(session, ENV_A, "A")
    s1 = _version(session, org)
    coord = SemanticTransactionCoordinator()
    green = _approved_claim(session, coord, key="S2-4")
    never = _approved_claim(session, coord, key="S2-4")
    for eid, et in _covered_ids(session, green.test_id):
        _materialise_entity(session, eid, et, org, valid_from=s1)
    persist_grounding_validity(session, connected_org_id=org, test_id=green.test_id,
                               version_seq=green.version_seq, evaluated_at_version_seq=s1,
                               validity=_gv(overall="intact"))
    _seed_run(session, claim_test_id=green.test_id, outcome="passed",
              finished_at="2026-09-06T10:00:00+00:00", claim_version_seq=green.version_seq,
              environment_id=ENV_A, connected_org_id=org, org_version_seq=s1)
    session.flush()
    out = _decide_for_session(session, session.connection(), ["S2-4"], None, tenant_id=None)
    assert out["recommendation"] == "cannot_determine"
    line = [r for r in out["reasoning"] if r["check"] == "readiness"][0]
    assert line["status"] == "fail" and "1 never run in this environment" in line["detail"]
    assert out["metrics"]["readiness"] == {"never_run": 1, "stale": 0, "current": 1,
                                           "cannot_determine": 0, "ungrounded": 0,
                                           "ungraded": 1}
    assert out["metrics"]["blockers"] == 0            # unknown blocks, it does not condemn


# ---- 5. D9 ---------------------------------------------------------------

def test_5_d9_holds_over_real_rows(session):
    _tenant(session)
    org = _org(session, ENV_A, "A")
    s1 = _version(session, org)
    coord = SemanticTransactionCoordinator()
    red = _approved_claim(session, coord, key="S2-5")
    never = _approved_claim(session, coord, key="S2-5")
    for eid, et in _covered_ids(session, red.test_id):
        _materialise_entity(session, eid, et, org, valid_from=s1)
    persist_grounding_validity(session, connected_org_id=org, test_id=red.test_id,
                               version_seq=red.version_seq, evaluated_at_version_seq=s1,
                               validity=_gv(overall="intact"))
    _seed_run(session, claim_test_id=red.test_id, outcome="failed",
              finished_at="2026-09-06T10:00:00+00:00", claim_version_seq=red.version_seq,
              environment_id=ENV_A, connected_org_id=org, org_version_seq=s1)
    session.flush()
    out = _decide_for_session(session, session.connection(), ["S2-5"], None, tenant_id=None)
    assert out["recommendation"] == "no_go"                      # the graded blocker stands
    assert out["metrics"]["readiness"]["ungraded"] == 1          # and the unknown is named
    assert any(r["check"] == "readiness" and r["status"] == "fail" for r in out["reasoning"])


# ---- 6. the legacy shape is never relabelled ------------------------------

def test_6_legacy_unstamped_reads_cannot_determine_and_nothing_relabels_it(session):
    _tenant(session)
    coord = SemanticTransactionCoordinator()
    cr = _approved_claim(session, coord, key="S2-6")
    _seed_run(session, claim_test_id=cr.test_id, outcome="passed",
              finished_at="2026-06-06T10:00:00+00:00", claim_version_seq=cr.version_seq,
              environment_id=ENV_A)                                   # the 727's shape
    session.flush()
    before = session.execute(text(
        "SELECT count(*) FROM s4_execution_runs WHERE claim_test_id = CAST(:t AS uuid) "
        "AND org_version_seq IS NULL"), {"t": str(cr.test_id)}).scalar()
    r = resolve_run_readiness(session, claim_test_id=cr.test_id, environment_id=ENV_A)
    assert r.state == R.READY_CANNOT_DETERMINE and r.reason == "unstamped"
    after = session.execute(text(
        "SELECT count(*) FROM s4_execution_runs WHERE claim_test_id = CAST(:t AS uuid) "
        "AND org_version_seq IS NULL"), {"t": str(cr.test_id)}).scalar()
    assert before == after == 1
    src = inspect.getsource(R)
    assert "UPDATE s4_execution_runs" not in src and "org_version_seq =" not in src


# ---- 7. the mint ----------------------------------------------------------

def test_7_the_inventory_checkpoint_is_org_bound_and_the_org_less_path_refuses(session):
    from primeqa.semantic.surface_entities import materialize_surface_entities
    _tenant(session)
    org = _org(session, ENV_A, "A")
    session.execute(text(
        "INSERT INTO ui_surface_inventories (inventory_version, notes, created_by) "
        "VALUES (901, 's2 mint test', 1) ON CONFLICT DO NOTHING"))
    session.execute(text(
        "INSERT INTO ui_surface_inventory_members (inventory_version, surface_key, site, "
        "path, persona_scope, display_name) VALUES (901, 's2-surface', 'portal', '/x', "
        "'customer', 'S2 surface') ON CONFLICT DO NOTHING"))
    session.flush()
    with pytest.raises(ValueError):
        materialize_surface_entities(session, inventory_version=901, connected_org_id=None)
    res = materialize_surface_entities(session, inventory_version=901, connected_org_id=org)
    assert res["entities_created"] == 1
    v = session.execute(text(
        "SELECT CAST(connected_org_id AS text) FROM logical_versions "
        "WHERE version_name = 'surface-materialization-inv901'")).scalar()
    assert v == org                                               # org-bound checkpoint
    e = session.execute(text(
        "SELECT connected_org_id, entity_origin, valid_from_seq FROM entities "
        "WHERE entity_type = 'Surface' AND sf_api_name = 's2-surface'")).first()
    # the Surface ENTITY stays org-less — S1's CHECK entities_org_only_for_sync
    # admits an org on sync-origin rows only; it is anchored at the org-bound
    # checkpoint's sequence, which is what removes the tenant-wide MAX+1 row
    assert e[0] is None and e[1] == "manual_curation"
    seq = session.execute(text(
        "SELECT version_seq FROM logical_versions "
        "WHERE version_name = 'surface-materialization-inv901'")).scalar()
    assert e[2] == seq
    assert session.execute(text(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'entities_org_only_for_sync'")).scalar()
