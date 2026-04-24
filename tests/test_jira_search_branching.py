"""Unit tests for build_search_core_jql + JiraClient.search_issues.

Four-branch JQL builder covering the Jira-picker search shapes. Pure
logic — no live Jira calls except the mocked ones that prove end-to-end
behaviour (branching + client-side filter + caching).

Branches:
  1. Full issue key         "SQ-205" / "sq-205"  → key = "SQ-205"
  2+3. Letters / letters+dash+digits
                            "SQ", "sq", "SQ-",
                            "SQ-20", "PROJ-5"    → (project = "SQ"
                                                    OR summary ~ "SQ-20*")
                            with client filter when a dash is present
  4. Free text              "lead", "account merge", "1234"
                                                 → summary ~ "lead*"
"""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from primeqa.app import app  # noqa: F401 — imports the app/blueprint wiring
from primeqa.runs.wizard import (
    JiraClient, _JIRA_SEARCH_CACHE, build_search_core_jql,
)


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


def run_tests():
    print("\n=== Jira search JQL branching ===\n")
    results = []

    # ----------------------------------------------------------------
    # Pure builder tests — no HTTP
    # ----------------------------------------------------------------

    def t_branch1_full_key_upper():
        jql, filt = build_search_core_jql("SQ-205")
        assert jql == 'key = "SQ-205"', jql
        assert filt is None
    results.append(test("B1. Full key SQ-205 → exact key JQL, no client filter",
                        t_branch1_full_key_upper))

    def t_branch1_full_key_lower():
        # Lowercase input still lands in Branch 1 — normalised to upper
        jql, filt = build_search_core_jql("sq-205")
        assert jql == 'key = "SQ-205"', jql
        assert filt is None
    results.append(test("B1. Lowercase sq-205 is normalised to SQ-205",
                        t_branch1_full_key_lower))

    def t_branch23_letters_only():
        jql, filt = build_search_core_jql("SQ")
        # project clause + summary wildcard
        assert 'project = "SQ"' in jql, jql
        assert 'summary ~ "SQ*"' in jql, jql
        # No dash → no client filter
        assert filt is None
    results.append(test("B2. Letters-only SQ → project + summary, no filter",
                        t_branch23_letters_only))

    def t_branch23_letters_lowercase():
        jql, filt = build_search_core_jql("sq")
        # project key is uppercased; summary token kept as-is (Jira ~
        # is case-insensitive anyway)
        assert 'project = "SQ"' in jql, jql
        assert 'summary ~ "sq*"' in jql, jql
        assert filt is None
    results.append(test("B2. Lowercase sq → project uppercased, summary as-is",
                        t_branch23_letters_lowercase))

    def t_branch23_dash_no_digits():
        jql, filt = build_search_core_jql("SQ-")
        assert 'project = "SQ"' in jql, jql
        assert 'summary ~ "SQ-*"' in jql, jql
        assert filt is not None, "dash present → client filter expected"
        rows = [{"key": "SQ-1", "summary": "a"}, {"key": "XY-2", "summary": "b"}]
        kept = filt(rows)
        assert [r["key"] for r in kept] == ["SQ-1"], kept
    results.append(test("B3. Letters+dash SQ- → project clause + key-prefix filter",
                        t_branch23_dash_no_digits))

    def t_branch23_partial_digits():
        jql, filt = build_search_core_jql("SQ-20")
        assert 'project = "SQ"' in jql, jql
        assert 'summary ~ "SQ-20*"' in jql, jql
        # Client filter narrows to SQ-20 + SQ-20x (anything whose key
        # has "SQ-20" as a literal prefix — NOT SQ-21xx or SQ-2XXX).
        assert filt is not None
        # Summaries kept free of "sq-20" so they don't accidentally
        # satisfy the summary-contains branch of the client filter.
        rows = [
            {"key": "SQ-2",    "summary": "ignore"},
            {"key": "SQ-20",   "summary": "ticket twenty"},
            {"key": "SQ-200",  "summary": "ticket two hundred"},
            {"key": "SQ-209",  "summary": "ticket two hundred nine"},
            {"key": "SQ-21",   "summary": "different prefix"},
            {"key": "SQ-2100", "summary": "unrelated"},
            {"key": "XYZ-1",   "summary": "SQ-20 mention inside summary"},
        ]
        kept = {r["key"] for r in filt(rows)}
        assert kept == {"SQ-20", "SQ-200", "SQ-209", "XYZ-1"}, kept
    results.append(test("B3. Letters+dash+digits SQ-20 → filter keeps prefix + summary hits",
                        t_branch23_partial_digits))

    def t_branch4_freetext_word():
        jql, filt = build_search_core_jql("lead")
        # "lead" matches the letter-only regex (branch 2+3), not
        # branch 4. The "lead" project gets a probe clause AND the
        # summary wildcard runs — both are safe. We assert the
        # summary wildcard is present; project clause is the extra
        # (no project "LEAD" → clause just returns 0 rows).
        assert 'summary ~ "lead*"' in jql, jql
        assert filt is None
    results.append(test(
        "B4. Word 'lead' lands in letters-only branch; summary wildcard fires",
        t_branch4_freetext_word))

    def t_branch4_freetext_multi_word():
        # "account merge" has a space → regex fails → Branch 4
        jql, filt = build_search_core_jql("account merge")
        assert jql == 'summary ~ "account merge*"', jql
        assert filt is None
    results.append(test(
        "B4. Multi-word 'account merge' → summary wildcard only",
        t_branch4_freetext_multi_word))

    def t_branch4_digits_only():
        jql, filt = build_search_core_jql("1234")
        # Digits only — regex fails (needs leading letter) → Branch 4
        assert jql == 'summary ~ "1234*"', jql
        assert filt is None
    results.append(test(
        "B4. Digits-only '1234' → free-text summary wildcard",
        t_branch4_digits_only))

    def t_quote_escaped():
        # Ensure quote in query doesn't break JQL
        jql, filt = build_search_core_jql('with "quote"')
        assert 'with \\"quote\\"' in jql, jql
    results.append(test(
        "Escape. Double-quote in query is escaped", t_quote_escaped))

    def t_whitespace_trimmed():
        jql, _ = build_search_core_jql("  SQ-205  ")
        assert jql == 'key = "SQ-205"', jql
    results.append(test("Trim. Leading/trailing whitespace stripped",
                        t_whitespace_trimmed))

    def t_empty_input_safe():
        # Caller gates on len>=2 but builder must not crash
        jql, filt = build_search_core_jql("")
        assert isinstance(jql, str) and len(jql) > 0
    results.append(test("Safety. Empty input returns a string, no crash",
                        t_empty_input_safe))

    # ----------------------------------------------------------------
    # Integration via JiraClient.search_issues (mocked HTTP)
    # ----------------------------------------------------------------

    def _capture_search(q, *, mock_issues):
        """Helper: invoke search_issues with a patched http_requests.get,
        returning (jql_sent, returned_rows)."""
        _JIRA_SEARCH_CACHE.clear()
        captured = {}
        def fake_get(url, **kwargs):
            captured["url"] = url
            captured["params"] = kwargs.get("params") or {}
            m = MagicMock(); m.raise_for_status = lambda: None
            m.json.return_value = {"issues": mock_issues, "isLast": True}
            return m
        c = JiraClient("https://jira.example", None)
        with patch("primeqa.runs.wizard.http_requests.get", side_effect=fake_get):
            rows = c.search_issues(q, connection_id=1)
        return captured.get("params", {}).get("jql"), rows

    def t_integration_sq_prefix():
        # "SQ" branch 2+3 (no dash): project clause + summary wildcard
        jql, rows = _capture_search("SQ", mock_issues=[
            {"id": "1", "key": "SQ-205", "fields": {"summary": "Case creation"}},
            {"id": "2", "key": "SQ-207", "fields": {"summary": "Opp update"}},
        ])
        assert 'project = "SQ"' in jql
        assert len(rows) == 2
    results.append(test(
        "I1. search_issues('SQ') fetches project + returns all SQ tickets",
        t_integration_sq_prefix))

    def t_integration_partial_key_filter():
        # "SQ-20" branch 2+3 WITH dash: client-filter narrows
        jql, rows = _capture_search("SQ-20", mock_issues=[
            {"id": "1", "key": "SQ-2",   "fields": {"summary": "skip"}},
            {"id": "2", "key": "SQ-20",  "fields": {"summary": "keep"}},
            {"id": "3", "key": "SQ-200", "fields": {"summary": "keep"}},
            {"id": "4", "key": "XY-1",   "fields": {"summary": "skip"}},
        ])
        assert 'project = "SQ"' in jql
        keys = {r["key"] for r in rows}
        assert keys == {"SQ-20", "SQ-200"}, keys
    results.append(test(
        "I2. search_issues('SQ-20') client-filters to SQ-20 + SQ-20x only",
        t_integration_partial_key_filter))

    def t_integration_full_key():
        jql, rows = _capture_search("SQ-205", mock_issues=[
            {"id": "1", "key": "SQ-205", "fields": {"summary": "match"}},
        ])
        assert jql.startswith('key = "SQ-205"'), jql
        assert len(rows) == 1
    results.append(test("I3. search_issues('SQ-205') uses exact key JQL",
                        t_integration_full_key))

    def t_integration_freetext():
        jql, rows = _capture_search("lead", mock_issues=[
            {"id": "1", "key": "ACME-10", "fields": {"summary": "lead conversion"}},
        ])
        assert 'summary ~ "lead*"' in jql
        assert len(rows) == 1
    results.append(test(
        "I4. search_issues('lead') uses summary wildcard",
        t_integration_freetext))

    def t_integration_no_match():
        jql, rows = _capture_search("xyznoticket", mock_issues=[])
        assert 'summary ~ "xyznoticket*"' in jql
        assert rows == []
    results.append(test(
        "I5. search_issues('xyznoticket') returns empty cleanly",
        t_integration_no_match))

    passed = sum(1 for r in results if r)
    total = len(results)
    print(f"\n{passed}/{total} tests passed\n")
    return passed == total


if __name__ == "__main__":
    ok = run_tests()
    sys.exit(0 if ok else 1)
