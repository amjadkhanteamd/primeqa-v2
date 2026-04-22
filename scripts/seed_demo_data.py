"""Seed realistic demo data so the customer demo doesn't walk through
an empty app.

Idempotent — re-runnable without creating duplicates. Each seed step
checks for its target first.

Seeds (all tenant_id=1):
  * Rename env 24 "Prime QA SFDC" -> "Acme UAT Sandbox" if not already
  * Create env "Acme Integration" (sandbox, allow_bulk_run=true)
  * Create env "Acme Production" (production, is_production=true, allow_bulk_run=true)
  * Ensure 5 user personas with realistic names by renaming existing
    fixture accounts — emails stay stable so tests keep passing:
      admin@primeqa.io    -> Amanda Rivera (superadmin)        [existing]
      tester_rt@primeqa.io -> Priya Sharma (tester)
      dev_rt@primeqa.io   -> Jordan Chen (tester)
      dev_x@primeqa.io    -> Michael Okoye (developer)
      ro_rd@primeqa.io    -> Elena Voss (release owner)
  * 4 additional Jira-style requirements with realistic summaries
    + a handful of test cases per requirement (coverage_type mix) so the
    library + coverage matrix look populated
  * 4 completed pipeline_runs spanning the last 5 days with realistic
    mix of pass/fail/blocked counts — writes the run_test_results rows
    too so the dashboard trend chart + Go/No-Go calculation has real
    inputs
  * 1 release "Acme Release 2026.04" with tickets + test plan items
    attached
  * Quality gate thresholds on the 2 existing suites
"""

from __future__ import annotations

import os
import sys
import random
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from primeqa.app import app  # noqa: F401 — initialises engine
from primeqa.db import SessionLocal
from primeqa.core.models import Environment, User
from primeqa.execution.models import PipelineRun, RunTestResult
from primeqa.release.models import (
    Release, ReleaseRequirement, ReleaseTestPlanItem,
)
from primeqa.test_management.models import (
    Requirement, Section, TestCase, TestCaseVersion, TestSuite,
    SuiteTestCase,
)

TENANT_ID = 1
log = []


def say(msg):
    print(msg)
    log.append(msg)


# ==========================================================================
# Step 1: Environments
# ==========================================================================

def seed_envs(db):
    """Ensure Acme-labelled envs exist. Reuses env 24 for the primary sandbox."""
    env24 = db.query(Environment).filter_by(id=24, tenant_id=TENANT_ID).first()
    if env24 and env24.name != "Acme UAT Sandbox":
        say(f"  renaming env 24: '{env24.name}' -> 'Acme UAT Sandbox'")
        env24.name = "Acme UAT Sandbox"
        db.commit()

    # Acme Integration: reuse id=23 (Prime QA SFDC 1) if available; else create
    integ = db.query(Environment).filter_by(
        tenant_id=TENANT_ID, name="Acme Integration").first()
    if not integ:
        existing23 = db.query(Environment).filter_by(id=23).first()
        if existing23:
            say(f"  renaming env 23: '{existing23.name}' -> 'Acme Integration'; activating")
            existing23.name = "Acme Integration"
            existing23.is_active = True
            existing23.is_production = False
            existing23.allow_bulk_run = True
            existing23.env_type = "sandbox"
            db.commit()
        else:
            integ = Environment(
                tenant_id=TENANT_ID, name="Acme Integration",
                env_type="sandbox", sf_instance_url="https://acme--integration.my.salesforce.com",
                sf_api_version="59.0", is_active=True, is_production=False,
                allow_single_run=True, allow_bulk_run=True, created_by=1,
            )
            db.add(integ); db.commit()
            say(f"  created env 'Acme Integration' id={integ.id}")

    prod = db.query(Environment).filter_by(
        tenant_id=TENANT_ID, name="Acme Production").first()
    if not prod:
        existing39 = db.query(Environment).filter_by(id=39).first()
        if existing39:
            say(f"  renaming env 39: '{existing39.name}' -> 'Acme Production'; activating + marking prod")
            existing39.name = "Acme Production"
            existing39.is_active = True
            existing39.is_production = True
            existing39.env_type = "production"
            existing39.allow_bulk_run = True
            existing39.allow_single_run = True
            existing39.require_approval = True
            db.commit()
        else:
            prod = Environment(
                tenant_id=TENANT_ID, name="Acme Production",
                env_type="production",
                sf_instance_url="https://acme.my.salesforce.com",
                sf_api_version="59.0", is_active=True, is_production=True,
                require_approval=True, allow_single_run=True, allow_bulk_run=True,
                created_by=1,
            )
            db.add(prod); db.commit()
            say(f"  created env 'Acme Production' id={prod.id}")


