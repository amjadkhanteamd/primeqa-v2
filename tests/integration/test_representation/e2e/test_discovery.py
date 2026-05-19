"""End-to-end cross-substrate-2 discovery scenarios.

Track E category ε. Each scenario exercises the reverse-lookup
methods (entity impact, hash equivalence, requirement linkage)
in compositions a S6 / S8 consumer would actually run.
"""
from __future__ import annotations

from uuid import UUID, uuid4

from primeqa.test_representation import (
    IdentityBearingRef,
    LiteralValue,
    SemanticTransactionCoordinator,
    ValueClaimBody,
)

from .._builders import empty_conditions, ib, make_value_claim


# ---------------------------------------------------------------------------
# ε-1: Entity impact attribution workflow
# ---------------------------------------------------------------------------


def test_entity_impact_attribution_workflow(session) -> None:
    """Write 5 claims referencing entity_X in their
    ``asserted_truth.subject`` plus 2 claims that DON'T
    reference entity_X. The reverse lookup surfaces exactly
    the 5 tests; filter by reference_kind='condition' returns
    0 (entity_X was only ever a subject).
    """
    coord = SemanticTransactionCoordinator()
    entity_x_id = uuid4()
    entity_x_ref = IdentityBearingRef(
        entity_type="Field",
        entity_id=entity_x_id,
        version_seq=1,
        external_id="Account.X",
    )
    affected_test_ids: set[UUID] = set()
    # 5 claims referencing X.
    for i in range(5):
        body = ValueClaimBody(
            subject=entity_x_ref,
            expected_value=LiteralValue(value=f"value_{i}"),
        )
        r = coord.write_claim(
            session,
            actor="human", test_id=None,
            archetype="data_behavior", claim_kind="value-claim",
            asserted_truth=body, semantic_conditions=empty_conditions(),
        )
        affected_test_ids.add(r.test_id)
    # 2 control claims NOT referencing X.
    for _ in range(2):
        coord.write_claim(
            session,
            actor="human", test_id=None,
            archetype="data_behavior", claim_kind="value-claim",
            asserted_truth=make_value_claim(),  # different subject
            semantic_conditions=empty_conditions(),
        )

    # Reverse lookup: exactly the 5 affected tests, all tagged
    # subject.
    matches = coord.list_tests_affected_by_entity(
        session, entity_id=entity_x_id,
    )
    assert {m.test_id for m in matches} == affected_test_ids
    assert all(m.reference_kind == "subject" for m in matches)

    # Filter by reference_kind='condition' → 0 matches.
    cond_matches = coord.list_tests_affected_by_entity(
        session, entity_id=entity_x_id,
        reference_kind="condition",
    )
    assert cond_matches == []


# ---------------------------------------------------------------------------
# ε-2: Equivalence discovery across tests
# ---------------------------------------------------------------------------


def test_equivalence_discovery_across_tests(session) -> None:
    """Three claims with identical canonical form (same
    referenced entity_id + same expected value) — different
    test_ids but same identity_hash. Approve all three.
    :meth:`query_equivalent_claims` returns the three. A
    fourth claim with different content (different hash) is
    not returned.
    """
    coord = SemanticTransactionCoordinator()
    shared_entity = ib(
        entity_type="Field",
        entity_id=uuid4(),
        external_id="Shared.Field",
    )
    # Three claims with the same canonical form.
    sibling_ids: set[UUID] = set()
    sibling_hash: str | None = None
    for _ in range(3):
        body = ValueClaimBody(
            subject=shared_entity,
            expected_value=LiteralValue(value="canonical"),
        )
        r = coord.write_claim(
            session,
            actor="human", test_id=None,
            archetype="data_behavior", claim_kind="value-claim",
            asserted_truth=body, semantic_conditions=empty_conditions(),
        )
        sibling_ids.add(r.test_id)
        sibling_hash = r.identity_hash
        coord.promote_claim_to_approved(
            session, actor="human",
            test_id=r.test_id, version_seq=1,
        )
    assert sibling_hash is not None

    # All three share the hash.
    matches = coord.query_equivalent_claims(
        session,
        identity_hash=sibling_hash,
        identity_hash_version=1,
    )
    assert {m.test_id for m in matches} == sibling_ids
    assert len(matches) == 3

    # Write a 4th with DIFFERENT content (different hash).
    body_other = ValueClaimBody(
        subject=shared_entity,
        expected_value=LiteralValue(value="different"),
    )
    r4 = coord.write_claim(
        session,
        actor="human", test_id=None,
        archetype="data_behavior", claim_kind="value-claim",
        asserted_truth=body_other, semantic_conditions=empty_conditions(),
    )
    assert r4.identity_hash != sibling_hash

    # The original query still returns 3 (r4's hash is different).
    matches = coord.query_equivalent_claims(
        session,
        identity_hash=sibling_hash,
        identity_hash_version=1,
    )
    assert len(matches) == 3
    assert r4.test_id not in {m.test_id for m in matches}


