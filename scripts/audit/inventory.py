#!/usr/bin/env python3
"""Route inventory from the LIVE Flask app, not from source text.

`scripts/authz_inventory.py` reads decorators out of the AST. That is a
self-report about gating: it sees names, not behaviour, and it misses any
alias (it reported three `@_require_auth_api` routes as open). This tool
boots the real app and walks each view's wrapper chain via `__wrapped__`, so
the gate it reports is the one that will actually run.

Usage: DATABASE_URL=... JWT_SECRET=... CREDENTIAL_ENCRYPTION_KEY=... \
       python scripts/audit/inventory.py > routes.json
"""
from __future__ import annotations

import inspect
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _chain(fn):
    """Names of every wrapper around the view, outermost first."""
    names = []
    seen = set()
    while fn is not None and id(fn) not in seen:
        seen.add(id(fn))
        q = getattr(fn, "__qualname__", "") or ""
        mod = getattr(fn, "__module__", "") or ""
        names.append("%s:%s" % (mod.split(".")[-1], q))
        fn = getattr(fn, "__wrapped__", None)
    return names


def _closure_gates(fn):
    """Gate decorators keep their arguments in closures; surface them."""
    found = []
    seen = set()
    while fn is not None and id(fn) not in seen:
        seen.add(id(fn))
        for cell in (getattr(fn, "__closure__", None) or ()):
            try:
                v = cell.cell_contents
            except ValueError:
                continue
            r = repr(v)
            if "Tier." in r or isinstance(v, tuple) and all(isinstance(x, str) for x in v):
                found.append(r)
        fn = getattr(fn, "__wrapped__", None)
    return found


def main():
    from primeqa.app import create_app
    app = create_app()
    out = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        view = app.view_functions[rule.endpoint]
        chain = _chain(view)
        wrappers = [c.split(":")[1].split(".")[0] for c in chain[:-1]]
        out.append({
            "rule": rule.rule,
            "endpoint": rule.endpoint,
            "methods": sorted(m for m in rule.methods if m not in ("HEAD", "OPTIONS")),
            "wrappers": wrappers,
            "gate_args": _closure_gates(view),
            "innermost": chain[-1] if chain else None,
            "source": "%s:%s" % (inspect.getsourcefile(inspect.unwrap(view)).split("/primeqa/")[-1],
                                 inspect.getsourcelines(inspect.unwrap(view))[1]),
        })
    json.dump(sorted(out, key=lambda r: r["rule"]), sys.stdout, indent=1)


if __name__ == "__main__":
    main()
