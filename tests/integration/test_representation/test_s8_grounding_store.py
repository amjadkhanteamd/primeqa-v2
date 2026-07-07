"""Integration: S8 recorded-verdict store + read API (D-142).

Persist a hand-built ``GroundingValidity`` to the per-tenant
``s8_grounding_validity`` table and read it back — the round-trip (incl. ``detail``
rehydration), the UPSERT (re-grounding refreshes the row), and the ``list``
filters. Reuses the package's per-test transactional ``session`` fixture; the
``20260603_0030`` migration applies via the autouse ``alembic upgrade head`` setup.
"""
from __future__ import annotations

from uuid import uuid4

from primeqa.evolution import (
    Artifact,  # noqa: F401  (kept: documents the source of a GroundingValidity)
    GroundingValidity,
    RecipeVerdict,
    S8GroundingValidity,
    list_grounding_validity,
    persist_grounding_validity,
    read_grounding_validity,
)
from primeqa.evolution.claim_grounding import ClaimGroundingResult
from primeqa.evolution.field_value_grounding import FieldValueGroundingResult
from primeqa.evolution.recipe_grounding import RecipeGroundingResult
from primeqa.test_representation.models.primitives import RejectionExpectation
from primeqa.test_representation.models.recipes.data_recipe import (
    CreateStep,
    DataRecipeBody,
)
from primeqa.test_representation.models.references import LogicalRef


def _recipe() -> DataRecipeBody:
    return DataRecipeBody(
        api_choice="rest", identity_context="system",
        execution_mechanism="direct_api",
        steps=[CreateStep(
            step_id="s1",
            target_object=LogicalRef(entity_type="Object", external_id="Account"),
            field_values={"Industry": "Mining"},
            expect_rejection=RejectionExpectation(error_code="X"))],
    )


def _gv(*, overall="broken", claim_verdict="intact") -> GroundingValidity:
    claim = ClaimGroundingResult(
        claim_verdict,
        reason=None if claim_verdict == "intact" else "subject_not_resolved",
        unresolved=() if claim_verdict == "intact" else (("Field", "Account.X"),))
    rv = RecipeVerdict(
        recipe=_recipe(),
        recipe_grounding=RecipeGroundingResult("intact"),
        field_value=FieldValueGroundingResult(
            "broken", reason="picklist_value_removed",
            invalid=(("Industry", "Mining"),)),
        rolled_up="broken")
    return GroundingValidity(claim, (rv,), overall)


def test_persist_read_round_trip(session, grounding_org):
    tid = uuid4()
    persist_grounding_validity(
        session, connected_org_id=grounding_org, test_id=tid, version_seq=3, evaluated_at_version_seq=17,
        validity=_gv(overall="broken", claim_verdict="intact"))
    session.flush()

    read = read_grounding_validity(session, tid, 3)
    assert read is not None
    assert (read.test_id, read.version_seq) == (tid, 3)
    assert read.evaluated_at_version_seq == 17
    assert (read.overall, read.claim_verdict) == ("broken", "intact")
    # detail rehydrates the claim + per-recipe leg verdicts.
    assert read.detail["claim_grounding"]["verdict"] == "intact"
    rv = read.detail["recipe_verdicts"][0]
    assert rv["recipe_grounding"]["verdict"] == "intact"
    assert rv["field_value"]["reason"] == "picklist_value_removed"
    assert rv["field_value"]["invalid"] == [["Industry", "Mining"]]
    assert rv["rolled_up"] == "broken"


def test_read_absent_is_none(session, grounding_org):
    assert read_grounding_validity(session, uuid4(), 1) is None


def test_persist_upserts_same_claim_version(session, grounding_org):
    tid = uuid4()
    persist_grounding_validity(
        session, connected_org_id=grounding_org, test_id=tid, version_seq=1, evaluated_at_version_seq=10,
        validity=_gv(overall="intact", claim_verdict="intact"))
    # re-ground the SAME claim version at a later S1 seq -> refresh, not collide.
    persist_grounding_validity(
        session, connected_org_id=grounding_org, test_id=tid, version_seq=1, evaluated_at_version_seq=20,
        validity=_gv(overall="broken", claim_verdict="broken"))
    session.flush()

    rows = session.query(S8GroundingValidity).filter_by(test_id=tid).all()
    assert len(rows) == 1                                  # one row, not two
    read = read_grounding_validity(session, tid, 1)
    assert read.evaluated_at_version_seq == 20            # refreshed
    assert (read.overall, read.claim_verdict) == ("broken", "broken")


