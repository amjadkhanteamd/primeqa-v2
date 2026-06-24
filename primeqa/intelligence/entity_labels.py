"""Org-scoped business-label resolution for the projection spine.

The narrative spine (test-case titles, run attribution, evidence steps) must read
in business language — "Case SLA", "SLA Code" — not raw Salesforce API names
(``Case_SLA__c``, ``Case_SLA__c.SLA_Code__c``). S1 already stores the human label
on every entity: ``entities.display_name`` (the SF ``label`` / ``MasterLabel``),
keyed per connected org by ``sf_api_name`` (object-qualified for fields, so the
key matches the claim's ``external_id`` verbatim — no parent/child join).

:func:`label_map` is the single resolver: ONE query → ``{sf_api_name:
display_name}`` for one org, built once per page and threaded into the pure
presentation functions (``claim_presentation.claim_title`` / ``step_plain`` /
``humanize_attribution``). It is **best-effort** (returns ``{}`` on any error —
presentation must never break a page) and **org-scoped** (never blends labels
across orgs, per the per-org restructure D-255..D-260): a standard SF label is
org-invariant, but a custom object's label belongs to one org's metadata lineage.

Org selection:
  - ``connected_org_id`` given → that org (the precise scope).
  - ``environment_id`` given → resolve its org via
    :func:`primeqa.sync.credentials.get_connected_org_for_environment` (the run
    detail uses this — the run carries the env it executed against).
  - neither → the tenant's **primary org**: the connected org with the most
    active entities (deterministic, ties broken by id). This is the live
    generation org today (env-59); the org-agnostic surfaces (claims library,
    test plan) have no env in scope, so they resolve against it. A single org —
    no blend.

The map omits rows whose ``display_name`` is NULL or equals the ``sf_api_name``
(a label that equals the api name adds nothing; a lean map keeps the caller's
fallback-to-api honest).
"""
from __future__ import annotations

import logging

from sqlalchemy import text

log = logging.getLogger(__name__)


_PRIMARY_ORG_SQL = (
    "SELECT connected_org_id FROM entities "
    "WHERE valid_to_seq IS NULL AND connected_org_id IS NOT NULL "
    "GROUP BY connected_org_id ORDER BY COUNT(*) DESC, connected_org_id LIMIT 1")

_LABELS_SQL = (
    "SELECT sf_api_name, display_name FROM entities "
    "WHERE valid_to_seq IS NULL AND connected_org_id = CAST(:org AS uuid) "
    "AND display_name IS NOT NULL AND display_name <> sf_api_name")


def label_map(tenant_id: int, *, connected_org_id=None, environment_id=None) -> dict:
    """Best-effort ``{sf_api_name: display_name}`` for one org of a tenant.

    Resolves the org from (in priority order) an explicit ``connected_org_id``,
    an ``environment_id`` (→ its connected org), or — when neither is given —
    the tenant's primary org (most active entities). ONE query for the labels;
    never raises → ``{}`` on any error or when no org/labels exist."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            org = connected_org_id
            if org is None and environment_id is not None:
                from primeqa.sync.credentials import (
                    get_connected_org_for_environment,
                )
                org = get_connected_org_for_environment(conn, environment_id)
            if org is None:
                org = conn.execute(text(_PRIMARY_ORG_SQL)).scalar()
            if org is None:
                return {}
            rows = conn.execute(text(_LABELS_SQL), {"org": str(org)}).all()
        return {r[0]: r[1] for r in rows if r[0]}
    except Exception as exc:                              # presentation never breaks
        log.warning("label_map unavailable for tenant %s: %s", tenant_id, exc)
        return {}
