"""R3 tests \u2014 Metadata sync DAG, per-category status, preflight integration."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from primeqa.app import app

client = app.test_client()
TENANT_ID = 1


def test(name, fn):
    try:
        fn(); print(f"  PASS  {name}"); return True
    except AssertionError as e:
        print(f"  FAIL  {name}: {e}"); return False
    except Exception as e:
        import traceback; print(f"  ERROR {name}: {type(e).__name__}: {e}"); traceback.print_exc(); return False


def run_tests():
    print("\n=== R3 Metadata Sync Tests ===\n")
    results = []

    def t_schema():
        from primeqa.db import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        try:
            rows = list(db.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='meta_sync_status' AND column_name IN "
                "('meta_version_id','category','status','items_count','retry_count','error_message')"
            )))
            assert len(rows) == 6, f"expected 6, got {[r[0] for r in rows]}"
        finally:
            db.close()
    results.append(test("R3-1. meta_sync_status table schema", t_schema))

    def t_dag_ordering():
        from primeqa.metadata.sync_engine import SyncEngine, DEPENDS_ON
        # objects has no parents
        assert DEPENDS_ON["objects"] == set()
        # fields / record_types depend only on objects
        assert DEPENDS_ON["fields"] == {"objects"}
        assert DEPENDS_ON["record_types"] == {"objects"}
        # VRs / flows / triggers depend on fields + objects
        assert DEPENDS_ON["validation_rules"] == {"objects", "fields"}
    results.append(test("R3-2. Dependency DAG matches plan", t_dag_ordering))

    def t_sync_engine_healthy_path():
        """Simulate a full successful sync: all categories marked complete."""
        import uuid
        from primeqa.db import SessionLocal
        from primeqa.metadata.models import MetaVersion, MetaSyncStatus
        from primeqa.metadata.repository import MetadataRepository
        from primeqa.metadata.sync_engine import SyncEngine
        from primeqa.core.models import Environment
        db = SessionLocal()
        try:
            env = db.query(Environment).filter(Environment.tenant_id == TENANT_ID).first()
            assert env, "need at least one env"
            mv = MetaVersion(environment_id=env.id, version_label=f"r3{uuid.uuid4().hex[:6]}",
                             status="in_progress")
            db.add(mv); db.commit(); db.refresh(mv)

            # Fake fetchers that just return fake counts, no SF
            fetchers = {
                cat: (lambda _mv, _repo, c=cat: {"objects": 10, "fields": 42, "record_types": 3,
                                                  "validation_rules": 5, "flows": 2, "triggers": 1}[c])
                for cat in ["objects", "fields", "record_types", "validation_rules", "flows", "triggers"]
            }
            eng = SyncEngine(db, MetadataRepository(db), fetchers)
            outcomes = eng.run(mv.id, ["objects", "fields", "record_types",
                                        "validation_rules", "flows", "triggers"])
            assert all(v == "complete" for v in outcomes.values()), f"unexpected: {outcomes}"

            statuses = {r.category: r for r in db.query(MetaSyncStatus).filter_by(meta_version_id=mv.id).all()}
            assert statuses["fields"].items_count == 42
            assert statuses["objects"].items_count == 10

            # Cleanup
            db.query(MetaSyncStatus).filter_by(meta_version_id=mv.id).delete()
            db.query(MetaVersion).filter_by(id=mv.id).delete()
            db.commit()
        finally:
            db.close()
    results.append(test("R3-3. SyncEngine happy path marks every category complete", t_sync_engine_healthy_path))

    def t_sync_engine_parent_fail_cascades():
        """If objects fails, fields/record_types/VRs/flows/triggers skip_parent_failed."""
        import uuid
        from primeqa.db import SessionLocal
        from primeqa.metadata.models import MetaVersion, MetaSyncStatus
        from primeqa.metadata.repository import MetadataRepository
        from primeqa.metadata.sync_engine import SyncEngine
        from primeqa.core.models import Environment
        db = SessionLocal()
        try:
            env = db.query(Environment).filter(Environment.tenant_id == TENANT_ID).first()
            mv = MetaVersion(environment_id=env.id, version_label=f"r3f{uuid.uuid4().hex[:6]}",
                             status="in_progress")
            db.add(mv); db.commit(); db.refresh(mv)

            def boom(_mv, _repo): raise RuntimeError("object fetch broke")
            def normal(_mv, _repo): return 5

            fetchers = {
                "objects": boom, "fields": normal, "record_types": normal,
                "validation_rules": normal, "flows": normal, "triggers": normal,
            }
            eng = SyncEngine(db, MetadataRepository(db), fetchers)
            outcomes = eng.run(mv.id, ["objects", "fields", "validation_rules", "triggers"])
            assert outcomes["objects"] == "failed"
            assert outcomes["fields"] == "skipped_parent_failed"
            # VRs depend on fields (skipped) AND objects (failed)
            assert outcomes["validation_rules"] == "skipped_parent_failed"
            assert outcomes["triggers"] == "skipped_parent_failed"

            # Cleanup
            db.query(MetaSyncStatus).filter_by(meta_version_id=mv.id).delete()
            db.query(MetaVersion).filter_by(id=mv.id).delete()
            db.commit()
        finally:
            db.close()
    results.append(test("R3-4. Parent failure cascades skipped_parent_failed to dependents",
                        t_sync_engine_parent_fail_cascades))

    def t_preflight_reads_meta_sync_status():
        """Preflight recognizes per-category health via meta_sync_status rows."""
        import uuid
        from primeqa.db import SessionLocal
        from primeqa.metadata.models import MetaVersion, MetaSyncStatus
        from primeqa.runs.preflight import Preflight
        from primeqa.core.models import Environment
        db = SessionLocal()
        try:
            env = db.query(Environment).filter(Environment.tenant_id == TENANT_ID).first()
            mv = MetaVersion(environment_id=env.id, version_label=f"r3p{uuid.uuid4().hex[:6]}",
                             status="complete")
            db.add(mv); db.commit(); db.refresh(mv)

            # Only objects + fields healthy; validation_rules failed
            for cat, st in [("objects","complete"),("fields","complete"),
                            ("record_types","complete"),("validation_rules","failed"),
                            ("flows","skipped"),("triggers","skipped")]:
                db.add(MetaSyncStatus(meta_version_id=mv.id, category=cat, status=st,
                                      items_count=0))
            db.commit()

            # Fake minimal repo objects \u2014 only env_repo matters here
            from primeqa.core.repository import EnvironmentRepository, ConnectionRepository
            from primeqa.test_management.repository import TestCaseRepository
            from primeqa.metadata.repository import MetadataRepository
            pf = Preflight(db,
                env_repo=EnvironmentRepository(db),
                conn_repo=ConnectionRepository(db),
                tc_repo=TestCaseRepository(db),
                meta_repo=MetadataRepository(db))
            healthy = pf._healthy_meta_categories(mv)
            assert healthy == {"objects", "fields", "record_types"}, f"got {healthy}"

            # Cleanup
            db.query(MetaSyncStatus).filter_by(meta_version_id=mv.id).delete()
            db.query(MetaVersion).filter_by(id=mv.id).delete()
            db.commit()
        finally:
            db.close()
    results.append(test("R3-5. Preflight reads per-category health from meta_sync_status",
                        t_preflight_reads_meta_sync_status))

    def t_api_sync_status_endpoint():
        # Login
        r = client.post("/api/auth/login", json={
            "email": "admin@primeqa.io", "password": "changeme123", "tenant_id": TENANT_ID,
        })
        tok = r.get_json()["access_token"]
        client.set_cookie("access_token", tok)

        from primeqa.db import SessionLocal
        from primeqa.core.models import Environment
        db = SessionLocal()
        env = db.query(Environment).filter(
            Environment.tenant_id == TENANT_ID,
            Environment.current_meta_version_id.isnot(None),
        ).first()
        db.close()
        if not env:
            raise AssertionError("no env with meta_version; skipping")
        r = client.get(f"/api/metadata/{env.id}/sync-status")
        assert r.status_code == 200
        body = r.get_json()
        assert "meta_version_id" in body and "statuses" in body
    results.append(test("R3-6. GET /api/metadata/:env/sync-status", t_api_sync_status_endpoint))

    passed = sum(results); total = len(results)
    print(f"\n{'='*40}\nResults: {passed}/{total} passed")
    print("ALL R3 TESTS PASSED" if passed == total else f"{total - passed} FAILED")
    return passed == total


if __name__ == "__main__":
    sys.exit(0 if run_tests() else 1)
