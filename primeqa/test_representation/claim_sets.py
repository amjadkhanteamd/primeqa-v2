"""S2 claim sets + surface inventory services (LLD 3A-3 §a/§d).

Two recorded-membership objects on the D-281 law:

  - **Surface inventory**: the declared, versioned surface universe. A
    version's membership is written in one transaction at creation and
    never recomputed; a change to the universe is a NEW version.
  - **claim_set**: one human act approves (persona × inventory version ×
    catalogue release) as a set. Membership + per-member applicability
    are recorded at set creation (enumeration output); approval promotes
    the members it recorded — never a reconstruction from parts.

Attribution (the fix for the actor="human" audit gap, recon 2026-08-21):
approval threads the REAL user id + claim_set id into every provenance
event via the coordinator's ``event_context`` (``event_actor`` stays
``"human"`` for D-ε-1 authority compatibility) and writes ONE
activity_log row for the act — ``entity_id`` is an INTEGER column, so
the set UUID rides in ``details``.
"""
from __future__ import annotations

import uuid as _uuid_mod
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from primeqa.test_representation.coordinator import (
    SemanticTransactionCoordinator,
)
from primeqa.test_representation.models.surface import (
    SurfaceNaturalKey,
    canonical_surface_key,
)

_APPLICABILITY_VALUES = frozenset(
    {"APPLICABLE", "NOT_APPLICABLE", "HUMAN_REVIEW"})


class ClaimSetError(ValueError):
    """Refusal from the inventory / claim_set services — always carries
    the reason; never a silent skip."""


# ---------------------------------------------------------------------------
# Surface inventory
# ---------------------------------------------------------------------------

def create_inventory_version(
    session: Session,
    *,
    members: list[dict],
    created_by: int,
    notes: str = "",
) -> int:
    """Record a NEW inventory version with its full membership in one
    transaction (flushed here; the caller commits — D-β posture).

    Each member dict carries the five v1 identity fields (``site``,
    ``path``, ``persona_scope``, optional ``record_context_ref`` /
    ``viewport``) plus optional operational metadata (``display_name``,
    ``notes``, ``auth_required``). Members are validated through
    :class:`SurfaceNaturalKey`; the stored ``surface_key`` is the frozen
    canonical form. An empty membership is refused — a declared universe
    of nothing is a mistake, not a version.
    """
    if not members:
        raise ClaimSetError(
            "inventory version with zero members refused — declare at "
            "least one surface")
    version = session.execute(text(
        "SELECT COALESCE(MAX(inventory_version), 0) + 1 "
        "FROM ui_surface_inventories")).scalar_one()
    session.execute(text("""
        INSERT INTO ui_surface_inventories
            (inventory_version, notes, created_by)
        VALUES (:v, :n, :u)
    """), {"v": version, "n": notes, "u": created_by})
    seen: set[str] = set()
    for m in members:
        surface = SurfaceNaturalKey(
            site=m["site"], path=m["path"],
            persona_scope=m["persona_scope"],
            record_context_ref=m.get("record_context_ref"),
            viewport=m.get("viewport"),
        )
        key = canonical_surface_key(surface)
        if key in seen:
            raise ClaimSetError(
                f"duplicate surface in inventory declaration: {key}")
        seen.add(key)
        session.execute(text("""
            INSERT INTO ui_surface_inventory_members
                (inventory_version, surface_key, site, path, persona_scope,
                 record_context_ref, viewport, display_name, notes,
                 auth_required)
            VALUES (:v, :k, :site, :path, :persona, :rcr, :vp, :dn, :notes,
                    :auth)
        """), {
            "v": version, "k": key, "site": surface.site,
            "path": surface.path, "persona": surface.persona_scope,
            "rcr": surface.record_context_ref, "vp": surface.viewport,
            "dn": m.get("display_name", ""), "notes": m.get("notes", ""),
            "auth": bool(m.get("auth_required", False)),
        })
    session.flush()
    return int(version)