def test_list_scopes_by_test_id(session, grounding_org):
    a, b = uuid4(), uuid4()
    persist_grounding_validity(session, connected_org_id=grounding_org, test_id=a, version_seq=1,
                               evaluated_at_version_seq=1, validity=_gv())
    persist_grounding_validity(session, connected_org_id=grounding_org, test_id=a, version_seq=2,
                               evaluated_at_version_seq=1, validity=_gv())
    persist_grounding_validity(session, connected_org_id=grounding_org, test_id=b, version_seq=1,
                               evaluated_at_version_seq=1, validity=_gv())
    session.flush()
    got = list_grounding_validity(session, test_id=a)
    assert {(r.test_id, r.version_seq) for r in got} == {(a, 1), (a, 2)}


def test_list_scopes_by_overall(session, grounding_org):
    tid = uuid4()
    persist_grounding_validity(session, connected_org_id=grounding_org, test_id=tid, version_seq=1,
                               evaluated_at_version_seq=1, validity=_gv(overall="intact"))
    persist_grounding_validity(session, connected_org_id=grounding_org, test_id=tid, version_seq=2,
                               evaluated_at_version_seq=1, validity=_gv(overall="broken"))
    session.flush()
    broken = [r for r in list_grounding_validity(session, overall="broken")
              if r.test_id == tid]
    assert [r.version_seq for r in broken] == [2]


def test_list_honors_limit(session, grounding_org):
    tid = uuid4()
    for seq in (1, 2, 3):
        persist_grounding_validity(session, connected_org_id=grounding_org, test_id=tid, version_seq=seq,
                                   evaluated_at_version_seq=1, validity=_gv())
    session.flush()
    assert len(list_grounding_validity(session, test_id=tid, limit=2)) == 2


# --- per-org grounding (3f Slices 2-3) -----------------------------------------

def _second_org(session):
    from sqlalchemy import text
    return session.execute(text(
        "INSERT INTO connected_orgs (org_type, sf_instance_url, label) "
        "VALUES ('sandbox', 'https://s8b.example', 's8-test-org-b') "
        "RETURNING CAST(id AS text)")).scalar()


def test_one_verdict_per_claim_version_per_org(session, grounding_org):
    org_b = _second_org(session)
    tid = uuid4()
    persist_grounding_validity(session, connected_org_id=grounding_org,
                               test_id=tid, version_seq=1,
                               evaluated_at_version_seq=10,
                               validity=_gv(overall="intact", claim_verdict="intact"))
    persist_grounding_validity(session, connected_org_id=org_b,
                               test_id=tid, version_seq=1,
                               evaluated_at_version_seq=20,
                               validity=_gv(overall="broken", claim_verdict="broken"))
    session.flush()
    rows = session.query(S8GroundingValidity).filter_by(test_id=tid).all()
    assert len(rows) == 2                    # one per org, not an upsert collision


def test_org_scoped_read_and_worst_of_blind_read(session, grounding_org):
    org_b = _second_org(session)
    tid = uuid4()
    persist_grounding_validity(session, connected_org_id=grounding_org,
                               test_id=tid, version_seq=1,
                               evaluated_at_version_seq=10,
                               validity=_gv(overall="intact", claim_verdict="intact"))
    persist_grounding_validity(session, connected_org_id=org_b,
                               test_id=tid, version_seq=1,
                               evaluated_at_version_seq=20,
                               validity=_gv(overall="broken", claim_verdict="broken"))
    session.flush()
    # org-scoped: each org sees its own verdict
    a = read_grounding_validity(session, tid, 1, connected_org_id=grounding_org)
    b = read_grounding_validity(session, tid, 1, connected_org_id=org_b)
    assert (a.overall, b.overall) == ("intact", "broken")
    assert str(a.connected_org_id) == grounding_org
    # org-blind: the WORST verdict wins (never MultipleResultsFound)
    blind = read_grounding_validity(session, tid, 1)
    assert blind.overall == "broken"


def test_list_filters_by_org(session, grounding_org):
    org_b = _second_org(session)
    tid = uuid4()
    persist_grounding_validity(session, connected_org_id=grounding_org,
                               test_id=tid, version_seq=1,
                               evaluated_at_version_seq=1, validity=_gv())
    persist_grounding_validity(session, connected_org_id=org_b,
                               test_id=tid, version_seq=1,
                               evaluated_at_version_seq=1, validity=_gv())
    session.flush()
    got = list_grounding_validity(session, test_id=tid,
                                  connected_org_id=grounding_org)
    assert len(got) == 1 and str(got[0].connected_org_id) == grounding_org
