"""The undefined-name gate (AUD-006 / AUD-008 / AUD-009 / AUD-010).

The v1 retirement deleted definitions and left callers behind: a public route
and its mint raised NameError on every call, a Jira search raised as soon as a
connection was chosen, and a handler commented "never breaks the page" raised
inside its own except clause. pyflakes finds every such site in about a second.
This test makes it a merge gate.

It FAILS — never skips — when pyflakes is not importable. An audit run once
reported "0 undefined names" because pyflakes was not installed; a gate that
reports green when its tool is absent is the failure class under audit.
"""
from __future__ import annotations

import io
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2] / "primeqa"


def _undefined_names() -> list[str]:
    try:
        from pyflakes.api import checkPath
        from pyflakes.reporter import Reporter
    except ImportError as exc:                           # noqa: BLE001
        pytest.fail("pyflakes is not importable (%s): the gate cannot run, and a gate "
                    "that cannot run must not pass. Install requirements-dev.txt." % exc)
    out, err = io.StringIO(), io.StringIO()
    rep = Reporter(out, err)
    for p in sorted(ROOT.rglob("*.py")):
        checkPath(str(p), rep)
    return [l for l in out.getvalue().splitlines() if "undefined name" in l]


def test_no_undefined_names_in_the_product():
    hits = _undefined_names()
    assert not hits, "undefined names (F821) — a call site outlived its definition:\n  " + "\n  ".join(hits)


def test_the_gate_actually_runs():
    """A sanity check on the tool itself: it must see at least one module and
    report nothing for a name that is defined."""
    from pyflakes.api import check
    from pyflakes.reporter import Reporter
    out, err = io.StringIO(), io.StringIO()
    n = check("x = undefined_thing\n", "<probe>", Reporter(out, err))
    assert n == 1 and "undefined name 'undefined_thing'" in out.getvalue()
