"""Integration: S6 in-substrate consumer (D-137).

The read API (``read_interpretation`` / ``list_interpretations`` + the
``InterpretationRead`` DTO), the phrasing live-fire (``read_and_phrase`` —
flag-as-param, the enricher stubbed), and the re-exported clustering read
surface, over the ``s6_interpretations`` store. Plus the v1 flag reader
``interpretation_phrasing_enabled`` (mocked db — fails closed).

Seeds interpretations directly via ``persist_interpretation`` (the store has only
logical FKs, so no run-path arrangement is needed) and scopes queries to a
test-local ``recipe_id`` for isolation. Reuses this package's per-test
transactional ``session`` fixture; the tenant migrations apply via the package's
``alembic upgrade head`` setup. The enricher's ``llm_call`` is stubbed (no
credits).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

from primeqa.intelligence import interpretation_phrasing as ip
from primeqa.interpretation import (
    InterpretationRead,
    cluster_recurring_causes,
    list_interpretations,
    read_interpretation,
)
from primeqa.interpretation.model import Cause, EvidenceRef, Interpretation
from primeqa.interpretation.result_store import (
    S6Interpretation,
    persist_interpretation,
)

_PATCH = "primeqa.intelligence.interpretation_phrasing.llm_call"


def _interp(*, recipe_id=None, claim_test_id=None, outcome="failed",
            verdict="prohibition_not_enforced", attribution="the rule did not block",
            refs=(), cause_kind=None, vr_name=None) -> Interpretation:
    cause = Cause(cause_kind=cause_kind, vr_name=vr_name) if cause_kind else None
    return Interpretation(
        run_id=uuid4(), recipe_id=recipe_id or uuid4(),
        claim_test_id=claim_test_id or uuid4(), outcome=outcome, verdict=verdict,
        attribution=attribution, evidence_refs=tuple(refs), cause=cause)


def _fake_resp(parsed):
    r = MagicMock()
    r.parsed_content = parsed
    r.model = "haiku"
    r.prompt_version = "interpretation_phrasing@v1"
    return r


_GOOD = {"headline": "Rule not enforced", "explanation": "The org accepted it."}


# ---------------------------------------------------------------------------
# read_interpretation — single-run round-trip (incl. cause + evidence rehydration)
# ---------------------------------------------------------------------------

def test_read_interpretation_round_trips_with_cause_and_refs(session):
    i = _interp(
        outcome="failed", verdict="prohibition_not_enforced",
        refs=(EvidenceRef(step_id="create-record", detail="create succeeded (http 201)"),),
        cause_kind="enforcement_gap", vr_name="Lead.RequireReason")
    persist_interpretation(session, i)
    session.flush()

    read = read_interpretation(session, i.run_id)
    assert isinstance(read, InterpretationRead)
    assert (read.run_id, read.recipe_id, read.claim_test_id) == (
        i.run_id, i.recipe_id, i.claim_test_id)
    assert (read.outcome, read.verdict) == ("failed", "prohibition_not_enforced")
    assert read.attribution == "the rule did not block"
    # evidence refs rehydrate to typed EvidenceRef objects.
    assert read.evidence_refs == (
        EvidenceRef(step_id="create-record", detail="create succeeded (http 201)"),)
    # the structured cause rehydrates from detail JSONB to a typed Cause.
    assert isinstance(read.cause, Cause)
    assert (read.cause.cause_kind, read.cause.vr_name) == (
        "enforcement_gap", "Lead.RequireReason")
    assert read.phrasing is None                       # nothing phrased yet


def test_read_interpretation_absent_is_none(session):
    assert read_interpretation(session, uuid4()) is None


def test_read_interpretation_no_cause_hydrates_none(session):
    i = _interp(outcome="passed", verdict="asserted_metadata_present")
    persist_interpretation(session, i)
    session.flush()
    read = read_interpretation(session, i.run_id)
    assert read.cause is None and read.evidence_refs == ()


# ---------------------------------------------------------------------------
# list_interpretations — scoping + bound
# ---------------------------------------------------------------------------

def test_list_interpretations_scopes_by_recipe(session):
    rid = uuid4()
    other = uuid4()
    a = _interp(recipe_id=rid, verdict="value_persisted", outcome="passed")
    b = _interp(recipe_id=rid, verdict="value_not_persisted", outcome="failed")
    c = _interp(recipe_id=other, verdict="value_persisted", outcome="passed")
    for i in (a, b, c):
        persist_interpretation(session, i)
    session.flush()

    got = list_interpretations(session, recipe_id=rid)
    ids = {r.run_id for r in got}
    assert ids == {a.run_id, b.run_id} and c.run_id not in ids
    assert all(isinstance(r, InterpretationRead) for r in got)


def test_list_interpretations_honors_limit(session):
    rid = uuid4()
    for _ in range(3):
        persist_interpretation(session, _interp(recipe_id=rid))
    session.flush()
    assert len(list_interpretations(session, recipe_id=rid, limit=2)) == 2


def test_list_interpretations_scopes_by_claim(session):
    rid, ct = uuid4(), uuid4()
    keep = _interp(recipe_id=rid, claim_test_id=ct)
    drop = _interp(recipe_id=rid, claim_test_id=uuid4())
    persist_interpretation(session, keep)
    persist_interpretation(session, drop)
    session.flush()
    got = list_interpretations(session, recipe_id=rid, claim_test_id=ct)
    assert [r.run_id for r in got] == [keep.run_id]


# ---------------------------------------------------------------------------
# read_and_phrase — the phrasing live-fire (flag-as-param, enricher stubbed)
# ---------------------------------------------------------------------------

def test_read_and_phrase_enabled_attaches_and_caches(session):
    i = _interp(cause_kind="enforcement_gap", vr_name="VR_A")
    persist_interpretation(session, i)
    session.flush()

    with patch(_PATCH, return_value=_fake_resp(_GOOD)) as spy:
        out = ip.read_and_phrase(
            session, i.run_id, tenant_id=1, api_key="k", phrasing_enabled=True)
    assert isinstance(out, InterpretationRead)
    assert out.phrasing["headline"] == "Rule not enforced"   # attached to the DTO
    assert spy.call_count == 1
    # cached on the row (so a later read / second call is a cache hit).
    row = session.query(S6Interpretation).filter_by(run_id=i.run_id).one()
    assert row.phrasing["headline"] == "Rule not enforced"


def test_read_and_phrase_disabled_is_unphrased_no_llm(session):
    i = _interp()
    persist_interpretation(session, i)
    session.flush()
    with patch(_PATCH, return_value=_fake_resp(_GOOD)) as spy:
        out = ip.read_and_phrase(
            session, i.run_id, tenant_id=1, api_key="k", phrasing_enabled=False)
    assert out.phrasing is None
    assert spy.call_count == 0                          # gate short-circuits the LLM
    row = session.query(S6Interpretation).filter_by(run_id=i.run_id).one()
    assert row.phrasing is None                         # nothing cached


def test_read_and_phrase_failure_is_unphrased(session):
    i = _interp()
    persist_interpretation(session, i)
    session.flush()
    # a non-dict parsed_content makes the enricher return None (best-effort).
    with patch(_PATCH, return_value=_fake_resp("not a dict")):
        out = ip.read_and_phrase(
            session, i.run_id, tenant_id=1, api_key="k", phrasing_enabled=True)
    assert out.phrasing is None                         # unphrased read returned
    row = session.query(S6Interpretation).filter_by(run_id=i.run_id).one()
    assert row.phrasing is None                         # nothing cached on failure


def test_read_and_phrase_absent_run_is_none(session):
    with patch(_PATCH, return_value=_fake_resp(_GOOD)) as spy:
        out = ip.read_and_phrase(
            session, uuid4(), tenant_id=1, api_key="k", phrasing_enabled=True)
    assert out is None
    assert spy.call_count == 0


# ---------------------------------------------------------------------------
# interpretation_phrasing_enabled — the v1 flag reader (fails closed)
# ---------------------------------------------------------------------------

def test_phrasing_enabled_true_when_flag_set():
    db = MagicMock()
    db.execute.return_value.first.return_value = (True,)
    assert ip.interpretation_phrasing_enabled(db, tenant_id=1) is True


def test_phrasing_enabled_false_when_flag_unset_or_absent():
    db = MagicMock()
    db.execute.return_value.first.return_value = (False,)
    assert ip.interpretation_phrasing_enabled(db, tenant_id=1) is False
    db.execute.return_value.first.return_value = None      # absent row
    assert ip.interpretation_phrasing_enabled(db, tenant_id=1) is False


def test_phrasing_enabled_fails_closed_on_error():
    db = MagicMock()
    db.execute.side_effect = RuntimeError("no such column")   # migration 050 unapplied
    assert ip.interpretation_phrasing_enabled(db, tenant_id=1) is False


# ---------------------------------------------------------------------------
# the clustering read surface (re-exported from the package)
# ---------------------------------------------------------------------------

def test_clustering_recurring_cause_via_reexported_surface(session):
    rid = uuid4()
    for _ in range(2):
        persist_interpretation(session, _interp(
            recipe_id=rid, outcome="failed", verdict="prohibition_not_enforced",
            cause_kind="enforcement_gap", vr_name="VR_A"))
    session.flush()
    clusters = cluster_recurring_causes(session, recipe_id=rid, min_runs=2)
    assert [(c.cause_kind, c.count) for c in clusters] == [("enforcement_gap", 2)]