def inventory_members(session: Session, inventory_version: int) -> list[dict]:
    """The RECORDED membership of one version (D-281: read the rows,
    never recompute). Refuses an unknown version."""
    anchor = session.execute(text(
        "SELECT inventory_version FROM ui_surface_inventories "
        "WHERE inventory_version = :v"), {"v": inventory_version}).fetchone()
    if anchor is None:
        raise ClaimSetError(
            f"inventory version {inventory_version} does not exist")
    rows = session.execute(text("""
        SELECT surface_key, site, path, persona_scope, record_context_ref,
               viewport, display_name, auth_required
        FROM ui_surface_inventory_members
        WHERE inventory_version = :v ORDER BY surface_key
    """), {"v": inventory_version}).fetchall()
    return [{"surface_key": r[0], "site": r[1], "path": r[2],
             "persona_scope": r[3], "record_context_ref": r[4],
             "viewport": r[5], "display_name": r[6], "auth_required": r[7]}
            for r in rows]


# ---------------------------------------------------------------------------
# claim_sets
# ---------------------------------------------------------------------------

def create_claim_set(
    session: Session,
    *,
    persona_scope: str,
    inventory_version: int,
    catalogue_release_id: int,
    created_by: int,
    members: list[dict],
    standard_profile: str = "WCAG22",
) -> UUID:
    """Record a DRAFT claim_set with its membership (enumeration output).
    Each member dict: ``test_id`` (UUID), ``applicability``,
    ``executable``. Membership is recorded here, once — approval
    approves exactly these rows."""
    if not members:
        raise ClaimSetError("claim_set with zero members refused")
    set_id = _uuid_mod.uuid4()
    session.execute(text("""
        INSERT INTO claim_sets
            (id, persona_scope, inventory_version, catalogue_release_id,
             standard_profile, status, created_by)
        VALUES (:i, :p, :v, :r, :s, 'draft', :u)
    """), {"i": str(set_id), "p": persona_scope, "v": inventory_version,
           "r": catalogue_release_id, "s": standard_profile,
           "u": created_by})
    for m in members:
        if m["applicability"] not in _APPLICABILITY_VALUES:
            raise ClaimSetError(
                f"unknown applicability {m['applicability']!r}")
        session.execute(text("""
            INSERT INTO claim_set_members
                (claim_set_id, test_id, applicability, executable)
            VALUES (:s, :t, :a, :e)
        """), {"s": str(set_id), "t": str(m["test_id"]),
               "a": m["applicability"], "e": bool(m["executable"])})
    session.flush()
    return set_id


