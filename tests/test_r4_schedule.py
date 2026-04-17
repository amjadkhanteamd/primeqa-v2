"""R4 tests \u2014 cron helpers, scheduled_runs CRUD, due-schedule firing, DMS."""

import sys
import os
from datetime import datetime, timedelta, timezone

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
    print("\n=== R4 Scheduled Runs Tests ===\n")
    results = []

    def t_presets_and_translator():
        from primeqa.runs.schedule import preset_to_cron, cron_to_preset, PRESETS
        for key, expr in PRESETS.items():
            assert preset_to_cron(key) == expr
            assert cron_to_preset(expr) == key
        assert cron_to_preset("5 4 * * 2") is None  # custom cron \u2192 no preset
    results.append(test("R4-1. preset <-> cron bidirectional", t_presets_and_translator))

    def t_validate_cron_accepts_valid():
        from primeqa.runs.schedule import validate_cron
        for e in ["0 2 * * *", "*/5 * * * *", "0 */4 * * 1-5"]:
            validate_cron(e)
    results.append(test("R4-2. validate_cron accepts valid expressions", t_validate_cron_accepts_valid))

    def t_validate_cron_rejects_bad():
        from primeqa.runs.schedule import validate_cron
        try:
            validate_cron("this is not cron")
            raise AssertionError("should have rejected")
        except ValueError:
            pass
    results.append(test("R4-3. validate_cron rejects garbage", t_validate_cron_rejects_bad))

    def t_crud_round_trip():
        from primeqa.db import SessionLocal
        from primeqa.runs.schedule import ScheduledRunRepository
        from primeqa.test_management.models import TestSuite
        from primeqa.core.models import Environment
        db = SessionLocal()
        try:
            suite = db.query(TestSuite).filter(
                TestSuite.tenant_id == TENANT_ID,
                TestSuite.deleted_at.is_(None),
            ).first()
            env = db.query(Environment).filter(Environment.tenant_id == TENANT_ID).first()
            assert suite and env

            repo = ScheduledRunRepository(db)
            created = repo.create(
                tenant_id=TENANT_ID, suite_id=suite.id, environment_id=env.id,
                cron_expr="0 2 * * *", preset_label=None, priority="normal",
                max_silence_hours=48, created_by=1,
            )
            assert created.id
            assert created.next_fire_at is not None
            assert created.preset_label == "daily_2am"

            # update
            updated = repo.update(
                created.id, TENANT_ID, updated_by=1, cron_expr="0 3 * * *",
            )
            assert updated.preset_label is None  # custom cron now
            assert updated.cron_expr == "0 3 * * *"

            # disable
            repo.update(created.id, TENANT_ID, updated_by=1, enabled=False)
            refetch = repo.get(created.id, TENANT_ID)
            assert refetch.enabled is False

            # delete
            assert repo.delete(created.id, TENANT_ID) is True
            assert repo.get(created.id, TENANT_ID) is None
        finally:
            db.close()
    results.append(test("R4-4. Schedule CRUD round-trip", t_crud_round_trip))

    def t_get_due_respects_enabled():
        from primeqa.db import SessionLocal
        from primeqa.runs.schedule import ScheduledRun, ScheduledRunRepository
        from primeqa.test_management.models import TestSuite
        from primeqa.core.models import Environment
        db = SessionLocal()
        try:
            suite = db.query(TestSuite).filter(
                TestSuite.tenant_id == TENANT_ID,
                TestSuite.deleted_at.is_(None),
            ).first()
            env = db.query(Environment).filter(Environment.tenant_id == TENANT_ID).first()
            past = datetime.now(timezone.utc) - timedelta(minutes=1)

            # One enabled due, one disabled due
            s_enabled = ScheduledRun(tenant_id=TENANT_ID, suite_id=suite.id,
                                     environment_id=env.id, cron_expr="0 2 * * *",
                                     next_fire_at=past, enabled=True, created_by=1)
            s_disabled = ScheduledRun(tenant_id=TENANT_ID, suite_id=suite.id,
                                      environment_id=env.id, cron_expr="0 2 * * *",
                                      next_fire_at=past, enabled=False, created_by=1)
            db.add_all([s_enabled, s_disabled]); db.commit(); db.refresh(s_enabled); db.refresh(s_disabled)

            due = ScheduledRunRepository(db).get_due()
            ids = {d.id for d in due}
            assert s_enabled.id in ids
            assert s_disabled.id not in ids

            db.query(ScheduledRun).filter(ScheduledRun.id.in_([s_enabled.id, s_disabled.id])).delete()
            db.commit()
        finally:
            db.close()
    results.append(test("R4-5. get_due only returns enabled due schedules", t_get_due_respects_enabled))

    def t_dms_finds_silent():
        from primeqa.db import SessionLocal
        from primeqa.runs.schedule import ScheduledRun, ScheduledRunRepository
        from primeqa.test_management.models import TestSuite
        from primeqa.core.models import Environment
        db = SessionLocal()
        try:
            suite = db.query(TestSuite).filter(
                TestSuite.tenant_id == TENANT_ID,
                TestSuite.deleted_at.is_(None),
            ).first()
            env = db.query(Environment).filter(Environment.tenant_id == TENANT_ID).first()
            # last_fired 48h ago, max_silence 24h \u2192 silent
            s = ScheduledRun(tenant_id=TENANT_ID, suite_id=suite.id,
                             environment_id=env.id, cron_expr="0 2 * * *",
                             enabled=True, max_silence_hours=24, created_by=1,
                             last_fired_at=datetime.now(timezone.utc) - timedelta(hours=48))
            db.add(s); db.commit(); db.refresh(s)
            silent = ScheduledRunRepository(db).find_silent(TENANT_ID)
            assert any(x.id == s.id for x in silent), "expected to find this silent schedule"
            db.query(ScheduledRun).filter(ScheduledRun.id == s.id).delete()
            db.commit()
        finally:
            db.close()
    results.append(test("R4-6. Dead-man's switch flags silent schedules", t_dms_finds_silent))

    def t_ui_list_renders():
        r = client.post("/api/auth/login", json={
            "email": "admin@primeqa.io", "password": "changeme123", "tenant_id": TENANT_ID,
        })
        tok = r.get_json()["access_token"]
        client.set_cookie("access_token", tok)
        r = client.get("/runs/scheduled")
        assert r.status_code == 200
        assert b"Scheduled runs" in r.data
    results.append(test("R4-7. /runs/scheduled renders", t_ui_list_renders))

    passed = sum(results); total = len(results)
    print(f"\n{'='*40}\nResults: {passed}/{total} passed")
    print("ALL R4 TESTS PASSED" if passed == total else f"{total - passed} FAILED")
    return passed == total


if __name__ == "__main__":
    sys.exit(0 if run_tests() else 1)
