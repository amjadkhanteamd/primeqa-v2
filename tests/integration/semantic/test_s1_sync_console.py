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
    # A terminal status must have completed_at non-NULL; a 'running' run must have it
    # NULL (the completion_implies_terminal CHECK).
    terminal = status in ("success", "partial_success", "failure")
    conn.execute(text(
        "INSERT INTO sync_runs (source_org_id, status, last_completed_phase, "
        "entities_inserted, edges_inserted, completed_at) "
        "VALUES (CAST(:o AS uuid), :s, :p, :en, :ed, "
        + ("NOW()" if terminal else "NULL") + ")"),
        {"o": str(org_id), "s": status, "p": phase, "en": ents, "ed": edges})


def _seed_job(conn, org_id, env_id, *, status="running", hb_minutes_ago=0):
    conn.execute(text(
        "INSERT INTO s1_sync_jobs (connected_org_id, environment_id, status, "
        "claimed_at, heartbeat_at) VALUES (CAST(:o AS uuid), :e, :s, NOW(), "
        "NOW() - make_interval(mins => :m))"),
        {"o": str(org_id), "e": env_id, "s": status, "m": hb_minutes_ago})


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


def test_read_status_flags_orphaned_sync(conn):
    # D-178: a running job whose heartbeat is stale (worker died mid-sync) is
    # orphaned — the panel must say so instead of implying progress.
    org = _seed_org(conn, 91)
    _seed_run(conn, org, status="running", phase="Object", ents=146, edges=0)
    _seed_job(conn, org, 91, status="running", hb_minutes_ago=20)   # > 10-min window
    st = _read_status(conn, 91)
    assert st["orphaned"] is True
    assert st["job"]["status"] == "running" and st["job"]["orphaned"] is True
    assert st["job"]["heartbeat_age_s"] >= 19 * 60


def test_read_status_active_sync_not_orphaned(conn):
    # A fresh heartbeat (the new periodic beat) ⇒ actively running, not orphaned.
    org = _seed_org(conn, 92)
    _seed_run(conn, org, status="running", phase="Object", ents=10, edges=0)
    _seed_job(conn, org, 92, status="running", hb_minutes_ago=0)
    st = _read_status(conn, 92)
    assert st["orphaned"] is False and st["job"]["orphaned"] is False


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


# --- re-enrich requeue (D-180) -----------------------------------------------

def _seed_entity(conn, org_id, version_seq, *, etype="Field",
                 api="Acme__c.X__c", valid_to=None):
    return conn.execute(text(
        "INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, "
        "valid_to_seq, last_synced_at, last_synced_from_org_id) "
        "VALUES (:t, :a, :s, :vt, NOW(), CAST(:o AS uuid)) RETURNING id"),
        {"t": etype, "a": api, "s": version_seq, "vt": valid_to,
         "o": str(org_id)}).scalar()


def _seed_queue(conn, entity_id, *, etype="Field", primitive="embedding",
                status="failed_permanent", attempts=5,
                err="VOYAGE_API_KEY not set in environment"):
    # ai_enrichment_queue_status_implies_timing: a terminal row needs started_at +
    # completed_at; in_progress needs started_at; pending has neither. error_text
    # only on failures.
    if status == "pending":
        timing = "NULL, NULL"
    elif status == "in_progress":
        timing = "NOW(), NULL"
    else:                                  # succeeded / failed_permanent / failed_retryable
        timing = "NOW(), NOW()"
    err_val = err if str(status).startswith("failed") else None
    conn.execute(text(
        "INSERT INTO ai_enrichment_queue (entity_type, entity_id, primitive_type, "
        "status, enqueued_at, attempts, started_at, completed_at, error_text) "
        f"VALUES (:t, CAST(:e AS uuid), :p, :s, NOW(), :a, {timing}, :err)"),
        {"t": etype, "e": str(entity_id), "p": primitive, "s": status,
         "a": attempts, "err": err_val})