def approve_claim_set(
    session: Session,
    *,
    claim_set_id: UUID,
    user_id: int,
    tenant_id: int,
) -> dict:
    """ONE human act approves the set (D3/F10): every non-revoked member
    claim promotes to ``approved`` (its unapproved recipes too — the
    `_approve_claim` posture, deprecated ones never resurrected), the set
    row is stamped, and the act is audited with REAL attribution.

    Applicability governs EXECUTION (the 3A-4 manifest builder includes
    only APPLICABLE + executable members), not approval — HUMAN_REVIEW
    and NOT_EXECUTABLE members approve with the set and stay visible.

    Approving twice is REFUSED (not idempotent): approval is a recorded
    human act with an actor and a timestamp; a second act would either
    silently no-op (hiding that someone believed the set unapproved) or
    re-stamp (rewriting who approved it). The error names the recorded
    approver.
    """
    row = session.execute(text("""
        SELECT status, approved_by, approved_at, persona_scope,
               inventory_version, catalogue_release_id
        FROM claim_sets WHERE id = :i
    """), {"i": str(claim_set_id)}).fetchone()
    if row is None:
        raise ClaimSetError(f"claim_set {claim_set_id} does not exist")
    if row[0] == "approved":
        raise ClaimSetError(
            f"claim_set {claim_set_id} is already approved by user "
            f"{row[1]} at {row[2]} — approval is a recorded human act, "
            f"not repeatable")
    if row[0] == "revoked":
        raise ClaimSetError(
            f"claim_set {claim_set_id} is revoked and cannot be approved")

    members = session.execute(text("""
        SELECT test_id FROM claim_set_members
        WHERE claim_set_id = :i AND revoked_at IS NULL ORDER BY test_id
    """), {"i": str(claim_set_id)}).fetchall()
    coord = SemanticTransactionCoordinator()
    event_context = {"user_id": user_id, "claim_set_id": str(claim_set_id)}
    claims_promoted = 0
    recipes_promoted = 0
    for (tid_str,) in members:
        tid = UUID(str(tid_str))
        claim = coord.get_latest_claim(session, tid)
        if claim is None:
            raise ClaimSetError(f"member claim {tid} has no current version")
        coord.promote_claim_to_approved(
            session, actor="human", test_id=tid,
            version_seq=claim.version_seq, event_context=event_context)
        claims_promoted += 1
        for r in coord.list_active_recipes(session, tid):
            if r.status == "deprecated":
                continue                 # D-226: never silently un-deprecate
            if r.status not in ("active", "approved"):
                coord.promote_recipe_to_approved(
                    session, actor="human", recipe_id=r.recipe_id,
                    version_seq=r.version_seq, event_context=event_context)
                recipes_promoted += 1

    session.execute(text("""
        UPDATE claim_sets
        SET status = 'approved', approved_by = :u, approved_at = NOW(),
            member_count = :n
        WHERE id = :i
    """), {"u": user_id, "n": len(members), "i": str(claim_set_id)})
    # The activity_log row today's bulk-approval route lacks
    # (views.py requirements_approve_drafts writes none). entity_id is an
    # INTEGER column — the set UUID rides in details.
    session.execute(text("""
        INSERT INTO public.activity_log
            (tenant_id, user_id, action, entity_type, entity_id, details)
        VALUES (:t, :u, 's2.claim_set.approve', 'claim_set', NULL,
                CAST(:d AS JSONB))
    """), {"t": tenant_id, "u": user_id,
           "d": __import__("json").dumps({
               "claim_set_id": str(claim_set_id),
               "persona_scope": row[3],
               "inventory_version": row[4],
               "catalogue_release_id": row[5],
               "member_count": len(members),
               "claims_promoted": claims_promoted,
               "recipes_promoted": recipes_promoted,
           })})
    session.flush()
    return {"claim_set_id": str(claim_set_id),
            "claims_promoted": claims_promoted,
            "recipes_promoted": recipes_promoted,
            "member_count": len(members)}


def revoke_member(
    session: Session,
    *,
    claim_set_id: UUID,
    test_id: UUID,
    user_id: int,
    reason: str,
) -> dict:
    """Revoke ONE member without dissolving the set: the claim deprecates
    (existing humans-only path, reason required per D-ε-5) and the member
    row is stamped. The set stays 'approved'; a manifest built from it
    excludes revoked members at build time (3A-4)."""
    member = session.execute(text("""
        SELECT revoked_at FROM claim_set_members
        WHERE claim_set_id = :s AND test_id = :t
    """), {"s": str(claim_set_id), "t": str(test_id)}).fetchone()
    if member is None:
        raise ClaimSetError(
            f"{test_id} is not a member of claim_set {claim_set_id}")
    if member[0] is not None:
        raise ClaimSetError(f"member {test_id} is already revoked")
    coord = SemanticTransactionCoordinator()
    claim = coord.get_latest_claim(session, test_id)
    if claim is None:
        raise ClaimSetError(f"member claim {test_id} has no current version")
    if claim.status != "deprecated":
        coord.deprecate_claim(
            session, actor="human", test_id=test_id,
            version_seq=claim.version_seq, reason=reason)
    session.execute(text("""
        UPDATE claim_set_members
        SET revoked_at = NOW(), revoked_by = :u
        WHERE claim_set_id = :s AND test_id = :t
    """), {"u": user_id, "s": str(claim_set_id), "t": str(test_id)})
    session.flush()
    return {"revoked": str(test_id)}


def approve_claim_set_for_tenant(
    tenant_id: int, claim_set_id, user_id: int,
) -> dict:
    """Best-effort route wrapper — one atomic tenant transaction (the
    connection helper commits on clean exit). Never raises."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as session:
            return {"ok": True, **approve_claim_set(
                session, claim_set_id=UUID(str(claim_set_id)),
                user_id=user_id, tenant_id=tenant_id)}
    except Exception as e:  # noqa: BLE001 — route boundary
        return {"ok": False, "error": str(e)}
