"""Integration: S8 S1-sync recompute orchestration (D-143).

The orchestration (`recompute_grounding`) is exercised over **injected**
`ArtifactRef`s + a stub tri-port reader — grounds + persists, skips fresh,
re-grounds stale, honors the cap — against the real `s8_grounding_validity`
store (no S1/S2 seeding needed). Plus one `load_current_artifacts` test that
seeds a real claim + data-recipe via the coordinator and confirms the decode.
Reuses the package's transactional `session` fixture.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from primeqa.evolution import (
    Artifact,
    ArtifactRef,
    grounding_validity,
    load_current_artifacts,
    persist_grounding_validity,
    read_grounding_validity,
    recompute_grounding,
)
from primeqa.test_representation import (
    IdentityBearingRef,
    LiteralValue,
    ValueClaimBody,
)
from primeqa.test_representation.coordinator import SemanticTransactionCoordinator
from primeqa.test_representation.models.primitives import RejectionExpectation
from primeqa.test_representation.models.recipes.data_recipe import (
    CreateStep,
    DataRecipeBody,
)
from primeqa.test_representation.models.references import LogicalRef

from ._fixtures import arrange_approved_claim, arrange_recipe_with_status


# --- stub tri-port reader (yields a clean intact verdict) ------------------

@dataclass(frozen=True)
class _StubVr:
    is_active: bool = True
    formula_text: str = "Amount < 100"


class _S1:
    """A tri-port stub: every subject resolves, one active VR that the payload
    fires, no picklists -> grounding_validity returns overall intact."""

    def resolves(self, entity_type, external_id):
        return True

    def vrs_for_object(self, _ext):
        return (_StubVr(),)

    def active_values(self, _obj, _field):
        return None


def _artifact() -> Artifact:
    claim = ValueClaimBody(
        subject=IdentityBearingRef(
            entity_type="Field", entity_id=uuid4(),
            version_seq=1, external_id="Account.Industry"),
        expected_value=LiteralValue(value="x"))
    recipe = DataRecipeBody(
        api_choice="rest", identity_context="system",
        execution_mechanism="direct_api",
        steps=[CreateStep(
            step_id="s1",
            target_object=LogicalRef(entity_type="Object", external_id="Account"),
            field_values={"Amount": 99},
            expect_rejection=RejectionExpectation(error_code="X"))])
    return Artifact(claim=claim, recipes=(recipe,))


def _ref(test_id=None, version_seq=1) -> ArtifactRef:
    return ArtifactRef(test_id or uuid4(), version_seq, _artifact())


# --- the orchestration -----------------------------------------------------

def test_recompute_grounds_and_persists(session):
    ref = _ref()
    result = recompute_grounding(session, [ref], s1=_S1(), at_seq=5)
    assert (result.grounded, result.skipped_fresh, result.remaining) == (1, 0, 0)
    read = read_grounding_validity(session, ref.test_id, ref.version_seq)
    assert read is not None
    assert read.evaluated_at_version_seq == 5
    assert read.overall == "intact"


def test_recompute_skips_fresh(session):
    ref = _ref()
    recompute_grounding(session, [ref], s1=_S1(), at_seq=5)          # ground at 5
    result = recompute_grounding(session, [ref], s1=_S1(), at_seq=5)  # already fresh
    assert (result.grounded, result.skipped_fresh) == (0, 1)


def test_recompute_regrounds_stale(session):
    ref = _ref()
    # pre-persist a verdict at an OLDER S1 seq (3) -> stale at seq 5.
    persist_grounding_validity(
        session, test_id=ref.test_id, version_seq=ref.version_seq,
        evaluated_at_version_seq=3,
        validity=grounding_validity(ref.artifact, subjects=_S1(), vrs=_S1(),
                                    picklists=_S1()))
    result = recompute_grounding(session, [ref], s1=_S1(), at_seq=5)
    assert result.grounded == 1
    assert read_grounding_validity(
        session, ref.test_id, ref.version_seq).evaluated_at_version_seq == 5


def test_recompute_honors_cap(session):
    refs = [_ref(), _ref()]
    result = recompute_grounding(session, refs, s1=_S1(), at_seq=5, cap=1)
    assert (result.grounded, result.remaining) == (1, 1)            # one deferred
    # exactly one of the two got persisted.
    persisted = sum(
        1 for r in refs
        if read_grounding_validity(session, r.test_id, r.version_seq) is not None)
    assert persisted == 1


# --- the S2 read + decode --------------------------------------------------

def test_load_current_artifacts_decodes_claim_and_recipe(session):
    coord = SemanticTransactionCoordinator()
    test_id, _ = arrange_approved_claim(session, coord)
    arrange_recipe_with_status(
        session, coord, claim_test_id=test_id, status="active", priority=5)
    session.flush()

    refs = load_current_artifacts(session)
    mine = [r for r in refs if r.test_id == test_id]
    assert len(mine) == 1
    art = mine[0].artifact
    assert art.claim is not None                                   # decoded body
    assert len(art.recipes) == 1
    assert isinstance(art.recipes[0], DataRecipeBody)              # data-recipe decoded
