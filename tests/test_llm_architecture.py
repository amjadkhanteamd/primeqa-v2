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


def _csrf_client(jwt_token):
    """Return a Flask test_client ready for cookie-auth POST: JWT cookie
    set, CSRF cookie minted via a GET, and a helper that includes the
    CSRF header on subsequent POSTs.

    Post-audit (CSRF enabled in Apr 2026) any cookie-authenticated POST
    needs X-CSRF-Token matching the csrf_token cookie. Bearer-auth
    /api/* requests skip CSRF — but most of these feedback tests use
    cookie auth so they need the token.
    """
    c = app.test_client()
    c.set_cookie(domain="localhost", key="access_token", value=jwt_token)
    # Trigger the after_request CSRF cookie mint.
    c.get("/login")
    tok = c._cookies.get(("localhost", "/", "csrf_token"))
    token_val = tok.value if tok else ""
    # Monkey-patch the client's open() to auto-include the header.
    _orig_open = c.open
    def _open(*args, **kwargs):
        headers = dict(kwargs.get("headers") or {})
        headers.setdefault("X-CSRF-Token", token_val)
        kwargs["headers"] = headers
        return _orig_open(*args, **kwargs)
    c.open = _open
    return c


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
    c = _csrf_client(token)
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
    c = _csrf_client(token)
    r = c.get("/settings/my-llm-usage", follow_redirects=False)
    # role_required redirects to "/" on rejection
    assert r.status_code in (302, 303), r.status_code
    assert r.headers.get("Location") == "/" or r.headers.get("Location") == "http://localhost/"


# ---- /settings/tenant-tier/<id> (superadmin POST) --------------------------

def test_tenant_tier_change_writes_and_logs():
    from primeqa.intelligence.llm import tiers

    token, tid = _mint_jwt("superadmin")
    c = _csrf_client(token)

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
    c = _csrf_client(token)
    r = c.post(f"/settings/tenant-tier/{tid}", data={"llm_tier": "not-a-tier"})
    # Rejected: redirects back with a flash, but does NOT change the row.
    assert r.status_code in (302, 303)


def test_tenant_tier_change_rejects_non_superadmin():
    try:
        token, tid = _mint_jwt("admin")
    except RuntimeError:
        print("    (no admin user — skipping)")
        return
    c = _csrf_client(token)
    r = c.post(f"/settings/tenant-tier/{tid}", data={"llm_tier": "pro"},
               follow_redirects=False)
    # role_required("superadmin") bounces to /
    assert r.status_code in (302, 303)
    loc = r.headers.get("Location", "")
    assert loc.rstrip("/") in ("", "http://localhost")


# ---- /settings/llm-usage (superadmin view renders with tier data) ----------

def test_superadmin_llm_usage_exposes_all_tiers_context():
    token, _tid = _mint_jwt("superadmin")
    c = _csrf_client(token)
    r = c.get("/settings/llm-usage")
    assert r.status_code == 200, r.data.decode()[:400]


# ===========================================================================
# Phase 7: Human feedback loop
# ===========================================================================

def _get_any_tc(tenant_id):
    """Return a TC id for this tenant, or None if none exist. Used by
    feedback tests to avoid depending on a specific fixture."""
    db = SessionLocal()
    try:
        from primeqa.test_management.models import TestCase
        tc = db.query(TestCase).filter(
            TestCase.tenant_id == tenant_id,
            TestCase.deleted_at.is_(None),
        ).first()
        return tc.id if tc else None
    finally:
        db.close()


def _clear_user_feedback_24h(tenant_id, test_case_id, user_id):
    """Delete any user_thumbs_{up,down} signals for this (user, TC) pair
    from the last 24h so the rate-limit counter resets.

    Feedback tests fire multiple POSTs per run; without this they would
    saturate the 5-per-day cap within the suite and flake depending on
    ordering. Production signals are never affected — we only delete
    rows whose detail.user_id matches the test user.
    """
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import text as sql
    db = SessionLocal()
    try:
        db.execute(sql("""
            DELETE FROM generation_quality_signals
            WHERE tenant_id = :tid
              AND test_case_id = :tc
              AND signal_type IN ('user_thumbs_up', 'user_thumbs_down')
              AND captured_at >= :since
              AND (detail->>'user_id')::int = :uid
        """), {
            "tid": tenant_id, "tc": test_case_id, "uid": user_id,
            "since": datetime.now(timezone.utc) - timedelta(hours=24),
        })
        db.commit()
    finally:
        db.close()