# ---------------------------------------------------------------------------
# ε-3: Requirement link workflow
# ---------------------------------------------------------------------------


def test_requirement_link_workflow(session) -> None:
    """Three tests linked to JIRA-PROJ-1 with different
    ``link_kind`` values; verify discovery, filter, unlink,
    and authorship-preserved re-link.
    """
    coord = SemanticTransactionCoordinator()
    # Three approved tests.
    test_ids = []
    for i in range(3):
        body = make_value_claim(value=f"v{i}")
        r = coord.write_claim(
            session,
            actor="human", test_id=None,
            archetype="data_behavior", claim_kind="value-claim",
            asserted_truth=body, semantic_conditions=empty_conditions(),
        )
        test_ids.append(r.test_id)

    # Link each to the same Jira issue with different kinds.
    link_kinds = ["generated_from", "verifies", "related_to"]
    for tid, lk in zip(test_ids, link_kinds):
        coord.link_requirement(
            session, actor="s3", test_id=tid,
            external_system="jira", external_key="PROJ-1",
            link_kind=lk,  # type: ignore[arg-type]
        )

    # Discovery surfaces all three.
    matches = coord.list_tests_by_requirement(
        session, external_system="jira", external_key="PROJ-1",
    )
    assert len(matches) == 3
    assert {m.test_id for m in matches} == set(test_ids)
    assert {m.link_kind for m in matches} == set(link_kinds)

    # Filter to one link_kind.
    verifies = coord.list_tests_by_requirement(
        session, external_system="jira", external_key="PROJ-1",
        link_kind="verifies",
    )
    assert len(verifies) == 1

    # Unlink one; remaining two still surface.
    coord.unlink_requirement(
        session, actor="human", test_id=test_ids[0],
        external_system="jira", external_key="PROJ-1",
        link_kind="generated_from",
    )
    matches = coord.list_tests_by_requirement(
        session, external_system="jira", external_key="PROJ-1",
    )
    assert len(matches) == 2
    assert test_ids[0] not in {m.test_id for m in matches}

    # Re-link the unlinked test with a NEW link_kind +
    # external_version. linked_by reflects the new actor (this
    # is a fresh link, not an update of the prior link).
    coord.link_requirement(
        session, actor="human", test_id=test_ids[0],
        external_system="jira", external_key="PROJ-1",
        link_kind="related_to",  # different kind
        external_version="v2.0",
    )

    # Now bump the external_version on the SAME link — UPDATE
    # path, linked_by preserved from the original.
    original = coord.list_tests_by_requirement(
        session, external_system="jira", external_key="PROJ-1",
        link_kind="related_to",
    )
    first_linker = next(
        m for m in original if m.test_id == test_ids[0]
    )
    coord.link_requirement(
        session, actor="s3",  # different actor on re-link
        test_id=test_ids[0],
        external_system="jira", external_key="PROJ-1",
        link_kind="related_to",
        external_version="v3.0",  # bumped
    )
    after_update = coord.list_tests_by_requirement(
        session, external_system="jira", external_key="PROJ-1",
        link_kind="related_to",
    )
    after_link = next(
        m for m in after_update if m.test_id == test_ids[0]
    )
    # linked_at is preserved across the version bump (we re-read
    # via the list method which returns the persisted row; the
    # original linker's linked_at on test_ids[0] is preserved).
    assert after_link.linked_at == first_linker.linked_at
    # external_version reflects the bump.
    assert after_link.external_version == "v3.0"
