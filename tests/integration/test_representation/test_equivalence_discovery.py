"""Equivalence + discovery tests per Track D-γ.1.

Per SPEC §6.3.9 Rule 4 + §10.2. Covers:
  - :meth:`query_equivalent_claims` is scoped to a specific
    ``identity_hash_version``; cross-version matches are silently
    excluded.
  - :meth:`list_tests_affected_by_entity` walks coverage by
    ``entity_id`` with optional ``entity_type`` and
    ``reference_kind`` filters.
  - :meth:`list_tests_by_requirement` walks
    ``test_requirement_links`` by ``(external_system,
    external_key)`` with optional ``link_kind`` filter.

``test_requirement_links`` has no Coordinator write method yet
(D-δ work); tests insert rows directly via the ORM.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from primeqa.test_representation import (
    Condition,
    CoverageMatch,
    IdentityBearingRef,
    LiteralValue,
    RequirementMatch,
    SemanticConditionsBody,
    SemanticTransactionCoordinator,
    TestClaim,
    TestRequirementLink,
)

from ._builders import (
    empty_conditions,
    ib,
    make_value_claim,
)


# ---------------------------------------------------------------------------
# query_equivalent_claims
# ---------------------------------------------------------------------------


def test_query_equivalent_claims_returns_both_when_hashes_match(
    session,
) -> None:
    """Two distinct test_ids with structurally identical bodies
    produce the same identity_hash → both surface from the
    query."""
    coord = SemanticTransactionCoordinator()
    # Same subject entity, same value → same hash.
    subject = ib()
    body_a = make_value_claim(subject=subject, value="Tech")
    body_b = make_value_claim(subject=subject, value="Tech")
    r_a = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body_a, semantic_conditions=empty_conditions(),
    )
    r_b = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body_b, semantic_conditions=empty_conditions(),
    )
    assert r_a.identity_hash == r_b.identity_hash
    matches = coord.query_equivalent_claims(
        session,
        identity_hash=r_a.identity_hash,
        identity_hash_version=1,
    )
    test_ids = {m.test_id for m in matches}
    assert {r_a.test_id, r_b.test_id}.issubset(test_ids)


def test_query_equivalent_claims_scoped_to_policy_version(
    session,
) -> None:
    """Per SPEC §6.3.9 Rule 4: hashes computed under different
    canonicalization policy versions are NOT equivalent. Two
    rows with the same identity_hash but different
    identity_hash_version surface only the one matching the
    queried version."""
    coord = SemanticTransactionCoordinator()
    body = make_value_claim()
    r1 = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body, semantic_conditions=empty_conditions(),
    )
    # Simulate a hypothetical v2 hash via direct insert. Provide
    # a structurally-valid body so the read path's deserialization
    # succeeds; the test asserts about the policy-version filter,
    # not the body contents.
    v2_test_id = uuid4()
    session.add(TestClaim(
        test_id=v2_test_id,
        version_seq=1,
        archetype="data_behavior",
        claim_kind="value-claim",
        asserted_truth={
            "body_schema_version": 1, "kind": "value-claim",
            "subject": {
                "ref_kind": "pinned",
                "entity_type": "Field",
                "entity_id": str(uuid4()),
                "version_seq": 1,
                "external_id": "X.Y",
            },
            "expected_value": {"kind": "literal", "value": "Tech"},
        },
        semantic_conditions={
            "body_schema_version": 1, "kind": "conditions",
            "conditions": [],
        },
        identity_hash=r1.identity_hash,  # same hash text
        identity_hash_version=2,  # different policy version
        status="draft",
    ))
    session.flush()
    # Query for v1 — only r1 surfaces.
    matches_v1 = coord.query_equivalent_claims(
        session,
        identity_hash=r1.identity_hash,
        identity_hash_version=1,
    )
    assert {m.test_id for m in matches_v1} == {r1.test_id}
    # Query for v2 — only the synthetic v2 row surfaces.
    matches_v2 = coord.query_equivalent_claims(
        session,
        identity_hash=r1.identity_hash,
        identity_hash_version=2,
    )
    assert {m.test_id for m in matches_v2} == {v2_test_id}


def test_query_equivalent_claims_excludes_historical_versions(
    session,
) -> None:
    """Historical (``valid_to IS NOT NULL``) versions of the
    same test_id sharing the hash are excluded — the query
    surfaces current canonical versions only."""
    coord = SemanticTransactionCoordinator()
    body = make_value_claim(value="Tech")
    r1 = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body, semantic_conditions=empty_conditions(),
    )
    # S8 hash-preserving rewrite → v2 with the same hash, v1
    # closed.
    r2 = coord.write_claim(
        session,
        actor="s8", test_id=r1.test_id,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body, semantic_conditions=empty_conditions(),
    )
    assert r1.identity_hash == r2.identity_hash
    matches = coord.query_equivalent_claims(
        session,
        identity_hash=r1.identity_hash,
        identity_hash_version=1,
    )
    # Only one match (the current v2 row); the v1 closed row
    # is excluded.
    assert len(matches) == 1
    assert matches[0].test_id == r1.test_id
    assert matches[0].version_seq == 2


def test_query_equivalent_claims_unknown_hash_returns_empty(
    session,
) -> None:
    coord = SemanticTransactionCoordinator()
    matches = coord.query_equivalent_claims(
        session,
        identity_hash="deadbeef" * 8,
        identity_hash_version=1,
    )
    assert matches == []


def test_query_equivalent_claims_requires_both_parameters(
    session,
) -> None:
    """Per SPEC §6.3.9 Rule 4: passing identity_hash without
    identity_hash_version would silently return cross-policy
    matches. The keyword-only signature makes a single-arg
    call a TypeError at call time."""
    coord = SemanticTransactionCoordinator()
    with pytest.raises(TypeError):
        coord.query_equivalent_claims(
            session, identity_hash="x" * 64,
        )
    with pytest.raises(TypeError):
        coord.query_equivalent_claims(
            session, identity_hash_version=1,
        )


# ---------------------------------------------------------------------------
# list_tests_affected_by_entity
# ---------------------------------------------------------------------------


def test_list_tests_affected_by_entity_subject_reference(session) -> None:
    coord = SemanticTransactionCoordinator()
    subject = ib(entity_type="Field", external_id="Account.Industry")
    body = make_value_claim(subject=subject, value="Tech")
    r = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body, semantic_conditions=empty_conditions(),
    )
    matches = coord.list_tests_affected_by_entity(
        session, entity_id=subject.entity_id,
    )
    assert len(matches) == 1
    assert isinstance(matches[0], CoverageMatch)
    assert matches[0].test_id == r.test_id
    assert matches[0].reference_kind == "subject"


def test_list_tests_affected_by_entity_condition_reference(
    session,
) -> None:
    """A reference inside ``semantic_conditions`` lands as
    ``reference_kind="condition"``."""
    coord = SemanticTransactionCoordinator()
    subject = ib(external_id="A.B")
    cond_subject = ib(external_id="X.Y", entity_id=uuid4())
    body = make_value_claim(subject=subject, value="Tech")
    conditions = SemanticConditionsBody(conditions=[
        Condition(
            subject=cond_subject, predicate="is_not_null",
        ),
    ])
    r = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body, semantic_conditions=conditions,
    )
    # The condition entity is found as a condition reference:
    cond_matches = coord.list_tests_affected_by_entity(
        session, entity_id=cond_subject.entity_id,
    )
    assert len(cond_matches) == 1
    assert cond_matches[0].test_id == r.test_id
    assert cond_matches[0].reference_kind == "condition"


def test_list_tests_affected_by_entity_filter_by_reference_kind(
    session,
) -> None:
    coord = SemanticTransactionCoordinator()
    same_entity = ib(external_id="Shared.X", entity_id=uuid4())
    # Reference the same entity as BOTH subject and condition.
    body = make_value_claim(subject=same_entity, value="Tech")
    conditions = SemanticConditionsBody(conditions=[
        Condition(subject=same_entity, predicate="is_not_null"),
    ])
    r = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body, semantic_conditions=conditions,
    )
    # No filter → both kinds returned.
    all_matches = coord.list_tests_affected_by_entity(
        session, entity_id=same_entity.entity_id,
    )
    assert len(all_matches) == 2
    kinds = {m.reference_kind for m in all_matches}
    assert kinds == {"subject", "condition"}
    # Filter to subject only.
    subj_only = coord.list_tests_affected_by_entity(
        session, entity_id=same_entity.entity_id,
        reference_kind="subject",
    )
    assert len(subj_only) == 1
    assert subj_only[0].reference_kind == "subject"


def test_list_tests_affected_by_entity_filter_by_entity_type(
    session,
) -> None:
    coord = SemanticTransactionCoordinator()
    eid = uuid4()
    # Reference the same entity_id under two different types
    # (pathological but supported).
    field_ref = IdentityBearingRef(
        entity_type="Field", entity_id=eid,
        version_seq=1, external_id="Acct.F1",
    )
    obj_ref = IdentityBearingRef(
        entity_type="Object", entity_id=eid,
        version_seq=1, external_id="Account",
    )
    body_a = make_value_claim(subject=field_ref, value="x")
    body_b = make_value_claim(subject=obj_ref, value="y")
    r_a = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body_a, semantic_conditions=empty_conditions(),
    )
    r_b = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body_b, semantic_conditions=empty_conditions(),
    )
    # Without filter: both rows.
    all_matches = coord.list_tests_affected_by_entity(
        session, entity_id=eid,
    )
    assert len(all_matches) == 2
    # Filter to "Field" entity_type.
    field_matches = coord.list_tests_affected_by_entity(
        session, entity_id=eid, entity_type="Field",
    )
    assert len(field_matches) == 1
    assert field_matches[0].test_id == r_a.test_id


def test_list_tests_affected_by_entity_unknown_returns_empty(
    session,
) -> None:
    coord = SemanticTransactionCoordinator()
    assert coord.list_tests_affected_by_entity(
        session, entity_id=uuid4(),
    ) == []


def test_list_tests_affected_by_entity_multiple_tests(session) -> None:
    """Same entity referenced by N different tests → N matches."""
    coord = SemanticTransactionCoordinator()
    shared = ib(external_id="Shared.Field", entity_id=uuid4())
    written = []
    for value in ("A", "B", "C"):
        body = make_value_claim(subject=shared, value=value)
        r = coord.write_claim(
            session,
            actor="human", test_id=None,
            archetype="data_behavior", claim_kind="value-claim",
            asserted_truth=body, semantic_conditions=empty_conditions(),
        )
        written.append(r.test_id)
    matches = coord.list_tests_affected_by_entity(
        session, entity_id=shared.entity_id,
    )
    assert len(matches) == 3
    assert {m.test_id for m in matches} == set(written)


# ---------------------------------------------------------------------------
# list_tests_by_requirement
# ---------------------------------------------------------------------------


def _insert_requirement_link(
    session,
    *,
    test_id: UUID,
    external_system: str = "jira",
    external_key: str = "PROJ-1",
    link_kind: str = "generated_from",
    external_version: str | None = None,
) -> None:
    """Helper: insert a row directly into test_requirement_links.
    D-δ will add a Coordinator method for this; for D-γ.1 we
    test the read path against direct inserts."""
    session.add(TestRequirementLink(
        test_id=test_id,
        external_system=external_system,
        external_key=external_key,
        external_version=external_version,
        link_kind=link_kind,
        linked_at=datetime.now(timezone.utc),
        linked_by="test-fixture",
    ))
    session.flush()


def test_list_tests_by_requirement_returns_linked_test(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id = uuid4()
    _insert_requirement_link(session, test_id=test_id)
    matches = coord.list_tests_by_requirement(
        session,
        external_system="jira", external_key="PROJ-1",
    )
    assert len(matches) == 1
    assert isinstance(matches[0], RequirementMatch)
    assert matches[0].test_id == test_id
    assert matches[0].link_kind == "generated_from"


def test_list_tests_by_requirement_filter_by_link_kind(session) -> None:
    coord = SemanticTransactionCoordinator()
    test_id = uuid4()
    _insert_requirement_link(
        session, test_id=test_id, link_kind="generated_from",
    )
    _insert_requirement_link(
        session, test_id=test_id, link_kind="verifies",
    )
    # No filter: both kinds returned.
    all_matches = coord.list_tests_by_requirement(
        session,
        external_system="jira", external_key="PROJ-1",
    )
    assert len(all_matches) == 2
    kinds = {m.link_kind for m in all_matches}
    assert kinds == {"generated_from", "verifies"}
    # Filter to one kind.
    verifies_only = coord.list_tests_by_requirement(
        session,
        external_system="jira", external_key="PROJ-1",
        link_kind="verifies",
    )
    assert len(verifies_only) == 1
    assert verifies_only[0].link_kind == "verifies"


def test_list_tests_by_requirement_unknown_returns_empty(session) -> None:
    coord = SemanticTransactionCoordinator()
    matches = coord.list_tests_by_requirement(
        session,
        external_system="jira", external_key="NONEXISTENT-42",
    )
    assert matches == []


def test_list_tests_by_requirement_carries_external_version(
    session,
) -> None:
    coord = SemanticTransactionCoordinator()
    test_id = uuid4()
    _insert_requirement_link(
        session, test_id=test_id,
        external_version="v2.3", link_kind="verifies",
    )
    matches = coord.list_tests_by_requirement(
        session,
        external_system="jira", external_key="PROJ-1",
    )
    assert matches[0].external_version == "v2.3"
    assert matches[0].linked_at is not None
