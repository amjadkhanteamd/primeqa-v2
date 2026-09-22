"""Round 4, part C (AUD-053) — the CLASS GUARD for AUD-041's kind of defect.

Since D-497 every undo of a declared act (a waiver's revocation, a target's
removal, a surface's unlink) refuses an empty reason in its service and at its
table. A form that sends ``<input type="hidden" name="reason" value="">`` is a
button that can never work. AUD-041 found two such forms (the waiver
revocations); AUD-053 found two more (Remove target, Unlink surface). This
sweep fails on ANY hidden ``reason`` input with an empty value, in ANY
template — RED on main naming both AUD-053 forms — and is shown able to fail
on a planted one while ignoring a visible reason field.
"""
from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.unit

TEMPLATES = pathlib.Path(__file__).resolve().parents[2] / "primeqa" / "templates"
HIDDEN_EMPTY_REASON = re.compile(
    r"<input\b(?=[^>]*\btype=[\"']hidden[\"'])(?=[^>]*\bname=[\"']reason[\"'])(?:(?![^>]*\bvalue=[\"'][^\"']+[\"'])[^>]*)>",
    re.I)


def hidden_empty_reason_inputs(root: pathlib.Path) -> list[str]:
    out = []
    for path in sorted(root.rglob("*.html")):
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if HIDDEN_EMPTY_REASON.search(line):
                out.append(f"{path.relative_to(root)}:{i}")
    return out


def test_no_form_sends_a_hidden_empty_reason():
    hits = hidden_empty_reason_inputs(TEMPLATES)
    assert hits == [], ("these forms send a hidden empty reason the service refuses (D-497) — "
                        "the button can never work:\n  " + "\n  ".join(hits))


def test_the_sweep_finds_a_planted_form_and_ignores_a_visible_reason(tmp_path):
    (tmp_path / "dead.html").write_text('<form>{{ csrf_input | safe }}<input type="hidden" name="reason" value=""><button>Revoke</button></form>\n')
    (tmp_path / "dead2.html").write_text("<form><input name='reason' type='hidden'><button>Remove</button></form>\n")
    (tmp_path / "alive.html").write_text('<form><input type="text" name="reason" required minlength="3"><button>Revoke</button></form>\n'
                                         '<input type="hidden" name="reason" value="{{ prefilled }}">\n')
    assert hidden_empty_reason_inputs(tmp_path) == ["dead.html:1", "dead2.html:1"]
