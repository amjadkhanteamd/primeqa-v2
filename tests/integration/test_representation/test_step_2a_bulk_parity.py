"""Step 2a — PARITY IS THE ACCEPTANCE TEST.

``resolve_run_readiness`` (one pair) remains the DEFINITION of readiness;
``resolve_run_readiness_bulk`` is an optimisation of it. This suite asserts the
two agree **element for element** over a fixture set spanning every state and
every pathological shape, not a sample:

  CURRENT · STALE · NEVER_RUN · CANNOT_DETERMINE (unstamped, no_coverage,
  org refusal) · both lanes (a functional and a conformance claim) · two orgs ·
  two environments · a claim whose latest run is in a DIFFERENT org · a claim
  with no coverage rows at all · the empty input.

Equality is over the full ``as_dict()`` AND ``changed_reads`` (order included),
plus the rendered ``sentence`` — nothing a caller can read may differ.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

from primeqa.evolution import persist_grounding_validity
from primeqa.sync.readiness import (
    READY_CANNOT_DETERMINE, READY_CURRENT, READY_NEVER_RUN, READY_STALE,
    REASON_NO_COVERAGE, REASON_UNSTAMPED,
    resolve_run_readiness, resolve_run_readiness_bulk,
)
from primeqa.test_representation import SemanticTransactionCoordinator
from primeqa.test_representation.models.claims.ui.conformance_claim import ConformanceClaimBody
from primeqa.test_representation.models.conditions import SemanticConditionsBody
from primeqa.test_representation.models.surface import SurfaceNaturalKey

from .test_step_2_contemporaneity import (
    _covered_ids, _materialise_entity, _org, _tenant, _version,
)
from .test_substrate_decision_evidence import _approved_claim, _gv, _seed_run

ENV_A, ENV_B = 59, 78


def _full(r):
    """Everything a caller can read off a resolution."""
    d = dict(r.as_dict())
    d["changed_reads"] = tuple(r.changed_reads)
    d["sentence"] = r.sentence
    return d


def _assert_parity(session, pairs):
    """Element for element: same keys, same values, in the same order."""
    bulk = resolve_run_readiness_bulk(session, pairs)
    one = {(str(c), int(e)): resolve_run_readiness(session, claim_test_id=c,
                                                   environment_id=e)
           for c, e in pairs}
    assert set(bulk) == set(one), "the two paths disagree on which pairs exist"
    for key in sorted(one):
        assert _full(bulk[key]) == _full(one[key]), f"parity broken at {key}"
    return bulk


@pytest.fixture
def world(session):
    """Every state, in one tenant, across two orgs and two environments."""
    _tenant(session)
    coord = SemanticTransactionCoordinator()
    org_a = _org(session, ENV_A, "parity-A")
    org_b = _org(session, ENV_B, "parity-B")
    org_unsynced = session.execute(text(
        "INSERT INTO connected_orgs (org_type, sf_instance_url, label) "
        "VALUES ('sandbox', 'https://parity-unsynced.example', 'parity-unsynced') "
        "RETURNING CAST(id AS text)")).scalar()
    s_a, s_b = _version(session, org_a), _version(session, org_b)
    w = {"org_a": org_a, "org_b": org_b, "org_unsynced": org_unsynced,
         "s_a": s_a, "s_b": s_b}

    def stamped(key, *, org, seq, env, outcome="passed", cover=True, when="2026-09-01T10:00:00+00:00"):
        cr = _approved_claim(session, coord, key=key)
        if cover:
            for eid, et in _covered_ids(session, cr.test_id):
                _materialise_entity(session, eid, et, org, valid_from=seq)
            persist_grounding_validity(session, connected_org_id=org, test_id=cr.test_id,
                                       version_seq=cr.version_seq, evaluated_at_version_seq=seq,
                                       validity=_gv(overall="intact"))
        _seed_run(session, claim_test_id=cr.test_id, outcome=outcome, finished_at=when,
                  claim_version_seq=cr.version_seq, environment_id=env,
                  connected_org_id=org, org_version_seq=seq)
        return cr

    # 1. CURRENT — stamped, covered, nothing moved
    w["current"] = stamped("P2A-CURRENT", org=org_a, seq=s_a, env=ENV_A)
    # 2. STALE — a covered read closed after the stamp
    w["stale"] = stamped("P2A-STALE", org=org_a, seq=s_a, env=ENV_A)
    later = _version(session, org_a)
    ids = _covered_ids(session, w["stale"].test_id)
    assert ids, "the fixture claim must record what it reads"
    session.execute(text("UPDATE entities SET valid_to_seq = :s WHERE id = CAST(:i AS uuid)"),
                    {"s": later, "i": str(ids[0][0])})
    # 3. NEVER_RUN — approved, never executed in this environment
    w["never"] = _approved_claim(session, coord, key="P2A-NEVER")
    # 4. CANNOT_DETERMINE / unstamped — a legacy run with no org stamp
    w["unstamped"] = _approved_claim(session, coord, key="P2A-UNSTAMPED")
    _seed_run(session, claim_test_id=w["unstamped"].test_id, outcome="passed",
              finished_at="2026-08-01T10:00:00+00:00",
              claim_version_seq=w["unstamped"].version_seq, environment_id=ENV_A)
    # 5. CANNOT_DETERMINE / no_coverage — stamped, but the claim records no reads
    w["nocover"] = stamped("P2A-NOCOVER", org=org_a, seq=s_a, env=ENV_A, cover=False)
    session.execute(text("DELETE FROM test_claim_coverage WHERE claim_test_id = CAST(:t AS uuid)"),
                    {"t": str(w["nocover"].test_id)})
    # 6. CANNOT_DETERMINE / org refusal — the run's org has never synced
    w["orgless"] = _approved_claim(session, coord, key="P2A-ORGLESS")
    _seed_run(session, claim_test_id=w["orgless"].test_id, outcome="passed",
              finished_at="2026-09-01T10:00:00+00:00",
              claim_version_seq=w["orgless"].version_seq, environment_id=ENV_A,
              connected_org_id=org_unsynced, org_version_seq=1)
    # 7. the SECOND org / environment — the same claim run in both
    w["both"] = stamped("P2A-BOTH", org=org_a, seq=s_a, env=ENV_A)
    _seed_run(session, claim_test_id=w["both"].test_id, outcome="failed",
              finished_at="2026-09-02T10:00:00+00:00",
              claim_version_seq=w["both"].version_seq, environment_id=ENV_B,
              connected_org_id=org_b, org_version_seq=s_b)
    # 8. the CONFORMANCE lane — a ui-archetype claim with a stamped run
    cr = coord.write_claim(session, actor="s3", test_id=None, archetype="ui",
                           claim_kind="conformance-claim",
                           asserted_truth=ConformanceClaimBody(
                               plimsol_rule_id="PLM-A11Y-001",
                               surface=SurfaceNaturalKey(site="p2a.example.my.site.com",
                                                         path="/s", persona_scope="customer")),
                           semantic_conditions=SemanticConditionsBody())
    coord.promote_claim_to_approved(session, actor="human", test_id=cr.test_id,
                                    version_seq=cr.version_seq)
    _seed_run(session, claim_test_id=cr.test_id, outcome="passed",
              finished_at="2026-09-01T11:00:00+00:00", claim_version_seq=cr.version_seq,
              environment_id=ENV_A, connected_org_id=org_a, org_version_seq=s_a)
    w["conformance"] = cr
    session.flush()
    return w


def _pairs(w):
    return [(w["current"].test_id, ENV_A), (w["stale"].test_id, ENV_A),
            (w["never"].test_id, ENV_A), (w["unstamped"].test_id, ENV_A),
            (w["nocover"].test_id, ENV_A), (w["orgless"].test_id, ENV_A),
            (w["both"].test_id, ENV_A), (w["both"].test_id, ENV_B),
            (w["conformance"].test_id, ENV_A),
            (w["current"].test_id, ENV_B)]          # ran in A only → NEVER_RUN in B


# 1 — the states are all actually present (a parity test over one state proves little)

def test_the_fixture_spans_every_readiness_state(session, world):
    w = world
    got = resolve_run_readiness_bulk(session, _pairs(w))
    by = {k: v for k, v in got.items()}
    assert by[(str(w["current"].test_id), ENV_A)].state == READY_CURRENT
    assert by[(str(w["stale"].test_id), ENV_A)].state == READY_STALE
    assert by[(str(w["never"].test_id), ENV_A)].state == READY_NEVER_RUN
    assert by[(str(w["current"].test_id), ENV_B)].state == READY_NEVER_RUN
    u = by[(str(w["unstamped"].test_id), ENV_A)]
    assert u.state == READY_CANNOT_DETERMINE and u.reason == REASON_UNSTAMPED
    n = by[(str(w["nocover"].test_id), ENV_A)]
    assert n.state == READY_CANNOT_DETERMINE and n.reason == REASON_NO_COVERAGE
    o = by[(str(w["orgless"].test_id), ENV_A)]
    assert o.state == READY_CANNOT_DETERMINE and o.reason == "org_never_synced"
    assert by[(str(w["both"].test_id), ENV_A)].state == READY_CURRENT
    assert by[(str(w["both"].test_id), ENV_B)].state == READY_CURRENT
    # the CONFORMANCE lane: a ui claim records no org reads, so the ORG axis
    # cannot grade it — CANNOT_DETERMINE / no_coverage, never a false CURRENT.
    # This is exactly why Step 5 split the lanes (D-488); the resolver is
    # unchanged here and the bulk path reproduces it.
    cf = by[(str(w["conformance"].test_id), ENV_A)]
    assert cf.state == READY_CANNOT_DETERMINE and cf.reason == REASON_NO_COVERAGE
    assert {r.state for r in got.values()} == {READY_CURRENT, READY_STALE,
                                               READY_NEVER_RUN, READY_CANNOT_DETERMINE}


# 2 — PARITY, element for element, over the whole set

def test_bulk_equals_per_pair_element_for_element(session, world):
    _assert_parity(session, _pairs(world))


def test_parity_holds_pair_by_pair_in_isolation(session, world):
    """One pair at a time: the bulk path must not depend on its neighbours."""
    for pair in _pairs(world):
        _assert_parity(session, [pair])


def test_parity_holds_on_the_pathological_shapes(session, world):
    w = world
    # the empty input
    assert resolve_run_readiness_bulk(session, []) == {}
    assert resolve_run_readiness_bulk(session, None) == {}
    # a claim that never ran anywhere, in an environment nothing ran in
    _assert_parity(session, [(w["never"].test_id, 9999)])
    # duplicates in the input resolve to one key, identically
    dup = [(w["current"].test_id, ENV_A), (w["current"].test_id, ENV_A)]
    assert _assert_parity(session, dup).keys() == {(str(w["current"].test_id), ENV_A)}
    # a claim whose latest run is in a DIFFERENT org than its other run
    _assert_parity(session, [(w["both"].test_id, ENV_B), (w["both"].test_id, ENV_A)])
    # the stale claim's named reads must match, order included
    b = resolve_run_readiness_bulk(session, [(w["stale"].test_id, ENV_A)])[(str(w["stale"].test_id), ENV_A)]
    s = resolve_run_readiness(session, claim_test_id=w["stale"].test_id, environment_id=ENV_A)
    assert b.changed_reads == s.changed_reads and len(b.changed_reads) >= 1


def test_parity_survives_input_order(session, world):
    """The bulk result must not depend on the order pairs arrive in."""
    pairs = _pairs(world)
    a = {k: _full(v) for k, v in resolve_run_readiness_bulk(session, pairs).items()}
    b = {k: _full(v) for k, v in resolve_run_readiness_bulk(session, list(reversed(pairs))).items()}
    assert a == b


# 3 — the round-trip claim: a fixed number of queries whatever the pair count

def test_the_bulk_path_issues_a_fixed_number_of_round_trips(session, world):
    from sqlalchemy import event
    counts = {"n": 0}

    def _count(conn, cursor, statement, params, context, executemany):
        counts["n"] += 1
    bind = session.get_bind()
    event.listen(bind, "before_cursor_execute", _count)
    try:
        pairs = _pairs(world)
        counts["n"] = 0
        resolve_run_readiness_bulk(session, pairs)
        bulk_q = counts["n"]
        counts["n"] = 0
        for c, e in pairs:
            resolve_run_readiness(session, claim_test_id=c, environment_id=e)
        loop_q = counts["n"]
    finally:
        event.remove(bind, "before_cursor_execute", _count)
    # 3 set-based reads + one current-sequence read per distinct org (3 orgs here)
    assert bulk_q <= 8, f"the bulk path issued {bulk_q} queries for {len(_pairs(world))} pairs"
    assert bulk_q < loop_q, f"bulk {bulk_q} is not fewer than per-pair {loop_q}"


# 4 — the measurement AK asked for, reproducible and living with the code.
#     Gated: it builds 140 claims, which is slow for the ordinary suite.
#     Run with STEP_2A_MEASURE=1.

@pytest.mark.skipif(not __import__("os").environ.get("STEP_2A_MEASURE"),
                    reason="set STEP_2A_MEASURE=1 — builds a 140-claim page-shaped world")
def test_measure_a_requirements_shaped_page(session):
    """A 20-row Requirements page spans ~136 functional claims on production
    (one requirement alone 79). Build that shape and count what each path
    costs — queries first, because the count is the environment-independent
    number; wall time second, on the local harness so it measures the CODE."""
    import time

    from sqlalchemy import event
    _tenant(session)
    coord = SemanticTransactionCoordinator()
    org = _org(session, ENV_A, "measure-org")
    seq = _version(session, org)
    pairs = []
    for i in range(140):
        cr = _approved_claim(session, coord, key=f"P2A-MEASURE-{i}")
        for eid, et in _covered_ids(session, cr.test_id):
            _materialise_entity(session, eid, et, org, valid_from=seq)
        _seed_run(session, claim_test_id=cr.test_id, outcome="passed",
                  finished_at="2026-09-01T10:00:00+00:00", claim_version_seq=cr.version_seq,
                  environment_id=ENV_A, connected_org_id=org, org_version_seq=seq)
        pairs.append((str(cr.test_id), ENV_A))
    session.flush()

    counts = {"n": 0}

    def _count(*_a, **_k):
        counts["n"] += 1
    bind = session.get_bind()
    event.listen(bind, "before_cursor_execute", _count)
    try:
        resolve_run_readiness_bulk(session, pairs[:2])           # warm
        counts["n"] = 0; t0 = time.time()
        bulk = resolve_run_readiness_bulk(session, pairs)
        bulk_q, bulk_s = counts["n"], time.time() - t0
        counts["n"] = 0; t0 = time.time()
        loop = {(c, e): resolve_run_readiness(session, claim_test_id=c, environment_id=e)
                for c, e in pairs}
        loop_q, loop_s = counts["n"], time.time() - t0
    finally:
        event.remove(bind, "before_cursor_execute", _count)

    print(f"\n  pairs            : {len(pairs)}")
    print(f"  bulk (Step 2a)   : {bulk_q:>5} queries  {bulk_s:7.3f}s")
    print(f"  per-pair (before): {loop_q:>5} queries  {loop_s:7.3f}s")
    print(f"  identical        : {all(bulk[k].as_dict() == loop[k].as_dict() for k in loop)}")

    assert {k: v.as_dict() for k, v in bulk.items()} == {k: v.as_dict() for k, v in loop.items()}
    assert bulk_q <= 6, f"the bulk path issued {bulk_q} queries for {len(pairs)} pairs"
    assert loop_q > 3 * len(pairs) - 10, "the per-pair path should be ~3 queries per pair"
    assert {r.state for r in bulk.values()} == {READY_CURRENT}