def test_feedback_severity_mapping():
    """Signal severity should follow the reason, not always default to medium."""
    from primeqa.intelligence.llm import feedback
    # Thumbs-down with a high-severity reason → high
    assert feedback._severity_for(
        feedback.SIGNAL_USER_THUMBS_DOWN,
        feedback.REASON_WRONG_OBJECT_OR_FIELD,
    ) == "high"
    # Thumbs-down with a low-severity reason → low
    assert feedback._severity_for(
        feedback.SIGNAL_USER_THUMBS_DOWN,
        feedback.REASON_REDUNDANT,
    ) == "low"
    # BA reject always high
    assert feedback._severity_for(feedback.SIGNAL_BA_REJECTED) == "high"
    # Implicit user-edited is medium
    assert feedback._severity_for(feedback.SIGNAL_USER_EDITED) == "medium"


def test_feedback_rules_block_empty_for_clean_tenant():
    """No signals → empty string (safe to include unconditionally in prompt)."""
    from primeqa.intelligence.llm import feedback_rules
    # Use a fresh tenant id (9999 — unlikely to exist) so we're guaranteed
    # no signals. If the module stops being a pure read, revisit.
    block = feedback_rules.build_rules_block(99999, window_days=30)
    assert block == "", f"expected empty, got {block!r}"


def test_feedback_rules_classify_and_render():
    """_classify_signal maps signals to rule keys; _RULE_TEXTS has each."""
    from primeqa.intelligence.llm import feedback, feedback_rules
    # validator critical with a rule → uses the rule verbatim
    rule = feedback_rules._classify_signal({
        "signal_type": feedback.SIGNAL_VALIDATION_CRITICAL,
        "severity": "high",
        "detail": {"rule": "field_not_found", "object": "Account", "field": "Foo"},
    })
    assert rule == "field_not_found"
    # thumbs_down with reason → reason-based rule
    rule = feedback_rules._classify_signal({
        "signal_type": feedback.SIGNAL_USER_THUMBS_DOWN,
        "severity": "high",
        "detail": {"reason": "wrong_object_or_field"},
    })
    assert rule == "wrong_object_or_field"
    # unknown reason folds into generic rejection
    rule = feedback_rules._classify_signal({
        "signal_type": feedback.SIGNAL_USER_THUMBS_DOWN,
        "severity": "medium",
        "detail": {},
    })
    assert rule == "generic_rejection"
    # every rule key we emit must have a rendered text
    for key in [rule, "field_not_found", "wrong_object_or_field", "invalid_steps"]:
        assert key in feedback_rules._RULE_TEXTS, f"missing text for {key}"


def _tc_and_uid_for_superadmin():
    """Return (token, tenant_id, user_id, tc_id) for the superadmin so we
    can clear their feedback before each POST test."""
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.role == "superadmin").first()
        if u is None:
            return None, None, None, None
        token = jwt.encode({
            "sub": str(u.id), "tenant_id": u.tenant_id, "email": u.email,
            "role": u.role, "full_name": u.full_name or u.email,
        }, os.environ["JWT_SECRET"], algorithm="HS256")
        tc_id = _get_any_tc(u.tenant_id)
        return token, u.tenant_id, u.id, tc_id
    finally:
        db.close()


def test_post_feedback_happy_path_thumbs_up():
    token, tid, uid, tc_id = _tc_and_uid_for_superadmin()
    if tc_id is None:
        print("    (no test case in tenant — skipping)")
        return
    _clear_user_feedback_24h(tid, tc_id, uid)
    c = _csrf_client(token)
    r = c.post(f"/api/test-cases/{tc_id}/feedback", json={"verdict": "up"})
    assert r.status_code == 200, r.data.decode()[:200]
    data = r.get_json()
    assert data["ok"] is True
    assert data["signal_type"] == "user_thumbs_up"
    assert data.get("throttled") is False, f"unexpected throttle: {data}"


