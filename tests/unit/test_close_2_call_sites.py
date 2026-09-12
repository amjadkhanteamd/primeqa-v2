"""Close 2 — D-469's obligation: the seams and the call sites, bound together.

D-469's defect was a signature edit that silently failed to match while its
call-site edits applied, so every call raised TypeError at runtime. This slice
adds one keyword-only ``session=`` parameter to seven readers and passes it from
two views. That is exactly the shape D-469 warns about, so the guard is
mechanical: read the REAL signatures, and read the REAL call sites out of
``views.py`` with the AST — not a grep, and not an assertion in prose.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import pathlib

import pytest

VIEWS = pathlib.Path(__file__).resolve().parents[2] / "primeqa" / "views.py"

# (module, function) -> every console this slice taught to take a shared session
SEAMS = [
    ("primeqa.core.permissions", "pending_human_attention"),
    ("primeqa.intelligence.quarantine", "manual_states"),
    ("primeqa.intelligence.org_state_console", "org_state_for_request"),
    ("primeqa.intelligence.org_state_console", "read_org_state"),
    ("primeqa.intelligence.requirement_identity_console", "identity_overview"),
    ("primeqa.intelligence.s3_generation_console", "count_claims_by_requirement_status"),
    ("primeqa.intelligence.requirements_board_console", "board_rows"),
    ("primeqa.intelligence.release_substrate_console", "get_release_substrate"),
    ("primeqa.intelligence.substrate_decision", "get_release_substrate_decision"),
    ("primeqa.intelligence.substrate_decision", "release_scope_readiness"),
]

# the calls each view must make WITH the shared session
CALL_SITES = {
    "requirements_list": ["board_rows", "count_claims_by_requirement_status",
                          "identity_overview", "pending_human_attention",
                          "org_state_for_request"],
    "releases_detail": ["get_release_substrate", "get_release_substrate_decision",
                        "release_scope_readiness", "org_state_for_request"],
}


@pytest.mark.parametrize("mod,fn", SEAMS, ids=[f"{m.split('.')[-1]}.{f}" for m, f in SEAMS])
def test_the_seam_exists_and_defaults_to_off(mod, fn):
    """``session`` must be KEYWORD-ONLY and default to None, so every existing
    caller keeps the behaviour it had before this slice."""
    p = inspect.signature(getattr(importlib.import_module(mod), fn)).parameters
    assert "session" in p, f"{mod}.{fn} lost its session seam"
    assert p["session"].kind is inspect.Parameter.KEYWORD_ONLY
    assert p["session"].default is None


def _view(name):
    tree = ast.parse(VIEWS.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in views.py")


@pytest.mark.parametrize("view", sorted(CALL_SITES))
def test_the_view_hands_its_session_to_every_console(view):
    node = _view(view)
    passed, bare = {}, {}
    for c in ast.walk(node):
        if not isinstance(c, ast.Call):
            continue
        f = c.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
        if name not in CALL_SITES[view]:
            continue
        kw = {k.arg for k in c.keywords}
        (passed if "session" in kw else bare)[name] = c.lineno
    missing = [n for n in CALL_SITES[view] if n not in passed]
    assert not missing, f"{view} does not pass session= to {missing}"
    assert not bare, (
        f"{view} calls {sorted(bare)} without session= — a console left off the "
        f"render's connection at line(s) {sorted(bare.values())}")


def test_the_scope_is_bound_before_the_try_in_both_views():
    """A stack created inside the body leaves the finally raising
    UnboundLocalError on any earlier failure, MASKING the real error. That is
    not hypothetical: it is how this was caught, with a page reporting a name
    error instead of the missing table underneath it."""
    for view in CALL_SITES:
        node = _view(view)
        body = node.body
        tries = [i for i, st in enumerate(body) if isinstance(st, ast.Try)]
        assert tries, f"{view} has no try block"
        first_try = tries[0]
        binds = [i for i, st in enumerate(body)
                 if isinstance(st, ast.Assign)
                 and any(getattr(t, "id", None) == "_read" for t in st.targets)]
        assert binds, f"{view} never binds _read at function level"
        assert min(binds) < first_try, (
            f"{view} binds its ExitStack inside the try; the finally can raise "
            f"UnboundLocalError and mask the real error")