# ==========================================================================
# Step 2: Users (rename fixtures to realistic-looking names)
# ==========================================================================

USER_RENAMES = [
    ("admin@primeqa.io",    "Amanda Rivera"),
    ("tester_rt@primeqa.io", "Priya Sharma"),
    ("dev_rt@primeqa.io",   "Jordan Chen"),
    ("dev_x@primeqa.io",    "Michael Okoye"),
    ("ro_rd@primeqa.io",    "Elena Voss"),
]


def seed_users(db):
    """Rename existing fixture users so the admin Users page shows realistic
    names. Emails stay stable so all tests continue to pass.
    """
    for email, new_name in USER_RENAMES:
        u = db.query(User).filter_by(email=email, tenant_id=TENANT_ID).first()
        if u and u.full_name != new_name:
            say(f"  renaming user {email}: '{u.full_name}' -> '{new_name}'")
            u.full_name = new_name
    db.commit()


# ==========================================================================
# Step 3: Requirements + test cases (beef up to 8+ visible tickets)
# ==========================================================================

DEMO_REQS = [
    ("ACME-201", "Case creation auto-assigns Tier-1 agent",
     "A new Case with Priority=High should be assigned to the next "
     "available Tier-1 agent round-robin within 2 minutes."),
    ("ACME-202", "Opportunity stage closure archives line items",
     "When an Opportunity moves to Closed Won, its line items are "
     "frozen and CloseDate stamped; subsequent edits require unlock."),
    ("ACME-203", "Lead conversion merges duplicate Accounts",
     "Converting a Lead whose Company matches an existing Account "
     "should merge into that Account rather than create a duplicate."),
    ("ACME-204", "Contact merge preserves activity timeline",
     "Merging two Contacts must retain every Task and Event on the "
     "losing record under the surviving Contact."),
    ("ACME-205", "Quote approval required over $50k",
     "Any Quote with TotalPrice > $50,000 must route to the deal-desk "
     "approval queue before it can be Accepted."),
    ("ACME-206", "Service contract auto-renewal 30 days out",
     "Service contracts within 30 days of EndDate should generate a "
     "renewal Opportunity automatically."),
    ("ACME-207", "Campaign member status sync with marketing",
     "CampaignMember.Status changes in Salesforce should push to the "
     "marketing platform via the integration bus within 5 minutes."),
    ("ACME-208", "Account hierarchy reparenting updates territories",
     "Changing an Account's ParentId must recompute the territory "
     "assignment for every child in the hierarchy."),
]


def seed_requirements_and_tcs(db):
    """Create missing demo requirements under a real section so the
    Requirements list has ≥8 rows + TCs."""
    section = (db.query(Section)
               .filter(Section.tenant_id == TENANT_ID,
                       Section.deleted_at.is_(None))
               .order_by(Section.id.asc())
               .first())
    if section is None:
        section = Section(tenant_id=TENANT_ID, name="Demo", created_by=1)
        db.add(section); db.commit(); db.refresh(section)
        say(f"  created section 'Demo' id={section.id}")

    now = datetime.now(timezone.utc)
    for jira_key, summary, description in DEMO_REQS:
        r = db.query(Requirement).filter_by(
            tenant_id=TENANT_ID, jira_key=jira_key).first()
        if r is None:
            r = Requirement(
                tenant_id=TENANT_ID, section_id=section.id,
                source="jira", jira_key=jira_key, jira_summary=summary,
                jira_description=description,
                jira_version=1, jira_last_synced=now, created_by=1,
            )
            db.add(r); db.commit(); db.refresh(r)
            say(f"  created requirement {jira_key}")
        # Ensure each requirement has at least 1 active TC for demo
        existing_tc = db.query(TestCase).filter(
            TestCase.tenant_id == TENANT_ID,
            TestCase.requirement_id == r.id,
            TestCase.deleted_at.is_(None)).first()
        if existing_tc is None:
            tc = TestCase(
                tenant_id=TENANT_ID, title=f"[+] {summary}"[:500],
                owner_id=1, created_by=1,
                requirement_id=r.id, section_id=section.id,
                visibility="shared", status="active",
                coverage_type="positive",
            )
            db.add(tc); db.commit(); db.refresh(tc)
            # Needs a version to be executable
            tcv = TestCaseVersion(
                test_case_id=tc.id, version_number=1,
                metadata_version_id=(
                    db.query(Environment).filter_by(id=24).first().current_meta_version_id
                ),
                steps=[{"step_order": 1, "action": "query",
                        "target_object": "Account",
                        "soql": "SELECT Id FROM Account LIMIT 1",
                        "expected_result": "non-empty"}],
                expected_results=["one row returned"],
                preconditions=[],
                generation_method="manual",
                confidence_score=0.9,
                created_by=1,
            )
            db.add(tcv); db.commit(); db.refresh(tcv)
            tc.current_version_id = tcv.id
            db.commit()


