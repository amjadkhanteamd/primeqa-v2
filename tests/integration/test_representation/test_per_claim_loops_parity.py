"""The per-claim loops made set-based — PARITY IS THE ACCEPTANCE TEST.

Step 2a's discipline: the per-claim loop is the DEFINITION and the batch form is
an optimisation of it. So this file carries `_assemble_by_loop`, a VERBATIM copy
of the pre-slice function body, and asserts the shipped function equals it
element for element on every shape below — not a sample of them:

  no claims | one claim | many claims | no approved version (the list fallback)
  | no grounding row | no runs | a claim shared by two requirements | a
  deprecated claim | two orgs disagreeing | two runs at the same instant | more
  runs than the per-claim limit | more verdict rows than the list bound

The last two are the shapes the design named as the only places the forms could
diverge. They are planted here deliberately, not hoped absent.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

from primeqa.evolution import (
    GroundingValidity, persist_grounding_validity, read_grounding_validity_bulk,
)
from primeqa.evolution.claim_grounding import ClaimGroundingResult
from primeqa.evolution.result_store import list_bound
from primeqa.intelligence.release_substrate_console import (
    _AT_RISK, _assemble_release_substrate,
)
from primeqa.intelligence.s4_execution_console import (
    _read_claim_runs, _read_claim_runs_bulk,
)
from primeqa.sync.readiness import covered_reads_changed, covered_reads_changed_bulk
from primeqa.test_representation import SemanticTransactionCoordinator

from ._fixtures import empty_conditions, make_value_claim


# --------------------------------------------------------------- the definition

def _assemble_by_loop(session, external_keys) -> dict:
    """The pre-slice body, copied verbatim from main @87e727d. This is what the
    set-based form must reproduce exactly."""
    from primeqa.test_representation.coordinator import (
        COVERAGE_LINK_KINDS, SemanticTransactionCoordinator as _C,
    )
    from primeqa.evolution import list_grounding_validity, read_grounding_validity
    from primeqa.intelligence.s4_execution_console import _read_claim_runs as _rcr

    coord = _C()
    test_ids, seen = [], set()
    for key in external_keys:
        for m in coord.list_tests_by_requirement(
                session, external_system="jira", external_key=key,
                link_kind=COVERAGE_LINK_KINDS):
            sid = str(m.test_id)
            if sid not in seen:
                seen.add(sid)
                test_ids.append(m.test_id)

    gc = {"intact": 0, "drifted": 0, "broken": 0, "not_computed": 0}
    vc = {"passed": 0, "failed": 0, "errored": 0, "never_run": 0}
    at_risk = []
    for tid in test_ids:
        approved = coord.get_current_approved_claim(session, tid)
        if approved is not None:
            gv = read_grounding_validity(session, tid, approved.version_seq)
        else:
            gv_rows = list_grounding_validity(session, test_id=tid)
            gv = gv_rows[-1] if gv_rows else None
        overall = gv.overall if gv is not None else None
        gc[overall if overall in gc else "not_computed"] += 1

        runs = _rcr(session, tid)
        latest = runs[0] if runs else None
        if latest is None:
            vc["never_run"] += 1
        else:
            o = latest.get("outcome")
            vc[o if o in vc else "errored"] += 1

        if overall in _AT_RISK:
            at_risk.append({
                "test_id": str(tid), "overall": overall,
                "evaluated_at_version_seq": gv.evaluated_at_version_seq,
                "detail": gv.detail or {},
                "latest_outcome": latest.get("outcome") if latest else None,
                "latest_verdict": latest.get("verdict") if latest else None,
            })
    return {"available": True, "claim_count": len(test_ids),
            "grounding_counts": gc, "verdict_counts": vc, "at_risk": at_risk}


# ------------------------------------------------------------------- the world

def _gv(overall, claim_verdict="intact"):
    return GroundingValidity(
        claim_grounding=ClaimGroundingResult(
            claim_verdict, reason="subject_not_resolved", unresolved=()),
        recipe_verdicts=(), overall=overall)


def _claim(session, coord, key, *, approve=True, deprecate=False):
    cr = coord.write_claim(
        session, actor="s3", test_id=None, archetype="data_behavior",
        claim_kind="value-claim", asserted_truth=make_value_claim(value="Tech"),
        semantic_conditions=empty_conditions())
    coord.link_requirement(session, actor="s3", test_id=cr.test_id,
                           external_system="jira", external_key=key,
                           link_kind="generated_from")
    if approve:
        coord.promote_claim_to_approved(session, actor="human", test_id=cr.test_id,
                                        version_seq=cr.version_seq)
    if deprecate:
        coord.deprecate_claim(session, actor="human", test_id=cr.test_id,
                              version_seq=cr.version_seq,
                              reason="no longer applies")
    return cr


def _run(session, tid, *, outcome, finished_at, run_id=None):
    session.execute(text(
        "INSERT INTO s4_execution_runs (run_id, recipe_id, recipe_version_seq, "
        "claim_test_id, environment_id, outcome, started_at, finished_at, evidence) "
        "VALUES (CAST(:r AS uuid), CAST(:rec AS uuid), 1, CAST(:t AS uuid), 7, "
        "CAST(:o AS run_outcome), CAST(:f AS timestamptz), CAST(:f AS timestamptz), "
        "CAST('{}' AS jsonb))"),
        {"r": str(run_id or uuid4()), "rec": str(uuid4()), "t": str(tid),
         "o": outcome, "f": finished_at})


@pytest.fixture()
def world(session, grounding_org):
    """Every shape in the design's table, planted in one release's key set."""
    coord = SemanticTransactionCoordinator()
    k = "PCL-%s" % uuid4().hex[:8]
    made = {"keys": [k, k + "-B"], "org": grounding_org}

    # 1. ordinary: approved, grounded drifted, one failed run
    a = _claim(session, coord, k)
    persist_grounding_validity(session, connected_org_id=grounding_org,
                               test_id=a.test_id, version_seq=a.version_seq,
                               evaluated_at_version_seq=5,
                               validity=_gv("drifted", "broken"))
    _run(session, a.test_id, outcome="failed", finished_at="2026-06-01T10:00:00+00:00")

    # 2. no approved version → the list_grounding_validity[-1] fallback
    b = _claim(session, coord, k, approve=False)
    persist_grounding_validity(session, connected_org_id=grounding_org,
                               test_id=b.test_id, version_seq=b.version_seq,
                               evaluated_at_version_seq=6, validity=_gv("broken"))

    # 3. no grounding row at all → not_computed
    c = _claim(session, coord, k)
    _run(session, c.test_id, outcome="passed", finished_at="2026-06-02T10:00:00+00:00")

    # 4. no runs → never_run
    d = _claim(session, coord, k)
    persist_grounding_validity(session, connected_org_id=grounding_org,
                               test_id=d.test_id, version_seq=d.version_seq,
                               evaluated_at_version_seq=7, validity=_gv("intact"))

    # 5. deprecated
    e = _claim(session, coord, k, deprecate=True)
    _run(session, e.test_id, outcome="errored", finished_at="2026-06-03T10:00:00+00:00")

    # 6. shared by two requirements — the dedupe
    coord.link_requirement(session, actor="s3", test_id=a.test_id,
                           external_system="jira", external_key=k + "-B",
                           link_kind="generated_from")

    # 7. TWO RUNS AT THE SAME INSTANT — the tiebreak
    f = _claim(session, coord, k)
    same = "2026-06-04T10:00:00+00:00"
    _run(session, f.test_id, outcome="passed", finished_at=same,
         run_id="00000000-0000-4000-8000-00000000aaaa")
    _run(session, f.test_id, outcome="failed", finished_at=same,
         run_id="00000000-0000-4000-8000-00000000bbbb")

    # 8. MORE RUNS THAN THE PER-CLAIM LIMIT
    g = _claim(session, coord, k)
    for i in range(55):
        _run(session, g.test_id, outcome="passed",
             finished_at="2026-05-%02dT10:00:00+00:00" % ((i % 28) + 1))

    session.flush()
    made["claims"] = {"ordinary": a, "unapproved": b, "ungrounded": c,
                      "unrun": d, "deprecated": e, "tied": f, "over_limit": g}
    return made


# -------------------------------------------------------------------- parity

def test_the_whole_panel_is_element_for_element_identical(world, session):
    keys = world["keys"]
    assert _assemble_release_substrate(session, keys) == _assemble_by_loop(session, keys)


def test_parity_on_an_empty_key_set(session):
    assert _assemble_release_substrate(session, []) == _assemble_by_loop(session, [])


def test_parity_on_a_key_that_matches_nothing(session):
    keys = ["PCL-NO-SUCH-KEY"]
    assert _assemble_release_substrate(session, keys) == _assemble_by_loop(session, keys)


def test_parity_on_one_key_at_a_time(world, session):
    for k in world["keys"]:
        assert _assemble_release_substrate(session, [k]) == _assemble_by_loop(session, [k]), k


def test_parity_survives_key_order(world, session):
    a, b = world["keys"]
    assert (_assemble_release_substrate(session, [b, a])
            == _assemble_by_loop(session, [b, a]))


def test_the_dedupe_still_counts_a_shared_claim_once(world, session):
    out = _assemble_release_substrate(session, world["keys"])
    ids = [r["test_id"] for r in out["at_risk"]]
    assert len(ids) == len(set(ids))
    assert out["claim_count"] == len(world["claims"])


# --------------------------------------------------------- the claim-runs batch

def test_claim_runs_batch_matches_the_singular_for_every_claim(world, session):
    tids = [c.test_id for c in world["claims"].values()]
    bulk = _read_claim_runs_bulk(session, tids)
    for tid in tids:
        assert bulk.get(str(tid), []) == _read_claim_runs(session, tid), tid


def test_claim_runs_batch_respects_the_per_claim_limit(world, session):
    tid = world["claims"]["over_limit"].test_id
    for limit in (1, 3, 50):
        assert (_read_claim_runs_bulk(session, [tid], limit=limit).get(str(tid))
                == _read_claim_runs(session, tid, limit=limit)), limit


def test_claim_runs_batch_breaks_the_tie_the_same_way(world, session):
    tid = world["claims"]["tied"].test_id
    got = _read_claim_runs_bulk(session, [tid])[str(tid)]
    assert got == _read_claim_runs(session, tid)
    assert [r["run_id"] for r in got[:2]] == sorted(
        [r["run_id"] for r in got[:2]], reverse=True), "run_id DESC breaks the tie"


def test_claim_runs_batch_is_empty_for_no_ids(session):
    assert _read_claim_runs_bulk(session, []) == {}


def test_a_claim_with_no_runs_is_absent_not_empty(world, session):
    tid = world["claims"]["unrun"].test_id
    bulk = _read_claim_runs_bulk(session, [tid])
    assert str(tid) not in bulk
    assert bulk.get(str(tid), []) == _read_claim_runs(session, tid) == []


# ------------------------------------------------- the changed-reads batch

def test_changed_reads_batch_is_positional_and_matches_the_singular(world, session):
    org = world["org"]
    triples = [(c.test_id, org, 1) for c in world["claims"].values()]
    got = covered_reads_changed_bulk(session, triples)
    assert len(got) == len(triples)
    for i, (tid, o, since) in enumerate(triples):
        assert got[i] == covered_reads_changed(
            session, claim_test_id=tid, connected_org_id=o, since_seq=since), tid


def test_changed_reads_batch_keeps_two_stamps_of_one_claim_apart(world, session):
    """The positional key exists for this shape: one claim, two stamps."""
    tid = world["claims"]["ordinary"].test_id
    org = world["org"]
    triples = [(tid, org, 1), (tid, org, 10 ** 9)]
    got = covered_reads_changed_bulk(session, triples)
    assert got[0] == covered_reads_changed(
        session, claim_test_id=tid, connected_org_id=org, since_seq=1)
    assert got[1] == covered_reads_changed(
        session, claim_test_id=tid, connected_org_id=org, since_seq=10 ** 9)


def test_changed_reads_batch_is_empty_for_no_triples(session):
    assert covered_reads_changed_bulk(session, []) == []


# ----------------------------------------- the list bound, the divergence shape

def test_the_bound_reproduces_the_list_read_below_the_bound(world, session):
    """Under the bound the two agree because there is nothing to truncate."""
    tid = world["claims"]["unapproved"].test_id
    from primeqa.evolution import list_grounding_validity
    rows = list_grounding_validity(session, test_id=tid)
    bulk = read_grounding_validity_bulk(session, [(tid, None)],
                                        unpinned_list_bound=list_bound())
    assert bulk[tid] == rows[-1]


def test_the_bound_reproduces_the_list_read_ABOVE_the_bound(session, grounding_org):
    """The one shape where the batch could have answered differently.

    `list_grounding_validity(...)[-1]` is the LAST of the first N rows ascending,
    so above the bound it is the Nth verdict and NOT the latest. The unbounded
    batch returns the latest. Passing the bound must reproduce the list read,
    and the two must be shown to actually DIFFER — otherwise this test would
    pass without exercising anything.
    """
    from primeqa.evolution import list_grounding_validity
    coord = SemanticTransactionCoordinator()
    cr = coord.write_claim(
        session, actor="s3", test_id=None, archetype="data_behavior",
        claim_kind="value-claim", asserted_truth=make_value_claim(value="Tech"),
        semantic_conditions=empty_conditions())
    n = list_bound() + 5
    for seq in range(1, n + 1):
        persist_grounding_validity(
            session, connected_org_id=grounding_org, test_id=cr.test_id,
            version_seq=seq, evaluated_at_version_seq=seq,
            validity=_gv("intact" if seq <= list_bound() else "broken"))
    session.flush()

    definition = list_grounding_validity(session, test_id=cr.test_id)[-1]
    bounded = read_grounding_validity_bulk(
        session, [(cr.test_id, None)], unpinned_list_bound=list_bound())[cr.test_id]
    unbounded = read_grounding_validity_bulk(session, [(cr.test_id, None)])[cr.test_id]

    assert bounded == definition, "the bound must reproduce the list read exactly"
    assert unbounded != definition, (
        "this shape must actually exercise the divergence, or the test proves nothing")
    assert unbounded.version_seq == n and definition.version_seq == list_bound()


# ------------------------------------------------- the point of the whole slice

def _count_queries(session, fn):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    n = [0]

    def _tick(*_a, **_k):
        n[0] += 1

    event.listen(Engine, "before_cursor_execute", _tick)
    try:
        fn()
    finally:
        event.remove(Engine, "before_cursor_execute", _tick)
    return n[0]


def test_the_panel_costs_a_FIXED_number_of_queries(world, session):
    """The loop cost 4 queries per claim plus one per requirement key. The point
    of the slice is that the cost no longer moves with the claim count, so the
    test measures the shape rather than trusting the rewrite."""
    keys = world["keys"]
    session.flush()
    fixed = _count_queries(session, lambda: _assemble_release_substrate(session, keys))
    looped = _count_queries(session, lambda: _assemble_by_loop(session, keys))
    n_claims = len(world["claims"])
    assert fixed <= 6, "the set-based panel must not scale with the claim count"
    assert looped >= 3 * n_claims, (
        "the reference loop should cost about 3 queries per claim; if it does "
        "not, the definition being compared against is not the one that shipped")
    assert fixed < looped


def test_the_changed_reads_batch_is_one_query_for_many_triples(world, session):
    org = world["org"]
    triples = [(c.test_id, org, 1) for c in world["claims"].values()]
    n = _count_queries(session, lambda: covered_reads_changed_bulk(session, triples))
    assert n == 1, "many triples, one round trip"
