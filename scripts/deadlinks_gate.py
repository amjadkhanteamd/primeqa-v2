#!/usr/bin/env python3
"""deadlinks_gate.py — the dead-link sweep, repaired (AUD-002), as a merge gate.

The previous sweep (`tests/unit/test_step_6a_routes.py`) matched
`href="(/[^"{}\\s]*)"` — every href carrying a Jinja expression was excluded
by its own regex, so a parameterised link to a route that does not exist
(the release page's "View Reasoning" → `/releases/{{ release.id }}/decision`,
AUD-003) was invisible to the gate by construction. It also read `href`
only: form actions, htmx verbs and JS literals were never checked.

This sweep resolves EVERY internal target a browser or htmx will follow,
against the LIVE url_map (never a source-text guess):

  * attributes: href, action, formaction, hx-get / hx-post / hx-put /
    hx-patch / hx-delete, data-url, data-href, data-confirm-form;
  * `url_for('endpoint', kw=...)` anywhere in a template — the endpoint
    must exist and the keyword arguments must cover the rule's parameters
    (a missing one is a BuildError at render time);
  * JS literals in templates and static JS: fetch('/…'), location.href =
    '/…', window.location = '/…', location.assign('/…'), open('/…').

Jinja inside a target is collapsed to a wildcard SEGMENT (`{{ x }}` matches
one `<param>` of a rule; a segment mixing text and Jinja is wildcarded
whole); `{% if %}…{% endif %}` inside a target yields both branches and
every branch must resolve. The query string and fragment are ignored.
A target whose FIRST character is Jinja (a dynamic base) cannot be resolved
from source and is COUNTED as unresolvable, never as alive.

The METHOD is checked too: a form action or hx-post whose only matching
rule is GET-only is dead on submit.

Exit 0 with the counts when nothing is dead; exit 1 listing every dead
target otherwise. A sweep must be shown to fail before it is believed:
`tests/unit/test_no_dead_links.py` plants dead targets of every kind in a
temporary template tree and asserts this module reports each one.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ATTRS = ("href", "action", "formaction", "hx-get", "hx-post", "hx-put", "hx-patch", "hx-delete",
         "data-url", "data-href", "data-confirm-form")
ATTR_RX = re.compile(r'\b(' + "|".join(re.escape(a) for a in ATTRS) + r')\s*=\s*"([^"]*)"')
#: ANY data-* attribute whose value is a path. Round 3, part E: the graph named
#: three data attributes by hand, so a URL parked in a fourth (a script reads
#: `el.dataset.whatever` and fetches it) was invisible — the audit found its two
#: JS-built cases by eye, which is not a graph. A path in a data attribute is a
#: target; the attribute's name does not matter.
DATA_ATTR_RX = re.compile(r'\b(data-[a-z][a-z0-9-]*)\s*=\s*"(/[^"]*)"')
# what each attribute submits with; href/data-* follow as GET, the verbs as themselves
ATTR_METHOD = {"hx-post": "POST", "hx-put": "PUT", "hx-patch": "PATCH", "hx-delete": "DELETE"}
FORM_METHOD_RX = re.compile(r'\bmethod\s*=\s*"([A-Za-z]+)"')
JS_RX = re.compile(r"""(?:fetch|open|location\.href\s*=|window\.location\s*=|location\.assign)\s*\(?\s*['"`](/[^'"`]*)['"`]""")
#: htmx's programmatic call: htmx.ajax('GET', '/claims/' + id + '/panel', ...).
#: The drawer uses exactly this, and the audit found it by hand (AUD-025 noted
#: /claims/<id>/panel as "reached by a JS-built URL and not an orphan") — found
#: by eye, not by the graph. The verb is the method.
HTMX_AJAX_RX = re.compile(r"""htmx\.ajax\s*\(\s*['"]([A-Za-z]+)['"]\s*,\s*['"`](/[^'"`]*)['"`]""")
JS_METHOD_RX = re.compile(r"""method\s*:\s*['"]([A-Za-z]+)['"]""")
URL_FOR_RX = re.compile(r"""url_for\(\s*['"]([A-Za-z0-9_.]+)['"]\s*(?:,\s*([^)]*))?\)""")
KWARG_RX = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*=")
IF_RX = re.compile(r"\{%\s*if\b[^%]*%\}(.*?)\{%\s*endif\s*%\}", re.S)
JINJA_EXPR_RX = re.compile(r"\{\{.*?\}\}|\$\{[^}]*\}", re.S)   # Jinja {{ }} and JS template ${ }
JINJA_STMT_RX = re.compile(r"\{%.*?%\}", re.S)
WILD = "__J__"


@dataclass
class Target:
    kind: str            # attribute name, "url_for" or "js"
    raw: str
    where: str           # "templates/x.html:12"
    method: str = "GET"
    endpoint: str = ""   # url_for only
    kwargs: tuple = ()   # url_for only


@dataclass
class Report:
    targets: list = field(default_factory=list)
    alive: list = field(default_factory=list)
    dead: list = field(default_factory=list)          # (Target, reason)
    unresolvable: list = field(default_factory=list)  # dynamic base

    def as_dict(self) -> dict:
        return {"targets": len(self.targets), "alive": len(self.alive),
                "unresolvable": len(self.unresolvable), "dead": len(self.dead),
                "dead_list": [{"where": t.where, "kind": t.kind, "target": t.raw, "reason": r}
                              for t, r in self.dead],
                "unresolvable_list": [{"where": t.where, "kind": t.kind, "target": t.raw}
                                      for t in self.unresolvable]}


# --------------------------------------------------------------------------
# collection
# --------------------------------------------------------------------------

def _form_method_near(line: str, lines: list, i: int) -> str:
    """The <form method=...> a `action=` belongs to: on the same line, else
    on the nearest line above that opens a <form (within twelve lines)."""
    for j in range(i, max(-1, i - 12), -1):
        seg = lines[j]
        if j == i:
            seg = seg[:seg.find("action=")] if "action=" in seg else seg
        m = FORM_METHOD_RX.search(seg)
        if m:
            return m.group(1).upper()
        if "<form" in seg.lower() and j != i:
            break
    return "GET"


def collect(templates_dir: str, static_dir: str | None) -> list:
    out: list = []
    for root, _, files in os.walk(templates_dir):
        for f in sorted(files):
            if not f.endswith((".html", ".htm", ".jinja", ".j2")):
                continue
            p = os.path.join(root, f)
            rel = os.path.relpath(p, os.path.dirname(templates_dir))
            lines = open(p, encoding="utf-8", errors="replace").read().splitlines()
            for i, line in enumerate(lines):
                for attr, value in ATTR_RX.findall(line):
                    if URL_FOR_RX.search(value):
                        continue                  # judged by the url_for arm below, not as a dynamic base
                    if attr == "action":
                        method = _form_method_near(line, lines, i)
                    else:
                        method = ATTR_METHOD.get(attr, "GET")
                    out.append(Target(attr, value, f"{rel}:{i + 1}", method))
                for ep, args in URL_FOR_RX.findall(line):
                    out.append(Target("url_for", f"url_for('{ep}', {args.strip()})" if args.strip() else f"url_for('{ep}')",
                                      f"{rel}:{i + 1}", "GET", ep, tuple(KWARG_RX.findall(args or ""))))
                for value in JS_RX.findall(line):
                    m = JS_METHOD_RX.search(line)
                    out.append(Target("js", value, f"{rel}:{i + 1}", (m.group(1).upper() if m else "GET")))
                for verb, value in HTMX_AJAX_RX.findall(line):
                    out.append(Target("htmx-ajax", value, f"{rel}:{i + 1}", verb.upper()))
                for attr, value in DATA_ATTR_RX.findall(line):
                    if attr in ATTRS or URL_FOR_RX.search(value):
                        continue                  # already collected above
                    out.append(Target(attr, value, f"{rel}:{i + 1}", "GET"))
    if static_dir and os.path.isdir(static_dir):
        for root, _, files in os.walk(static_dir):
            for f in sorted(files):
                if not f.endswith(".js"):
                    continue
                p = os.path.join(root, f)
                rel = os.path.relpath(p, os.path.dirname(static_dir))
                for i, line in enumerate(open(p, encoding="utf-8", errors="replace").read().splitlines()):
                    for value in JS_RX.findall(line):
                        m = JS_METHOD_RX.search(line)
                        out.append(Target("js", value, f"{rel}:{i + 1}", (m.group(1).upper() if m else "GET")))
                    for verb, value in HTMX_AJAX_RX.findall(line):
                        out.append(Target("htmx-ajax", value, f"{rel}:{i + 1}", verb.upper()))
    return out


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------

def _branches(target: str) -> list:
    m = IF_RX.search(target)
    if not m:
        return [target]
    with_ = target[:m.start()] + m.group(1) + target[m.end():]
    without = target[:m.start()] + target[m.end():]
    return _branches(with_) + _branches(without)


def _normalise(target: str) -> str:
    """Jinja → wildcard segments; query/fragment dropped; trailing slash kept out."""
    t = JINJA_EXPR_RX.sub(WILD, target)
    t = JINJA_STMT_RX.sub("", t)
    t = t.split("?", 1)[0].split("#", 1)[0]
    segs = []
    for seg in t.split("/"):
        segs.append(WILD if WILD in seg else seg)
    t = "/".join(segs)
    return (t.rstrip("/") or "/") if t != "/" else "/"


#: What each Flask converter accepts at the router, as a per-SEGMENT test.
#: AUD-044 (round 3): the first form replaced every ``<...>`` with ``[^/]+``,
#: so a literal segment a converter refuses — ``inbox`` against
#: ``<uuid:test_id>`` — matched a rule the live router 404s on, and the sweep
#: called a dead link alive. The converter decides now.
_CONVERTER_RX = {
    "int": re.compile(r"^-?\d+$"),
    "float": re.compile(r"^-?\d+(?:\.\d+)?$"),
    "uuid": re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"),
    "string": re.compile(r"^[^/]+$"),
    "any": re.compile(r"^[^/]+$"),
}
_PARAM_RX = re.compile(r"^<(?:([a-z]+)(?:\([^)]*\))?:)?([^>]+)>$")


def _rule_segments(rule: str) -> tuple:
    """``([(kind, value), ...], has_path_tail)`` — ``kind`` is 'lit' for a
    literal segment or the converter name for a parameter."""
    segs, tail = [], False
    for seg in (rule.rstrip("/") or "/").split("/"):
        m = _PARAM_RX.match(seg)
        if m is None:
            segs.append(("lit", seg))
            continue
        conv = m.group(1) or "string"
        if conv == "path":
            tail = True
        segs.append((conv, m.group(2)))
    return segs, tail


def _segment_ok(kind: str, value: str, seg: str) -> bool:
    """Does the target's segment satisfy this rule segment? A Jinja wildcard
    segment stands for a value the template renders, so it satisfies any
    parameter and (conservatively, never crying wolf) any literal too."""
    if seg == WILD:
        return True
    if kind == "lit":
        return seg == value
    rx = _CONVERTER_RX.get(kind)
    return True if rx is None else bool(rx.match(seg))


class Resolver:
    """Resolves targets against a Flask url_map (the LIVE one, from the app)."""

    def __init__(self, url_map):
        self.rules = []
        self.endpoints: dict = {}
        for r in url_map.iter_rules():
            rule = str(r.rule)
            methods = {m for m in (r.methods or set()) if m not in ("HEAD", "OPTIONS")}
            segs, tail = _rule_segments(rule)
            self.rules.append((segs, tail, rule, methods))
            self.endpoints.setdefault(r.endpoint, []).append(set(r.arguments))

    def _hits(self, path: str):
        """Every rule whose segments accept this target path (AUD-044)."""
        want = (path.rstrip("/") or "/").split("/")
        out = []
        for segs, tail, rule, methods in self.rules:
            if tail:
                if len(want) < len(segs):
                    continue
                pairs = list(zip(segs, want[:len(segs) - 1] + ["/".join(want[len(segs) - 1:])]))
            else:
                if len(want) != len(segs):
                    continue
                pairs = list(zip(segs, want))
            if all(_segment_ok(k, v, seg) for (k, v), seg in pairs):
                out.append((rule, methods))
        return out

    def _match(self, path: str, method: str) -> str | None:
        """None when alive; else the reason."""
        hit = self._hits(path)
        if not hit:
            return "no route matches"
        if any(method in methods for _, methods in hit):
            return None
        allowed = sorted({m for _, methods in hit for m in methods})
        return f"route exists but not for {method} (allows {', '.join(allowed)})"

    def resolve(self, t: Target) -> tuple:
        """('alive'|'dead'|'unresolvable', reason)."""
        if t.kind == "url_for":
            args = self.endpoints.get(t.endpoint)
            if args is None:
                return "dead", f"no endpoint named {t.endpoint!r}"
            given = set(t.kwargs)
            if any(required <= given for required in args):
                return "alive", ""
            need = sorted(min(args, key=len))
            return "dead", f"url_for({t.endpoint!r}) lacks {', '.join(sorted(set(need) - given))}"
        raw = t.raw.strip()
        if not raw:
            return "unresolvable", "empty target"
        if raw.startswith(("{{", "{%")):
            return "unresolvable", "dynamic base"
        if not raw.startswith("/") or raw.startswith("//"):
            return "alive", "external or relative — not judged"
        for branch in _branches(raw):
            path = _normalise(branch)
            if path.startswith("/static/") or path == "/static":
                continue
            if WILD in path and path.replace(WILD, "").replace("/", "") == "":
                # every segment is Jinja — nothing literal to resolve against
                return "unresolvable", "every segment is dynamic"
            why = self._match(path, t.method)
            if why and t.kind in ("js", "htmx-ajax") and raw.rstrip("?#").endswith("/"):
                # a JS literal ending in "/" is a PREFIX the code appends an id
                # to (fetch('/api/x/' + id)): judge it with one more segment
                why = self._match(path + "/" + WILD, t.method)
            if why:
                return "dead", why + (f" [{path}]" if path != raw else "")
        return "alive", ""


def sweep(url_map, templates_dir: str, static_dir: str | None) -> Report:
    rep = Report()
    res = Resolver(url_map)
    for t in collect(templates_dir, static_dir):
        rep.targets.append(t)
        state, why = res.resolve(t)
        if state == "alive":
            rep.alive.append(t)
        elif state == "dead":
            rep.dead.append((t, why))
        else:
            rep.unresolvable.append(t)
    return rep


def sweep_repo(templates_dir: str | None = None, static_dir: str | None = None) -> Report:
    sys.path.insert(0, REPO)
    from primeqa.app import app
    return sweep(app.url_map,
                 templates_dir or os.path.join(REPO, "primeqa", "templates"),
                 static_dir if static_dir is not None else os.path.join(REPO, "primeqa", "static"))


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in argv
    rep = sweep_repo()
    d = rep.as_dict()
    if as_json:
        print(json.dumps(d, indent=1))
    else:
        print(f"dead-link sweep: {d['targets']} targets, {d['alive']} alive, "
              f"{d['unresolvable']} unresolvable (dynamic base), {d['dead']} dead")
        for x in d["dead_list"]:
            print(f"  DEAD {x['where']} [{x['kind']}] {x['target']} — {x['reason']}")
    return 1 if rep.dead else 0


if __name__ == "__main__":
    sys.exit(main())
