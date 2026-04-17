"""PrimeQA Self-Validation Suite runner.

Loads `primeqa/system_validation/suites/primeqa_core.json` and executes
every test against the Flask test client. Prints a structured report and
exits non-zero on any failure.

Also unit-tests the runner primitives (variable substitution, verify
assertions) so the grammar itself is validated.

See docs/design/system-validation.md.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from primeqa.app import app
from primeqa.system_validation.runner import (
    load_suite, run_suite, _substitute, _VAR_RE,
)

SUITE_PATH = os.path.join(
    os.path.dirname(__file__), "..",
    "primeqa", "system_validation", "suites", "primeqa_core.json",
)


def test(name, fn):
    try:
        fn(); print(f"  PASS  {name}"); return True
    except AssertionError as e:
        print(f"  FAIL  {name}: {e}"); return False
    except Exception as e:
        import traceback; print(f"  ERROR {name}: {type(e).__name__}: {e}"); traceback.print_exc(); return False


def run_tests():
    print("\n=== System Validation Suite + Runner Unit Checks ===\n")
    results = []

    # ---- Runner unit coverage ----------------------------------------------

    def t_substitute_uuid():
        out = _substitute("hello $uuid", {})
        assert "hello " in out and len(out) > 8
        # Different each call
        out2 = _substitute("hello $uuid", {})
        assert out != out2
    results.append(test("SV-U1. $uuid expands and differs each call", t_substitute_uuid))

    def t_substitute_dotted():
        vars = {"foo": {"bar": {"baz": 42}}}
        assert _substitute("$foo.bar.baz", vars) == 42
        assert _substitute("id=$foo.bar.baz end", vars) == "id=42 end"
    results.append(test("SV-U2. Dotted variable lookup works", t_substitute_dotted))

    def t_substitute_recursive_dicts():
        vars = {"tc_id": 7, "thing": {"x": "hello"}}
        # A bare $var returns the raw value (preserves int), interpolation stringifies
        out = _substitute({"url": "/api/test-cases/$tc_id",
                           "body": {"id": "$tc_id", "x": "$thing.x"}}, vars)
        assert out == {"url": "/api/test-cases/7",
                       "body": {"id": 7, "x": "hello"}}, f"got {out!r}"
    results.append(test("SV-U3. Substitution recurses through dicts/lists", t_substitute_recursive_dicts))

    # ---- Run the canonical suite -------------------------------------------

    def t_run_canonical():
        suite = load_suite(SUITE_PATH)
        client = app.test_client()
        report = run_suite(suite, client=client)
        print()
        print(report.render())
        # Require no failures; skips are OK
        assert report.failed == 0, f"{report.failed} test(s) failed in system suite"
        assert report.passed > 0, "suite produced zero passes"
    results.append(test("SV-1. Canonical suite runs green (skips allowed, failures aren't)",
                        t_run_canonical))

    passed = sum(results); total = len(results)
    print(f"\n{'='*40}\nResults: {passed}/{total} passed")
    print("ALL SYSTEM VALIDATION TESTS PASSED" if passed == total
          else f"{total - passed} FAILED")
    return passed == total


if __name__ == "__main__":
    sys.exit(0 if run_tests() else 1)
