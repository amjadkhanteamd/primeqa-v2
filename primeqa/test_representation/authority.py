"""Authority model for substrate-2 mutations.

Per D-061 (mutation paths and authority) + SPEC §7 + §10.3
(Coordinator behavioral contracts). The substrate enforces a
trust asymmetry across actors: humans have full authority over
semantic content; S3 has authority to regenerate semantics but
hash-preserving regenerations are no-op skips; S8 has authority
to evolve operational realization but **NOT** semantic content.

The most consequential rule is the S8-with-hash-changed
**REJECTION** — D-061 §7.2 "no autonomous semantic divergence."
S8 may rewrite recipes (operational layer) under preservation of
the claim's identity_hash; if S8's proposed write would change
the claim's hash, that's autonomous semantic divergence and the
substrate refuses. This is the core governance check that lets
S8 operate autonomously without humans having to re-approve
every recipe rewrite.

This module exports:
  - :data:`ActorKind` — closed taxonomy of actor identifiers.
  - :class:`AuthorityDecision` — structured result of an authority
    check.
  - :func:`check_claim_write_authority` — pure function over the
    (actor, has_prior_version, hash_changed) tuple → decision.
  - :func:`enforce_authority` — raises
    :class:`AuthorityViolationError` if the decision disallows.

Recipe authority is a separate model (recipes always require
re-approval per SPEC §7.4) and lands in Track D-β.3 alongside
the recipe write flow.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from primeqa.test_representation.errors import AuthorityViolationError


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

ActorKind = Literal["human", "s3", "s8"]
"""Closed taxonomy of actor identifiers per D-061.

  - ``"human"`` — direct human authorship via the UI or CLI; full
    authority over claim semantics (hash-changing edits permitted).
  - ``"s3"`` — generation actor (the AI generator). Authority to
    regenerate; hash-preserving regenerations are no-op skips
    per SPEC §7.7 to avoid creating duplicate-meaning versions.
  - ``"s8"`` — evolution actor. Authority over operational
    realization only; semantic content is OFF-LIMITS.
