"""S5 Rule Registry — the ONLY write path (LLD 3A-1 §2.2/§4).

Lifecycle: DRAFT -> REVIEW -> APPROVED -> VERSIONED -> ACTIVE -> RETIRED,
strictly sequential; ACTIVE is immutable (change = new_draft_version).
The DB enforces the corruption-class invariants (version PK uniqueness,
the single-ACTIVE partial unique index, the state CHECK); THIS module
enforces transition legality, actor attribution (REAL user ids, never a
literal), superadmin gating, and activity_log audit on every transition.

Bootstrap non-repeatability (LLD §3, amended per Gate-2 GO): no service
path creates a version in any state but DRAFT; direct-to-ACTIVE is refused
for any row not carrying seed_provenance.bootstrap=true — and no service
path supplies that either. The 063 seed migration was the only such path,
and it cannot recur (idempotent ON CONFLICT DO NOTHING + this guard).
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import text

from primeqa.core.authz import Tier, rank

_TRANSITIONS = {
    "DRAFT": "REVIEW",
    "REVIEW": "APPROVED",
    "APPROVED": "VERSIONED",
    "VERSIONED": "ACTIVE",
    "ACTIVE": "RETIRED",
}


class LifecycleError(RuntimeError):
    """An illegal lifecycle operation (bad transition, authority, or the
    bootstrap direct-to-ACTIVE guard). Never a silent no-op."""


def _require_superadmin(actor_role: str) -> None:
    if rank(actor_role) < Tier.SUPERADMIN:
        raise LifecycleError(
            "catalogue lifecycle writes are platform actions: superadmin only")


def _audit(session, actor_tenant_id: int, actor_user_id: int, action: str,
           rule_id: str | None, details: dict,
           entity_id: int | None = None) -> None:
    """activity_log.entity_id is INTEGER (001 + core model) — rule ids are
    strings, so they ride in details; only integer ids (release id) use
    entity_id. Caught by the scratch-DB replay before it could ship."""
    from primeqa.core.repository import ActivityLogRepository
    if rule_id is not None:
        details = {**details, "rule_id": rule_id}
    ActivityLogRepository(session).log_activity(
        actor_tenant_id, actor_user_id, action, "s5_rule", entity_id, details)


def create_rule(session, *, rule_id: str, name: str, description: str,
                automation_capability: str, human_review_required: bool,
                actor_user_id: int, actor_tenant_id: int,
                actor_role: str) -> None:
    """New rule + version 1 in DRAFT. The ONLY creation state is DRAFT —
    the bootstrap guard is structural: no state parameter exists."""
    _require_superadmin(actor_role)
    session.execute(text(
        "INSERT INTO s5_rules (rule_id, owner) VALUES (:rid, 'plimsol')"),
        {"rid": rule_id})
    _insert_version(session, rule_id, 1, name, description,
                    automation_capability, human_review_required,
                    state="DRAFT", seed_provenance=None,
                    actor_user_id=actor_user_id)
    _audit(session, actor_tenant_id, actor_user_id, "s5.rule.create",
           rule_id, {"version": 1})
    session.commit()


def new_draft_version(session, *, rule_id: str, name: str, description: str,
                      automation_capability: str, human_review_required: bool,
                      actor_user_id: int, actor_tenant_id: int,
                      actor_role: str) -> int:
    """The ONLY change path for an ACTIVE rule: a fresh DRAFT at vN+1
    (ACTIVE rows are immutable — there is no update API at all)."""
    _require_superadmin(actor_role)
    row = session.execute(text(
        "SELECT COALESCE(MAX(version), 0) FROM s5_rule_versions "
        "WHERE rule_id = :rid"), {"rid": rule_id}).fetchone()
    if row[0] == 0:
        raise LifecycleError(f"unknown rule {rule_id!r}; use create_rule")
    next_v = row[0] + 1
    _insert_version(session, rule_id, next_v, name, description,
                    automation_capability, human_review_required,
                    state="DRAFT", seed_provenance=None,
                    actor_user_id=actor_user_id)
    _audit(session, actor_tenant_id, actor_user_id, "s5.rule.new_version",
           rule_id, {"version": next_v})
    session.commit()
    return next_v


def _insert_version(session, rule_id, version, name, description,
                    automation_capability, human_review_required, *,
                    state, seed_provenance, actor_user_id) -> None:
    """Internal insert. THE bootstrap non-repeatability guard lives here:
    ACTIVE (or any non-DRAFT state) at insert time is refused unless the
    row carries bootstrap seed provenance — which no service caller ever
    supplies; the 063 seed migration wrote its rows directly in SQL."""
    if state != "DRAFT" and not (
            isinstance(seed_provenance, dict)
            and seed_provenance.get("bootstrap") is True):
        raise LifecycleError(
            f"direct-to-{state} requires bootstrap seed provenance; the seed "
            "migration was the only such path and it cannot recur")
    session.execute(text("""
        INSERT INTO s5_rule_versions
            (rule_id, version, name, description, automation_capability,
             human_review_required, state, seed_provenance, created_by,
             state_changed_by)
        VALUES (:rid, :v, :n, :d, :cap, :hrr, :st,
                CAST(:prov AS JSONB), :actor, :actor)
    """), {"rid": rule_id, "v": version, "n": name, "d": description,
           "cap": automation_capability, "hrr": human_review_required,
           "st": state,
           "prov": json.dumps(seed_provenance) if seed_provenance else None,
           "actor": actor_user_id})


def next_rule_id(session, family: str = "A11Y") -> str:
    """Next-available PLM id for a family — assigned at APPEND TIME, never
    reserved (LLD §3a wording, amended 2026-08-25)."""
    row = session.execute(text(
        "SELECT COALESCE(MAX(CAST(RIGHT(rule_id, 3) AS INT)), 0) "
        "FROM s5_rules WHERE rule_id LIKE :pfx"),
        {"pfx": f"PLM-{family}-%"}).fetchone()
    return f"PLM-{family}-{row[0] + 1:03d}"


def _require_draft(session, rule_id: str, version: int) -> None:
    """Authoring writes (bindings, standard maps) are allowed ONLY while the
    version is in DRAFT — content is frozen from REVIEW onward (ACTIVE
    immutability starts, in authoring terms, at submission)."""
    row = session.execute(text(
        "SELECT state FROM s5_rule_versions "
        "WHERE rule_id = :rid AND version = :v"),
        {"rid": rule_id, "v": version}).fetchone()
    if row is None:
        raise LifecycleError(f"no such rule version {rule_id} v{version}")
    if row[0] != "DRAFT":
        raise LifecycleError(
            f"authoring writes require DRAFT; {rule_id} v{version} is {row[0]}")


def add_engine_binding(session, *, rule_id: str, version: int, engine: str,
                       engine_version: str, engine_rule_id: str,
                       actor_user_id: int, actor_tenant_id: int,
                       actor_role: str) -> None:
    _require_superadmin(actor_role)
    _require_draft(session, rule_id, version)
    session.execute(text("""
        INSERT INTO s5_engine_bindings
            (rule_id, rule_version, engine, engine_version, engine_rule_id)
        VALUES (:rid, :v, :e, :ev, :eid)
    """), {"rid": rule_id, "v": version, "e": engine, "ev": engine_version,
           "eid": engine_rule_id})
    _audit(session, actor_tenant_id, actor_user_id, "s5.rule.bind_engine",
           rule_id, {"version": version, "engine": engine,
                     "engine_version": engine_version,
                     "engine_rule_id": engine_rule_id})
    session.commit()


def add_standard_map(session, *, rule_id: str, version: int, standard: str,
                     criterion: str, level: str | None,
                     actor_user_id: int, actor_tenant_id: int,
                     actor_role: str) -> None:
    _require_superadmin(actor_role)
    _require_draft(session, rule_id, version)
    session.execute(text("""
        INSERT INTO s5_standard_maps
            (rule_id, rule_version, standard, criterion, level)
        VALUES (:rid, :v, :std, :c, :lvl)
    """), {"rid": rule_id, "v": version, "std": standard, "c": criterion,
           "lvl": level})
    _audit(session, actor_tenant_id, actor_user_id, "s5.rule.map_standard",
           rule_id, {"version": version, "standard": standard,
                     "criterion": criterion, "level": level})
    session.commit()


def transition(session, *, rule_id: str, version: int, to_state: str,
               actor_user_id: int, actor_tenant_id: int,
               actor_role: str) -> None:
    """One legal step of the machine. Activation atomically retires the
    previously-ACTIVE version in the same transaction; the DB's
    single-ACTIVE partial unique index makes the swap atomic-or-refused."""
    _require_superadmin(actor_role)
    row = session.execute(text(
        "SELECT state FROM s5_rule_versions "
        "WHERE rule_id = :rid AND version = :v FOR UPDATE"),
        {"rid": rule_id, "v": version}).fetchone()
    if row is None:
        raise LifecycleError(f"no such rule version {rule_id} v{version}")
    current = row[0]
    if _TRANSITIONS.get(current) != to_state:
        raise LifecycleError(
            f"illegal transition {current} -> {to_state} for {rule_id} "
            f"v{version}; legal next state: {_TRANSITIONS.get(current)!r}")
    if to_state == "ACTIVE":
        session.execute(text("""
            UPDATE s5_rule_versions
            SET state = 'RETIRED', state_changed_at = NOW(),
                state_changed_by = :actor
            WHERE rule_id = :rid AND state = 'ACTIVE'
        """), {"rid": rule_id, "actor": actor_user_id})
    session.execute(text("""
        UPDATE s5_rule_versions
        SET state = :st, state_changed_at = NOW(), state_changed_by = :actor
        WHERE rule_id = :rid AND version = :v
    """), {"st": to_state, "rid": rule_id, "v": version,
           "actor": actor_user_id})
    _audit(session, actor_tenant_id, actor_user_id, "s5.rule.transition",
           rule_id, {"version": version, "from": current, "to": to_state})
    session.commit()


def create_release(session, *, notes: str, actor_user_id: int,
                   actor_tenant_id: int, actor_role: str) -> int:
    """Snapshot the CURRENT ACTIVE set as a catalogue release — membership
    RECORDED at creation (D-281 drift-immunity law), never recomputed."""
    _require_superadmin(actor_role)
    members = session.execute(text("""
        SELECT rule_id, version FROM s5_rule_versions
        WHERE state = 'ACTIVE' ORDER BY rule_id
    """)).fetchall()
    if not members:
        raise LifecycleError("refusing an empty catalogue release")
    content_hash = hashlib.sha256(
        "\n".join(f"{m[0]}:{m[1]}" for m in members).encode()).hexdigest()
    rel = session.execute(text("""
        INSERT INTO s5_catalogue_releases (notes, content_hash, created_by)
        VALUES (:n, :h, :actor) RETURNING id
    """), {"n": notes, "h": content_hash, "actor": actor_user_id}).fetchone()
    for rid, ver in members:
        session.execute(text("""
            INSERT INTO s5_catalogue_release_members
                (release_id, rule_id, rule_version)
            VALUES (:rel, :rid, :v)
        """), {"rel": rel[0], "rid": rid, "v": ver})
    _audit(session, actor_tenant_id, actor_user_id, "s5.release.create",
           None, {"members": len(members), "content_hash": content_hash},
           entity_id=rel[0])
    session.commit()
    return rel[0]
