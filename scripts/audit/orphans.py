#!/usr/bin/env python3
"""RUN 2 PASS 1e — orphan ROUTES and orphan TEMPLATES, from the link graph the
repaired sweep builds (scripts/deadlinks_gate.py) plus every redirect target
and fetch literal in Python.

An orphan route: a rule in the LIVE url_map that no template attribute, JS
literal, Python redirect(), url_for or hard-coded path string references — a
route reachable only by typing its URL. Reported with its gate, so a
reachable-but-unlinked ACT is visible. An orphan template: a file under
primeqa/templates that no render_template / include / extends / import names.

Usage: DATABASE_URL=... JWT_SECRET=... CREDENTIAL_ENCRYPTION_KEY=... python scripts/audit/orphans.py > orphans.json
"""
from __future__ import annotations

import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO); sys.path.insert(0, os.path.join(REPO, "scripts"))
import deadlinks_gate as dl  # noqa: E402

PY_PATH_RX = re.compile(r"""['"](/(?:api/|settings/|releases|requirements|claims|runs|plans|results|sections|groups|environments|connections|users|ask|dashboard|shared|setup|login|logout|health|milestones|standards|reports|coverage|suites)[^'"\s]*)['"]""")
ENDPOINT_RX = re.compile(r"""url_for\(\s*['"]([A-Za-z0-9_.]+)['"]""")


