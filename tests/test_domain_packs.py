"""Domain Packs tests (migration 049).

Covers the six surfaces touched by the feature:

  1. Pack-file parsing — good, bad, missing-keys, malformed YAML
  2. Library reload-on-mtime
  3. Selector scoring (keyword word-boundary, object 2x weight, dormant
     v1 path when referenced_objects=None, token-budget cap)
  4. Provider attribution shape
  5. generation.py feature-flag gate + llm_call context carrying packs
  6. test_plan_generation.build() appends uncached packs block +
     populates context_for_log["domain_packs_applied"]

No real LLM calls — every gateway invocation is mocked. Tenant-flag
toggle uses a detached Session so it doesn't close the caller's
scoped_session (same pattern as test_story_view.py).
"""

import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from primeqa.app import app  # noqa: F401 — initialises ORM mappers
from primeqa.db import SessionLocal
from primeqa.core.models import TenantAgentSettings
from primeqa.test_management.models import (
    BAReview, GenerationBatch, Requirement, Section, TestCase,
    TestCaseVersion,
)
from primeqa.test_management.service import TestManagementService
from primeqa.test_management.repository import (
    BAReviewRepository, MetadataImpactRepository, RequirementRepository,
    SectionRepository, TestCaseRepository, TestSuiteRepository,
)
from primeqa.core.repository import ActivityLogRepository
from primeqa.core.models import Environment


TENANT_ID = 1


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


# ---------------------------------------------------------------------------
# Helpers — detached sessions so the caller's scoped_session stays clean.
# ---------------------------------------------------------------------------

def _set_packs_flag(tenant_id: int, enabled: bool):
    from sqlalchemy.orm import Session
    from primeqa.db import engine
    db = Session(bind=engine)
    try:
        row = db.query(TenantAgentSettings).filter_by(tenant_id=tenant_id).first()
        if row is None:
            row = TenantAgentSettings(
                tenant_id=tenant_id,
                llm_enable_domain_packs=enabled,
            )
            db.add(row)
        else:
            row.llm_enable_domain_packs = enabled
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Synthetic pack fixtures — written to a tempdir so tests don't touch the
# real `salesforce_domain_packs/` directory.
# ---------------------------------------------------------------------------

PACK_GOOD = """---
id: widget_sprockets
title: Widget Sprocket Patterns
keywords: [widget, sprocket, whirr]
objects: [Widget__c, Sprocket__c]
token_budget: 1000
version: v1
---

# Widget Sprocket

Short body content for testing. About 60 chars total.
"""

PACK_BAD_YAML = """---
id: broken
title: [not a string
keywords: []
objects: []
token_budget: 100
version: v1
---
body
"""

PACK_MISSING_KEYS = """---
id: only_id
---
body
"""

PACK_NO_FENCE = """just a body, no frontmatter
"""

PACK_SECOND = """---
id: alpha_beta
title: Alpha Beta
keywords: [alpha, beta, gamma]
objects: [Alpha__c]
token_budget: 800
version: v1
---

# Alpha Beta

Another pack for multi-match tests.
"""


def _write_packs_tmpdir(packs: dict):
    """Write {'name.md': content} into a fresh tempdir and return its path."""
    tmp = Path(tempfile.mkdtemp(prefix="dp_test_"))
    for name, body in packs.items():
        (tmp / name).write_text(body, encoding="utf-8")
    return tmp


# ---------------------------------------------------------------------------
# Tests 1-8: library + selector + provider (pure-function)
# ---------------------------------------------------------------------------

