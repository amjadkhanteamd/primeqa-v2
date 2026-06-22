"""Unified authorization — the role ladder and the single ``authorize()`` path.

D-245 (Authorization Model: Role Ladder × Environment Scope). This module is the
**one** role-tier enforcement point. The stored DB ``role`` values + their CHECK
constraints are unchanged; ``rank()`` maps each to an ordered ladder tier so that
"is this caller allowed?" reduces to a single ``>=`` comparison.

Two axes (per D-245), kept deliberately separate:
  1. **Role ladder** — this module: ``Tier`` + ``rank()`` + ``authorize()``.
  2. **Environment access scope** — Groups, via ``list_environments(...)`` (Phase 3).

Three gates, three distinct errors — never conflated:
  * Authorization  → ``AuthorizationError``  (role tier — *this* module)
  * Resource policy → ``PolicyError``          (env ``execution_policy`` — Phase 4)
  * Executability  → ``NotExecutableError``    (can the substrate realize it — pre-existing)

Phase 1 is **additive**: nothing here is wired into a route yet. The legacy
``require_role`` / ``role_required`` decorators become thin wrappers over
``authorize()`` in a later phase.
"""
from __future__ import annotations

import enum
from typing import Any


class Tier(enum.IntEnum):
    """The role ladder — ordered tiers. Higher rank ⊇ lower rank's authority.

    ``IntEnum`` so tiers compare directly (``Tier.ADMIN >= Tier.MEMBER``) and a
    bare ``int`` may be passed wherever a ``Tier`` is expected.
    """

    VIEWER = 1
    MEMBER = 2
    ADMIN = 3
    SUPERADMIN = 4


# Stored DB role value → ladder tier. The DB values + CHECK constraints are kept
# (D-245); this is the code-side mapping only.
ROLE_TO_TIER: dict[str, Tier] = {
    "viewer": Tier.VIEWER,
    "ba": Tier.MEMBER,
    "tester": Tier.MEMBER,
    "admin": Tier.ADMIN,
    "superadmin": Tier.SUPERADMIN,
}


class AuthorizationError(Exception):
    """The caller's role tier is below the required minimum (the authorization gate).

    Distinct from ``PolicyError`` (the target env refuses the action) and
    ``NotExecutableError`` (the substrate cannot realize it). Carries a human
    ``reason`` for surfacing.
    """

    def __init__(self, reason: str = "not authorized") -> None:
        super().__init__(reason)
        self.reason = reason


def rank(role: Any) -> Tier:
    """Map a stored DB role value to its ladder tier.

    Fail **low**: an unknown / missing / ``None`` role maps to ``Tier.VIEWER`` (the
    least privilege), never to a higher tier. Case- and whitespace-insensitive.
    """
    if role is None:
        return Tier.VIEWER
    return ROLE_TO_TIER.get(str(role).strip().lower(), Tier.VIEWER)


def floor_tier(roles) -> Tier:
    """The minimum ladder tier that admits every role in a legacy role list.

    D-245 Phase 6: a legacy ``role_required("admin", "tester")`` gate is
    re-expressed as ``authorize(min_tier=floor_tier(roles))`` — the floor is the
    lowest tier among the listed roles, so everyone at-or-above it passes.
    ``superadmin`` in a list is redundant (the ladder top always passes). An
    empty list yields ``SUPERADMIN`` (admit only god-mode), matching the old
    ``role not in []`` deny-all-but-superadmin behaviour.

    The one *intended* consequence: a list naming ``tester`` but not ``ba`` now
    also admits ``ba`` — both map to ``MEMBER``."""
    tiers = [rank(r) for r in roles]
    return min(tiers) if tiers else Tier.SUPERADMIN


def tier_label(role: Any) -> str:
    """Human display label for a stored role's ladder tier — ``Viewer`` /
    ``Member`` / ``Admin`` / ``Superadmin``. The stored DB role value
    (``viewer`` / ``ba`` / ``tester`` / ``admin`` / ``superadmin``) is unchanged;
    this is the user-facing name for the *tier* it maps to (D-245, Phase 7)."""
    return rank(role).name.capitalize()