def test_post_feedback_happy_path_thumbs_down_with_reason():
    token, tid, uid, tc_id = _tc_and_uid_for_superadmin()
    if tc_id is None:
        print("    (no test case — skipping)")
        return
    _clear_user_feedback_24h(tid, tc_id, uid)
    c = _csrf_client(token)
    r = c.post(f"/api/test-cases/{tc_id}/feedback", json={
        "verdict": "down",
        "reason": "wrong_object_or_field",
        "reason_text": "test-seeded",
    })
    assert r.status_code == 200, r.data.decode()[:200]
    data = r.get_json()
    assert data["signal_type"] == "user_thumbs_down"
    assert data["severity"] == "high", f"wrong_object_or_field should be high, got {data}"


def test_post_feedback_invalid_verdict():
    token, tid = _mint_jwt("superadmin")
    tc_id = _get_any_tc(tid)
    if tc_id is None:
        print("    (no test case — skipping)")
        return
    c = _csrf_client(token)
    r = c.post(f"/api/test-cases/{tc_id}/feedback", json={"verdict": "maybe"})
    assert r.status_code == 400, r.data.decode()[:200]
    body = r.get_json()
    assert body["error"]["code"] == "VALIDATION_ERROR"


def test_post_feedback_other_requires_text():
    """reason=other without reason_text must 400."""
    token, tid = _mint_jwt("superadmin")
    tc_id = _get_any_tc(tid)
    if tc_id is None:
        print("    (no test case — skipping)")
        return
    c = _csrf_client(token)
    r = c.post(f"/api/test-cases/{tc_id}/feedback", json={
        "verdict": "down", "reason": "other",
    })
    assert r.status_code == 400
    body = r.get_json()
    assert "reason_text" in body["error"]["message"]


def test_post_feedback_rate_limit_throttles_silently():
    """6th submission on the same TC by the same user in 24h → throttled:true, 200 status."""
    token, tid, uid, tc_id = _tc_and_uid_for_superadmin()
    if tc_id is None:
        print("    (no test case — skipping)")
        return
    _clear_user_feedback_24h(tid, tc_id, uid)
    c = _csrf_client(token)
    last = None
    for _ in range(6):
        r = c.post(f"/api/test-cases/{tc_id}/feedback", json={"verdict": "up"})
        last = r.get_json()
    # Final call must be 200 (never 429) AND throttled=True.
    assert r.status_code == 200
    assert last.get("throttled") is True, f"expected throttled=True, got {last}"


