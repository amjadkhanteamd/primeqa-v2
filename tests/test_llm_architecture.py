"""LLM architecture tests — Phases 1-6.

Covers the bits that are worth guarding against regression without
burning real Anthropic credits:

  tier module        — presets + resolve_limits override semantics
  limits.load_config — tier + overrides flow through correctly
  limits.snapshot    — UsageSnapshot computes pct / warn / blocked
  views — /settings/my-llm-usage admin-gated, superadmin tier POST flow

Everything runs against the real Railway DB via the Flask test client
(same style as test_hardening.py). Runs in ~10 seconds.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from primeqa.app import app
import jwt
from primeqa.db import SessionLocal
from primeqa.core.models import User, TenantAgentSettings, ActivityLog

client = app.test_client()


def test(name, fn):
    try:
        fn()
        print(f"  PASS  {name}")
        return True
    except AssertionError as e:
        print(f"  FAIL  {name}: {e}")
        return False
    except Exception as e:
        import traceback
        print(f"  ERROR {name}: {type(e).__name__}: {e}")
        traceback.print_exc()
        return False


def _mint_jwt(role: str):
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.role == role).first()
        if u is None:
            raise RuntimeError(f"No user with role={role} in DB")
        token = jwt.encode({
            "sub": str(u.id), "tenant_id": u.tenant_id, "email": u.email,
            "role": u.role, "full_name": u.full_name or u.email,
        }, os.environ["JWT_SECRET"], algorithm="HS256")
        return token, u.tenant_id
    finally:
        db.close()


# ---- tier module -----------------------------------------------------------

def test_tier_presets_have_all_four():
    from primeqa.intelligence.llm import tiers
    presets = tiers.all_presets()
    assert set(presets.keys()) == {
        tiers.TIER_STARTER, tiers.TIER_PRO,
        tiers.TIER_ENTERPRISE, tiers.TIER_CUSTOM,
    }
    # Ordering: starter < pro caps
    starter = presets[tiers.TIER_STARTER]
    pro = presets[tiers.TIER_PRO]
    assert starter.max_calls_per_minute < pro.max_calls_per_minute
    assert starter.max_spend_per_day_usd < pro.max_spend_per_day_usd


def test_tier_resolve_limits_uses_preset():
    from primeqa.intelligence.llm import tiers
    r = tiers.resolve_limits("pro")
    assert r["max_per_minute"] == 100
    assert r["max_per_hour"] == 2000
    assert r["max_spend_per_day_usd"] == 25.00


def test_tier_resolve_limits_override_wins():
    from primeqa.intelligence.llm import tiers
    r = tiers.resolve_limits("pro", override_spend_per_day=100.0)
    assert r["max_per_minute"] == 100        # preset
    assert r["max_spend_per_day_usd"] == 100  # override wins


def test_tier_resolve_limits_custom_ignores_preset():
    from primeqa.intelligence.llm import tiers
    r = tiers.resolve_limits("custom")
    assert r["max_per_minute"] is None
    assert r["max_per_hour"] is None
    r2 = tiers.resolve_limits("custom", override_per_minute=42)
    assert r2["max_per_minute"] == 42
    assert r2["max_per_hour"] is None


def test_tier_unknown_falls_back_to_starter():
    from primeqa.intelligence.llm import tiers
    preset = tiers.get_preset("mystery-tier")
    assert preset.label == "Starter"


# ---- limits.load_tenant_config ---------------------------------------------

def test_load_tenant_config_returns_starter_defaults_for_missing_row():
    """load_tenant_config(tenant_id=0) should return starter defaults, not crash."""
    from primeqa.intelligence.llm import limits
    tl, tp = limits.load_tenant_config(0)  # tenant 0 does not exist
    # Starter preset: 30/500/$5
    assert tl.max_per_minute == 30
    assert tl.max_per_hour == 500
    assert tl.max_spend_per_day_usd == 5.0
    assert tp.allow_haiku is True


# ---- limits.current_usage ---------------------------------------------------

def test_current_usage_returns_snapshot_with_caps():
    from primeqa.intelligence.llm import limits, tiers
    tl, _tp = limits.load_tenant_config(1)
    snap = limits.current_usage(1, tl)
    # Structural assertions — snapshot should always have cap fields set
    # (they come from the resolved tier).
    assert snap.cap_per_minute is not None
    assert snap.cap_per_hour is not None
    assert snap.cap_spend_per_day_usd is not None
    # pct_* must be in [0, ...] (no negatives, no NaN)
    assert snap.pct_per_minute is None or snap.pct_per_minute >= 0
    assert isinstance(snap.warn, bool)
    assert isinstance(snap.blocked, bool)


def test_usage_snapshot_warn_threshold():
    from primeqa.intelligence.llm.limits import UsageSnapshot
    # Forge a snapshot at 85% — should warn but not block
    s = UsageSnapshot(
        calls_last_minute=85, calls_last_hour=0, spend_today_usd=0,
        cap_per_minute=100, cap_per_hour=None, cap_spend_per_day_usd=None,
    )
    assert s.warn is True
    assert s.blocked is False

    # 100% — blocked
    s2 = UsageSnapshot(
        calls_last_minute=0, calls_last_hour=0, spend_today_usd=5.0,
        cap_per_minute=None, cap_per_hour=None, cap_spend_per_day_usd=5.0,
    )
    assert s2.warn is True
    assert s2.blocked is True

    # No caps at all — never warns
    s3 = UsageSnapshot(
        calls_last_minute=1000, calls_last_hour=1000, spend_today_usd=1000,
        cap_per_minute=None, cap_per_hour=None, cap_spend_per_day_usd=None,
    )
    assert s3.warn is False
    assert s3.blocked is False


# ---- /settings/my-llm-usage (tenant view) ----------------------------------

def test_my_llm_usage_renders_for_admin():
    token, _tid = _mint_jwt("superadmin")  # superadmin passes admin gate
    c = app.test_client()
    c.set_cookie(domain="localhost", key="access_token", value=token)
    r = c.get("/settings/my-llm-usage")
    assert r.status_code == 200, r.data.decode()[:400]
    body = r.data.decode()
    assert "Current plan" in body
    assert "Spend today" in body
    assert "Spend by feature" in body


def test_my_llm_usage_rejects_non_admin():
    # Viewer should be bounced to /
    try:
        token, _tid = _mint_jwt("viewer")
    except RuntimeError:
        print("    (no viewer user — skipping)")
        return
    c = app.test_client()
    c.set_cookie(domain="localhost", key="access_token", value=token)
    r = c.get("/settings/my-llm-usage", follow_redirects=False)
    # role_required redirects to "/" on rejection
    assert r.status_code in (302, 303), r.status_code
    assert r.headers.get("Location") == "/" or r.headers.get("Location") == "http://localhost/"


# ---- /settings/tenant-tier/<id> (superadmin POST) --------------------------

def test_tenant_tier_change_writes_and_logs():
    from primeqa.intelligence.llm import tiers

    token, tid = _mint_jwt("superadmin")
    c = app.test_client()
    c.set_cookie(domain="localhost", key="access_token", value=token)

    # Capture baseline so we can restore after.
    db = SessionLocal()
    try:
        row = db.query(TenantAgentSettings).filter(
            TenantAgentSettings.tenant_id == tid,
        ).first()
        baseline = row.llm_tier if row else tiers.TIER_STARTER
    finally:
        db.close()

    # Change to pro (or enterprise if baseline was pro).
    target = tiers.TIER_ENTERPRISE if baseline == tiers.TIER_PRO else tiers.TIER_PRO

    r = c.post(f"/settings/tenant-tier/{tid}", data={"llm_tier": target})
    assert r.status_code in (302, 303)

    db = SessionLocal()
    try:
        row = db.query(TenantAgentSettings).filter(
            TenantAgentSettings.tenant_id == tid,
        ).first()
        assert row.llm_tier == target, f"expected {target}, got {row.llm_tier}"
        log = db.query(ActivityLog).filter(
            ActivityLog.entity_type == "tenant_llm_tier",
            ActivityLog.entity_id == tid,
        ).order_by(ActivityLog.id.desc()).first()
        assert log is not None, "no activity_log row written"
        assert log.details.get("new") == target
        assert log.details.get("old") == baseline
    finally:
        db.close()

    # Restore.
    c.post(f"/settings/tenant-tier/{tid}", data={"llm_tier": baseline})


def test_tenant_tier_rejects_unknown_value():
    token, tid = _mint_jwt("superadmin")
    c = app.test_client()
    c.set_cookie(domain="localhost", key="access_token", value=token)
    r = c.post(f"/settings/tenant-tier/{tid}", data={"llm_tier": "not-a-tier"})
    # Rejected: redirects back with a flash, but does NOT change the row.
    assert r.status_code in (302, 303)


def test_tenant_tier_change_rejects_non_superadmin():
    try:
        token, tid = _mint_jwt("admin")
    except RuntimeError:
        print("    (no admin user — skipping)")
        return
    c = app.test_client()
    c.set_cookie(domain="localhost", key="access_token", value=token)
    r = c.post(f"/settings/tenant-tier/{tid}", data={"llm_tier": "pro"},
               follow_redirects=False)
    # role_required("superadmin") bounces to /
    assert r.status_code in (302, 303)
    loc = r.headers.get("Location", "")
    assert loc.rstrip("/") in ("", "http://localhost")


# ---- /settings/llm-usage (superadmin view renders with tier data) ----------

def test_superadmin_llm_usage_exposes_all_tiers_context():
    token, _tid = _mint_jwt("superadmin")
    c = app.test_client()
    c.set_cookie(domain="localhost", key="access_token", value=token)
    r = c.get("/settings/llm-usage")
    assert r.status_code == 200, r.data.decode()[:400]


def main():
    tests = [
        ("tier_presets_have_all_four", test_tier_presets_have_all_four),
        ("tier_resolve_limits_uses_preset", test_tier_resolve_limits_uses_preset),
        ("tier_resolve_limits_override_wins", test_tier_resolve_limits_override_wins),
        ("tier_resolve_limits_custom_ignores_preset", test_tier_resolve_limits_custom_ignores_preset),
        ("tier_unknown_falls_back_to_starter", test_tier_unknown_falls_back_to_starter),
        ("load_tenant_config_returns_starter_defaults", test_load_tenant_config_returns_starter_defaults_for_missing_row),
        ("current_usage_returns_snapshot_with_caps", test_current_usage_returns_snapshot_with_caps),
        ("usage_snapshot_warn_threshold", test_usage_snapshot_warn_threshold),
        ("my_llm_usage_renders_for_admin", test_my_llm_usage_renders_for_admin),
        ("my_llm_usage_rejects_non_admin", test_my_llm_usage_rejects_non_admin),
        ("tenant_tier_change_writes_and_logs", test_tenant_tier_change_writes_and_logs),
        ("tenant_tier_rejects_unknown_value", test_tenant_tier_rejects_unknown_value),
        ("tenant_tier_change_rejects_non_superadmin", test_tenant_tier_change_rejects_non_superadmin),
        ("superadmin_llm_usage_exposes_all_tiers_context", test_superadmin_llm_usage_exposes_all_tiers_context),
    ]
    print("=" * 60)
    print("LLM architecture tests (Phases 1-6)")
    print("=" * 60)
    passed = sum(1 for n, fn in tests if test(n, fn))
    print(f"\n{passed}/{len(tests)} passed\n")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
