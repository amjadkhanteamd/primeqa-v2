"""FAITHFUL real-DB proof for the 1b.2 ValidationRule deletion reconcile.

Mocks nothing — seeds real ``entities`` + ``edges`` rows in the isolated test
schema, runs the REAL ``reconcile_deletions_by_sf_id`` (the actual SCD-2 close +
edge close SQL), and asserts the ACTUAL post-reconcile model state. The per-test
transaction rolls back at teardown (conftest ``conn`` fixture).

Two cases form the safety proof:
  * WITH the reconcile → the VR absent from Salesforce's current id set is
    superseded (valid_to_seq closed) AND its edges are closed; the surviving VR +
    its edges are untouched.
  * WITHOUT the reconcile (red-on-disable) → the same deleted VR stays
    currently-valid. This is the test that proves a delta is UNSAFE without its
    reconcile: deletion is invisible to a LastModifiedDate delta, so only the
    reconcile catches it.
"""
from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import text

from primeqa.sync.materialize import reconcile_deletions_by_sf_id
from primeqa.sync.result import PhaseResult


def _make_org(conn) -> str:
    return str(conn.execute(text(
        "INSERT INTO connected_orgs (org_type, sf_instance_url, label) "
        "VALUES ('sandbox', 'https://t.my.salesforce.com', 'delta-test-org') "
        "RETURNING id"
    )).scalar())


def _scope_to_org(conn, org_id: str, entity_ids: list) -> None:
    conn.execute(text(
        "UPDATE entities SET last_synced_from_org_id = CAST(:o AS uuid) "
        "WHERE id = ANY(CAST(:ids AS uuid[]))"
    ), {"o": org_id, "ids": [str(e) for e in entity_ids]})


def _valid_to(conn, entity_id) -> int | None:
    return conn.execute(text(
        "SELECT valid_to_seq FROM entities WHERE id = CAST(:id AS uuid)"
    ), {"id": str(entity_id)}).scalar()


def _edge_valid_to(conn, edge_id) -> int | None:
    return conn.execute(text(
        "SELECT valid_to_seq FROM edges WHERE id = CAST(:id AS uuid)"
    ), {"id": str(edge_id)}).scalar()


def test_reconcile_supersedes_deleted_vr_and_its_edges(conn, seed) -> None:
    org = _make_org(conn)
    v0 = seed.version()
    obj = seed.entity("Object", "Account", valid_from_seq=v0, sf_id="OBJACC")
    keep = seed.entity("ValidationRule", "Account.Keep", valid_from_seq=v0, sf_id="VRKEEP")
    deleted = seed.entity("ValidationRule", "Account.Del", valid_from_seq=v0, sf_id="VRDEL")
    _scope_to_org(conn, org, [obj, keep, deleted])
    del_edge = seed.edge(deleted, obj, "BELONGS_TO", "STRUCTURAL", valid_from_seq=v0)
    keep_edge = seed.edge(keep, obj, "BELONGS_TO", "STRUCTURAL", valid_from_seq=v0)

    v1 = seed.version()  # the reconcile run's logical version
    ctx = SimpleNamespace(connected_org_id=org, logical_version_seq=v1)
    result = PhaseResult(entity_type="ValidationRule")

    # Salesforce's full current id set has ONLY the kept VR — the deleted VR is
    # absent (it was removed in SF). The Object id is irrelevant (reconcile is
    # entity_type-scoped to ValidationRule).
    n = reconcile_deletions_by_sf_id(conn, ctx, "ValidationRule", {"VRKEEP"}, result)

    assert n == 1
    assert result.entities_superseded == 1
    # the deleted VR is superseded (closed at the reconcile version) ...
    assert _valid_to(conn, deleted) == v1
    # ... and its edge is closed (no dangling active edge off a superseded entity)
    assert _edge_valid_to(conn, del_edge) == v1
    # the surviving VR + its edge are untouched
    assert _valid_to(conn, keep) is None
    assert _edge_valid_to(conn, keep_edge) is None


def test_red_on_disable_without_reconcile_the_deletion_is_missed(conn, seed) -> None:
    # RED-ON-DISABLE: identical setup, but the reconcile is DISABLED (not called).
    # The VR deleted in Salesforce stays currently-valid — proving the reconcile
    # is exactly the mechanism that makes the delta safe. (On the pre-1b.2 code,
    # and on the full-fetch path, this is the standing behavior: deletions are
    # never detected.)
    org = _make_org(conn)
    v0 = seed.version()
    obj = seed.entity("Object", "Account", valid_from_seq=v0, sf_id="OBJACC")
    deleted = seed.entity("ValidationRule", "Account.Del", valid_from_seq=v0, sf_id="VRDEL")
    _scope_to_org(conn, org, [obj, deleted])
    del_edge = seed.edge(deleted, obj, "BELONGS_TO", "STRUCTURAL", valid_from_seq=v0)

    # --- reconcile intentionally NOT invoked ---

    assert _valid_to(conn, deleted) is None      # deletion missed: still valid
    assert _edge_valid_to(conn, del_edge) is None

    # And to confirm the assertion above is the ONLY thing standing between
    # "missed" and "caught": running the reconcile now flips it.
    v1 = seed.version()
    ctx = SimpleNamespace(connected_org_id=org, logical_version_seq=v1)
    reconcile_deletions_by_sf_id(
        conn, ctx, "ValidationRule", set(), PhaseResult(entity_type="ValidationRule"))
    assert _valid_to(conn, deleted) == v1        # now caught


def test_reconcile_noop_when_all_present(conn, seed) -> None:
    # Nothing absent from the SF id set → nothing superseded (the steady state:
    # a delta run that deleted nothing must not close any live entity).
    org = _make_org(conn)
    v0 = seed.version()
    keep = seed.entity("ValidationRule", "Account.Keep", valid_from_seq=v0, sf_id="VRKEEP")
    _scope_to_org(conn, org, [keep])
    v1 = seed.version()
    ctx = SimpleNamespace(connected_org_id=org, logical_version_seq=v1)
    result = PhaseResult(entity_type="ValidationRule")
    n = reconcile_deletions_by_sf_id(conn, ctx, "ValidationRule", {"VRKEEP"}, result)
    assert n == 0
    assert _valid_to(conn, keep) is None


def test_reconcile_is_org_scoped(conn, seed) -> None:
    # A VR another org sourced (different last_synced_from_org_id) is NEVER
    # superseded by this org's fetch, even when absent from this org's id set —
    # the multi-org safety guard.
    org_a = _make_org(conn)
    org_b = _make_org(conn)
    v0 = seed.version()
    other = seed.entity("ValidationRule", "Account.Other", valid_from_seq=v0, sf_id="VROTHER")
    _scope_to_org(conn, org_b, [other])          # belongs to org B
    v1 = seed.version()
    ctx = SimpleNamespace(connected_org_id=org_a, logical_version_seq=v1)
    # org A reconciles with an EMPTY present set — but `other` is org B's, so it
    # must survive.
    n = reconcile_deletions_by_sf_id(
        conn, ctx, "ValidationRule", set(), PhaseResult(entity_type="ValidationRule"))
    assert n == 0
    assert _valid_to(conn, other) is None
