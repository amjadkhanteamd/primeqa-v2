"""Step 3 — the requirement → surface link (LLD_STEP_3_REQUIREMENT_SURFACE §a-§b).

A human DECLARES that a requirement is verified on a portal surface. The
declaration is a first-class, provenanced row in ``requirement_surface_links``
(the identity × the frozen surface key × the inventory version, with actor
and time). Declaring MATERIALISES S2 ``verifies`` links from the
requirement to every conformance claim on that surface in the ACTIVE claim
set — through the coordinator's own API, ledgered per claim in
``requirement_surface_link_claims`` — so the readers that already look at
COVERAGE_LINK_KINDS (the requirement page's test plan, the plan sequence,
the release scope / decision engine) see the checks with no reader change.

Rules (all ruled at the design GO, 2026-09-09):

- **DECLARED only at v1.** ``DERIVED`` / ``VERIFIED_DERIVED`` are reserved
  vocabulary: admitted by the table's vocabulary CHECK, refused by a named
  v1 write guard, and refused here before the DB is asked.
- **Unlink is a state change, never a delete** (Fork 1): the declaration
  row goes ``active=false`` with actor/time/reason; the ledger rows are
  stamped; the S2 ``verifies`` rows THIS declaration created are removed
  through S2's own ``unlink_requirement`` so every reader drops the claims
  at once; a pre-existing independent ``verifies`` link on the same claim
  is left alone (``created_link = false``).
- **The active inventory is a derivation** (Fork 3): the highest
  inventory version that has an approved, unrevoked claim set. Its
  recorded successor is the inventory lifecycle flag (TA condition 5).
- **The link follows the surface, not the claim version** (Fork 5):
  rematerialisation on claim-set approval adds links for new claims on a
  declared surface; it never removes — a check leaves a requirement's
  plan by human unlink or claim deprecation only.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from primeqa.test_representation.models.surface import (
    SurfaceNaturalKey, canonical_surface_key,
)

log = logging.getLogger(__name__)

JIRA = "jira"
LINK_KIND = "verifies"
ACTOR = "human"                      # the coordinator's ActorKind for a person's act

SOURCE_DECLARED = "DECLARED"
SOURCE_DERIVED = "DERIVED"
SOURCE_VERIFIED_DERIVED = "VERIFIED_DERIVED"
SOURCES = (SOURCE_DECLARED, SOURCE_DERIVED, SOURCE_VERIFIED_DERIVED)
V1_WRITABLE_SOURCES = (SOURCE_DECLARED,)

REASON_NO_ACTIVE_INVENTORY = "no_active_inventory"
REASON_NOT_IN_ACTIVE_INVENTORY = "not_in_active_inventory"
REASON_RECORD_CONTEXT = "record_context"
REASON_NO_IDENTITY = "no_identity"
REASON_SOURCE_NOT_WRITABLE = "source_not_writable"

REASON_SENTENCES = {
    REASON_NO_ACTIVE_INVENTORY: "There is no active inventory (no inventory version has an approved claim set).",
    REASON_NOT_IN_ACTIVE_INVENTORY: "That surface is not a member of the active inventory.",
    REASON_RECORD_CONTEXT: "Record-context surfaces cannot be declared (the record-context revision is a separate step).",
    REASON_NO_IDENTITY: "This requirement has no established identity — decorate it first.",
    REASON_SOURCE_NOT_WRITABLE: "Only DECLARED links can be written at v1; DERIVED is reserved.",
}


class SurfaceLinkError(ValueError):
    """A refused surface-link act; ``reason`` is one of the REASON_* codes."""

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {REASON_SENTENCES.get(reason, reason)}"
                         + (f" ({detail})" if detail else ""))


@dataclass(frozen=True)
class ClaimOnSurface:
    test_id: str
    applicability: str
    executable: bool


@dataclass(frozen=True)
class SurfaceCandidate:
    surface_key: str
    inventory_version: int
    display_name: str
    site: str
    path: str
    persona_scope: str


@dataclass(frozen=True)
class DeclaredSurface:
    link_id: str
    external_system: str
    requirement_key: str
    surface_key: str
    inventory_version: int
    source: str
    declared_by: int
    declared_at: object
    active: bool
    deactivated_by: Optional[int]
    deactivated_at: object
    deactivation_reason: Optional[str]
    display_name: str
    site: str
    path: str
    persona_scope: str


@dataclass(frozen=True)
class DeclareResult:
    link_id: str
    created: bool          # a new declaration row was written
    materialised: int      # ledger rows written by this call
    already_linked: int    # claims already ledgered for this link (idempotent re-run)


@dataclass(frozen=True)
class UnlinkResult:
    link_id: str
    removed_links: int     # S2 verifies rows removed (only those this declaration created)
    kept_links: int        # pre-existing S2 rows left in place
    already_inactive: bool


# --------------------------------------------------------------------------
# The active inventory / claim set — derivations, stated (Fork 3)
# --------------------------------------------------------------------------

def active_inventory_version(session: Session) -> Optional[int]:
    """The highest inventory version that has an approved, unrevoked claim
    set. A derivation — inventories carry no lifecycle flag today; the
    inventory lifecycle flag (TA condition 5) is this function's recorded
    successor."""
    return session.execute(text(
        "SELECT MAX(inventory_version) FROM claim_sets WHERE status = 'approved'"
    )).scalar()


def active_claim_set(session: Session, inventory_version: int) -> Optional[str]:
    """The latest-approved unrevoked claim set on ``inventory_version``."""
    v = session.execute(text("""
        SELECT CAST(id AS text) FROM claim_sets
        WHERE status = 'approved' AND inventory_version = :v
        ORDER BY approved_at DESC NULLS LAST, created_at DESC
        LIMIT 1
    """), {"v": inventory_version}).scalar()
    return v


def inventory_member(session: Session, inventory_version: int,
                     surface_key: str) -> Optional[dict]:
    r = session.execute(text("""
        SELECT surface_key, site, path, persona_scope, record_context_ref,
               viewport, display_name
        FROM ui_surface_inventory_members
        WHERE inventory_version = :v AND surface_key = :k
    """), {"v": inventory_version, "k": surface_key}).mappings().first()
    return dict(r) if r else None


def surface_claims(session: Session, *, claim_set_id: str,
                   surface_key: str) -> list[ClaimOnSurface]:
    """The unrevoked members of ``claim_set_id`` whose latest claim body's
    surface canonicalises to ``surface_key`` — the same derivation the
    manifest builder makes (ui_manifest.py). Every applicability counts:
    a HUMAN_REVIEW claim is still a check on the surface."""
    rows = session.execute(text("""
        SELECT CAST(m.test_id AS text), m.applicability, m.executable,
               c.asserted_truth->'surface'
        FROM claim_set_members m
        JOIN test_claims c ON c.test_id = m.test_id AND c.valid_to IS NULL
        WHERE m.claim_set_id = CAST(:i AS uuid) AND m.revoked_at IS NULL
        ORDER BY m.test_id
    """), {"i": str(claim_set_id)}).fetchall()
    out = []
    for tid, applicability, executable, surface in rows:
        if not surface:
            continue                       # not a conformance claim body
        try:
            key = canonical_surface_key(SurfaceNaturalKey(**surface))
        except Exception:                  # noqa: BLE001 — a foreign body shape is not ours
            continue
        if key == surface_key:
            out.append(ClaimOnSurface(test_id=tid, applicability=applicability,
                                      executable=bool(executable)))
    return out


def identity_exists(session: Session, requirement_key: str,
                    external_system: str = JIRA) -> bool:
    return bool(session.execute(text("""
        SELECT 1 FROM requirement_identities
        WHERE external_system = CAST(:s AS external_system) AND external_key = :k
    """), {"s": external_system, "k": requirement_key}).scalar())


# --------------------------------------------------------------------------
# Reads the card and the picker render
# --------------------------------------------------------------------------

_LINK_COLS = """
    CAST(l.id AS text) AS link_id, CAST(l.external_system AS text) AS external_system,
    l.requirement_key, l.surface_key, l.inventory_version, l.source,
    l.declared_by, l.declared_at, l.active, l.deactivated_by, l.deactivated_at,
    l.deactivation_reason,
    m.display_name, m.site, m.path, m.persona_scope
