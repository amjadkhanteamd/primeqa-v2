"""S7 retrieval recipes — governance on the semantic conn+seed harness (D-163.2).

Exercises each deterministic recipe against seeded substrate rows via the bridge's
own dual-derivation: one tenant ``conn`` → ``Session(bind=conn)`` for the S6/S8/S2
ORM reads + ``SemanticOrgModel(conn)`` for S1. S6 seeded via
``persist_interpretation``, S8 via ``persist_grounding_validity`` (the
``test_s6_s8_insights_surface`` helpers), S1 via the ``seed`` fixture. Every write
rolls back.
"""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from primeqa.conversation import QuestionContext
from primeqa.conversation.retrieval import (
    retrieve_failure_cause,
    retrieve_grounding_drift,
    retrieve_impact,
)
from primeqa.evolution import GroundingValidity, persist_grounding_validity
from primeqa.evolution.claim_grounding import ClaimGroundingResult
from primeqa.interpretation.model import Cause, Interpretation
from primeqa.interpretation.result_store import persist_interpretation
from primeqa.semantic.query import SemanticOrgModel

_CTX = QuestionContext(tenant_id=1)


def _seed_run(session, recipe_id, claim_test_id, outcome):
    """A real ``s4_execution_runs`` row for the verdict to hang on — D-497's
    ``fk_s6_interpretations_run`` (AUD-026: a verdict cannot exist without its
    run) refuses an invented run id."""
    run_id = uuid4()
    session.execute(text(
        "INSERT INTO s4_execution_runs (run_id, recipe_id, recipe_version_seq, claim_test_id, "
        "claim_version_seq, environment_id, outcome, started_at, finished_at, evidence) "
        "VALUES (CAST(:r AS uuid), CAST(:p AS uuid), 1, CAST(:c AS uuid), 1, 4, "
        "CAST(:o AS run_outcome), now(), now(), '{}'::jsonb)"),
        {"r": str(run_id), "p": str(recipe_id), "c": str(claim_test_id), "o": outcome})
    return run_id


def _interp(*, recipe_id, claim_test_id, outcome, verdict, cause_kind=None, vr_name=None, session=None):
    cause = Cause(cause_kind=cause_kind, vr_name=vr_name) if cause_kind else None
    run_id = _seed_run(session, recipe_id, claim_test_id, outcome) if session is not None else uuid4()
    return Interpretation(run_id=run_id, recipe_id=recipe_id,
                          claim_test_id=claim_test_id, outcome=outcome,
                          verdict=verdict, attribution="seeded", cause=cause)


def _gv(*, overall, claim_verdict="intact"):
    return GroundingValidity(
        claim_grounding=ClaimGroundingResult(claim_verdict, reason=None, unresolved=()),
        recipe_verdicts=(), overall=overall)


def test_failure_cause_surfaces_interpretations_and_clusters(conn, seed):
    session = Session(bind=conn)
    rid = uuid4()
    for _ in range(2):                          # enforcement_gap x2 / VR_A x2 → recur
        persist_interpretation(session, _interp(
            recipe_id=rid, claim_test_id=uuid4(), outcome="failed",
            verdict="prohibition_not_enforced",
            cause_kind="enforcement_gap", vr_name="VR_A", session=session))
    session.flush()
    items = retrieve_failure_cause(None, session, _CTX)
    kinds = {it.kind for it in items}
    assert {"interpretation", "cause_cluster", "vr_cluster"} <= kinds
    assert all(it.source == "S6" for it in items)
    causes = [it for it in items if it.kind == "cause_cluster"]
    assert causes and causes[0].data["cause_kind"] == "enforcement_gap"


def test_grounding_drift_only_drifted_and_broken(conn, seed):
    from sqlalchemy import text
    session = Session(bind=conn)
    org = session.execute(text(
        "INSERT INTO connected_orgs (org_type, sf_instance_url, label) "
        "VALUES ('sandbox', 'https://s8.example', 's8-org') "
        "RETURNING CAST(id AS text)")).scalar()
    persist_grounding_validity(session, test_id=uuid4(), version_seq=1,
                               evaluated_at_version_seq=5, validity=_gv(overall="drifted"),
                               connected_org_id=org)
    persist_grounding_validity(session, test_id=uuid4(), version_seq=1,
                               evaluated_at_version_seq=5, validity=_gv(overall="broken"),
                               connected_org_id=org)
    persist_grounding_validity(session, test_id=uuid4(), version_seq=1,
                               evaluated_at_version_seq=5, validity=_gv(overall="intact"),
                               connected_org_id=org)
    session.flush()
    items = retrieve_grounding_drift(None, session, _CTX)
    assert {it.data["overall"] for it in items} == {"drifted", "broken"}   # intact excluded
    assert all(it.source == "S8" and it.kind == "grounding_validity" for it in items)
    assert all(it.data["connected_org_id"] == org for it in items)   # org rides the citation


def test_impact_walks_object_inbound_edges(conn, seed):
    session = Session(bind=conn)
    v1 = seed.version()
    obj = seed.entity("Object", "Account", v1)
    fld = seed.entity("Field", "Account.Name", v1)
    seed.edge(fld, obj, "BELONGS_TO", "STRUCTURAL", v1)
    vr = seed.entity("ValidationRule", "Account.RequireName", v1)
    seed.edge(vr, obj, "APPLIES_TO", "BEHAVIOR", v1)
    s1 = SemanticOrgModel(conn)

    items = retrieve_impact(
        s1, session, QuestionContext(tenant_id=1, object_api_name="Account"))
    kinds = [it.kind for it in items]
    assert "entity" in kinds                    # the Account object itself
    assert "related_entity" in kinds            # its inbound field + VR
    edge_types = {it.data.get("edge_type") for it in items if it.kind == "related_entity"}
    assert {"BELONGS_TO", "APPLIES_TO"} <= edge_types
    assert all(it.source == "S1" for it in items)


def test_impact_empty_when_object_absent(conn, seed):
    session = Session(bind=conn)
    seed.version()                              # a version exists, but no Account
    s1 = SemanticOrgModel(conn)
    items = retrieve_impact(
        s1, session, QuestionContext(tenant_id=1, object_api_name="Ghost__c"))
    assert items == ()


def test_recipes_empty_on_unseeded_stores(conn, seed):
    session = Session(bind=conn)
    assert retrieve_failure_cause(None, session, _CTX) == ()
    assert retrieve_grounding_drift(None, session, _CTX) == ()