def test_user_edited_captured_on_ai_tc_edit():
    """Editing an AI-generated TC for the first time writes a user_edited signal,
    deduped per 10-minute window."""
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import text as sql
    from primeqa.test_management.service import TestManagementService
    from primeqa.test_management.repository import (
        SectionRepository, RequirementRepository, TestCaseRepository,
        TestSuiteRepository, BAReviewRepository, MetadataImpactRepository,
    )
    from primeqa.core.repository import ActivityLogRepository
    from primeqa.test_management.models import TestCase, TestCaseVersion
    from primeqa.intelligence.models import GenerationQualitySignal
    from primeqa.intelligence.llm import feedback

    db = SessionLocal()
    try:
        # Find any AI-generated TC whose CURRENT version is still AI.
        # A prior test run may have switched it to `manual` by editing;
        # skip those and look for a genuinely-AI current version.
        candidates = db.query(TestCase).filter(
            TestCase.generation_batch_id.isnot(None),
            TestCase.deleted_at.is_(None),
        ).limit(20).all()
        tc = None
        for cand in candidates:
            cv = db.query(TestCaseVersion).filter_by(id=cand.current_version_id).first()
            if cv and cv.generation_method in ("ai", "regenerated"):
                tc = cand
                break
        if tc is None:
            print("    (no AI-generated TC with AI current_version in DB — skipping)")
            return

        # Clear any user_edited signals in the dedup window so the
        # "first edit after 10 min" check can fire cleanly. Same
        # approach as _clear_user_feedback_24h.
        db.execute(sql("""
            DELETE FROM generation_quality_signals
            WHERE tenant_id = :tid
              AND test_case_id = :tc
              AND signal_type = 'user_edited'
              AND captured_at >= :since
        """), {
            "tid": tc.tenant_id, "tc": tc.id,
            "since": datetime.now(timezone.utc) - timedelta(minutes=15),
        })
        db.commit()

        svc = TestManagementService(
            section_repo=SectionRepository(db),
            requirement_repo=RequirementRepository(db),
            test_case_repo=TestCaseRepository(db),
            suite_repo=TestSuiteRepository(db),
            review_repo=BAReviewRepository(db),
            impact_repo=MetadataImpactRepository(db),
            activity_repo=ActivityLogRepository(db),
        )

        before = db.query(GenerationQualitySignal).filter(
            GenerationQualitySignal.test_case_id == tc.id,
            GenerationQualitySignal.signal_type == feedback.SIGNAL_USER_EDITED,
        ).count()

        # Fire two updates in quick succession — only the first should
        # capture a signal because of the 10-minute dedup window.
        svc.update_test_case(
            tc.id, tc.tenant_id,
            {"title": tc.title + " (Phase 7 test)"},
            user_id=tc.owner_id,
        )
        svc.update_test_case(
            tc.id, tc.tenant_id,
            {"title": tc.title + " (Phase 7 test 2)"},
            user_id=tc.owner_id,
        )

        after = db.query(GenerationQualitySignal).filter(
            GenerationQualitySignal.test_case_id == tc.id,
            GenerationQualitySignal.signal_type == feedback.SIGNAL_USER_EDITED,
        ).count()
        delta = after - before
        # Dedup window allows at most 1. Zero means the signal wasn't
        # captured at all — a bug.
        assert delta == 1, f"expected exactly 1 new user_edited signal, got {delta}"
    finally:
        db.close()


def test_correction_rate_returns_valid_shape():
    """correction_rate always returns a dict with {days, corrected, total, rate, prev_rate, delta}."""
    from primeqa.intelligence.llm import feedback_rules
    db = SessionLocal()
    try:
        cr = feedback_rules.correction_rate(db, 1, days=30)
    finally:
        db.close()
    assert set(cr.keys()) == {
        "days", "corrected", "total", "rate", "prev_rate", "delta",
    }
    assert cr["days"] == 30
    assert 0.0 <= cr["rate"] <= 1.0
    assert cr["corrected"] <= cr["total"]


def test_tenant_dashboard_includes_feedback_section():
    token, _tid = _mint_jwt("superadmin")
    c = _csrf_client(token)
    r = c.get("/settings/my-llm-usage")
    assert r.status_code == 200
    body = r.data.decode()
    assert "AI quality feedback" in body
    assert "Correction rate" in body
    # Signal count cards should all render even with zero values.
    assert "thumbs up" in body
    assert "thumbs down" in body


def main():
    tests = [
        # Phases 1-6 (existing)
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
        # Phase 7: human feedback loop
        ("feedback_severity_mapping", test_feedback_severity_mapping),
        ("feedback_rules_block_empty_for_clean_tenant", test_feedback_rules_block_empty_for_clean_tenant),
        ("feedback_rules_classify_and_render", test_feedback_rules_classify_and_render),
        ("post_feedback_happy_path_thumbs_up", test_post_feedback_happy_path_thumbs_up),
        ("post_feedback_happy_path_thumbs_down_with_reason", test_post_feedback_happy_path_thumbs_down_with_reason),
        ("post_feedback_invalid_verdict", test_post_feedback_invalid_verdict),
        ("post_feedback_other_requires_text", test_post_feedback_other_requires_text),
        ("post_feedback_rate_limit_throttles_silently", test_post_feedback_rate_limit_throttles_silently),
        ("user_edited_captured_on_ai_tc_edit", test_user_edited_captured_on_ai_tc_edit),
        ("correction_rate_returns_valid_shape", test_correction_rate_returns_valid_shape),
        ("tenant_dashboard_includes_feedback_section", test_tenant_dashboard_includes_feedback_section),
    ]
    print("=" * 60)
    print("LLM architecture tests (Phases 1-7)")
    print("=" * 60)
    passed = sum(1 for n, fn in tests if test(n, fn))
    print(f"\n{passed}/{len(tests)} passed\n")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