"""


# ---------------------------------------------------------------------------
# Decision structure
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AuthorityDecision:
    """The outcome of an authority check.

    Fields:
      allowed: Whether the proposed write is permitted. If
        ``False``, callers raise :class:`AuthorityViolationError`
        via :func:`enforce_authority`.
      is_noop: Whether the write should be skipped entirely with
        the existing version returned to the caller. Used for the
        SPEC §7.7 case of S3 same-hash regeneration.
      new_status: The status the new claim version should carry
        if the write proceeds. ``None`` means "preserve prior
        version's status" (the hash-unchanged-by-human case).
      rejection_reason: Diagnostic message for the rejection
        path. ``None`` when ``allowed=True``.
    """

    allowed: bool
    is_noop: bool
    new_status: Optional[str]
    rejection_reason: Optional[str]


# ---------------------------------------------------------------------------
# Claim-write authority policy
# ---------------------------------------------------------------------------

# Reason text used in the S8 hash-changed rejection. Spelled out
# verbatim so future debuggers searching the codebase for the
# governance phrasing find it.
_S8_DIVERGENCE_REASON = (
    "S8 attempted to change a claim's identity_hash; D-061 §7.2 "
    "'no autonomous semantic divergence' forbids autonomous "
    "semantic mutation by the evolution actor"
)


def check_claim_write_authority(
    actor: ActorKind,
    has_prior_version: bool,
    hash_changed: Optional[bool],
) -> AuthorityDecision:
    """Evaluate the authority decision for a proposed claim write.

    Per D-061 + SPEC §7.4 (trust boundary asymmetry) + §7.7 (S3
    same-hash no-op):

    +--------+-----------+-----------+-----------+-----------+
    | actor  | has_prior | hash_chg  | allowed   | semantics |
    +========+===========+===========+===========+===========+
    | any    | False     | None      | True      | new draft |
    +--------+-----------+-----------+-----------+-----------+
    | human  | True      | False     | True      | preserve  |
    | human  | True      | True      | True      | →draft    |
    +--------+-----------+-----------+-----------+-----------+
    | s3     | True      | False     | True+noop | skip      |
    | s3     | True      | True      | True      | →draft    |
    +--------+-----------+-----------+-----------+-----------+
    | s8     | True      | False     | True      | preserve  |
    | s8     | True      | True      | False     | rejected  |
    +--------+-----------+-----------+-----------+-----------+

    Args:
      actor: The actor performing the write. Closed taxonomy per
        :data:`ActorKind`.
      has_prior_version: ``True`` iff a prior version of this
        ``test_id`` exists in the substrate; ``False`` for new
        claims.
      hash_changed: ``True`` if the proposed write's
        ``identity_hash`` differs from the prior version's;
        ``False`` if equal; ``None`` ONLY when
        ``has_prior_version=False`` (no prior hash to compare to).

    Returns:
      An :class:`AuthorityDecision` recording the policy outcome.

    Raises:
      ``ValueError`` — if ``hash_changed`` is ``None`` while
      ``has_prior_version=True`` (programming error; callers
      MUST compute the comparison before calling).
    """
    if has_prior_version and hash_changed is None:
        raise ValueError(
            "hash_changed must be specified when has_prior_version=True"
        )

    # Case 1: no prior version — any actor can create a new claim
    # as a draft.
    if not has_prior_version:
        return AuthorityDecision(
            allowed=True,
            is_noop=False,
            new_status="draft",
            rejection_reason=None,
        )

    # Cases 2-7: prior version exists; behavior depends on
    # (actor, hash_changed).
    if actor == "human":
        if hash_changed:
            # Hash change invalidates approval per D-059 §6.3.9
            # Rule 2: semantic edits → re-approval required.
            return AuthorityDecision(
                allowed=True,
                is_noop=False,
                new_status="draft",
                rejection_reason=None,
            )
        # Hash unchanged: preserve prior status (no semantic
        # change → no re-approval needed).
        return AuthorityDecision(
            allowed=True,
            is_noop=False,
            new_status=None,
            rejection_reason=None,
        )

    if actor == "s3":
        if hash_changed:
            # S3 regenerated and produced new semantics; treat
            # like a human draft edit.
            return AuthorityDecision(
                allowed=True,
                is_noop=False,
                new_status="draft",
                rejection_reason=None,
            )
        # SPEC §7.7: S3 same-hash regeneration is a no-op skip.
        # The Coordinator returns existing version's metadata.
        return AuthorityDecision(
            allowed=True,
            is_noop=True,
            new_status=None,
            rejection_reason=None,
        )

    if actor == "s8":
        if hash_changed:
            # The core governance check per D-061 §7.2.
            return AuthorityDecision(
                allowed=False,
                is_noop=False,
                new_status=None,
                rejection_reason=_S8_DIVERGENCE_REASON,
            )
        # S8 hash-preserving operational evolution is the
        # authorized S8 mutation path.
        return AuthorityDecision(
            allowed=True,
            is_noop=False,
            new_status=None,
            rejection_reason=None,
        )

    raise ValueError(f"unknown actor kind: {actor!r}")


# ---------------------------------------------------------------------------
# Enforcement helper
# ---------------------------------------------------------------------------

def enforce_authority(decision: AuthorityDecision) -> None:
    """Raise :class:`AuthorityViolationError` if ``decision``
    disallows the write; otherwise return without effect.

    Convenience for the Coordinator's 11-step write-flow per
    SPEC §4.7.6 — the authority check is one explicit step, and
    this helper keeps the call site clean (`enforce_authority(decision)`
    rather than an inline ``if`` block).
    """
    if decision.allowed:
        return
    raise AuthorityViolationError(
        decision.rejection_reason or "Authority check failed",
    )
