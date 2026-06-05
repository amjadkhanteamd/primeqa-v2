"""Knowledge admin console (UI Area 7 slice 7a, D-176).

Tests the v1 read-only bridge over the S5 knowledge substrate. The system-rules
and domain-packs channels are file-backed (no DB), so these run hermetically; the
learned-rules channel (which hits the v1 feedback signals) is patched out so the
test never touches a database.

Run: python tests/test_knowledge_console.py
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from primeqa.intelligence import knowledge_console as kc


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def test_system_rules_reads_real_file():
    sr = kc._read_system_rules()
    _assert(sr["available"] is True, "system rules should be available")
    _assert(sr["count"] >= 1 and sr["count"] == len(sr["rules"]), "count matches rows")
    r = sr["rules"][0]
    for k in ("id", "object_name", "field_name", "category", "rule_text",
              "confidence", "source"):
        _assert(k in r, f"system rule missing key {k}")
    # sorted by category precedence (field_behaviour < operation < assertion)
    order = {"field_behaviour": 0, "operation": 1, "assertion": 2}
    ranks = [order.get(x["category"], 9) for x in sr["rules"]]
    _assert(ranks == sorted(ranks), "rules not category-ordered")


def test_domain_packs_reads_real_files():
    dp = kc._read_domain_packs()
    _assert(dp["available"] is True, "domain packs should be available")
    _assert(dp["count"] >= 1 and dp["count"] == len(dp["packs"]), "count matches rows")
    p = dp["packs"][0]
    for k in ("id", "title", "keywords", "objects", "version",
              "measured_tokens", "filename"):
        _assert(k in p, f"pack missing key {k}")
    # filename is a basename, never an absolute deploy path (no leak)
    _assert("/" not in p["filename"] and p["filename"].endswith(".md"),
            f"pack filename should be a basename: {p['filename']}")
    _assert(p["measured_tokens"] >= 1, "measured_tokens computed")


def test_domain_packs_missing_dir_is_graceful():
    # A missing pack dir returns available=True, count=0 (feature stays off, no raise).
    with patch.object(kc, "_PACKS_DIR", "/nonexistent/packs/dir/xyz"):
        dp = kc._read_domain_packs()
    _assert(dp["available"] is True and dp["count"] == 0, "missing dir → empty, available")


def test_learned_rules_best_effort_on_failure():
    # If the feedback read blows up, the channel degrades, never raises.
    with patch("primeqa.intelligence.llm.feedback_rules.build_rules_block",
               side_effect=RuntimeError("db down")):
        lr = kc._read_learned_rules(1)
    _assert(lr["available"] is False and lr["block"] == "" and lr["has_signals"] is False,
            "learned rules should degrade to unavailable on error")


def test_learned_rules_no_signals_is_available_empty():
    with patch("primeqa.intelligence.llm.feedback_rules.build_rules_block",
               return_value=""):
        lr = kc._read_learned_rules(1)
    _assert(lr["available"] is True and lr["has_signals"] is False,
            "empty block → available, no signals")


def test_overview_shape_never_raises():
    # The top-level read assembles all three channels; learned patched to avoid DB.
    with patch.object(kc, "_read_learned_rules",
                      return_value={"available": True, "block": "", "has_signals": False}):
        ov = kc.get_knowledge_overview(1)
    for k in ("system_rules", "domain_packs", "learned_rules", "packs_dir"):
        _assert(k in ov, f"overview missing key {k}")
    _assert(ov["system_rules"]["available"] and ov["domain_packs"]["available"],
            "file channels available in overview")


def run_tests():
    tests = [
        test_system_rules_reads_real_file,
        test_domain_packs_reads_real_files,
        test_domain_packs_missing_dir_is_graceful,
        test_learned_rules_best_effort_on_failure,
        test_learned_rules_no_signals_is_available_empty,
        test_overview_shape_never_raises,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            print(f"  FAIL  {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
    return passed == len(tests)


if __name__ == "__main__":
    sys.exit(0 if run_tests() else 1)
