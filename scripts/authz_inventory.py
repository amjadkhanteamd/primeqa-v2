#!/usr/bin/env python3
"""Authorization inventory extractor (authz-role-ladder, D-245).

Walks every Flask route file (primeqa/views.py + primeqa/**/routes.py),
parses with ``ast``, and emits one row per route:
``{file:line, function, methods, route_path, gate decorators}``.

The gate decorators recognised are the full authorization census:
``require_auth``, ``login_required``, ``require_role``, ``role_required``,
``require_permission``, ``require_page_permission``, ``require_run_permission``,
plus inline ``require_env_policy`` if present as a decorator.

Used as the Phase-5 regression oracle: run BEFORE the teardown and AFTER, and
diff — no route may drop to login-only / auth-only.

Usage:  python scripts/authz_inventory.py          # markdown table to stdout
"""
from __future__ import annotations

import ast
import glob
import os

GATE_NAMES = {
    "require_auth",
    "login_required",
    "require_role",
    "role_required",
    "require_tier",          # D-245 new role-ladder gate (web)
    "require_tier_api",      # D-245 new role-ladder gate (API)
    "require_permission",
    "require_page_permission",
    "require_run_permission",
    "require_env_policy",
}

# The role gates that survive the Phase-5 permission-layer deletion. A route that
# carries one of these is NOT at risk of dropping to login-only.
ROLE_GATES = {"require_role", "role_required", "require_tier", "require_tier_api"}
PERMISSION_GATES = {"require_permission", "require_page_permission", "require_run_permission"}

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _route_files() -> list[str]:
    files = [os.path.join(REPO, "primeqa", "views.py")]
    files += sorted(glob.glob(os.path.join(REPO, "primeqa", "**", "routes.py"), recursive=True))
    return [f for f in files if os.path.exists(f)]


def _literal(node) -> str:
    try:
        return ast.literal_eval(node)
    except Exception:
        try:
            return ast.unparse(node)
        except Exception:
            return "?"


def _decorator_name(dec) -> str | None:
    """Return the bare callable name of a decorator (handles Call + Attribute)."""
    target = dec.func if isinstance(dec, ast.Call) else dec
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _is_route_decorator(dec) -> bool:
    return isinstance(dec, ast.Call) and _decorator_name(dec) == "route"


def _route_meta(dec) -> tuple[str, str]:
    path = _literal(dec.args[0]) if dec.args else "?"
    methods = ["GET"]
    for kw in dec.keywords:
        if kw.arg == "methods":
            try:
                methods = list(ast.literal_eval(kw.value))
            except Exception:
                methods = [ast.unparse(kw.value)]
    return str(path), ",".join(methods)


def _gate_repr(dec) -> str | None:
    name = _decorator_name(dec)
    if name not in GATE_NAMES:
        return None
    if isinstance(dec, ast.Call):
        args = [_literal(a) for a in dec.args]
        kws = [f"{k.arg}={_literal(k.value)}" for k in dec.keywords]
        inner = ", ".join(repr(a) if isinstance(a, str) else str(a) for a in args)
        if kws:
            inner = (inner + ", " if inner else "") + ", ".join(kws)
        return f"{name}({inner})"
    return name


def _collect_gates(func_node) -> list[str]:
    """Effective gates = the route function's own gate decorators PLUS gate
    decorators on nested functions (the ``@require_page_permission`` on the
    inner ``_render()`` closure pattern used throughout views.py)."""
    gates: list[str] = []
    for d in func_node.decorator_list:
        g = _gate_repr(d)
        if g:
            gates.append(g)
    for child in ast.walk(func_node):
        if child is func_node:
            continue
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in child.decorator_list:
                g = _gate_repr(d)
                if g:
                    gates.append(f"{g} [inner:{child.name}]")
    return gates


def extract() -> list[dict]:
    rows: list[dict] = []
    for path in _route_files():
        rel = os.path.relpath(path, REPO)
        with open(path) as fh:
            tree = ast.parse(fh.read(), filename=path)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            route_decs = [d for d in node.decorator_list if _is_route_decorator(d)]
            if not route_decs:
                continue
            gates = _collect_gates(node)
            for rdec in route_decs:
                route_path, methods = _route_meta(rdec)
                rows.append({
                    "file": rel,
                    "line": node.lineno,
                    "func": node.name,
                    "methods": methods,
                    "route": route_path,
                    "gates": gates or ["(none — auth/login only or open)"],
                })
    rows.sort(key=lambda r: (r["file"], r["line"]))
    return rows


def main() -> None:
    rows = extract()
    print("# Authorization inventory — BEFORE (authz-role-ladder branch point)")
    print()
    print(f"Generated by `scripts/authz_inventory.py`. {len(rows)} route handlers across "
          f"{len(set(r['file'] for r in rows))} files.")
    print()
    print("| file:line | func | methods | route | gate decorator(s) |")
    print("|---|---|---|---|---|")
    for r in rows:
        gates = "<br>".join(r["gates"])
        print(f"| {r['file']}:{r['line']} | `{r['func']}` | {r['methods']} | "
              f"`{r['route']}` | {gates} |")


if __name__ == "__main__":
    main()
