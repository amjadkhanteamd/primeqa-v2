"""R2 tests \u2014 Super Admin cap exclusion, agent settings, cost forecast."""

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


def login(email, password):
    r = client.post("/api/auth/login", json={
        "email": email, "password": password, "tenant_id": TENANT_ID,
    })
    return r.get_json().get("access_token")


def run_tests():
    print("\n=== R2 Super Admin / Cost / Agent Settings ===\n")
    results = []
    admin_token = login("admin@primeqa.io", "changeme123")

    def t_superadmin_cap_exclusion():
        from primeqa.db import SessionLocal
        from primeqa.core.repository import UserRepository
        from primeqa.core.models import User
        db = SessionLocal()
        try:
            count = UserRepository(db).count_active_users(TENANT_ID)
            total_active = db.query(User).filter(
                User.tenant_id == TENANT_ID, User.is_active == True,
            ).count()
            superadmins = db.query(User).filter(
                User.tenant_id == TENANT_ID, User.is_active == True,
                User.role == "superadmin",
            ).count()
            assert count == total_active - superadmins, \
                f"cap count {count} should exclude {superadmins} superadmin(s) from total {total_active}"
        finally:
            db.close()
    results.append(test("R2-1. count_active_users excludes superadmin", t_superadmin_cap_exclusion))

    def t_agent_settings_get():
        client.set_cookie("access_token", admin_token)
        r = client.get("/settings/agent")
        assert r.status_code == 200
        body = r.data.decode()
        assert "Agent autonomy" in body
        assert "trust_threshold_high" in body
    results.append(test("R2-2. /settings/agent renders for superadmin", t_agent_settings_get))

    def t_agent_settings_update():
        client.set_cookie("access_token", admin_token)
        r = client.post("/settings/agent", data={
            "agent_enabled": "1",
            "trust_threshold_high": "0.90",
            "trust_threshold_medium": "0.55",
            "max_fix_attempts_per_run": "2",
        })
        assert r.status_code in (200, 302)
        # Verify via repo
        from primeqa.db import SessionLocal
        from primeqa.core.agent_settings import AgentSettingsRepository
        db = SessionLocal()
        try:
            s = AgentSettingsRepository(db).get(TENANT_ID)
            assert abs(s.trust_threshold_high - 0.90) < 0.001
            assert abs(s.trust_threshold_medium - 0.55) < 0.001
            assert s.max_fix_attempts_per_run == 2
        finally:
            db.close()
    results.append(test("R2-3. Superadmin updates agent trust bands + attempts", t_agent_settings_update))

    def t_agent_settings_bad_bands_rejected():
        from primeqa.db import SessionLocal
        from primeqa.core.agent_settings import AgentSettingsRepository
        db = SessionLocal()
        try:
            repo = AgentSettingsRepository(db)
            try:
                repo.update(TENANT_ID, updated_by=1,
                            trust_threshold_high=0.5, trust_threshold_medium=0.7)
                raise AssertionError("expected ValueError for inverted thresholds")
            except ValueError:
                db.rollback()
        finally:
            db.close()
    results.append(test("R2-4. Inverted thresholds rejected", t_agent_settings_bad_bands_rejected))

    def t_cost_forecast_executor_only():
        from primeqa.runs.cost import estimate_run_cost
        r = estimate_run_cost(50, model="claude-sonnet-4-20250514", run_type="execute_only")
        assert r["usd_estimate"] == 0.0
        assert r["tokens_in"] == 0
        assert r["sf_api_calls_estimate"] == 400
    results.append(test("R2-5. Cost forecast returns 0 for execute_only runs", t_cost_forecast_executor_only))

    def t_cost_forecast_full_run():
        from primeqa.runs.cost import estimate_run_cost
        r = estimate_run_cost(10, model="claude-sonnet-4-20250514", run_type="full")
        # 10 tests \xd7 2000 tokens in at $3/M = $0.06; 10 \xd7 1000 out at $15/M = $0.15 \u2192 $0.21
        assert 0.19 <= r["usd_estimate"] <= 0.23, f"unexpected {r['usd_estimate']}"
        assert r["model"] == "claude-sonnet-4-20250514"
    results.append(test("R2-6. Cost forecast matches Sonnet pricing math", t_cost_forecast_full_run))

    def t_release_decision_flag_exists():
        from primeqa.db import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        try:
            rows = list(db.execute(text(
                "SELECT column_name, column_default FROM information_schema.columns "
                "WHERE table_name='release_decisions' AND column_name='agent_verdict_counts'"
            )))
            assert rows, "agent_verdict_counts column missing"
        finally:
            db.close()
    results.append(test("R2-7. release_decisions.agent_verdict_counts exists", t_release_decision_flag_exists))

    passed = sum(results); total = len(results)
    print(f"\n{'='*40}\nResults: {passed}/{total} passed")
    print("ALL R2 TESTS PASSED" if passed == total else f"{total - passed} FAILED")
    return passed == total


if __name__ == "__main__":
    sys.exit(0 if run_tests() else 1)