# ==========================================================================
# Step 4: Completed pipeline_runs with mixed results (last 5 days)
# ==========================================================================

def seed_runs(db):
    """Generate 4 realistic completed runs against Acme UAT Sandbox
    (env 24) spanning the last 5 days, so the dashboard trend chart
    has data."""
    env = db.query(Environment).filter_by(id=24, tenant_id=TENANT_ID).first()
    if env is None:
        say("  SKIP runs: env 24 missing")
        return

    # Pick real TCs to reference in run_test_results
    tcs = (db.query(TestCase)
           .filter(TestCase.tenant_id == TENANT_ID,
                   TestCase.deleted_at.is_(None),
                   TestCase.current_version_id.isnot(None))
           .order_by(TestCase.id.asc())
           .limit(8).all())
    if len(tcs) < 4:
        say(f"  WARN: only {len(tcs)} usable TCs; trend chart will be thin")

    now = datetime.now(timezone.utc)
    # Skip if we already have demo runs (look for the label tag)
    existing = (db.query(PipelineRun)
                .filter(PipelineRun.tenant_id == TENANT_ID,
                        PipelineRun.label == "demo-seed-2026-04")
                .count())
    if existing >= 4:
        say(f"  demo runs already present ({existing})")
        return

    scenarios = [
        # (days_ago, label, total, passed, failed, status, env_id_to_use)
        (5, "Sprint 24 baseline",          8, 7, 1, "completed", env.id),
        (4, "Sprint 24 mid-sprint",        8, 6, 2, "completed", env.id),
        (2, "Sprint 24 regression",        8, 5, 3, "completed", env.id),
        (1, "Sprint 24 release candidate", 8, 8, 0, "completed", env.id),
    ]

    for days_ago, lbl_suffix, total, passed, failed, status, eid in scenarios:
        queued = now - timedelta(days=days_ago, hours=random.randint(1, 8))
        started = queued + timedelta(minutes=random.randint(1, 3))
        completed = started + timedelta(
            minutes=random.randint(8, 25),
            seconds=random.randint(1, 59),
        )
        run = PipelineRun(
            tenant_id=TENANT_ID, environment_id=eid, triggered_by=1,
            run_type="execute_only", source_type="test_cases",
            source_ids=[tc.id for tc in tcs[:total]],
            cancellation_token=f"demo-{queued.timestamp()}-{lbl_suffix[:8]}",
            status=status, priority="normal",
            total_tests=total, passed=passed, failed=failed, skipped=0,
            queued_at=queued, started_at=started, completed_at=completed,
            label="demo-seed-2026-04",
            source_refs={"mode": "demo_seed", "scenario": lbl_suffix},
        )
        db.add(run); db.commit(); db.refresh(run)

        # Populate run_test_results matching passed/failed split
        selected = tcs[:total] if len(tcs) >= total else (tcs * (total // max(1, len(tcs)) + 1))[:total]
        for idx, tc in enumerate(selected):
            result_status = "passed" if idx < passed else "failed"
            rtr = RunTestResult(
                run_id=run.id, test_case_id=tc.id,
                test_case_version_id=tc.current_version_id,
                environment_id=eid,
                status=result_status,
                total_steps=3, passed_steps=3 if result_status == "passed" else 2,
                failed_steps=0 if result_status == "passed" else 1,
                duration_ms=random.randint(2500, 9500),
                executed_at=started + timedelta(seconds=30 + idx * 12),
                failure_summary=(None if result_status == "passed"
                                 else "Assertion failed: StageName expected Closed Won got Closed Lost"),
            )
            db.add(rtr)
        db.commit()
        say(f"  seeded run #{run.id}: {lbl_suffix} ({passed}/{total} passed, {days_ago}d ago)")


# ==========================================================================
# Step 5: Release with tickets + test plan
# ==========================================================================

def seed_release(db):
    """Ensure 1 realistic release exists with tickets + TC attachments."""
    rel = db.query(Release).filter_by(
        tenant_id=TENANT_ID, name="Acme Release 2026.04").first()
    if rel is None:
        rel = Release(
            tenant_id=TENANT_ID, name="Acme Release 2026.04",
            version_tag="R-2026.04", description="April 2026 release wave",
            status="in_progress", created_by=1,
            decision_criteria={"min_pass_rate": 95,
                               "max_flaky_percent": 10,
                               "critical_tests_must_pass": True,
                               "no_unresolved_high_risk_impacts": True},
        )
        db.add(rel); db.commit(); db.refresh(rel)
        say(f"  created release '{rel.name}' id={rel.id}")

    # Attach some demo requirements
    reqs = (db.query(Requirement)
            .filter(Requirement.tenant_id == TENANT_ID,
                    Requirement.jira_key.like("ACME-%"),
                    Requirement.deleted_at.is_(None))
            .all())
    for r in reqs[:5]:
        link = db.query(ReleaseRequirement).filter_by(
            release_id=rel.id, requirement_id=r.id).first()
        if link is None:
            db.add(ReleaseRequirement(
                release_id=rel.id, requirement_id=r.id,
                added_by=1))
    db.commit()

    # Attach some TCs (whatever we have)
    tcs = (db.query(TestCase)
           .filter(TestCase.tenant_id == TENANT_ID,
                   TestCase.deleted_at.is_(None))
           .order_by(TestCase.id.asc())
           .limit(6).all())
    for pos, tc in enumerate(tcs):
        item = db.query(ReleaseTestPlanItem).filter_by(
            release_id=rel.id, test_case_id=tc.id).first()
        if item is None:
            db.add(ReleaseTestPlanItem(
                release_id=rel.id, test_case_id=tc.id,
                priority=(["high", "medium", "high", "medium",
                           "critical", "low"])[pos % 6],
                position=pos + 1,
                inclusion_reason="demo_seed",
            ))
    db.commit()


# ==========================================================================
# Step 6: Suites with quality gate
# ==========================================================================

def seed_suite_gates(db):
    """Ensure the two existing suites have a quality_gate_threshold and a
    realistic name."""
    suites = (db.query(TestSuite)
              .filter(TestSuite.tenant_id == TENANT_ID,
                      TestSuite.deleted_at.is_(None))
              .order_by(TestSuite.id.asc())
              .all())
    if len(suites) >= 2:
        # First suite = "Smoke" 100% gate; second = "Regression" 90% gate
        targets = [("Smoke Suite", 100), ("Regression Suite", 90)]
        for (name, thr), s in zip(targets, suites[:2]):
            changed = False
            if s.name != name:
                say(f"  renaming suite {s.id} '{s.name}' -> '{name}'")
                s.name = name
                changed = True
            if s.quality_gate_threshold != thr:
                say(f"  setting gate threshold on '{name}' -> {thr}%")
                s.quality_gate_threshold = thr
                changed = True
            if changed:
                db.commit()
    # Populate suite TCs if empty
    tcs = (db.query(TestCase)
           .filter(TestCase.tenant_id == TENANT_ID,
                   TestCase.deleted_at.is_(None))
           .order_by(TestCase.id.asc())
           .limit(6).all())
    for s in suites[:2]:
        existing_count = db.query(SuiteTestCase).filter_by(
            suite_id=s.id).count()
        if existing_count < 3:
            for pos, tc in enumerate(tcs[:4]):
                exists = db.query(SuiteTestCase).filter_by(
                    suite_id=s.id, test_case_id=tc.id).first()
                if exists is None:
                    db.add(SuiteTestCase(
                        suite_id=s.id, test_case_id=tc.id,
                        position=pos + 1))
            db.commit()
            say(f"  added TCs to suite '{s.name}'")


# ==========================================================================

def main():
    db = SessionLocal()
    try:
        say("=== seeding demo data ===")
        seed_envs(db)
        seed_users(db)
        seed_requirements_and_tcs(db)
        seed_runs(db)
        seed_release(db)
        seed_suite_gates(db)
        say("=== done ===")
    finally:
        db.close()


if __name__ == "__main__":
    main()
