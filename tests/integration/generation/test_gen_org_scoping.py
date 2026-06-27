"""Integration: generation org-scoping (D-286) — the synthetic mirror of the live
PROOF 1 (env-59's Opportunity: org-blind → 2, scoped → 1), plus the explicit
single-org NO-OP that guarantees no regression for every env-59-only tenant.

These are the tests that would catch a regression if someone later un-scoped
generation. DB-gated (local-PG; skips when unreachable). Writes only to the local
test DB's S1 tables — never prod.
"""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy import text

from primeqa.generation.intake import resolve_current_s1_version
from primeqa.semantic.connection import get_tenant_connection
from primeqa.semantic.query import SemanticOrgModel
from primeqa.sync.credentials import (
    ensure_connected_org_for_environment, resolve_connected_org_or_raise)

from .conftest import TEST_ENV_ID, TEST_TENANT_ID


def _version(conn, prefix: str, org: str) -> int:
    return conn.execute(text(
        "INSERT INTO logical_versions (version_name, version_type, connected_org_id) "
        "VALUES (:n,'manual_checkpoint',CAST(:org AS uuid)) RETURNING version_seq"
    ), {"n": f"{prefix}_{uuid4().hex[:8]}", "org": org}).scalar()


def _object(conn, api: str, vfrom: int, org: str) -> str:
    return conn.execute(text(
        "INSERT INTO entities (entity_type, sf_id, sf_api_name, display_name, "
        "attributes, connected_org_id, valid_from_seq, valid_to_seq, last_synced_at) "
        "VALUES ('Object',NULL,:api,:api,'{}'::jsonb,CAST(:org AS uuid),:vf,NULL,NOW()) "
        "RETURNING id"
    ), {"api": api, "vf": vfrom, "org": org}).scalar()


def _objects(model, name, at):
    return model.get_entities("Object", at_seq=at, filters={"sf_api_name": name})


# --- 2-org discrimination: the synthetic mirror of the live PROOF 1 ----------

def test_two_org_discrimination_mirrors_live_proof(seeded):
    """Same object name in TWO orgs at one version: org-blind reads BOTH (2 — the
    #283 ambiguous-reference bug), scoping to one org reads exactly ONE (the fix).
    Mirrors the live env-59 result (Opportunity 2 → 1)."""
    name = f"Discrim{uuid4().hex[:6]}__c"
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        org_a = ensure_connected_org_for_environment(conn, 5901, "https://a.example.local")
        org_b = ensure_connected_org_for_environment(conn, 5902, "https://b.example.local")
        v = _version(conn, "discrim", org_a)
        _object(conn, name, v, org_a)
        _object(conn, name, v, org_b)              # same name, the OTHER org

        blind = _objects(SemanticOrgModel(conn), name, v)
        scoped_a = _objects(SemanticOrgModel(conn, connected_org_id=org_a), name, v)
        scoped_b = _objects(SemanticOrgModel(conn, connected_org_id=org_b), name, v)

        assert len(blind) == 2                     # org-blind → ambiguous (the bug)
        assert len(scoped_a) == 1                  # scoped → exactly one (the fix)
        assert len(scoped_b) == 1
        assert scoped_a[0].id != scoped_b[0].id    # each org its own entity


# --- single-org NO-OP: the no-regression guarantee (reads AND pin) -----------

def test_single_org_read_is_noop(seeded):
    """An object that exists in exactly ONE org: scoped(org) == org-blind — the
    same rows. The connected_org predicate is VACUOUS on a single-org world, so a
    single-org (env-59-only) tenant's generation is byte-identical to pre-D-286."""
    name = f"Solo{uuid4().hex[:6]}__c"
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        org = resolve_connected_org_or_raise(conn, TEST_ENV_ID)
        v = _version(conn, "solo", org)
        _object(conn, name, v, org)                # the ONLY org with this object

        blind = _objects(SemanticOrgModel(conn), name, v)
        scoped = _objects(SemanticOrgModel(conn, connected_org_id=org), name, v)

        assert len(blind) == len(scoped) == 1
        assert [e.id for e in blind] == [e.id for e in scoped]   # identical rows


def test_single_org_pin_is_noop(seeded):
    """The version pin is a no-op on the SINGLE-org case: when an org owns the
    newest sync, its org-scoped ``current_version_seq`` equals the org-blind
    tenant-MAX (on a single-org tenant the org always owns the max). This proves
    the no-regression EQUIVALENCE; the DISCRIMINATION (that the pin is genuinely
    org-scoped, catching a blind-pin half-fix) is
    :func:`test_two_org_pin_discrimination` below."""
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        org = ensure_connected_org_for_environment(conn, 5903, "https://z.example.local")
        vz = _version(conn, "pinz", org)           # newest (version_seq is monotonic)

        blind_max = SemanticOrgModel(conn).current_version_seq()
        scoped_max = SemanticOrgModel(conn, connected_org_id=org).current_version_seq()

        assert blind_max == vz                     # the just-inserted is the global MAX
        assert scoped_max == vz                    # org owns it → scoped == blind


def test_two_org_pin_discrimination(seeded):
    """The version pin is ORG-SCOPED through the generation wiring: when ANOTHER
    org owns the tenant-wide newest version, ``resolve_current_s1_version(env)``
    returns the ENV's org's latest — NOT the global MAX. A blind-pin half-fix
    (scoping the reads but not the pin) would return the other org's seq; this is
    the generation-layer mirror of the live 90-vs-89 result. The regression-catcher
    that would fire if someone un-scoped the pin."""
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        other = ensure_connected_org_for_environment(conn, 5904, "https://other.example.local")
        other_seq = _version(conn, "otherpin", other)     # inserted last → the global MAX
        blind_max = SemanticOrgModel(conn).current_version_seq()
        assert blind_max == other_seq                     # the OTHER org owns the tenant MAX

    # env's org pins ITS OWN latest, which is NOT the other org's brand-new MAX.
    got_seq, _ = resolve_current_s1_version(TEST_TENANT_ID, TEST_ENV_ID)
    assert got_seq != other_seq                           # org-scoped (a blind pin → other_seq)
    assert got_seq < other_seq                            # env's latest is older than the new MAX
