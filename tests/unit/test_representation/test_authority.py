"""Tests for the claim-write authority model.

Per D-061 (mutation paths) + SPEC §7.4 (trust boundary asymmetry)
+ §7.7 (S3 same-hash no-op). Covers the full
{actor} × {has_prior} × {hash_changed} matrix.

The S8-with-hash-changed REJECTION is the most architecturally
consequential check in the substrate — verified explicitly that
the error message names the governing rule ("no autonomous
semantic divergence" per D-061 §7.2) so future debuggers find
the policy context.
"""
from __future__ import annotations

import pytest

from primeqa.test_representation import (
    ActorKind,
    AuthorityDecision,
    AuthorityViolationError,
    check_claim_write_authority,
    enforce_authority,
)


# ---------------------------------------------------------------------------
# No-prior-version path: any actor → allowed, status=draft
# ---------------------------------------------------------------------------

class TestAuthorityNoPriorVersion:
    @pytest.mark.parametrize("actor", ["human", "s3", "s8"])
    def test_new_claim_allowed_for_any_actor(self, actor: ActorKind) -> None:
        decision = check_claim_write_authority(
            actor=actor, has_prior_version=False, hash_changed=None,
        )
        assert decision.allowed is True
        assert decision.is_noop is False
        assert decision.new_status == "draft"
        assert decision.rejection_reason is None

    def test_hash_changed_unused_when_no_prior_version(self) -> None:
        """``hash_changed`` is informational-only when
        ``has_prior_version=False``. Passing it explicitly should
        not affect the decision."""
        decision = check_claim_write_authority(
            actor="human", has_prior_version=False, hash_changed=None,
        )
        assert decision.allowed is True

    def test_hash_changed_required_when_prior_exists(self) -> None:
        """Programming-error guard: hash_changed=None with
        has_prior_version=True is a caller bug."""
        with pytest.raises(ValueError):
            check_claim_write_authority(
                actor="human", has_prior_version=True, hash_changed=None,
            )


# ---------------------------------------------------------------------------
# Human actor — has prior version
# ---------------------------------------------------------------------------

class TestAuthorityHumanWithPrior:
    def test_human_hash_unchanged_preserves_status(self) -> None:
        decision = check_claim_write_authority(
            actor="human", has_prior_version=True, hash_changed=False,
        )
        assert decision.allowed is True
        assert decision.is_noop is False
        # new_status=None means "preserve prior version's status".
        assert decision.new_status is None
        assert decision.rejection_reason is None

    def test_human_hash_changed_resets_to_draft(self) -> None:
        """Per D-059 §6.3.9 Rule 2: semantic edits invalidate
        prior approval. New version starts as draft, requiring
        re-approval."""
        decision = check_claim_write_authority(
            actor="human", has_prior_version=True, hash_changed=True,
        )
        assert decision.allowed is True
        assert decision.is_noop is False
        assert decision.new_status == "draft"


# ---------------------------------------------------------------------------
# S3 actor — has prior version
# ---------------------------------------------------------------------------

class TestAuthorityS3WithPrior:
    def test_s3_hash_unchanged_is_noop(self) -> None:
        """SPEC §7.7: S3 same-hash regeneration is a no-op skip.
        Coordinator returns the existing version's metadata
        without creating a new row."""
        decision = check_claim_write_authority(
            actor="s3", has_prior_version=True, hash_changed=False,
        )
        assert decision.allowed is True
        assert decision.is_noop is True
        assert decision.new_status is None
        assert decision.rejection_reason is None

    def test_s3_hash_changed_treated_as_draft_edit(self) -> None:
        """S3 produced new semantics — proceed like a human draft
        edit (allowed; status=draft for re-approval)."""
        decision = check_claim_write_authority(
            actor="s3", has_prior_version=True, hash_changed=True,
        )
        assert decision.allowed is True
        assert decision.is_noop is False
        assert decision.new_status == "draft"


# ---------------------------------------------------------------------------
# S8 actor — the core governance check
# ---------------------------------------------------------------------------