def test_read_status_counts_failed_enrichment(conn, seed):
    v = seed.version()
    org = _seed_org(conn, 201)
    e1 = _seed_entity(conn, org, v, api="A.f1")
    e2 = _seed_entity(conn, org, v, api="A.f2")
    _seed_queue(conn, e1, primitive="embedding")           # failed
    _seed_queue(conn, e2, primitive="embedding")           # failed
    _seed_queue(conn, e1, primitive="summary", status="succeeded")  # not counted
    st = _read_status(conn, 201)
    assert st["failed_enrichment"] == 2


def test_read_status_failed_enrichment_zero_when_clean(conn, seed):
    v = seed.version()
    org = _seed_org(conn, 204)
    e1 = _seed_entity(conn, org, v, api="D.f1")
    _seed_queue(conn, e1, status="succeeded")
    st = _read_status(conn, 204)
    assert st["failed_enrichment"] == 0


def test_requeue_failed_enrichment_resets_rows(conn, seed):
    from primeqa.sync.readiness import (
        count_failed_enrichment, requeue_failed_enrichment,
    )
    v = seed.version()
    org = _seed_org(conn, 202)
    e1 = _seed_entity(conn, org, v, api="B.f1")
    _seed_queue(conn, e1, status="failed_permanent", attempts=5)
    assert count_failed_enrichment(conn, org) == 1

    n = requeue_failed_enrichment(conn, org)
    assert n == 1
    row = conn.execute(text(
        "SELECT status, attempts, error_text, started_at, completed_at "
        "FROM ai_enrichment_queue WHERE entity_id = CAST(:e AS uuid)"),
        {"e": str(e1)}).mappings().first()
    assert row["status"] == "pending"
    assert row["attempts"] == 0
    assert row["error_text"] is None
    assert row["started_at"] is None and row["completed_at"] is None
    # readiness flips off the failed count once the rows are pending again
    assert count_failed_enrichment(conn, org) == 0


def test_requeue_scoped_to_active_entities(conn, seed):
    """A superseded entity's failed row (valid_to_seq set) is NOT
    requeued — only the org's *active* entities."""
    from primeqa.sync.readiness import requeue_failed_enrichment
    v1 = seed.version()
    v2 = seed.version()
    org = _seed_org(conn, 203)
    active = _seed_entity(conn, org, v1, api="C.f1", valid_to=None)
    superseded = _seed_entity(conn, org, v1, api="C.f0", valid_to=v2)
    _seed_queue(conn, active, status="failed_permanent")
    _seed_queue(conn, superseded, status="failed_permanent")
    n = requeue_failed_enrichment(conn, org)
    assert n == 1


def test_requeue_recomputes_org_status_off_complete(conn, seed):
    """Org reads 'complete' (all terminal) → after requeue it must not
    still read 'complete' (pending rows now exist)."""
    from primeqa.sync.readiness import apply_org_status, requeue_failed_enrichment
    v = seed.version()
    org = _seed_org(conn, 205)
    # 'complete' requires a finalized run linked as last_sync_run_id with a
    # non-structural phase (else compute_org_status short-circuits to 'none').
    run_id = conn.execute(text(
        "INSERT INTO sync_runs (source_org_id, status, last_completed_phase, "
        "phase, completed_at) VALUES (CAST(:o AS uuid), 'partial_success', "
        "'Flow', 'done', NOW()) RETURNING id"), {"o": str(org)}).scalar()
    conn.execute(text(
        "UPDATE connected_orgs SET last_sync_run_id = CAST(:r AS uuid) "
        "WHERE id = CAST(:o AS uuid)"), {"r": str(run_id), "o": str(org)})
    e1 = _seed_entity(conn, org, v, api="E.f1")
    _seed_queue(conn, e1, status="failed_permanent")   # only queue row → all terminal
    assert apply_org_status(conn, org) == "complete"
    requeue_failed_enrichment(conn, org)
    new_status = conn.execute(text(
        "SELECT ai_enrichment_status FROM connected_orgs WHERE id = CAST(:o AS uuid)"),
        {"o": str(org)}).scalar()
    assert new_status != "complete"


def test_requeue_s1_enrichment_best_effort_on_bad_tenant():
    from primeqa.metadata.s1_sync_console import requeue_s1_enrichment
    res = requeue_s1_enrichment(-1, 1)
    assert res["ok"] is False


# --- S1 freshness for Preflight (D-183, GAP-2) -------------------------------