def main():
    from primeqa.app import create_app
    app = create_app()
    rules = [r for r in app.url_map.iter_rules() if r.endpoint != "static"]
    # 1. every referenced path: templates + static JS (the sweep's collector) + Python string literals
    referenced = set()
    for t in dl.collect(os.path.join(REPO, "primeqa", "templates"), os.path.join(REPO, "primeqa", "static")):
        if t.kind == "url_for":
            referenced.add("endpoint:" + t.endpoint); continue
        raw = t.raw.strip()
        if raw.startswith("/"):
            for b in dl._branches(raw):
                referenced.add(dl._normalise(b))
    # Python: redirect() targets are real links; any OTHER string literal that
    # looks like a path is a WEAK reference (a docstring, a log line, a test
    # string) and is reported separately, never counted as a link.
    redirect_refs, weak_refs = set(), {}
    REDIRECT_RX = re.compile(r"""redirect\(\s*f?['"](/[^'"\s]*)['"]""")
    for root, _, files in os.walk(os.path.join(REPO, "primeqa")):
        for f in files:
            if not f.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(root, f), REPO)
            src = open(os.path.join(root, f), encoding="utf-8", errors="replace").read()
            for m in REDIRECT_RX.findall(src):
                redirect_refs.add(dl._normalise(re.sub(r"\{[^}]*\}|%[sd]", dl.WILD, m)))
            if rel.endswith("core/navigation.py"):
                # the nav renders its hrefs from this module's data: real links
                for m in re.findall(r"""["'](/[a-z][^"'\s]*)["']""", src):
                    redirect_refs.add(dl._normalise(m))
            for m in PY_PATH_RX.findall(src):
                weak_refs.setdefault(dl._normalise(re.sub(r"\{[^}]*\}|%[sd]", dl.WILD, m)), set()).add(rel)
            for ep in ENDPOINT_RX.findall(src):
                referenced.add("endpoint:" + ep)
    referenced |= redirect_refs
    # 2. resolve every referenced path to the rules it hits
    res = dl.Resolver(app.url_map)
    hit_rules = set()
    for ref in referenced:
        if ref.startswith("endpoint:"):
            hit_rules |= {str(r.rule) for r in rules if r.endpoint == ref[9:]}
            continue
        probe = ref.replace(dl.WILD, "1")
        for rx, rule, methods in res.rules:
            if rx.match(probe):
                hit_rules.add(rule)
    # the sweep's own probe value "1" cannot match uuid/other converters: also match structurally
    for ref in referenced:
        if ref.startswith("endpoint:"):
            continue
        segs = ref.split("/")
        for r in rules:
            rs = (str(r.rule).rstrip("/") or "/").split("/")
            if len(rs) != len(segs):
                continue
            if all(b == a or b.startswith("<") and a == dl.WILD or (b.startswith("<") and a not in ("",)) for a, b in zip(segs, rs)) and any(b.startswith("<") for b in rs):
                # a wildcard segment against a parameter segment, literals equal
                if all((b == a) or b.startswith("<") for a, b in zip(segs, rs)):
                    hit_rules.add(str(r.rule))
    # 2b. the weak references resolve the same way, into their own set
    weak_hit = {}
    for ref, files in weak_refs.items():
        segs = ref.split("/")
        for r in rules:
            rs = (str(r.rule).rstrip("/") or "/").split("/")
            if len(rs) == len(segs) and all((b == a) or b.startswith("<") for a, b in zip(segs, rs)):
                weak_hit.setdefault(str(r.rule), set()).update(files)
    # 3. orphan routes, with their gate (from the closure the decorator keeps — the real gate)
    import inspect
    from primeqa.core.authz import Tier

    def gate_of(fn):
        """The gate the wrappers actually carry: walk __wrapped__ AND every
        function held in a closure cell (a decorator's inner function lives
        in a cell, not on the __wrapped__ chain), collecting Tier values and
        role tuples. Names are never read (wraps() copies names, not gates)."""
        seen, found, stack = set(), [], [fn]
        while stack:
            cur = stack.pop()
            if cur is None or id(cur) in seen or len(seen) > 40:
                continue
            seen.add(id(cur))
            for cell in (getattr(cur, "__closure__", None) or []):
                try:
                    v = cell.cell_contents
                except ValueError:
                    continue
                if isinstance(v, Tier):
                    found.append(v.name)
                elif isinstance(v, (tuple, list)) and v and all(isinstance(x, str) for x in v) and set(v) <= {"viewer", "ba", "tester", "admin", "superadmin"}:
                    found.append("roles:" + ",".join(v))
                elif callable(v) and hasattr(v, "__code__"):
                    stack.append(v)
            w = getattr(cur, "__wrapped__", None)
            if w is not None:
                stack.append(w)
        return sorted(set(found)) or ["OPEN? (no tier or role in any closure)"]
    orphan_routes = []
    for r in sorted(rules, key=lambda x: str(x.rule)):
        if str(r.rule) in hit_rules:
            continue
        fn = app.view_functions.get(r.endpoint)
        orphan_routes.append({"rule": str(r.rule), "methods": sorted(m for m in r.methods if m not in ("HEAD", "OPTIONS")),
                              "endpoint": r.endpoint, "gate": gate_of(fn) if fn else "?",
                              "module": getattr(fn, "__module__", "?"), "line": (inspect.getsourcelines(inspect.unwrap(fn))[1] if fn else None),
                              "weakly_referenced_by": sorted(weak_hit.get(str(r.rule), ()))})
    # 4. orphan templates
    tdir = os.path.join(REPO, "primeqa", "templates")
    all_t = set()
    for root, _, files in os.walk(tdir):
        for f in files:
            if f.endswith(".html"):
                all_t.add(os.path.relpath(os.path.join(root, f), tdir))
    named = set()
    rx = re.compile(r"""(?:render_template\(\s*|{%\s*(?:include|extends|import|from)\s+)['"]([^'"]+\.html)['"]""")
    for root, _, files in os.walk(os.path.join(REPO, "primeqa")):
        for f in files:
            if f.endswith((".py", ".html")):
                named |= set(rx.findall(open(os.path.join(root, f), encoding="utf-8", errors="replace").read()))
    dynamic = []
    for root, _, files in os.walk(os.path.join(REPO, "primeqa")):
        for f in files:
            if f.endswith(".py"):
                src = open(os.path.join(root, f), encoding="utf-8", errors="replace").read()
                dynamic += [f"{os.path.relpath(os.path.join(root, f), REPO)}: {m}" for m in re.findall(r"render_template\(\s*[^'\"\s)][^)]{0,60}", src)]
    orphan_templates = sorted(all_t - named)
    json.dump({"rules": len(rules), "referenced_paths": len(referenced), "linked_rules": len(hit_rules),
               "orphan_routes": orphan_routes, "orphan_templates": orphan_templates,
               "dynamic_render_calls": dynamic}, sys.stdout, indent=1)


if __name__ == "__main__":
    main()
