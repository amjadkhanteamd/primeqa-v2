"""Substrate-4 (Execution Engine) error types.

Per the S4 SPEC (``docs/architecture/substrate_4_execution/SPEC.md``).
Small + S4-owned, mirroring substrate-2's ``errors`` module idiom. Slice 1
(D-108) only needs the bridge's failure surface; later slices extend this
module (executor / capture / posture errors) as they land.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID


class ExecutionEngineError(Exception):
    """Base for every substrate-4 error.

    Lets callers ``except ExecutionEngineError`` to catch any S4-originated
    failure without enumerating subclasses.
    """


class PlanTranslationError(ExecutionEngineError):
    """The recipe-to-plan bridge could not turn an S2 recipe into an
    executable plan.

    Raised when the recipe is not a metadata-inspection shape the slice-1
    bridge can plan: wrong ``recipe_kind`` / ``trigger_kind``, an unexpected
    body type, a write-mode (deploy) recipe, an out-of-vertical step
    (``RetrieveStep`` / ``DeployStep``), a pinned read target (D-099.3
    requires re-reading current state via a logical reference), or a
    structurally incomplete plan (missing read or assert).

    This is a *shape* error about the recipe the bridge was handed — not a
    runtime/execution failure (those surface later, once the executor runs).
    ``recipe_id`` is carried for diagnostics when the caller has it.
    """

    def __init__(
        self, message: str, *, recipe_id: Optional[UUID] = None,
    ) -> None:
        super().__init__(message)
        self.recipe_id = recipe_id


class CredentialResolutionError(ExecutionEngineError):
    """Could not resolve an authenticated client for a target environment —
    the environment / connection is missing, or the OAuth flow yielded no
    token. A binding failure, not a run outcome (the run never started)."""


class UnsupportedEdgeError(ExecutionEngineError):
    """The translator has no edge→SOQL mapping for the edge a read captures.

    Fail-loud: a representation/realization gap (this vertical doesn't yet
    translate this edge), never a silent empty query or a run outcome.
    """


class UnsupportedPredicateError(ExecutionEngineError):
    """The executor cannot evaluate an assertion's predicate (only ``exists``
    is supported in slice 2).

    Fail-loud: a representation gap in the recipe, not a run outcome. S4
    realizes only what it can faithfully evaluate; it never guesses.
    """


class AssertionResolutionError(ExecutionEngineError):
    """An assertion's ``subject_ref`` does not resolve to a prior read step's
    captured output.

    Fail-loud: a malformed plan (the read/assert graph is incoherent), not a
    run outcome.
    """