"""


def _row_to_declared(r) -> DeclaredSurface:
    return DeclaredSurface(
        link_id=r["link_id"], external_system=r["external_system"],
        requirement_key=r["requirement_key"], surface_key=r["surface_key"],
        inventory_version=r["inventory_version"], source=r["source"],
        declared_by=r["declared_by"], declared_at=r["declared_at"],
        active=r["active"], deactivated_by=r["deactivated_by"],
        deactivated_at=r["deactivated_at"],
        deactivation_reason=r["deactivation_reason"],
        display_name=r["display_name"] or "", site=r["site"], path=r["path"],
        persona_scope=r["persona_scope"])


def declared_surfaces(session: Session, *, requirement_key: str,
                      external_system: str = JIRA,
                      include_inactive: bool = False) -> list[DeclaredSurface]:
    rows = session.execute(text(f"""
        SELECT {_LINK_COLS}
        FROM requirement_surface_links l
        JOIN ui_surface_inventory_members m
          ON m.inventory_version = l.inventory_version AND m.surface_key = l.surface_key
        WHERE l.external_system = CAST(:s AS external_system)
          AND l.requirement_key = :k
          {"" if include_inactive else "AND l.active"}
        ORDER BY l.inventory_version DESC, m.display_name, l.surface_key, l.declared_at
    """), {"s": external_system, "k": requirement_key}).mappings().all()
    return [_row_to_declared(r) for r in rows]


def get_link(session: Session, link_id: str) -> Optional[DeclaredSurface]:
    r = session.execute(text(f"""
        SELECT {_LINK_COLS}
        FROM requirement_surface_links l
        JOIN ui_surface_inventory_members m
          ON m.inventory_version = l.inventory_version AND m.surface_key = l.surface_key
        WHERE l.id = CAST(:i AS uuid)
    """), {"i": str(link_id)}).mappings().first()
    return _row_to_declared(r) if r else None


def picker_candidates(session: Session, *, requirement_key: str,
                      external_system: str = JIRA
                      ) -> tuple[Optional[int], list[SurfaceCandidate]]:
    """The ACTIVE inventory's members minus this requirement's active
    declarations minus record-context surfaces. ``(active_version, [...])``;
    ``(None, [])`` when no inventory is active."""
    v = active_inventory_version(session)
    if v is None:
        return None, []
    rows = session.execute(text("""
        SELECT m.surface_key, m.inventory_version, m.display_name, m.site, m.path,
               m.persona_scope
        FROM ui_surface_inventory_members m
        WHERE m.inventory_version = :v
          AND m.record_context_ref IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM requirement_surface_links l
              WHERE l.active
                AND l.external_system = CAST(:s AS external_system)
                AND l.requirement_key = :k
                AND l.inventory_version = m.inventory_version
                AND l.surface_key = m.surface_key)
        ORDER BY m.display_name, m.surface_key
    """), {"v": v, "s": external_system, "k": requirement_key}).fetchall()
    return v, [SurfaceCandidate(surface_key=r[0], inventory_version=r[1],
                                display_name=r[2] or "", site=r[3], path=r[4],
                                persona_scope=r[5]) for r in rows]


# --------------------------------------------------------------------------
# The acts
# --------------------------------------------------------------------------

def _audit(session: Session, *, tenant_id: Optional[int], user_id: int,
           action: str, details: dict) -> None:
    if tenant_id is None:
        return
    session.execute(text("""
        INSERT INTO public.activity_log
            (tenant_id, user_id, action, entity_type, entity_id, details)
        VALUES (:t, :u, :a, 'requirement_surface_link', NULL, CAST(:d AS JSONB))
    """), {"t": tenant_id, "u": user_id, "a": action, "d": json.dumps(details)})


def _materialise(session: Session, *, link: DeclaredSurface, claim_set_id: str,
                 actor_user_id: int) -> tuple[int, int]:
    """Write the S2 ``verifies`` links for every claim on the declared
    surface in ``claim_set_id`` that this link has not ledgered yet.
    Returns ``(materialised, already_linked)``."""
    from primeqa.test_representation.coordinator import (
        SemanticTransactionCoordinator,
    )
    coord = SemanticTransactionCoordinator()
    ledgered = {r[0] for r in session.execute(text(
        "SELECT CAST(test_id AS text) FROM requirement_surface_link_claims "
        "WHERE link_id = CAST(:l AS uuid)"), {"l": link.link_id}).fetchall()}
    claims = surface_claims(session, claim_set_id=claim_set_id,
                            surface_key=link.surface_key)
    new, old = 0, 0
    for c in claims:
        if c.test_id in ledgered:
            old += 1
            continue
        r = coord.link_requirement(
            session, actor=ACTOR, test_id=UUID(c.test_id),
            external_system=link.external_system,
            external_key=link.requirement_key, link_kind=LINK_KIND)
        session.execute(text("""
            INSERT INTO requirement_surface_link_claims
                (link_id, test_id, claim_set_id, created_link, materialised_by)
            VALUES (CAST(:l AS uuid), CAST(:t AS uuid), CAST(:c AS uuid), :n, :u)
        """), {"l": link.link_id, "t": c.test_id, "c": str(claim_set_id),
               "n": not r.was_noop, "u": actor_user_id})
        new += 1
    return new, old


def declare(session: Session, *, requirement_key: str, surface_key: str,
            actor_user_id: int, tenant_id: Optional[int] = None,
            external_system: str = JIRA,
            source: str = SOURCE_DECLARED) -> DeclareResult:
    """Declare ``requirement_key`` verified on ``surface_key`` (the ACTIVE
    inventory only) and materialise the S2 links. Idempotent: an existing
    active declaration is returned (``created=False``) and re-materialised.
    Refusals raise :class:`SurfaceLinkError` with a REASON_* code."""
    if source not in V1_WRITABLE_SOURCES:
        raise SurfaceLinkError(REASON_SOURCE_NOT_WRITABLE, source)
    v = active_inventory_version(session)
    if v is None:
        raise SurfaceLinkError(REASON_NO_ACTIVE_INVENTORY)
    member = inventory_member(session, v, surface_key)
    if member is None:
        raise SurfaceLinkError(REASON_NOT_IN_ACTIVE_INVENTORY, surface_key)
    if member["record_context_ref"] is not None:
        raise SurfaceLinkError(REASON_RECORD_CONTEXT, surface_key)
    if not identity_exists(session, requirement_key, external_system):
        raise SurfaceLinkError(REASON_NO_IDENTITY, requirement_key)

    existing = session.execute(text("""
        SELECT CAST(id AS text) FROM requirement_surface_links
        WHERE active AND external_system = CAST(:s AS external_system)
          AND requirement_key = :k AND surface_key = :sk AND inventory_version = :v
    """), {"s": external_system, "k": requirement_key, "sk": surface_key,
           "v": v}).scalar()
    created = existing is None
    if created:
        link_id = session.execute(text("""
            INSERT INTO requirement_surface_links
                (external_system, requirement_key, surface_key, inventory_version,
                 source, declared_by)
            VALUES (CAST(:s AS external_system), :k, :sk, :v, :src, :u)
            RETURNING CAST(id AS text)
        """), {"s": external_system, "k": requirement_key, "sk": surface_key,
               "v": v, "src": source, "u": actor_user_id}).scalar()
    else:
        link_id = existing
    link = get_link(session, link_id)
    claim_set_id = active_claim_set(session, v)
    materialised, already = (0, 0)
    if claim_set_id is not None:
        materialised, already = _materialise(
            session, link=link, claim_set_id=claim_set_id,
            actor_user_id=actor_user_id)
    _audit(session, tenant_id=tenant_id, user_id=actor_user_id,
           action="s2.surface_link.declare" if created else "s2.surface_link.rematerialise",
           details={"link_id": link_id, "requirement_key": requirement_key,
                    "surface_key": surface_key, "inventory_version": v,
                    "claim_set_id": claim_set_id, "materialised": materialised,
                    "already_linked": already})
    session.flush()
    return DeclareResult(link_id=link_id, created=created,
                         materialised=materialised, already_linked=already)


def rematerialise(session: Session, *, actor_user_id: int,
                  inventory_version: Optional[int] = None,
                  requirement_key: Optional[str] = None,
                  claim_set_id: Optional[str] = None,
                  external_system: str = JIRA) -> int:
    """Re-run materialisation over the ACTIVE declarations in scope (an
    inventory version, a requirement, or every one). Add-only (Fork 5).
    Returns the number of ledger rows written. Called from
    ``claim_sets.approve_claim_set`` with ``claim_set_id`` = the set just
    approved, so a superseding claim set is followed without a human
    remembering to — the declarations on THAT set's inventory version are
    materialised against THAT set (not "the latest approved by
    timestamp", which is the same thing in production and not inside a
    single transaction)."""
    if claim_set_id is not None:
        v = session.execute(text(
            "SELECT inventory_version FROM claim_sets WHERE id = CAST(:i AS uuid)"),
            {"i": str(claim_set_id)}).scalar()
        if v is None:
            return 0
        inventory_version = v
    where = ["l.active"]
    params: dict = {}
    if inventory_version is not None:
        where.append("l.inventory_version = :v"); params["v"] = inventory_version
    if requirement_key is not None:
        where.append("l.requirement_key = :k AND l.external_system = CAST(:s AS external_system)")
        params["k"] = requirement_key; params["s"] = external_system
    ids = [r[0] for r in session.execute(text(
        "SELECT CAST(l.id AS text) FROM requirement_surface_links l WHERE "
        + " AND ".join(where) + " ORDER BY l.declared_at"), params).fetchall()]
    total = 0
    for link_id in ids:
        link = get_link(session, link_id)
        cs = (str(claim_set_id) if claim_set_id is not None
              else active_claim_set(session, link.inventory_version))
        if cs is None:
            continue
        new, _ = _materialise(session, link=link, claim_set_id=cs,
                              actor_user_id=actor_user_id)
        total += new
    if total:
        session.flush()
    return total


def unlink(session: Session, *, link_id: str, actor_user_id: int, reason: str,
           tenant_id: Optional[int] = None) -> UnlinkResult:
    """Deactivate a declaration with provenance and remove the S2
    ``verifies`` rows THIS declaration created (through S2's own API);
    pre-existing rows are left. Idempotent on an inactive link."""
    from primeqa.test_representation.coordinator import (
        SemanticTransactionCoordinator,
    )
    link = get_link(session, link_id)
    if link is None:
        raise SurfaceLinkError("unknown_link", link_id)
    if not link.active:
        return UnlinkResult(link_id=link_id, removed_links=0, kept_links=0,
                            already_inactive=True)
    session.execute(text("""
        UPDATE requirement_surface_links
        SET active = FALSE, deactivated_by = :u, deactivated_at = now(),
            deactivation_reason = :r
        WHERE id = CAST(:i AS uuid)
    """), {"u": actor_user_id, "r": (reason or "").strip() or None, "i": link_id})
    coord = SemanticTransactionCoordinator()
    rows = session.execute(text("""
        SELECT CAST(test_id AS text), created_link
        FROM requirement_surface_link_claims
        WHERE link_id = CAST(:l AS uuid) AND deactivated_at IS NULL
        ORDER BY test_id
    """), {"l": link_id}).fetchall()
    removed, kept = 0, 0
    for tid, created_link in rows:
        if created_link:
            coord.unlink_requirement(
                session, actor=ACTOR, test_id=UUID(tid),
                external_system=link.external_system,
                external_key=link.requirement_key, link_kind=LINK_KIND)
            removed += 1
        else:
            kept += 1
        session.execute(text("""
            UPDATE requirement_surface_link_claims
            SET deactivated_by = :u, deactivated_at = now()
            WHERE link_id = CAST(:l AS uuid) AND test_id = CAST(:t AS uuid)
        """), {"u": actor_user_id, "l": link_id, "t": tid})
    _audit(session, tenant_id=tenant_id, user_id=actor_user_id,
           action="s2.surface_link.unlink",
           details={"link_id": link_id, "requirement_key": link.requirement_key,
                    "surface_key": link.surface_key,
                    "inventory_version": link.inventory_version,
                    "reason": reason, "removed_links": removed, "kept_links": kept})
    session.flush()
    return UnlinkResult(link_id=link_id, removed_links=removed, kept_links=kept,
                        already_inactive=False)