def _seed_run_at(conn, org_id, *, status, hours_ago, version_seq=None):
    """A terminal sync_run whose completed_at is `hours_ago` in the past, carrying
    `version_seq` as its env-scoped logical_version_seq."""
    conn.execute(text(
        "INSERT INTO sync_runs (source_org_id, status, last_completed_phase, "
        "logical_version_seq, entities_inserted, edges_inserted, completed_at) "
        "VALUES (CAST(:o AS uuid), :s, 'Flow', :v, 1, 0, "
        "NOW() - make_interval(hours => :h))"),
        {"o": str(org_id), "s": status, "v": version_seq, "h": hours_ago})


def _seed_running(conn, org_id):
    conn.execute(text(
        "INSERT INTO sync_runs (source_org_id, status, last_completed_phase, "
        "entities_inserted, edges_inserted) "
        "VALUES (CAST(:o AS uuid), 'running', 'Object', 1, 0)"),
        {"o": str(org_id)})


def test_freshness_not_provisioned(conn):
    from primeqa.metadata.s1_sync_console import _read_freshness
    f = _read_freshness(conn, 9001)              # no connected_orgs row
    assert f["available"] is True and f["provisioned"] is False
    assert f["usable"] is False and f["current_version_seq"] is None


def test_freshness_provisioned_no_successful_run_not_usable(conn):
    from primeqa.metadata.s1_sync_console import _read_freshness
    org = _seed_org(conn, 9002)
    _seed_running(conn, org)                      # provisioned, but no success yet
    f = _read_freshness(conn, 9002)
    assert f["provisioned"] is True
    assert f["current_version_seq"] is None and f["usable"] is False
    assert f["last_success_at"] is None and f["age_hours"] is None


def test_freshness_usable_and_fresh(conn, seed):
    from primeqa.metadata.s1_sync_console import _read_freshness
    v = seed.version()
    org = _seed_org(conn, 9003)
    _seed_run_at(conn, org, status="success", hours_ago=2, version_seq=v)
    f = _read_freshness(conn, 9003)
    assert f["provisioned"] is True and f["usable"] is True
    assert f["current_version_seq"] == v
    assert f["last_success_at"] is not None
    assert f["age_hours"] is not None and 1.5 < f["age_hours"] < 3.0


def test_freshness_partial_success_counts(conn, seed):
    from primeqa.metadata.s1_sync_console import _read_freshness
    v = seed.version()
    org = _seed_org(conn, 9005)
    _seed_run_at(conn, org, status="partial_success", hours_ago=5, version_seq=v)
    f = _read_freshness(conn, 9005)
    assert f["usable"] is True and f["current_version_seq"] == v
    assert 4.0 < f["age_hours"] < 6.0


def test_freshness_picks_latest_success(conn, seed):
    from primeqa.metadata.s1_sync_console import _read_freshness
    v = seed.version()
    org = _seed_org(conn, 9006)
    _seed_run_at(conn, org, status="success", hours_ago=100, version_seq=v)  # older
    _seed_run_at(conn, org, status="success", hours_ago=3, version_seq=v)    # newest
    f = _read_freshness(conn, 9006)
    assert 2.0 < f["age_hours"] < 4.0          # the newest, not the 100h one


def test_freshness_env_scoped_not_contaminated_by_sibling(conn, seed):
    """D-183 review regression: a never-synced env must read usable=False even when
    a SIBLING env in the same tenant schema has a synced version. The version is
    env-scoped via sync_runs.source_org_id, NOT the tenant-global MAX(version_seq)."""
    from primeqa.metadata.s1_sync_console import _read_freshness
    v = seed.version()                           # a tenant-global logical version
    org_a = _seed_org(conn, 9010)                # env A: synced
    _seed_run_at(conn, org_a, status="success", hours_ago=2, version_seq=v)
    org_b = _seed_org(conn, 9011)                # env B: provisioned, never synced
    _seed_running(conn, org_b)
    fa = _read_freshness(conn, 9010)
    fb = _read_freshness(conn, 9011)
    assert fa["usable"] is True and fa["current_version_seq"] == v
    # env B must NOT inherit env A's version
    assert fb["provisioned"] is True
    assert fb["usable"] is False and fb["current_version_seq"] is None


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
