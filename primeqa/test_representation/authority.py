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

ActorKind = Literal["human", "s3", "s8", "s4"]
"""Closed taxonomy of actor identifiers per D-061.

  - ``"human"`` — direct human authorship via the UI or CLI; full
    authority over claim semantics (hash-changing edits permitted).
  - ``"s3"`` — generation actor (the AI generator). Authority to
    regenerate; hash-preserving regenerations are no-op skips
    per SPEC §7.7 to avoid creating duplicate-meaning versions.
  - ``"s8"`` — evolution actor. Authority over operational
    realization only; semantic content is OFF-LIMITS.
  - ``"s4"`` — runtime-state-reporting actor. Authority is SCOPED
    to :func:`check_runtime_state_write_authority` only: S4 calls
    :meth:`SemanticTransactionCoordinator.report_run_outcome`
    per SPEC §8.3 and nothing else. Passing ``"s4"`` to claim or
    recipe write paths raises :class:`AuthorityViolationError`.
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

    # ``s4`` is scoped to runtime-state reporting only — see
    # :func:`check_runtime_state_write_authority`. Passing s4 to
    # the claim write path is a category error.
    if actor == "s4":
        return AuthorityDecision(
            allowed=False,
            is_noop=False,
            new_status=None,
            rejection_reason=(
                "actor='s4' is the runtime-state-reporting actor; "
                "not authorized for claim writes. Use "
                "report_run_outcome instead per SPEC §8.3."
            ),
        )

    # Case 1: no prior version — any actor (except s4, handled above)
    # can create a new claim as a draft.
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

def check_recipe_write_authority(
    actor: ActorKind,
    has_prior_version: bool,
) -> AuthorityDecision:
    """Evaluate the authority decision for a proposed recipe write.

    Per SPEC §7.4 (conservative re-approval default): every new
    recipe version requires explicit re-approval regardless of
    actor or change content. Unlike claims, recipes carry no
    ``identity_hash`` mechanism — the substrate cannot detect
    "recipe edit preserves behavior," and a hash-preserving
    no-op skip (the §7.7 claim-side case) has no analogue here.

    Per D-061 §7.2: the no-autonomous-semantic-divergence rule
    applies to identity-bearing content (claims + conditions),
    NOT to recipes. S8 CAN rewrite recipes autonomously. The
    conservative re-approval default is a SPEC §7.4 policy
    choice on the substrate's part, not an actor-authority
    restriction.

    Policy at v1:
      - All actors + any history → ``allowed=True``, never
        a no-op, ``new_status="generated_unapproved"``.

    This function exists as a forward-compat hook: future
    versions may relax the conservative default once behavior-
    preserving detection exists. Today it always returns the
    same decision.
    """
    # ``has_prior_version`` is unused at v1 but kept in the
    # signature to mirror :func:`check_claim_write_authority`
    # and to leave the door open for a future
    # "first version vs subsequent version" distinction.
    del has_prior_version
    # ``s4`` is scoped to runtime-state reporting only — see
    # :func:`check_runtime_state_write_authority`. Passing s4 to
    # the recipe write path is a category error.
    if actor == "s4":
        return AuthorityDecision(
            allowed=False,
            is_noop=False,
            new_status=None,
            rejection_reason=(
                "actor='s4' is the runtime-state-reporting actor; "
                "not authorized for recipe writes. Use "
                "report_run_outcome instead per SPEC §8.3."
            ),
        )
    if actor not in ("human", "s3", "s8"):
        raise ValueError(f"unknown actor kind: {actor!r}")
    return AuthorityDecision(
        allowed=True,
        is_noop=False,
        new_status="generated_unapproved",
        rejection_reason=None,
    )


def check_runtime_state_write_authority(actor: ActorKind) -> None:
    """S4 boundary callback authority per SPEC §8.3.

    Only ``actor='s4'`` may call
    :meth:`SemanticTransactionCoordinator.report_run_outcome`.
    Any other actor raises :class:`AuthorityViolationError` —
    runtime-state reporting is exclusively the S4 boundary's
    responsibility. Test runs are observed by S4; substrate-2
    only stores the snapshot S4 reports.

    Returns nothing on success (the authorized path is
    silent); raises on rejection. Used as a side-effecting
    guard, not a decision-returning function — runtime-state
    write authority has no decision space to evaluate, just an
    actor identity to verify.
    """
    if actor != "s4":
        raise AuthorityViolationError(
            f"report_run_outcome is the S4 boundary callback per "
            f"SPEC §8.3; only actor='s4' is authorized to call it. "
            f"Got actor={actor!r}.",
        )


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
