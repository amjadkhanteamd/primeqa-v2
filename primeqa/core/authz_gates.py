"""The LIVE authorization gate reader (D-245 oracle, rebuilt in round 3 for
AUD-001; moved here from ``scripts/authz_inventory.py`` in round 4 so the
runtime can ask the same question the audit asks).

A gate is recognised by the CODE OBJECT the decorator produces — never by a
name, which ``functools.wraps`` copies and an alias hides. ``gates_of(view)``
walks the view function through ``__wrapped__`` AND every function held in a
closure cell (a decorator's inner function lives in a cell, not on the
``__wrapped__`` chain) and returns ``(gates, min_tier)``; ``min_tier_by_rule``
answers the declared minimum Tier of every url_map rule once, for the request
log (AUD-034) and the inventory script alike.
"""
from __future__ import annotations

NONE = "(none)"


def gate_codes() -> dict:
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


def walk(fn):
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


def tier_in_closure(fn):
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
    codes = codes or gate_codes()
    gates, tiers = [], []
    for f in walk(view_fn):
        name = codes.get(f.__code__)
        if name is None:
            continue
        if name in ("require_tier", "require_tier_api"):
            t = tier_in_closure(f)
            if t is not None:
                tiers.append(t)
                gates.append(f"{name}({t.name})")
                continue
        gates.append(name)
    gates = sorted(set(gates))
    return gates, (max(tiers) if tiers else None)


def min_tier_by_rule(app) -> dict:
    """``{(rule, method): tier name | 'AUTH' | None}`` for every url_map rule:
    the declared minimum Tier of a ladder gate; ``'AUTH'`` for a route whose
    only gate is authentication (``require_auth`` / ``login_required``, the
    tier decided in the route body); ``None`` for an anonymous route."""
    codes = gate_codes()
    out = {}
    for r in app.url_map.iter_rules():
        if r.endpoint == "static":
            continue
        gates, tier = gates_of(app.view_functions[r.endpoint], codes)
        label = tier.name if tier is not None else ("AUTH" if gates else None)
        for m in r.methods:
            if m not in ("HEAD", "OPTIONS"):
                out[(str(r.rule), m)] = label
    return out
