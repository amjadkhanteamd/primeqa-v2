"""Org-scoped org-model browser read (multi-org closure, Part 1).

``_read_org_model`` on an org-bound ``SemanticOrgModel`` must return ONE org's
objects; the unscoped model is the UNION of every org's entities — the
ambiguity the /org-model picker retires on multi-org tenants. Seeds a minimal
two-org S1 spine (connected_orgs → logical_versions → entities) on the
rollback ``session`` fixture.
"""
from __future__ import annotations

from sqlalchemy import text

from primeqa.metadata_bridge.s1_sync_console import _read_org_model
from primeqa.semantic.query import SemanticOrgModel


def _seed_org(session, *, environment_id, label):
    return session.execute(text(
        "INSERT INTO connected_orgs (org_type, sf_instance_url, label, "
        "environment_id) VALUES ('sandbox', 'https://x.example', :l, :e) "
        "RETURNING CAST(id AS text)"),
        {"l": label, "e": environment_id}).scalar()


def _seed_version(session, *, org_id, name):
    return session.execute(text(
        "INSERT INTO logical_versions (version_name, version_type, "
        "connected_org_id) VALUES (:n, 'genesis', CAST(:o AS uuid)) "
        "RETURNING version_seq"), {"n": name, "o": org_id}).scalar()


def _seed_object(session, *, org_id, seq, api_name):
    session.execute(text(
        "INSERT INTO entities (entity_type, sf_api_name, display_name, "
        "valid_from_seq, connected_org_id, last_synced_at) "
        "VALUES ('Object', :a, :a, :s, CAST(:o AS uuid), NOW())"),
        {"a": api_name, "s": seq, "o": org_id})


def test_org_scoped_browser_read_separates_orgs(session):
    session.execute(text("SELECT set_config('app.tenant_id', '1', false)"))
    org_a = _seed_org(session, environment_id=7, label="env-7")
    org_b = _seed_org(session, environment_id=9, label="env-9")
    seq_a = _seed_version(session, org_id=org_a, name="orgA-genesis")
    seq_b = _seed_version(session, org_id=org_b, name="orgB-genesis")
    _seed_object(session, org_id=org_a, seq=seq_a, api_name="Account")
    _seed_object(session, org_id=org_b, seq=seq_b, api_name="Account")
    _seed_object(session, org_id=org_b, seq=seq_b, api_name="Case")
    session.flush()
    conn = session.connection()

    out_a = _read_org_model(SemanticOrgModel(conn, connected_org_id=org_a))
    assert [o["api_name"] for o in out_a["objects"]] == ["Account"]
    assert out_a["version_seq"] == seq_a

    out_b = _read_org_model(SemanticOrgModel(conn, connected_org_id=org_b))
    assert [o["api_name"] for o in out_b["objects"]] == ["Account", "Case"]
    assert out_b["version_seq"] == seq_b

    # the unscoped read is the UNION — the documented multi-org ambiguity
    blended = _read_org_model(SemanticOrgModel(conn))
    assert len(blended["objects"]) == 3
