"""Unit tests for scripts/check_decision_numbers.py (the decision-number
race guard). Pure — temp files only, never the real ledger."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "scripts"))

from check_decision_numbers import check_lines, main  # noqa: E402


def _write(tmp_path, text):
    p = tmp_path / "ledger.md"
    p.write_text(text, encoding="utf-8")
    return str(p)


CLEAN = """# Ledger
## D-458 — the baseline entry
## D-459 — a new decision
## D-460 — another new decision
"""


def test_clean_file_exits_0(tmp_path, capsys):
    assert main(["--file", _write(tmp_path, CLEAN)]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1 and "OK" in out[0]  # one line, nothing else


def test_duplicate_above_baseline_without_marker_exits_1(tmp_path, capsys):
    text = CLEAN + "## D-459 — a different decision reusing the number\n"
    assert main(["--file", _write(tmp_path, text)]) == 1
    out = capsys.readouterr().out
    assert "COLLISION on D-459" in out
    assert "## D-459 — a new decision" in out          # both headings printed
    assert "reusing the number" in out


def test_duplicate_above_baseline_with_marker_exits_0(tmp_path):
    for marker in ["(close)", "CLOSE:", "(design)", "Result", "REALIZED",
                   "(cont.)"]:
        text = CLEAN + f"## D-459 {marker} — follow-up of the same decision\n"
        assert main(["--file", _write(tmp_path, text)]) == 0, marker


def test_skipped_number_exits_0(tmp_path):
    text = "## D-459 — a decision\n## D-462 — skipped 460 and 461\n"
    assert main(["--file", _write(tmp_path, text)]) == 0


def test_decimal_number_heading_does_not_crash(tmp_path):
    text = ("## D-459 — a decision\n"
            "## D-459.1 — its sub-decision (a distinct dotted number)\n"
            "## D-110.3 — an old dotted number, below the baseline\n")
    assert main(["--file", _write(tmp_path, text)]) == 0


def test_unparseable_heading_exits_1_and_names_line(tmp_path, capsys):
    text = "## D-459 — fine\n\n## D-abc — the number does not parse\n"
    assert main(["--file", _write(tmp_path, text)]) == 1
    out = capsys.readouterr().out
    assert "UNPARSEABLE heading at line 3" in out


def test_duplicate_at_or_below_baseline_exits_0(tmp_path):
    # The six historical design/close pairs (and the repaired races) live
    # at or below the baseline and are never judged.
    text = ("## D-179 — S1 enrichment provider keys resolve from per-env connections\n"
            "## D-179 — a duplicate with no marker, but at/below baseline\n"
            "## D-458 — the baseline number itself\n"
            "## D-458 — duplicated too, still not judged\n")
    assert main(["--file", _write(tmp_path, text)]) == 0


def test_missing_file_exits_1(tmp_path, capsys):
    assert main(["--file", str(tmp_path / "absent.md")]) == 1
    assert "FAIL: cannot read" in capsys.readouterr().out


def test_template_placeholder_line_is_not_an_entry(tmp_path):
    # The ledger preamble documents the entry format with a literal
    # '## D-NNN — <One-line decision>' line; it is grammar, not an entry.
    text = "## D-NNN — <One-line decision>\n## D-459 — a real decision\n"
    assert main(["--file", _write(tmp_path, text)]) == 0


def test_check_lines_pure_baseline_override():
    lines = ["## D-448 — first\n", "## D-448 — second, no marker\n"]
    assert check_lines(lines, baseline="447")      # judged: collision
    assert not check_lines(lines, baseline="448")  # at baseline: not judged
