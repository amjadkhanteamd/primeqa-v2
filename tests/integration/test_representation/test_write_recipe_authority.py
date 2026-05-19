"""Authority matrix for ``write_recipe``.

Per Track D-β.3 + SPEC §7.4. Recipe authority at v1 is the
conservative re-approval default: every actor succeeds, every
new version starts at ``generated_unapproved``, no no-op skips,
no rejection cases. The event_kind distinguishes S8 rewrites
(``recipe_s8_rewrite``) from human / S3 edits
(``recipe_edited``).

Simpler than the claim-side matrix because recipes have no
``identity_hash`` to gate behavior on.
"""
from __future__ import annotations

import pytest

from primeqa.test_representation import (
    SemanticTransactionCoordinator,
    TestProvenance,
)

from ._builders import (
    build_minimal_data_recipe_body,
    build_minimal_execution_environment_body,
    build_minimal_ui_trigger_body,
    write_anchor_claim,
)


def _write_recipe(
    session, coord, actor: str, recipe_id=None,
    claim_test_id=None,
):
    """Helper: write a recipe with the given actor. Returns the
    result + the recipe_id for chained writes."""
    if claim_test_id is None:
        claim_test_id, _ = write_anchor_claim(session, coord)
    result = coord.write_recipe(
        session,
        actor=actor, recipe_id=recipe_id,
        claim_test_id=claim_test_id,
        trigger_kind="ui-trigger", recipe_kind="data-recipe",
        causal_initiation=build_minimal_ui_trigger_body(),
        observation_realization=build_minimal_data_recipe_body(),
        execution_environment=build_minimal_execution_environment_body(),
    )
    return result, claim_test_id


# ---------------------------------------------------------------------------
# No-prior path: every actor permitted, status=generated_unapproved
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("actor", ["human", "s3", "s8"])
def test_no_prior_each_actor_creates_generated_unapproved(
    session, actor: str,
) -> None:
    coord = SemanticTransactionCoordinator()
    result, _ = _write_recipe(session, coord, actor=actor)
    assert result.version_seq == 1
    assert result.status == "generated_unapproved"


@pytest.mark.parametrize("actor", ["human", "s3", "s8"])
def test_no_prior_event_kind_is_recipe_created(
    session, actor: str,
) -> None:
    coord = SemanticTransactionCoordinator()
    result, _ = _write_recipe(session, coord, actor=actor)
    evt = session.query(TestProvenance).filter(
        TestProvenance.recipe_id == result.recipe_id,
    ).one()
    assert evt.event_kind == "recipe_created"


# ---------------------------------------------------------------------------
# Prior + each actor — event_kind distinguishes S8 from human/S3
# ---------------------------------------------------------------------------


def test_human_edit_produces_recipe_edited_event(session) -> None:
    coord = SemanticTransactionCoordinator()
    r1, claim_id = _write_recipe(session, coord, actor="human")
    r2, _ = _write_recipe(
        session, coord, actor="human",
        recipe_id=r1.recipe_id, claim_test_id=claim_id,
    )
    assert r2.version_seq == 2
    assert r2.status == "generated_unapproved"
    events = session.query(TestProvenance).filter(
        TestProvenance.recipe_id == r1.recipe_id,
    ).order_by(TestProvenance.event_at).all()
    assert [e.event_kind for e in events] == [
        "recipe_created", "recipe_edited",
    ]


def test_s3_edit_produces_recipe_edited_event(session) -> None:
    """S3 + recipe → ``recipe_edited`` (NOT ``recipe_regenerated``
    — that's claim-side). Recipes have no hash, so the
    SPEC §7.7 no-op skip doesn't apply; S3 acts like a regular
    editor for recipes."""
    coord = SemanticTransactionCoordinator()
    r1, claim_id = _write_recipe(session, coord, actor="human")
    r2, _ = _write_recipe(
        session, coord, actor="s3",
        recipe_id=r1.recipe_id, claim_test_id=claim_id,
    )
    assert r2.version_seq == 2
    events = session.query(TestProvenance).filter(
        TestProvenance.recipe_id == r1.recipe_id,
    ).order_by(TestProvenance.event_at).all()
    assert [e.event_kind for e in events] == [
        "recipe_created", "recipe_edited",
    ]


def test_s8_rewrite_produces_recipe_s8_rewrite_event(session) -> None:
    """The architecturally consequential event_kind:
    ``recipe_s8_rewrite`` distinguishes S8's autonomous recipe
    evolution from human/S3 edits for future audit + evolution
    analysis."""
    coord = SemanticTransactionCoordinator()
    r1, claim_id = _write_recipe(session, coord, actor="human")
    r2, _ = _write_recipe(
        session, coord, actor="s8",
        recipe_id=r1.recipe_id, claim_test_id=claim_id,
    )
    assert r2.version_seq == 2
    assert r2.status == "generated_unapproved"
    events = session.query(TestProvenance).filter(
        TestProvenance.recipe_id == r1.recipe_id,
    ).order_by(TestProvenance.event_at).all()
    assert [e.event_kind for e in events] == [
        "recipe_created", "recipe_s8_rewrite",
    ]


# ---------------------------------------------------------------------------
# Conservative re-approval default: even hash-identical S8 rewrites
# land in generated_unapproved
# ---------------------------------------------------------------------------


def test_s8_rewrite_with_identical_bodies_still_unapproved(
    session,
) -> None:
    """Recipes have no hash; the substrate can't detect
    'behavior-preserving rewrite' yet. So the conservative
    default per SPEC §7.4 fires even when the recipe content is
    structurally identical to its predecessor: new version,
    generated_unapproved status."""
    coord = SemanticTransactionCoordinator()
    r1, claim_id = _write_recipe(session, coord, actor="human")
    # Manually approve v1 (D-γ adds promote(); for now SQL).
    from sqlalchemy import text
    session.execute(text(
        "UPDATE test_recipes SET status = 'approved' "
        "WHERE recipe_id = :r AND version_seq = 1"
    ), {"r": str(r1.recipe_id)})
    session.flush()

    # S8 writes v2 with the SAME body shape. Even though no
    # behavior changed, the conservative default resets to
    # generated_unapproved.
    r2, _ = _write_recipe(
        session, coord, actor="s8",
        recipe_id=r1.recipe_id, claim_test_id=claim_id,
    )
    assert r2.status == "generated_unapproved"
