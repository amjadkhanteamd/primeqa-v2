#!/usr/bin/env python3
"""Dead-link sweep, repo-wide: every internal target in templates and static
JS — href, action, hx-*, data-url, data-confirm-form, fetch()/location literals
— resolved against the LIVE url_map. The existing unit sweep checks `href=`
only; this checks every attribute a browser or htmx will actually follow.

Jinja-bearing targets have `{{ ... }}` collapsed to a wildcard segment.

Usage: python scripts/audit/deadlinks.py url_map.json  (from the inventory)
"""
from __future__ import annotations

import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ATTRS = r"(href|action|hx-get|hx-post|hx-put|hx-patch|hx-delete|data-url|data-href|data-confirm-form)"
ATTR_RX = re.compile(ATTRS + r'="(/[^"]*)"')
JS_RX = re.compile(r"""(?:fetch|open|location\.href\s*=|window\.location\s*=|location\.assign)\s*\(?\s*['"`](/[^'"`]*)['"`]""")
TEMPLATE_RX = re.compile(r"\{\{[^}]*\}\}|\{%[^%]*%\}")


def _rule_regex(rule: str):
    """`/a/<int:x>/b` -> `^/a/[^/]+/b/?$`: escape the literal parts only."""
    parts = re.split(r"<[^>]+>", rule)
    return re.compile("^" + "[^/]+".join(re.escape(p) for p in parts) + "/?$")


def _rules(url_map_path):
    return [(_rule_regex(r["rule"]), r) for r in json.load(open(url_map_path))]


IF_RX = re.compile(r"\{%\s*if[^%]*%\}(.*?)\{%\s*endif\s*%\}", re.S)


def _variants(target: str) -> list[str]:
    """A `{% if %}...{% endif %}` inside a target yields BOTH branches."""
    m = IF_RX.search(target)
    if not m:
        return [target]
    with_, without = target[:m.start()] + m.group(1) + target[m.end():], target[:m.start()] + target[m.end():]
    return _variants(with_) + _variants(without)


def _normalise(target: str) -> str:
    t = re.sub(r"\{\{[^}]*\}\}", "__J__", target)
    t = t.split("?", 1)[0].split("#", 1)[0]
    t = re.sub(r"__J__[^/]*", "__J__", t)
    return t


def main():
    rules = _rules(sys.argv[1])
    hits = {}
    for root, _, files in os.walk(os.path.join(REPO, "primeqa", "templates")):
        for f in files:
            p = os.path.join(root, f)
            for i, line in enumerate(open(p, encoding="utf-8"), 1):
                for _, t in ATTR_RX.findall(line):
                    hits.setdefault(t, []).append("%s:%d" % (p.split("/primeqa/")[-1], i))
                for t in JS_RX.findall(line):
                    hits.setdefault(t, []).append("%s:%d" % (p.split("/primeqa/")[-1], i))
    for root, _, files in os.walk(os.path.join(REPO, "primeqa", "static")):
        for f in files:
            if not f.endswith(".js"):
                continue
            p = os.path.join(root, f)
            for i, line in enumerate(open(p, encoding="utf-8", errors="replace"), 1):
                for t in JS_RX.findall(line):
                    hits.setdefault(t, []).append("%s:%d" % (p.split("/primeqa/")[-1], i))

    dead, alive = [], 0
    for target, where in sorted(hits.items()):
        variants = [_normalise(v) for v in _variants(target)]
        if any(v.startswith("/static/") for v in variants):
            alive += 1
            continue
        if all(any(rx.match(v.replace("__J__", "1")) for rx, _ in rules) for v in variants):
            alive += 1
        else:
            dead.append({"target": target, "variants": variants, "where": where[:5], "count": len(where)})
    print(json.dumps({"targets": len(hits), "alive": alive, "dead": dead}, indent=1))


if __name__ == "__main__":
    main()