class TestAuthorityS8WithPrior:
    def test_s8_hash_unchanged_authorized(self) -> None:
        """S8's authorized mutation path: hash-preserving
        operational evolution. Prior status preserved (no
        re-approval needed)."""
        decision = check_claim_write_authority(
            actor="s8", has_prior_version=True, hash_changed=False,
        )
        assert decision.allowed is True
        assert decision.is_noop is False
        assert decision.new_status is None
        assert decision.rejection_reason is None

    def test_s8_hash_changed_REJECTED(self) -> None:
        """The most architecturally consequential check per D-061
        §7.2: S8 cannot autonomously diverge a claim's semantics.
        The substrate refuses; ``allowed=False`` and the caller
        is expected to surface :class:`AuthorityViolationError`."""
        decision = check_claim_write_authority(
            actor="s8", has_prior_version=True, hash_changed=True,
        )
        assert decision.allowed is False
        assert decision.is_noop is False
        assert decision.rejection_reason is not None

    def test_s8_rejection_message_names_governing_rule(self) -> None:
        """The rejection reason must reference D-061's
        'no autonomous semantic divergence' phrasing or
        equivalent so future debuggers find the policy
        context."""
        decision = check_claim_write_authority(
            actor="s8", has_prior_version=True, hash_changed=True,
        )
        reason = (decision.rejection_reason or "").lower()
        assert "autonomous" in reason
        assert "semantic divergence" in reason


# ---------------------------------------------------------------------------
# enforce_authority
# ---------------------------------------------------------------------------

class TestEnforceAuthority:
    def test_enforce_passes_for_allowed_decision(self) -> None:
        decision = AuthorityDecision(
            allowed=True, is_noop=False,
            new_status="draft", rejection_reason=None,
        )
        # Should not raise.
        enforce_authority(decision)

    def test_enforce_raises_for_rejected_decision(self) -> None:
        decision = AuthorityDecision(
            allowed=False, is_noop=False,
            new_status=None, rejection_reason="custom rejection text",
        )
        with pytest.raises(AuthorityViolationError) as exc_info:
            enforce_authority(decision)
        assert "custom rejection text" in str(exc_info.value)

    def test_enforce_falls_back_to_default_message(self) -> None:
        """``rejection_reason=None`` on a rejected decision is a
        defensive case (the policy always sets a reason); falling
        back to a generic message keeps the error surfaceable."""
        decision = AuthorityDecision(
            allowed=False, is_noop=False,
            new_status=None, rejection_reason=None,
        )
        with pytest.raises(AuthorityViolationError):
            enforce_authority(decision)

    def test_enforce_integrates_with_check(self) -> None:
        """End-to-end: check + enforce. The S8 hash-changed case
        raises with the governance phrasing."""
        decision = check_claim_write_authority(
            actor="s8", has_prior_version=True, hash_changed=True,
        )
        with pytest.raises(AuthorityViolationError) as exc_info:
            enforce_authority(decision)
        assert "autonomous" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Unknown actor — defensive
# ---------------------------------------------------------------------------

class TestAuthorityUnknownActor:
    def test_unknown_actor_raises(self) -> None:
        with pytest.raises(ValueError):
            check_claim_write_authority(
                actor="alien",  # type: ignore[arg-type]
                has_prior_version=True, hash_changed=False,
            )


# ---------------------------------------------------------------------------
# AuthorityDecision is frozen + structured
# ---------------------------------------------------------------------------

class TestAuthorityDecisionShape:
    def test_decision_is_frozen(self) -> None:
        decision = AuthorityDecision(
            allowed=True, is_noop=False,
            new_status="draft", rejection_reason=None,
        )
        with pytest.raises(Exception):
            # FrozenInstanceError — dataclass(frozen=True) refuses
            # attribute mutation post-construction.
            decision.allowed = False  # type: ignore[misc]

    def test_decision_carries_all_four_fields(self) -> None:
        decision = AuthorityDecision(
            allowed=True, is_noop=True,
            new_status="approved", rejection_reason=None,
        )
        assert decision.allowed is True
        assert decision.is_noop is True
        assert decision.new_status == "approved"
        assert decision.rejection_reason is None
