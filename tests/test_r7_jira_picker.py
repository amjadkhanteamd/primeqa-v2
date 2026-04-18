"""R7 tests \u2014 Jira ticket search + live run preview + cache."""

import sys
import os
from unittest.mock import patch, MagicMock

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
    print("\n=== R7 Jira Picker + Live Preview ===\n")
    results = []

    r = client.post("/api/auth/login", json={
        "email": "admin@primeqa.io", "password": "changeme123", "tenant_id": TENANT_ID,
    })
    admin_token = r.get_json()["access_token"]
    client.set_cookie("access_token", admin_token)

    def t_search_jql_key_pattern():
        """Exact issue-key queries build a `key = "X"` JQL and hit the new
        /rest/api/3/search/jql endpoint (old /search is 410 Gone since May 2024)."""
        from primeqa.runs.wizard import JiraClient, _JIRA_SEARCH_CACHE
        _JIRA_SEARCH_CACHE.clear()
        captured = {}
        def fake_get(url, **kwargs):
            captured["url"] = url
            captured["params"] = kwargs.get("params") or {}
            m = MagicMock(); m.raise_for_status = lambda: None
            m.json.return_value = {
                "issues": [{"id": "1", "key": "PROJ-123", "fields": {
                    "summary": "x",
                    "status": {"name": "Done"},
                    "issuetype": {"name": "Bug"},
                    "project": {"key": "PROJ", "name": "Project"},
                }}],
                "isLast": True,
            }
            return m
        c = JiraClient("https://jira.example", None)
        with patch("primeqa.runs.wizard.http_requests.get", side_effect=fake_get):
            out = c.search_issues("PROJ-123", connection_id=1)
        assert len(out) == 1 and out[0]["key"] == "PROJ-123"
        assert captured["params"]["jql"] == 'key = "PROJ-123"'
        # Regression: must hit the new endpoint, not the deprecated one
        assert captured["url"].endswith("/rest/api/3/search/jql"), captured["url"]
    results.append(test("R7-1. Key-pattern query uses exact JQL on /search/jql",
                        t_search_jql_key_pattern))

    def t_search_jql_fulltext():
        from primeqa.runs.wizard import JiraClient, _JIRA_SEARCH_CACHE
        _JIRA_SEARCH_CACHE.clear()
        captured = {}
        def fake_get(url, **kwargs):
            captured["params"] = kwargs.get("params") or {}
            m = MagicMock(); m.raise_for_status = lambda: None; m.json.return_value = {"issues": []}
            return m
        c = JiraClient("https://jira.example", None)
        with patch("primeqa.runs.wizard.http_requests.get", side_effect=fake_get):
            c.search_issues("opportunity validation", connection_id=1)
        jql = captured["params"]["jql"]
        assert "summary ~" in jql and "ORDER BY updated DESC" in jql
    results.append(test("R7-2. Free-text query uses summary ~ JQL with ORDER BY", t_search_jql_fulltext))

    def t_search_cache_hit():
        """Second call with the same (conn,q,limit) within TTL is cached."""
        from primeqa.runs.wizard import JiraClient, _JIRA_SEARCH_CACHE
        _JIRA_SEARCH_CACHE.clear()
        call_count = {"n": 0}
        def fake_get(url, **kwargs):
            call_count["n"] += 1
            m = MagicMock(); m.raise_for_status = lambda: None
            m.json.return_value = {"issues": [{"id":"1","key":"PROJ-1","fields":{"summary":"x"}}]}
            return m
        c = JiraClient("https://jira.example", None)
        with patch("primeqa.runs.wizard.http_requests.get", side_effect=fake_get):
            c.search_issues("hello", connection_id=42)
            c.search_issues("hello", connection_id=42)
            c.search_issues("HELLO", connection_id=42)  # same key after lowercase
        assert call_count["n"] == 1, f"expected 1 upstream call, got {call_count['n']}"
    results.append(test("R7-3. TTL cache suppresses duplicate upstream calls", t_search_cache_hit))

    def t_search_endpoint_no_env():
        """env_id missing \u2192 HTML hint, no 4xx."""
        r = client.get("/api/jira/search?q=hi")
        assert r.status_code == 200
        assert b"Pick an environment" in r.data
    results.append(test("R7-4. GET /api/jira/search with no env renders hint fragment",
                        t_search_endpoint_no_env))

    def t_search_endpoint_short_query():
        """q < 2 chars \u2192 hint fragment."""
        r = client.get("/api/jira/search?env_id=1&q=a")
        assert r.status_code == 200
        assert b"at least 2 characters" in r.data
    results.append(test("R7-5. Short query returns Type-at-least-2 hint",
                        t_search_endpoint_short_query))

    def t_search_endpoint_json_mode():
        r = client.get("/api/jira/search?env_id=1&q=a&format=json")
        assert r.status_code == 200
        body = r.get_json()
        assert "results" in body and "hint" in body
    results.append(test("R7-6. ?format=json switches to JSON response",
                        t_search_endpoint_json_mode))

    def t_preview_endpoint_empty():
        r = client.post("/api/runs/preview", json={
            "environment_id": None, "jira_keys": [], "run_type": "execute_only",
        })
        assert r.status_code == 200
        body = r.get_json()
        assert body["test_case_count"] == 0
        assert body["summary_text"].startswith("Nothing selected")
    results.append(test("R7-7. Preview with empty selection returns 0 tests",
                        t_preview_endpoint_empty))

    def t_preview_endpoint_hand_picks():
        from primeqa.db import SessionLocal
        from primeqa.test_management.models import TestCase
        db = SessionLocal()
        tc = db.query(TestCase).filter(
            TestCase.tenant_id == TENANT_ID, TestCase.deleted_at.is_(None),
        ).order_by(TestCase.id.desc()).first()
        db.close()
        r = client.post("/api/runs/preview", json={
            "environment_id": 1, "jira_keys": [], "run_type": "execute_only",
            "test_case_ids": [tc.id],
        })
        assert r.status_code == 200
        body = r.get_json()
        assert body["test_case_count"] == 1
        assert "1 test case" in body["summary_text"]
    results.append(test("R7-8. Preview resolves hand-picked TC to 1",
                        t_preview_endpoint_hand_picks))

    def t_summary_text_format():
        from primeqa.execution.routes import _build_summary_text
        assert "Nothing selected" in _build_summary_text(0,0,0,0,0,0)
        s = _build_summary_text(2, 1, 0, 0, 0, 12)
        assert "2 Jira tickets" in s and "1 suite" in s and "\u2192 12 test cases" in s
    results.append(test("R7-9. Summary text matches \u20182 Jira tickets \u2192 12 test cases\u2019 pattern",
                        t_summary_text_format))

    def t_wizard_renders_new_picker():
        r = client.get("/runs/new")
        assert r.status_code == 200
        body = r.data.decode()
        assert 'id="jira-search"' in body
        assert 'id="jira-results"' in body
        assert 'id="jira-chips"' in body
        assert 'id="selection-summary-text"' in body
    results.append(test("R7-10. /runs/new renders new Jira picker + summary",
                        t_wizard_renders_new_picker))

    def t_wizard_sends_env_under_correct_name():
        """Regression: the Jira search input MUST send the environment under the
        query-string key `env_id` (the name the backend reads), not
        `environment_id` (the form-field name that hx-include would use). A
        mismatch silently breaks the entire picker."""
        r = client.get("/runs/new")
        assert r.status_code == 200
        body = r.data.decode()
        # The search input region
        start = body.find('id="jira-search"')
        assert start > 0
        end = body.find(">", start)
        search_tag = body[start:end]
        # Must reference env_id (the backend's arg name)
        assert "env_id" in search_tag, (
            "Jira search input must send the env under `env_id`; a bare "
            "hx-include on `environment_id` sends it under the wrong name "
            "and the backend silently gets no env \u2192 the \u2018Pick an environment\u2019 "
            "hint shows forever."
        )
    results.append(test("R7-11. Wizard Jira search sends env as env_id (not environment_id)",
                        t_wizard_sends_env_under_correct_name))

    def t_search_accepts_both_env_names_server_side():
        """Defence in depth: even if a future client accidentally sends
        `environment_id`, the backend falls back and doesn't silently
        disable the search. Current canonical name is env_id."""
        # Baseline: env_id works
        r1 = client.get("/api/jira/search?env_id=1&q=hi&format=json")
        assert r1.status_code == 200
        # Fallback: environment_id also works
        r2 = client.get("/api/jira/search?environment_id=1&q=hi&format=json")
        assert r2.status_code == 200
        # Both should produce comparable shape (either hint or results)
        b1 = r1.get_json()
        b2 = r2.get_json()
        assert set(b1.keys()) == set(b2.keys()), f"shape diverged: {b1} vs {b2}"
    results.append(test("R7-12. /api/jira/search accepts env_id OR environment_id",
                        t_search_accepts_both_env_names_server_side))

    passed = sum(results); total = len(results)
    print(f"\n{'='*40}\nResults: {passed}/{total} passed")
    print("ALL R7 TESTS PASSED" if passed == total else f"{total - passed} FAILED")
    return passed == total


if __name__ == "__main__":
    sys.exit(0 if run_tests() else 1)
