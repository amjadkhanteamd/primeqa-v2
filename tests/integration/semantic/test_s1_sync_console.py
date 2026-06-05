"""S1-sync console bridge — governance on the semantic conn harness (D-164, UI 1a).

Tests the pure `_read_status(conn, env_id)` over seeded `connected_orgs` + `sync_runs`
on the tenant-scoped `conn` fixture, plus the best-effort wrappers (`read_s1_sync_status`
/ `trigger_s1_sync` return available/ok=False rather than raising). Every write rolls back.
"""
from __future__ import annotations

from sqlalchemy import text

from primeqa.metadata.s1_sync_console import (
    _read_status,
    read_s1_sync_status,
    trigger_s1_sync,
)


def _seed_org(conn, env_id, *, instance_url="https://x.my.salesforce.com"):
    return conn.execute(text(
        "INSERT INTO connected_orgs (org_type, sf_instance_url, label, environment_id) "
        "VALUES ('production', :u, :l, :e) RETURNING id"),
        {"u": instance_url, "l": f"env-{env_id}", "e": env_id}).scalar()


def _seed_run(conn, org_id, *, status="success", phase="Flow", ents=42, edges=99):
    # status='success' is terminal -> completed_at must be non-NULL (the
    # completion_implies_terminal CHECK).
    conn.execute(text(
        "INSERT INTO sync_runs (source_org_id, status, last_completed_phase, "
        "entities_inserted, edges_inserted, completed_at) "
        "VALUES (CAST(:o AS uuid), :s, :p, :en, :ed, NOW())"),
        {"o": str(org_id), "s": status, "p": phase, "en": ents, "ed": edges})


def test_read_status_provisioned_with_run(conn):
    org = _seed_org(conn, 77)
    _seed_run(conn, org, status="success", phase="Flow", ents=42, edges=99)
    st = _read_status(conn, 77)
    assert st["available"] is True and st["provisioned"] is True
    assert st["run"]["status"] == "success"
    assert st["run"]["last_completed_phase"] == "Flow"
    assert st["run"]["entities_inserted"] == 42 and st["run"]["edges_inserted"] == 99


def test_read_status_provisioned_no_run_yet(conn):
    _seed_org(conn, 78)
    st = _read_status(conn, 78)
    assert st["provisioned"] is True and st["run"] is None and st["job"] is None


def test_read_status_not_provisioned(conn):
    assert _read_status(conn, 88) == {"available": True, "provisioned": False}


def test_read_s1_sync_status_best_effort_on_bad_tenant():
    # tenant -1 has no schema -> get_tenant_connection fails -> available=False, no raise.
    st = read_s1_sync_status(-1, 1)
    assert st["available"] is False


def test_trigger_best_effort_on_bad_tenant():
    res = trigger_s1_sync(-1, 1, "https://x.my.salesforce.com")
    assert res["ok"] is False


def test_trigger_rejects_missing_instance_url():
    res = trigger_s1_sync(1, 1, "")
    assert res["ok"] is False and "instance URL" in res["error"]


# --- org-model browser (1c) --------------------------------------------------

def test_read_org_model_lists_objects(conn, seed):
    from primeqa.metadata.s1_sync_console import _read_org_model
    from primeqa.semantic.query import SemanticOrgModel
    v1 = seed.version()
    seed.entity("Object", "Account", v1)
    seed.entity("Object", "Contact", v1)
    om = _read_org_model(SemanticOrgModel(conn))
    assert om["synced"] is True
    assert [o["api_name"] for o in om["objects"]] == ["Account", "Contact"]


def test_read_org_model_object_detail(conn, seed):
    from primeqa.metadata.s1_sync_console import _read_org_model
    from primeqa.semantic.query import SemanticOrgModel
    v1 = seed.version()
    obj = seed.entity("Object", "Account", v1)
    fld = seed.entity("Field", "Account.Name", v1)
    seed.edge(fld, obj, "BELONGS_TO", "STRUCTURAL", v1)
    vr = seed.entity("ValidationRule", "Account.RequireName", v1)
    seed.edge(vr, obj, "APPLIES_TO", "BEHAVIOR", v1)
    om = _read_org_model(SemanticOrgModel(conn), "Account")
    assert om["object"]["api_name"] == "Account"
    assert [f["api_name"] for f in om["object"]["fields"]] == ["Account.Name"]
    assert [v["api_name"] for v in om["object"]["validation_rules"]] == ["Account.RequireName"]


def test_read_org_model_raises_when_unsynced(conn):
    import pytest
    from primeqa.metadata.s1_sync_console import _read_org_model
    from primeqa.semantic.query import SemanticOrgModel, VersionNotFoundError
    with pytest.raises(VersionNotFoundError):       # no version seeded -> wrapper catches it
        _read_org_model(SemanticOrgModel(conn))


def test_read_org_model_best_effort_bad_tenant():
    from primeqa.metadata.s1_sync_console import read_org_model
    assert read_org_model(-1)["available"] is False
