"""Canonicalizer registry — GOVERNANCE-CRITICAL INFRASTRUCTURE.

Per SPEC §6.3.9 Rule 6 + D-059. A canonicalizer is a function
that compiles a Pydantic body into its canonical-form dict (the
input to SHA-256 hashing). Most bodies use the generic walk in
:mod:`primeqa.test_representation.canonicalization`; bodies whose
canonical form needs structural rewriting (e.g.,
:class:`StateTransitionClaimBody` re-keys its state dicts on
``entity_id`` per the C1-B resolution) register a CUSTOM
canonicalizer here.

**The registry is governance-critical.** Changes to a registered
canonicalizer change the canonical form of every body of that
class, which mechanically invalidates all existing approvals
computed under the prior canonicalizer (per SPEC §6.3.9 Rule 4 +
Rule 6). New canonicalizers may be added safely; **existing
canonicalizers MUST NOT be changed** without an explicit
:data:`IDENTITY_HASH_VERSION` bump and a documented migration.
The registry's duplicate-registration check enforces this at
runtime — a second registration for the same
``(body_class, version)`` pair raises rather than silently
overwriting.

The ``description`` field on each registration is the audit
trail: when v1 canonicalizers were inherited from v0, why a
canonicalizer was added, what the C1-B resolution is for —
future readers and audit tooling read this without having to
read the function body.

Two custom canonicalizers ship with Track C:
  - :func:`canonicalize_state_transition` for
    :class:`StateTransitionClaimBody`.
  - :func:`canonicalize_automation_effect` for
    :class:`AutomationEffectClaimBody`.

Both implement the C1-B resolution per D-059 §6.3.3 + §6.3.4:
re-key ``from_state.field_values`` / ``to_state.field_values`` /
``expected_effect.changes.field_values`` (where applicable) by
``entity_id`` rather than ``external_id``. The ``external_id`` is
informational and excluded from the hash; ``entity_id`` is the
canonical identity. Author-friendly external-id-keyed dicts get
translated into canonical entity-id-keyed dicts at hash time.

Importing this package triggers the two ``@register_canonicalizer``
side effects at the bottom of the module.
"""
from __future__ import annotations

from typing import Callable, Dict, Optional, Tuple, Type

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


Canonicalizer = Callable[[BaseModel], dict]
"""Type alias: a canonicalizer takes a Pydantic model instance
(typically a :class:`BodyBase` subclass) and returns its
canonical-form dict."""


class CanonicalizerRegistry:
    """Maps ``(body_class, identity_hash_version)`` → canonicalizer
    function.

    Multiple instances are supported (tests construct their own to
    avoid polluting the package-wide singleton); the package-wide
    instance is :data:`default_canonicalizer_registry`.

    Registration MUST be unique per ``(body_class, version)`` —
    duplicate registration raises :class:`ValueError` with the
    existing entry's module + qualname so the conflict is
    immediately diagnosable.
    """

    def __init__(self) -> None:
        self._fns: Dict[Tuple[Type[BaseModel], int], Canonicalizer] = {}
        self._descriptions: Dict[Tuple[Type[BaseModel], int], str] = {}

    def register(
        self,
        body_class: Type[BaseModel],
        identity_hash_version: int,
        canonicalizer: Canonicalizer,
        description: str,
    ) -> None:
        """Register ``canonicalizer`` for the
        ``(body_class, identity_hash_version)`` slot.

        ``description`` is the audit trail — a short paragraph
        explaining why this canonicalizer exists and what
        structural rewriting it performs. Stored verbatim for
        retrieval via :meth:`describe`.

        Raises:
          :class:`ValueError` — if a canonicalizer is already
          registered for the same ``(body_class, version)`` pair.
          The error names the existing canonicalizer's module +
          qualname so the conflict is diagnosable.
        """
        key = (body_class, int(identity_hash_version))
        if key in self._fns:
            existing = self._fns[key]
            raise ValueError(
                f"duplicate canonicalizer registration for "
                f"body_class={body_class.__qualname__!r} "
                f"identity_hash_version={identity_hash_version!r}: "
                f"already registered as "
                f"{existing.__module__}.{existing.__qualname__}"
            )
        self._fns[key] = canonicalizer
        self._descriptions[key] = description

    def get(
        self,
        body_class: Type[BaseModel],
        identity_hash_version: int,
    ) -> Optional[Canonicalizer]:
        """Return the canonicalizer registered for
        ``(body_class, version)``, or ``None`` if none is
        registered (which means the generic walk applies)."""
        return self._fns.get((body_class, int(identity_hash_version)))

    def describe(
        self,
        body_class: Type[BaseModel],
        identity_hash_version: int,
    ) -> Optional[str]:
        """Return the audit-trail description for the
        ``(body_class, version)`` registration, or ``None`` if no
        canonicalizer is registered for that slot."""
        return self._descriptions.get(
            (body_class, int(identity_hash_version)),
        )

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, tuple) or len(key) != 2:
            return False
        body_class, version = key
        return (body_class, int(version)) in self._fns

    def __len__(self) -> int:
        return len(self._fns)


# ---------------------------------------------------------------------------
# Default registry + decorator
# ---------------------------------------------------------------------------


default_canonicalizer_registry = CanonicalizerRegistry()
"""The package-wide :class:`CanonicalizerRegistry`. Concrete
canonicalizers register against this via
:func:`register_canonicalizer` at import time of their module."""


def register_canonicalizer(
    body_class: Type[BaseModel],
    identity_hash_version: int,
    description: str,
) -> Callable[[Canonicalizer], Canonicalizer]:
    """Decorator: register the decorated function as the
    canonicalizer for ``(body_class, identity_hash_version)`` in
    :data:`default_canonicalizer_registry`.

    Usage::

        @register_canonicalizer(
            StateTransitionClaimBody, 1,
            description="C1-B: re-key state dicts on entity_id",
        )
        def canonicalize_state_transition(body):
            ...

    Returns the function unchanged so it remains callable
    directly (e.g., for tests that bypass the registry)."""

    def _decorator(fn: Canonicalizer) -> Canonicalizer:
        default_canonicalizer_registry.register(
            body_class, identity_hash_version, fn, description,
        )
        return fn

    return _decorator


# ---------------------------------------------------------------------------
# Concrete canonicalizer registrations (side-effect imports)
#
# Imported at the bottom so the registry class + decorator are
# fully defined before the canonicalizer modules try to use them.
# ---------------------------------------------------------------------------

from primeqa.test_representation.canonicalizers import (  # noqa: E402, F401
    automation_effect,
    conformance,
    state_transition,
)


__all__ = [
    "Canonicalizer",
    "CanonicalizerRegistry",
    "default_canonicalizer_registry",
    "register_canonicalizer",
]
