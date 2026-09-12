"""Step 1 — the view layer's read of requirement IDENTITIES
(LLD_STEP_1_PROVENANCE §c, §d).

Best-effort console discipline (the D-476 bridge pattern): every function
returns ``{"available": bool, ...}`` and NEVER raises into a render. A
tenant with no substrate schema simply has no identities, and the pages
say so rather than 500ing.

Two things the surfaces need and could not ask for before:

* :func:`identity_overview` — the origin of every identity the tenant
  has, the hidden-by-default count, and the REFERENTIAL GAPS (an
  identity with claims and no live decorating row). A gap is a defect to
  see: it is never part of the hidden set.
* :func:`decorate_identity` — create the requirements row that decorates
  an EXISTING identity. It never mints a key: the row is created with
  ``external_key`` set to the identity's own key.
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from primeqa.test_representation.identity import (
    CANNOT_CLASSIFY,
    HIDDEN_BY_DEFAULT,
    ORIGINS,
    link_key_census,
)

log = logging.getLogger(__name__)


def identity_overview(tenant_id: int, *, keys=None, session=None) -> dict:
    """``{available, origins: {key: origin}, counts: {origin: n},
    hidden_count, hidden_keys, gaps: [...], unestablished}``.

    ``keys`` narrows the origin map to the page's rows; the counts, the
    hidden count and the gaps are always tenant-wide (a hidden count that
    only counted the current page would be a lie about what is hidden).
    An identity with no ``requirement_identities`` row reports
    ``CANNOT_CLASSIFY`` — the honest state before the backfill runs, never
    an invented origin.

    ``session`` (close 2) reads on the render's shared connection instead of
    opening one; the queries and the result are unchanged either way."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        from primeqa.semantic.read_scope import conn_of

        def _read(conn):
            rows = conn.execute(text(
                "SELECT external_key, origin FROM requirement_identities"
            )).mappings().all()
            return ({r["external_key"]: r["origin"] for r in rows},
                    link_key_census(conn),
                    {r[0] for r in conn.execute(text(
                        "SELECT COALESCE(external_key, jira_key, 'req-' || id) "
                        "FROM public.requirements "
                        "WHERE tenant_id = :t AND deleted_at IS NULL"),
                        {"t": tenant_id}).all()})

        shared = conn_of(session)
        if shared is not None:
            established, census, decorated = _read(shared)
        else:
            with get_tenant_connection(tenant_id) as conn:
                established, census, decorated = _read(conn)

        all_keys = set(established) | set(census) | decorated
        origins = {k: established.get(k, CANNOT_CLASSIFY) for k in all_keys}
        counts = {o: 0 for o in ORIGINS}
        for o in origins.values():
            counts[o] = counts.get(o, 0) + 1
        hidden_keys = {k for k, o in origins.items() if o in HIDDEN_BY_DEFAULT}
        gaps = sorted(
            ({"key": k, "origin": origins[k],
              "claims": census[k]["claims"],
              "approved_claims": census[k]["approved_claims"]}
             for k in census if k not in decorated),
            key=lambda g: g["key"])
        return {
            "available": True,
            "origins": ({k: origins[k] for k in keys if k in origins}
                        if keys is not None else origins),
            "counts": counts,
            "hidden_count": len(hidden_keys),
            "hidden_keys": sorted(hidden_keys),
            "gaps": gaps,
            "unestablished": sorted(all_keys - set(established)),
        }
    except Exception as exc:
        log.warning("requirement identities unavailable for tenant %s: %s",
                    tenant_id, exc)
        return {"available": False, "origins": {}, "counts": {},
                "hidden_count": 0, "hidden_keys": [], "gaps": [],
                "unestablished": []}


def decorate_identity(db, tenant_id: int, external_key: str, *, section_id: int,
                      summary: str, created_by: int,
                      acceptance_criteria: str = "",
                      origin: str = None) -> dict:
    """Create the requirements row that DECORATES an existing identity
    (§c). Returns ``{ok, requirement_id}`` or ``{ok: False, error}``.

    The identity's key is carried onto the row verbatim — no key is
    minted, and the identity row itself is untouched (its
    ``established_at`` and evidence stay as they were). An explicit
    ``origin`` re-classifies the identity as a recorded human override;
    absent one, the origin stands as it was."""
    from primeqa.core.repository import ActivityLogRepository
    from primeqa.shared.api import ValidationError
    from primeqa.test_management.repository import (
        RequirementRepository, SectionRepository,
    )
    from primeqa.test_management.service import TestManagementService
    try:
        svc = TestManagementService(
            section_repo=SectionRepository(db),
            requirement_repo=RequirementRepository(db),
            activity_repo=ActivityLogRepository(db))
        req = svc.create_requirement(
            tenant_id, section_id, "manual", created_by,
            external_key=external_key, jira_summary=summary,
            acceptance_criteria=acceptance_criteria)
        if origin:
            _override_origin(tenant_id, external_key, origin, created_by)
        return {"ok": True, "requirement_id": req["id"],
                "external_key": external_key}
    except ValidationError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:                                # pragma: no cover
        log.warning("decorate failed for %s on tenant %s: %s",
                    external_key, tenant_id, e)
        return {"ok": False, "error": "could not create the record"}


def _override_origin(tenant_id: int, external_key: str, origin: str,
                     user_id: int) -> None:
    """A HUMAN re-classification of an established identity — recorded as
    an override with its actor, never a silent re-write. The key columns
    are untouched (the immutability trigger would refuse them anyway)."""
    import json
    if origin not in ORIGINS:
        raise ValueError(f"unknown origin {origin!r}")
    from primeqa.semantic.connection import get_tenant_connection
    with get_tenant_connection(tenant_id) as conn:
        conn.execute(text(
            "UPDATE requirement_identities SET origin = :o, "
            "origin_evidence = CAST(:ev AS JSONB), established_by = :by "
            "WHERE external_key = :k"),
            {"o": origin, "k": external_key, "by": f"human:{user_id}",
             "ev": json.dumps({"rule": "override", "reason": "set on decorate",
                               "decided_by": user_id})})
        conn.execute(text(
            "INSERT INTO public.activity_log "
            "(tenant_id, user_id, action, entity_type, entity_id, details) "
            "VALUES (:t, :u, 'identity.origin_override', "
            "        'requirement_identity', NULL, CAST(:d AS JSONB))"),
            {"t": tenant_id, "u": user_id,
             "d": json.dumps({"external_key": external_key, "origin": origin})})
