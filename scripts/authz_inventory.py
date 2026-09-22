#!/usr/bin/env python3
"""Authorization inventory — the LIVE oracle (authz-role-ladder, D-245; rebuilt
in round 3 for AUD-001).

The first oracle read decorator NAMES out of the AST, so a gate imported
under an alias (``from primeqa.core.auth import require_auth as
_require_auth_api``) was invisible: three authenticated routes were reported
as "(none — auth/login only or open)" while they carried the gate. An oracle
that can be green while a route is open, or red while it is gated, is a
self-reporting control.

This one derives every gate from the RUNNING app: it imports the Flask app,
walks each endpoint's view function through ``__wrapped__`` AND every function
held in a closure cell (a decorator's inner function lives in a cell, not on
the ``__wrapped__`` chain), and recognises a gate by the CODE OBJECT of the
wrapper it produces — never by a name, which ``functools.wraps`` copies and an
alias hides. The Tier a ladder gate enforces is read from the closure cell that
holds it. ``role_required`` / ``require_role`` are thin wrappers over the
ladder and report as the tier they enforce.

Rows: ``{endpoint, methods, rule, file:line, gates, min_tier}``. A route with
no recognised gate reports ``(none)`` — the guard
``tests/unit/test_authz_oracle.py`` holds that set to the named anonymous
routes and fails the day another joins it.

Usage:  python scripts/authz_inventory.py          # markdown table to stdout
        python scripts/authz_inventory.py --json    # rows as JSON
"""
from __future__ import annotations

import inspect
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

NONE = "(none)"


def _gate_codes() -> dict:
    """The code objects the gate decorators produce, keyed by gate name —
    computed by applying each decorator to a throwaway function, so an alias
    or a rename at a call site changes nothing here.

    Shape of the ladder gates: ``require_tier_api(min)(f)`` returns
    ``require_auth``'s wrapper around the TIER wrapper (``@wraps(f)
    @require_auth def decorated``); ``wraps`` points the outer's
    ``__wrapped__`` straight at ``f``, so the tier wrapper is reached through
    the outer's closure cell, not its ``__wrapped__``. The web ladder gate has
    the same shape over ``login_required``."""
    from primeqa.core import auth as A
    from primeqa.core.authz import Tier
    from primeqa import views as V

    def probe():  # pragma: no cover - never called
        return None

    auth_code = A.require_auth(probe).__code__
    login_code = V.login_required(probe).__code__
    codes = {auth_code: "require_auth", login_code: "login_required"}

    def inner_of(outer, outer_code):
        for cell in (getattr(outer, "__closure__", None) or []):
            try:
                v = cell.cell_contents
            except ValueError:
                continue
            if callable(v) and hasattr(v, "__code__") and v.__code__ is not outer_code and v is not probe:
                return v
        raise RuntimeError("the ladder gate's tier wrapper was not found in the outer wrapper's closure")

    codes[inner_of(A.require_tier_api(Tier.MEMBER)(probe), auth_code).__code__] = "require_tier_api"
    codes[inner_of(V.require_tier(Tier.MEMBER)(probe), login_code).__code__] = "require_tier"
    return codes


def _walk(fn):
    """Every function reachable from ``fn`` through ``__wrapped__`` and closure cells."""
    seen, stack, out = set(), [fn], []
    while stack:
        cur = stack.pop()
        if cur is None or id(cur) in seen or len(seen) > 60:
            continue
        seen.add(id(cur))
        if hasattr(cur, "__code__"):
            out.append(cur)
        for cell in (getattr(cur, "__closure__", None) or []):
            try:
                v = cell.cell_contents
            except ValueError:
                continue
            if callable(v) and hasattr(v, "__code__"):
                stack.append(v)
        w = getattr(cur, "__wrapped__", None)
        if w is not None:
            stack.append(w)
    return out


def _tier_in_closure(fn):
    from primeqa.core.authz import Tier
    for cell in (getattr(fn, "__closure__", None) or []):
        try:
            v = cell.cell_contents
        except ValueError:
            continue
        if isinstance(v, Tier):
            return v
    return None


def gates_of(view_fn, codes=None) -> tuple[list[str], object]:
    """``(gates, min_tier)`` for one live view function. ``gates`` names each
    recognised wrapper (a ladder gate with its Tier); ``min_tier`` is the
    highest Tier among the ladder gates, or None."""
    codes = codes or _gate_codes()
    gates, tiers = [], []
    for f in _walk(view_fn):
        name = codes.get(f.__code__)
        if name is None:
            continue
        if name in ("require_tier", "require_tier_api"):
            t = _tier_in_closure(f)
            if t is not None:
                tiers.append(t)
                gates.append(f"{name}({t.name})")
                continue
        gates.append(name)
    gates = sorted(set(gates))
    return gates, (max(tiers) if tiers else None)


def extract(app=None) -> list[dict]:
    """One row per url_map rule of the live app (static excluded)."""
    if app is None:
        from primeqa.app import app as _app
        app = _app
    codes = _gate_codes()
    rows = []
    for r in app.url_map.iter_rules():
        if r.endpoint == "static":
            continue
        fn = app.view_functions[r.endpoint]
        gates, tier = gates_of(fn, codes)
        target = inspect.unwrap(fn)
        try:
            src = os.path.relpath(inspect.getsourcefile(target), REPO)
            line = inspect.getsourcelines(target)[1]
        except (OSError, TypeError):
            src, line = "?", 0
        rows.append({
            "endpoint": r.endpoint,
            "methods": ",".join(sorted(m for m in r.methods if m not in ("HEAD", "OPTIONS"))),
            "rule": str(r.rule),
            "file": src, "line": line,
            "gates": gates or [NONE],
            "min_tier": tier.name if tier is not None else None,
        })
    rows.sort(key=lambda x: (x["rule"], x["methods"]))
    return rows


def main(argv=None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    rows = extract()
    if "--json" in argv:
        json.dump(rows, sys.stdout, indent=1)
        return
    print("# Authorization inventory — from the LIVE app (round 3, AUD-001)")
    print()
    print(f"Generated by `scripts/authz_inventory.py`. {len(rows)} url_map rules; "
          f"{sum(1 for r in rows if r['gates'] == [NONE])} with no recognised gate.")
    print()
    print("| rule | methods | file:line | gates | min tier |")
    print("|---|---|---|---|---|")
    for r in rows:
        print(f"| `{r['rule']}` | {r['methods']} | {r['file']}:{r['line']} | "
              f"{'<br>'.join(r['gates'])} | {r['min_tier'] or ''} |")


if __name__ == "__main__":
    main()