def _subject_role(subject: Any) -> Any:
    """Extract the role value from a subject.

    Accepts the ``request.user`` dict (``{"role": ...}``), any object exposing a
    ``.role`` attribute (e.g. a ``User`` model row), or a bare role string.
    """
    if subject is None:
        return None
    if isinstance(subject, str):
        return subject
    if isinstance(subject, dict):
        return subject.get("role")
    return getattr(subject, "role", None)


def authorize(subject: Any, min_tier: Tier | int, resource: Any = None) -> tuple[bool, str]:
    """The single role-ladder enforcement point.

    Returns ``(allow, reason)`` — it does **not** raise (callers / decorator
    wrappers decide how to surface a denial). ``superadmin`` passes every tier
    because ``Tier.SUPERADMIN`` outranks all (god-mode is the ladder's top rung,
    no special case needed).

    ``resource`` is reserved for resource-scoped checks layered on top (e.g. the
    environment-access axis, Phase 3); the role-tier decision here is independent
    of it. Resource **policy** (``execution_policy``) is a *separate* gate
    (``PolicyError``) and is not decided here.
    """
    required = Tier(int(min_tier))
    role = _subject_role(subject)
    caller = rank(role)
    if caller >= required:
        return True, f"allow: role {role!r} (tier {caller.name}) >= {required.name}"
    return False, f"deny: role {role!r} (tier {caller.name}) < required {required.name}"


def can_assign_user_role(
    caller_tier: Tier | int,
    new_role: Any = None,
    target_current_tier: Tier | int | None = None,
) -> tuple[bool, str]:
    """The user-mutation invariant — *who may write whose `users` row, to what role*.

    A pure predicate (no I/O) layered **on top of** the tier gate that
    ``authorize()`` decides. The decorator already proved the caller is at least
    Admin; this proves the *target* and the *assigned role* are within the
    caller's authority. Returns ``(ok, reason)`` and never raises — the service
    chokepoint maps a deny to ``AuthorizationError`` (a forbidden op on a visible
    row) and an *invalid* role string to a ``ValueError`` (a 400 validation
    fault). It is the single source of truth behind both the create and update
    paths (F-1 + the decapitation guard); tenant-scope (F-2) is enforced
    separately by loading the target within the caller's tenant.

    Three invariants, all ``<=`` against the caller's own tier (so an admin may
    manage admins-and-below — matching ``create_user`` — but never a superadmin):

    1. **Valid role** — ``new_role`` (when assigning one) must be a known DB role.
    2. **No promotion above self** — ``rank(new_role) <= caller_tier`` (F-1: an
       admin cannot mint a superadmin, including themselves).
    3. **No modifying a user above you** — ``target_current_tier <= caller_tier``
       (the decapitation guard: an admin cannot edit/demote/deactivate a
       superadmin). Skipped on create (no pre-existing target).

    ``new_role=None`` means "this update touches no role" (e.g. a name or
    ``is_active`` change) — only invariant 3 applies. ``target_current_tier=None``
    means "no pre-existing target" (create) — only invariants 1-2 apply.
    """
    caller = Tier(int(caller_tier))

    if new_role is not None:
        if str(new_role).strip().lower() not in ROLE_TO_TIER:
            return False, f"invalid role {new_role!r}"
        assigned = rank(new_role)
        if assigned > caller:
            return (
                False,
                f"deny: cannot assign role {new_role!r} (tier {assigned.name}) "
                f"above your own tier {caller.name}",
            )

    if target_current_tier is not None:
        target = Tier(int(target_current_tier))
        if target > caller:
            return (
                False,
                f"deny: cannot modify a user at tier {target.name} "
                f"above your own tier {caller.name}",
            )

    return True, "allow"