def run_tests():
    results = []
    print("\n=== Domain Packs Tests (migration 049) ===\n")

    # ---- 1. Pack file parses correctly -----------------------------------
    def test_pack_file_parses():
        from primeqa.intelligence.knowledge.domain_packs import _parse_pack_file
        tmp = _write_packs_tmpdir({"widget.md": PACK_GOOD})
        path = tmp / "widget.md"
        pack = _parse_pack_file(path)
        assert pack is not None
        assert pack.id == "widget_sprockets"
        assert pack.title == "Widget Sprocket Patterns"
        assert pack.keywords == ["widget", "sprocket", "whirr"]
        assert pack.objects == ["Widget__c", "Sprocket__c"]
        assert pack.token_budget == 1000
        assert pack.version == "v1"
        assert pack.content.startswith("# Widget Sprocket")
        assert pack.source_path == str(path.absolute())
    results.append(test(
        "1. Pack file parses correctly", test_pack_file_parses))

    # ---- 2. Malformed pack file is skipped -------------------------------
    def test_malformed_pack_skipped():
        from primeqa.intelligence.knowledge.domain_packs import DomainPackLibrary
        tmp = _write_packs_tmpdir({
            "good.md": PACK_GOOD,
            "bad_yaml.md": PACK_BAD_YAML,
            "missing.md": PACK_MISSING_KEYS,
            "nofence.md": PACK_NO_FENCE,
        })
        lib = DomainPackLibrary(str(tmp))
        packs = lib.load()
        ids = [p.id for p in packs]
        # Only the well-formed one loads
        assert ids == ["widget_sprockets"], ids
    results.append(test(
        "2. Malformed pack files are skipped, library continues",
        test_malformed_pack_skipped))

    # ---- 3. Library reloads on mtime change ------------------------------
    def test_library_reloads_on_mtime():
        from primeqa.intelligence.knowledge.domain_packs import DomainPackLibrary
        tmp = _write_packs_tmpdir({"widget.md": PACK_GOOD})
        lib = DomainPackLibrary(str(tmp))
        first = lib.load()
        assert len(first) == 1 and first[0].id == "widget_sprockets"

        # Modify file — bump content + mtime
        time.sleep(1.1)  # coarse filesystem mtime resolution on macOS
        modified = PACK_GOOD.replace("Widget Sprocket Patterns", "Widget Sprocket v2")
        (tmp / "widget.md").write_text(modified, encoding="utf-8")

        second = lib.load()
        assert len(second) == 1
        assert second[0].title == "Widget Sprocket v2", second[0].title
    results.append(test(
        "3. Library reloads on file mtime change",
        test_library_reloads_on_mtime))

    # ---- 4. Keyword scoring uses word boundaries -------------------------
    def test_keyword_word_boundaries():
        from primeqa.intelligence.knowledge.domain_packs import (
            DomainPackLibrary, DomainPackSelector,
        )
        tmp = _write_packs_tmpdir({"widget.md": PACK_GOOD})
        lib = DomainPackLibrary(str(tmp))
        sel = DomainPackSelector(lib)

        # "widgetized" should NOT match "widget" (word-boundary + inflections)
        m1 = sel.select("The widgetized flange was bulk-processed", None)
        assert m1 == [], [x.pack.id for x in m1]

        # "the widget whirrs" should match "widget" and "whirr"
        m2 = sel.select("the widget whirrs happily", None)
        assert len(m2) == 1 and m2[0].pack.id == "widget_sprockets"
        assert set(m2[0].matched_keywords) == {"widget", "whirr"}
    results.append(test(
        "4. Keyword scoring uses word boundaries + inflections",
        test_keyword_word_boundaries))

    # ---- 5. Object-match scoring weighted 2x + dormant in v1 --------------
    def test_object_match_weight():
        from primeqa.intelligence.knowledge.domain_packs import (
            DomainPackLibrary, DomainPackSelector,
        )
        tmp = _write_packs_tmpdir({"widget.md": PACK_GOOD})
        lib = DomainPackLibrary(str(tmp))
        sel = DomainPackSelector(lib)

        # With referenced_objects + zero keyword hits: score == 2 per obj
        m = sel.select(
            "unrelated text about sales",
            referenced_objects=["Widget__c"],
        )
        assert len(m) == 1
        assert m[0].score == 2, m[0].score
        assert m[0].matched_objects == ["Widget__c"]
        assert m[0].matched_keywords == []

        # With referenced_objects=None: v1 dormant — score stays 0 even with
        # declared objects, and the pack is excluded because score==0.
        m2 = sel.select("unrelated text about sales", referenced_objects=None)
        assert m2 == []
    results.append(test(
        "5. Object-match scoring weighted 2x; dormant path when objects=None",
        test_object_match_weight))

    # ---- 6. Token budget cap (measured, not declared) ---------------------
    def test_token_budget_cap():
        from primeqa.intelligence.knowledge.domain_packs import (
            DomainPackLibrary, DomainPackSelector,
        )
        # Two packs, each roughly 60 chars → ~15 measured tokens each.
        # Set max_tokens=20 so only ONE fits. Higher-scoring one wins.
        tmp = _write_packs_tmpdir({
            "widget.md": PACK_GOOD,      # 3 keywords
            "alpha.md":  PACK_SECOND,    # 3 keywords
        })
        lib = DomainPackLibrary(str(tmp))
        sel = DomainPackSelector(lib)

        # Requirement hits 1 kw of each pack → ties on score 1.
        # Tiebreak is id asc → alpha_beta wins, widget_sprockets excluded.
        m = sel.select(
            "alpha and widget mentioned", referenced_objects=None,
            max_tokens=20,
        )
        assert len(m) == 1, [x.pack.id for x in m]
        assert m[0].pack.id == "alpha_beta", m[0].pack.id
    results.append(test(
        "6. Token budget enforced via measured content length",
        test_token_budget_cap))

    # ---- 7. No matches returns empty list --------------------------------
    def test_no_matches_empty():
        from primeqa.intelligence.knowledge.domain_packs import (
            DomainPackLibrary, DomainPackSelector,
        )
        tmp = _write_packs_tmpdir({"widget.md": PACK_GOOD})
        lib = DomainPackLibrary(str(tmp))
        sel = DomainPackSelector(lib)
        m = sel.select("completely unrelated requirement text", None)
        assert m == []
    results.append(test(
        "7. No matches returns empty list", test_no_matches_empty))

    # ---- 8. Provider returns packs + attribution -------------------------
    def test_provider_attribution():
        from primeqa.intelligence.knowledge.domain_pack_provider import DomainPackProvider
        tmp = _write_packs_tmpdir({"widget.md": PACK_GOOD})
        prov = DomainPackProvider(packs_dir=str(tmp))
        packs, attr = prov.get_packs("the widget whirrs", referenced_objects=None)
        assert len(packs) == 1 and packs[0].id == "widget_sprockets"
        assert attr == [{"id": "widget_sprockets", "version": "v1"}]

        # Empty case
        packs2, attr2 = prov.get_packs("nothing relevant here")
        assert packs2 == [] and attr2 == []
    results.append(test(
        "8. Provider returns packs + attribution shape",
        test_provider_attribution))

    # ---- 9. Feature flag OFF: provider not invoked -----------------------
    def test_flag_off_provider_not_constructed():
        _set_packs_flag(TENANT_ID, False)
        from primeqa.intelligence.generation import _domain_packs_enabled
        assert _domain_packs_enabled(TENANT_ID) is False

        # Drive TestCaseGenerator.generate_plan with a mocked llm_call and
        # assert that when the flag is off:
        #   (a) DomainPackProvider.__init__ is never called
        #   (b) context["domain_packs"] == []
        from primeqa.intelligence.generation import TestCaseGenerator
        gen = TestCaseGenerator(
            llm_client=MagicMock(),
            metadata_repo=MagicMock(),
            tenant_id=TENANT_ID,
            user_id=1,
            api_key="sk-test",
        )
        gen._build_metadata_context = MagicMock(return_value={"objects": [], "validation_rules": []})

        class FakeReq:
            id = 123
            jira_key = "SMOKE-FLAG-OFF"
            jira_summary = "sales pipeline forecasting"
            jira_description = ""
            acceptance_criteria = ""

        fake_resp = MagicMock()
        fake_resp.parsed_content = {"test_plan": {"test_cases": [], "explanation": "x"}}
        fake_resp.model = "claude-sonnet-4-5-20250929"
        fake_resp.cost_usd = 0.01
        fake_resp.input_tokens = 10
        fake_resp.output_tokens = 5
        fake_resp.cached_input_tokens = 0
        fake_resp.cache_write_tokens = 0
        fake_resp.usage_log_id = 999
        fake_resp.usage_log_ids = [999]

        with patch(
            "primeqa.intelligence.knowledge.domain_pack_provider.DomainPackProvider.__init__",
            side_effect=RuntimeError("provider should NOT be constructed"),
        ):
            with patch("primeqa.intelligence.llm.llm_call",
                       return_value=fake_resp) as mock_call:
                gen.generate_plan(
                    requirement=FakeReq(),
                    meta_version_id=1,
                    min_tests=3, max_tests=6,
                )
                # Assert llm_call received context with domain_packs=[]
                kwargs = mock_call.call_args.kwargs
                assert kwargs["context"]["domain_packs"] == []
    results.append(test(
        "9. Flag OFF: provider not constructed; llm_call sees empty packs",
        test_flag_off_provider_not_constructed))

    # ---- 10. Feature flag ON + matching requirement ----------------------
    def test_flag_on_packs_in_context():
        _set_packs_flag(TENANT_ID, True)
        try:
            from primeqa.intelligence.generation import TestCaseGenerator
            gen = TestCaseGenerator(
                llm_client=MagicMock(),
                metadata_repo=MagicMock(),
                tenant_id=TENANT_ID,
                user_id=1,
                api_key="sk-test",
            )
            gen._build_metadata_context = MagicMock(
                return_value={"objects": [], "validation_rules": []},
            )

            class SQ205LikeReq:
                id = 456
                jira_key = "SQ-205-ISH"
                jira_summary = (
                    "Case escalation triggers Escalation record and "
                    "Account notification"
                )
                jira_description = "Flow-triggered escalation when SLA breached"
                acceptance_criteria = (
                    "* Given a high-priority Case\n"
                    "* When escalation fires\n"
                    "* Then Escalation__c record exists"
                )

            fake_resp = MagicMock()
            fake_resp.parsed_content = {"test_plan": {"test_cases": [], "explanation": "x"}}
            fake_resp.model = "claude-sonnet-4-5-20250929"
            fake_resp.cost_usd = 0.01
            fake_resp.input_tokens = 10
            fake_resp.output_tokens = 5
            fake_resp.cached_input_tokens = 0
            fake_resp.cache_write_tokens = 0
            fake_resp.usage_log_id = 1001
            fake_resp.usage_log_ids = [1001]

            with patch("primeqa.intelligence.llm.llm_call",
                       return_value=fake_resp) as mock_call:
                gen.generate_plan(
                    requirement=SQ205LikeReq(),
                    meta_version_id=1,
                    min_tests=3, max_tests=6,
                )
                packs_in_ctx = mock_call.call_args.kwargs["context"]["domain_packs"]
                assert len(packs_in_ctx) == 1, [p.id for p in packs_in_ctx]
                assert packs_in_ctx[0].id == "case_escalation"
                assert packs_in_ctx[0].version == "v1"
        finally:
            _set_packs_flag(TENANT_ID, False)
    results.append(test(
        "10. Flag ON + SQ-205-like requirement: case_escalation flows into llm_call",
        test_flag_on_packs_in_context))

    # ---- 11. No matching packs — generation still succeeds ---------------
    def test_no_match_backward_compat():
        _set_packs_flag(TENANT_ID, True)
        try:
            from primeqa.intelligence.generation import TestCaseGenerator
            gen = TestCaseGenerator(
                llm_client=MagicMock(),
                metadata_repo=MagicMock(),
                tenant_id=TENANT_ID,
                user_id=1,
                api_key="sk-test",
            )
            gen._build_metadata_context = MagicMock(
                return_value={"objects": [], "validation_rules": []},
            )

            class BoringReq:
                id = 789
                jira_key = "BORING-1"
                jira_summary = "Quarterly sales forecast export"
                jira_description = "Export CSV of Opportunity rollups"
                acceptance_criteria = "* CSV downloads"

            fake_resp = MagicMock()
            fake_resp.parsed_content = {
                "test_plan": {
                    "test_cases": [{"title": "it works",
                                    "coverage_type": "positive",
                                    "steps": [], "expected_results": [],
                                    "preconditions": [],
                                    "referenced_entities": [],
                                    "confidence_score": 0.8}],
                    "explanation": "Basic export",
                },
            }
            fake_resp.model = "claude-sonnet-4-5-20250929"
            fake_resp.cost_usd = 0.01
            fake_resp.input_tokens = 10
            fake_resp.output_tokens = 5
            fake_resp.cached_input_tokens = 0
            fake_resp.cache_write_tokens = 0
            fake_resp.usage_log_id = 2001
            fake_resp.usage_log_ids = [2001]

            with patch("primeqa.intelligence.llm.llm_call",
                       return_value=fake_resp) as mock_call:
                result = gen.generate_plan(
                    requirement=BoringReq(),
                    meta_version_id=1,
                    min_tests=3, max_tests=6,
                )
                # No match → empty packs list
                assert mock_call.call_args.kwargs["context"]["domain_packs"] == []
                # Plan still got assembled from the mocked response
                assert result.get("test_cases"), result
        finally:
            _set_packs_flag(TENANT_ID, False)
    results.append(test(
        "11. No matching packs: generation still succeeds (backward compat)",
        test_no_match_backward_compat))

    # ---- 12. SQ-205 text matches case_escalation -------------------------
    def test_sq205_matches_case_escalation():
        from primeqa.intelligence.knowledge.domain_pack_provider import DomainPackProvider
        # Use the real pack dir shipped with the repo
        prov = DomainPackProvider(packs_dir="salesforce_domain_packs")

        # Hardcode SQ-205 text so the test doesn't depend on DB state
        jira_summary = "End-to-end test: Case escalation triggers Escalation record and Account notification"
        jira_description = (
            "When a high-priority Case remains unresolved for more than 2 hours, "
            "the system should automatically escalate it. The escalation process "
            "involves multiple steps across multiple objects and must be tested "
            "end to end."
        )
        ac = (
            "* Given a High priority Case linked to an Account, when the Case is "
            "created, then a Case_SLA__c record is created automatically with "
            "SLA_Start__c = now and Status = Active\n"
            "* Given an active Case_SLA__c, when SLA threshold is breached, "
            "then an Escalation__c record is created"
        )
        text = " ".join([jira_summary, jira_description, ac])
        packs, attr = prov.get_packs(
            requirement_text=text, referenced_objects=None,
        )
        assert len(packs) == 1, [p.id for p in packs]
        assert packs[0].id == "case_escalation"
        # At least "escalate", "escalation", "case" should match
        # (score = len(matched_keywords)). Require score >= 2 per the plan.
        from primeqa.intelligence.knowledge.domain_packs import DomainPackSelector, DomainPackLibrary
        sel = DomainPackSelector(DomainPackLibrary("salesforce_domain_packs"))
        matches = sel.select(text, None)
        assert matches[0].score >= 2, matches[0].score
    results.append(test(
        "12. SQ-205 text matches case_escalation with score >= 2",
        test_sq205_matches_case_escalation))

    # ---- 13. build() records attribution in context_for_log --------------
    def test_build_records_attribution():
        from primeqa.intelligence.llm.prompts.test_plan_generation import build
        from primeqa.intelligence.knowledge.domain_packs import DomainPack

        fake_pack = DomainPack(
            id="case_escalation",
            title="Case Escalation Patterns",
            keywords=["escalate"],
            objects=["Case"],
            token_budget=1200,
            version="v1",
            content="short body",
            source_path="/tmp/fake.md",
        )

        class FakeReq:
            id = 555
            jira_key = "SQ-TEST"
            jira_summary = "case escalation"
            jira_description = ""
            acceptance_criteria = ""

        ctx = {
            "requirement": FakeReq(),
            "metadata_context": {"objects": [], "validation_rules": []},
            "meta_version_id": 42,
            "min_tests": 3,
            "max_tests": 6,
            "domain_packs": [fake_pack],
        }
        spec = build(ctx, tenant_id=TENANT_ID)
        assert spec.context_for_log.get("domain_packs_applied") == [
            {"id": "case_escalation", "version": "v1"}
        ], spec.context_for_log

        # No packs → key absent (not empty list)
        ctx_empty = dict(ctx, domain_packs=[])
        spec_empty = build(ctx_empty, tenant_id=TENANT_ID)
        assert "domain_packs_applied" not in spec_empty.context_for_log, \
            spec_empty.context_for_log

        # None → key absent
        ctx_none = dict(ctx)
        del ctx_none["domain_packs"]
        spec_none = build(ctx_none, tenant_id=TENANT_ID)
        assert "domain_packs_applied" not in spec_none.context_for_log
    results.append(test(
        "13. build() puts attribution in context_for_log when packs present",
        test_build_records_attribution))

    # ---- 14. build() appends fourth uncached user_block when packs present
    def test_build_appends_uncached_block():
        from primeqa.intelligence.llm.prompts.test_plan_generation import build
        from primeqa.intelligence.knowledge.domain_packs import DomainPack

        fake_pack = DomainPack(
            id="case_escalation",
            title="Case Escalation Patterns",
            keywords=["escalate"],
            objects=["Case"],
            token_budget=1200,
            version="v1",
            content="short body content",
            source_path="/tmp/fake.md",
        )

        class FakeReq:
            id = 556
            jira_key = "SQ-BLOCK-TEST"
            jira_summary = "case escalation via flow"
            jira_description = ""
            acceptance_criteria = ""

        # With packs: user_blocks has a new final block with no cache_control
        ctx = {
            "requirement": FakeReq(),
            "metadata_context": {"objects": [], "validation_rules": []},
            "meta_version_id": 42,
            "min_tests": 3,
            "max_tests": 6,
            "domain_packs": [fake_pack],
        }
        spec = build(ctx, tenant_id=TENANT_ID)
        user_blocks = spec.messages[0]["content"]
        last = user_blocks[-1]
        assert "cache_control" not in last, \
            "packs block must be uncached in v1"
        assert last["text"].startswith("# DOMAIN PACKS"), last["text"][:80]
        assert "case_escalation" in last["text"]

        # Without packs: last block is the dynamic task block (NOT the
        # domain-packs block) — confirm by checking it does NOT start with
        # the domain-packs header.
        ctx_empty = dict(ctx, domain_packs=[])
        spec_empty = build(ctx_empty, tenant_id=TENANT_ID)
        last_empty = spec_empty.messages[0]["content"][-1]
        assert not last_empty["text"].startswith("# DOMAIN PACKS"), \
            "no packs → no domain packs block should exist"
    results.append(test(
        "14. build() appends uncached fourth user_block when packs present",
        test_build_appends_uncached_block))

    passed = sum(results)
    total = len(results)
    print(f"\n{'='*60}")
    print(f"  {passed}/{total} passed")
    print(f"{'='*60}\n")
    return passed == total


if __name__ == "__main__":
    ok = run_tests()
    sys.exit(0 if ok else 1)
