"""The F8 rule grammar — TEN forms, zero connectives, one gate
(LLD_PHASE5_AUTHORING §e, ratified by the TA 2026-09-01, D-471).

A custom rule is exactly: **one selector · one predicate · one
applicability gate · one population declaration** (§e.1). This module
is the ceiling made executable: a draft that does not validate never
becomes a rule — it becomes a REFUSAL with a class, a reason in the
customer's terms, and (where one exists mechanically) the nearest
expressible rule offered as a partial (§f).

THE COUNT, recorded once: v1 admits ten forms — `member_of`,
`not_member_of`, the `equals`/`not_equals` boolean-arity pair (one form,
two polarities), `present`, `absent`, `at_least`, `at_most`,
`count_at_least`, `count_at_most`, `count_equals` — eleven predicate
tokens. `idref_resolves_to_role` is RESERVED as the named first
extension (TA: "admitted only if the criterion catalogue shows a
material WCAG-coverage gain") and is refused like any unknown form,
with the reservation named in the refusal.

WHAT THIS MODULE NEVER DOES: touch a database, see a census, or decide
a verdict. Grammar is authoring-time; evaluation lives in S6
(`interpretation/cust_evaluation.py`); capture lives in the worker.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from primeqa.knowledge.census_schema import (
    ATTRIBUTE_ALLOWLIST, CENSUS_SCHEMA_VERSION, PROPERTY_ALLOWLIST)

# ---------------------------------------------------------------------------
# The vocabulary
# ---------------------------------------------------------------------------

MEMBERSHIP_FORMS = ("member_of", "not_member_of")
EQUALITY_FORMS = ("equals", "not_equals")
PRESENCE_FORMS = ("present", "absent")
GEOMETRY_FORMS = ("at_least", "at_most")
COUNT_FORMS = ("count_at_least", "count_at_most", "count_equals")
PREDICATE_TOKENS = (MEMBERSHIP_FORMS + EQUALITY_FORMS + PRESENCE_FORMS
                    + GEOMETRY_FORMS + COUNT_FORMS)          # 11 tokens
NEGATIVE_FORMS = ("not_member_of", "not_equals", "absent")   # §e.4 amendment

RESERVED_EXTENSION = "idref_resolves_to_role"                # D-471

SELECTOR_TERMS = ("role_is", "component_is", "owned_by_bundle",
                  "has_attribute", "within", "heading_level_is")
MAX_SELECTOR_TERMS = 4

GATE_TERMS = ("surface_contains", "surface_lacks")

TRAVERSAL_MODES = ("synthetic_aura", "native_open", "light_only", "any")

# Fact slots a predicate may test (§e.3's five families as capture slots).
#   attr:<name>      — an allowlisted attribute of the node itself
#   style:<prop>     — the node's own resolved value for a pinned property
#   component:tag    — the custom-element tag
#   role             — the node's computed role
#   geom:<dim>       — the node's bounding box (geometry family)
GEOM_DIMS = ("width", "height", "x", "y")

# The refusal classes (§f). The first three are assignable MECHANICALLY
# by this validator; the last three are reviewer judgments recorded on
# the ledger — a validator cannot know that a guideline is ambiguous.
MECHANICAL_REFUSALS = ("needs_prohibited_operator",
                       "needs_capability_not_captured",
                       "needs_interaction")
REVIEWER_REFUSALS = ("not_observable", "belongs_to_public_catalogue",
                     "ambiguous_guideline")
REFUSAL_CLASSES = MECHANICAL_REFUSALS + REVIEWER_REFUSALS

# Facts that exist only under interaction (Mode B) — naming them lets the
# validator route "focus ring" style drafts to needs_interaction rather
# than the generic capability class.
_INTERACTION_MARKERS = ("focus", "hover", "active", "pressed", "dragged")

# Anything CSS-shaped in an operand is the logic-language-in-costume
# §e.2 refuses: combinators, pseudo-classes, attribute matchers.
_CSS_SHAPED = re.compile(r"[#.()\[\]>~+*:]|\s")

_CONNECTIVE_KEYS = ("and", "or", "not", "all", "any", "if", "then",
                    "else", "when", "unless", "predicates", "conditions")


class GrammarError(ValueError):
    """An internal misuse of this module (not a draft refusal)."""


@dataclass
class Refusal:
    refusal_class: str
    reason: str
    nearest_expressible: list | None = None

    def as_record(self) -> dict:
        return {"refusal_class": self.refusal_class, "reason": self.reason,
                "nearest_expressible": self.nearest_expressible}


@dataclass
class ValidatedRule:
    name: str
    guideline_thread_id: str
    selector: list
    predicate: dict
    applicability: dict | None
    population: str
    criterion: dict
    census_schema_version: int
    traversal_mode_assumption: str
    token_set_pins: list = field(default_factory=list)

    def content(self) -> dict:
        return {
            "name": self.name,
            "selector": self.selector,
            "predicate": self.predicate,
            "applicability": self.applicability,
            "population": self.population,
            "criterion": self.criterion,
            "census_schema_version": self.census_schema_version,
            "traversal_mode_assumption": self.traversal_mode_assumption,
            "token_set_pins": self.token_set_pins,
        }

    def content_hash(self) -> str:
        canonical = json.dumps(self.content(), sort_keys=True,
                               separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Validation — every path out is a ValidatedRule or a classed Refusal
# ---------------------------------------------------------------------------

def validate(draft: dict) -> tuple:
    """``(rule, None)`` or ``(None, Refusal)``. Never raises on customer
    input; raises GrammarError only on a non-dict draft (caller bug)."""
    if not isinstance(draft, dict):
        raise GrammarError("draft must be a dict")
    try:
        return _validate(draft), None
    except _Refused as r:
        return None, r.refusal


class _Refused(Exception):
    def __init__(self, refusal: Refusal):
        self.refusal = refusal
        super().__init__(refusal.reason)


def _refuse(cls: str, reason: str, nearest=None):
    raise _Refused(Refusal(cls, reason, nearest))


def _validate(draft: dict) -> ValidatedRule:
    _scan_for_connectives(draft, path="draft")

    for req in ("name", "guideline_thread_id", "selector", "predicate",
                "population", "criterion"):
        if not draft.get(req):
            _refuse("needs_prohibited_operator",
                    f"draft is missing required part {req!r} — a rule is "
                    "one selector, one predicate, one gate, one population "
                    "declaration (§e.1)")

    selector = _validate_selector(draft["selector"])
    predicate = _validate_predicate(draft["predicate"])
    gate = _validate_gate(draft.get("applicability"))

    population = str(draft["population"]).strip()
    if len(population) < 8:
        _refuse("needs_prohibited_operator",
                "the population declaration is mandatory: one sentence "
                "naming the matched set the FORALL ranges over (§e.5)")

    criterion = draft["criterion"]
    if not isinstance(criterion, dict) or not criterion.get("profile"):
        _refuse("needs_prohibited_operator",
                "criterion must name the customer profile heading it maps "
                "(criterion.profile); binds_wcag_sc is optional")

    mode = draft.get("traversal_mode_assumption", "any")
    if mode not in TRAVERSAL_MODES:
        _refuse("needs_prohibited_operator",
                f"unknown traversal mode {mode!r}; one of {TRAVERSAL_MODES}")

    schema = int(draft.get("census_schema_version", CENSUS_SCHEMA_VERSION))

    pins = []
    for part in (predicate,):
        ts = part.get("token_set")
        if ts:
            pins.append({"token_set": ts["key"], "version": ts["version"]})

    return ValidatedRule(
        name=str(draft["name"])[:160],
        guideline_thread_id=str(draft["guideline_thread_id"]),
        selector=selector, predicate=predicate, applicability=gate,
        population=population,
        criterion={"profile": str(criterion["profile"]),
                   "binds_wcag_sc": criterion.get("binds_wcag_sc")},
        census_schema_version=schema,
        traversal_mode_assumption=mode,
        token_set_pins=pins)


def _scan_for_connectives(node, path: str) -> None:
    """Any connective ANYWHERE in the draft is the refusal §e.5 ratified.
    An LLM draft with {"and": [...]} dies here, before any human sees
    it as a rule."""
    if isinstance(node, dict):
        for k, v in node.items():
            lk = str(k).lower()
            if lk in _CONNECTIVE_KEYS:
                nearest = None
                if lk == "and" and isinstance(v, list):
                    nearest = [f"split into {len(v)} rules, one predicate "
                               "each, grouped by guideline_thread_id"]
                elif lk == "or":
                    nearest = ["widen the token set to cover every "
                               "alternative — OR is subsumed by membership"]
                elif lk == "not":
                    nearest = ["a second rule with the complementary "
                               "applicability gate"]
                _refuse("needs_prohibited_operator",
                        f"connective {k!r} at {path} — composition is "
                        "refused (D-471: zero composition; two rules give "
                        "two attested verdicts, strictly better evidence)",
                        nearest)
            if lk == RESERVED_EXTENSION:
                _refuse("needs_prohibited_operator",
                        f"{RESERVED_EXTENSION} is the RESERVED first "
                        "extension (D-471): admitted only if the criterion "
                        "catalogue shows a material WCAG-coverage gain; "
                        "not in the v1 vocabulary")
            _scan_for_connectives(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _scan_for_connectives(v, f"{path}[{i}]")


def _no_css(value: str, where: str) -> str:
    v = str(value)
    if _CSS_SHAPED.search(v):
        _refuse("needs_prohibited_operator",
                f"{where} operand {v!r} is CSS-shaped — raw CSS selectors "
                "are refused (§e.2: DE-13 'never CSS path', and a selector "
                "language is a logic language in costume)")
    return v


def _validate_selector(terms) -> list:
    if not isinstance(terms, list) or not terms:
        _refuse("needs_prohibited_operator",
                "selector must be a flat list of 1-4 terms")
    if len(terms) > MAX_SELECTOR_TERMS:
        _refuse("needs_prohibited_operator",
                f"selector has {len(terms)} terms; at most "
                f"{MAX_SELECTOR_TERMS} AND-joined flat terms (§e.2)")
    out = []
    for t in terms:
        if not isinstance(t, dict) or "term" not in t:
            _refuse("needs_prohibited_operator",
                    f"selector term {t!r} is not a {{term, value}} dict")
        name = t["term"]
        if name not in SELECTOR_TERMS:
            _refuse("needs_prohibited_operator",
                    f"unknown selector term {name!r}; the closed set is "
                    f"{SELECTOR_TERMS}")
        value = t.get("value")
        if name == "heading_level_is":
            if not isinstance(value, int) or not 1 <= value <= 6:
                _refuse("needs_prohibited_operator",
                        "heading_level_is takes an integer 1-6")
        elif name == "has_attribute":
            if value not in ATTRIBUTE_ALLOWLIST:
                _refuse("needs_capability_not_captured",
                        f"attribute {value!r} is not in the closed capture "
                        "allowlist — presence of an uncaptured attribute "
                        "cannot be witnessed")
        else:
            _no_css(value, f"selector {name}")
        out.append({"term": name, "value": value})
    return out


def _fact_kind(fact: str) -> str:
    """attr / style / component / role / geom — or a refusal."""
    if fact == "role":
        return "role"
    if fact == "component:tag":
        return "component"
    if fact.startswith("attr:"):
        name = fact[5:]
        for marker in _INTERACTION_MARKERS:
            if marker in name and name not in ATTRIBUTE_ALLOWLIST:
                _refuse("needs_interaction",
                        f"fact {fact!r} exists only under interaction — "
                        "Mode B territory, parked by design")
        if name not in ATTRIBUTE_ALLOWLIST:
            _refuse("needs_capability_not_captured",
                    f"attribute {name!r} is not in the closed capture "
                    f"allowlist ({len(ATTRIBUTE_ALLOWLIST)} names)")
        return "attr"
    if fact.startswith("style:"):
        prop = fact[6:]
        if any(m in prop for m in _INTERACTION_MARKERS):
            _refuse("needs_interaction",
                    f"style fact {fact!r} resolves only in an interaction "
                    "state (:focus-*, :hover) — Mode B, parked (§e.3: the "
                    "focus-ring family is a named non-goal)")
        if prop not in PROPERTY_ALLOWLIST:
            _refuse("needs_capability_not_captured",
                    f"property {prop!r} is not in the pinned closed "
                    f"property list ({len(PROPERTY_ALLOWLIST)} properties)")
        return "style"
    if fact.startswith("geom:"):
        dim = fact[5:]
        if dim not in GEOM_DIMS:
            _refuse("needs_prohibited_operator",
                    f"geometry dimension {dim!r}; the box is {GEOM_DIMS}")
        return "geom"
    _refuse("needs_capability_not_captured",
            f"fact {fact!r} names nothing the census captures — the fact "
            "families are attr:<name>, style:<prop>, component:tag, role, "
            "geom:<dim> (§e.3)")


def _validate_predicate(p) -> dict:
    if not isinstance(p, dict) or "form" not in p:
        _refuse("needs_prohibited_operator",
                "predicate must be one {form, ...} dict — exactly one "
                "predicate per rule (§e.1)")
    form = p["form"]
    if form == RESERVED_EXTENSION:
        _refuse("needs_prohibited_operator",
                f"{RESERVED_EXTENSION} is the RESERVED first extension "
                "(D-471), not in v1")
    if form not in PREDICATE_TOKENS:
        _refuse("needs_prohibited_operator",
                f"unknown predicate form {form!r}; the ten ratified forms "
                f"are {PREDICATE_TOKENS} (equals/not_equals one pair)")

    if form in COUNT_FORMS:
        n = p.get("n")
        if not isinstance(n, int) or n < 0:
            _refuse("needs_prohibited_operator",
                    f"{form} takes a non-negative integer n")
        return {"form": form, "n": n}

    fact = p.get("fact")
    if not fact:
        _refuse("needs_prohibited_operator",
                f"{form} needs a fact slot (attr:/style:/component:tag/"
                "role/geom:)")
    kind = _fact_kind(str(fact))

    if form in GEOMETRY_FORMS:
        if kind != "geom":
            _refuse("needs_prohibited_operator",
                    f"{form} is admitted for geometry only (§e.4) — "
                    f"fact {fact!r} is {kind}")
        px = p.get("px")
        if not isinstance(px, (int, float)) or px < 0:
            _refuse("needs_prohibited_operator",
                    f"{form} compares one captured dimension to one px "
                    "literal")
        return {"form": form, "fact": fact, "px": float(px)}

    if kind == "geom":
        _refuse("needs_prohibited_operator",
                "geometry admits only at_least/at_most — nothing else "
                "numeric is in the language (§e.4)")

    if form in PRESENCE_FORMS:
        if kind not in ("attr", "role"):
            _refuse("needs_prohibited_operator",
                    "present/absent test the node's OWN attribute or role, "
                    "never the page's contents (§e.4)")
        return {"form": form, "fact": fact}

    if form in MEMBERSHIP_FORMS:
        ts = p.get("token_set")
        if (not isinstance(ts, dict) or not ts.get("key")
                or not isinstance(ts.get("version"), int)):
            _refuse("needs_prohibited_operator",
                    f"{form} takes a versioned token set "
                    "{token_set: {key, version}} — the value domain is "
                    "pinned so a drifting design system invalidates the "
                    "projection rather than silently changing results (§h)")
        _no_css(ts["key"], "token_set key")
        return {"form": form, "fact": fact,
                "token_set": {"key": ts["key"],
                              "version": int(ts["version"])}}

    # equality pair
    if "literal" not in p:
        _refuse("needs_prohibited_operator",
                f"{form} compares the fact to one literal")
    literal = p["literal"]
    if isinstance(literal, str):
        if re.search(r"[*?^$|\\]", literal):
            _refuse("needs_prohibited_operator",
                    "regex/substring matching is refused (§e.7: NO SECOND "
                    "ENGINE — a pattern evaluator is an unpinned "
                    "computation)")
    return {"form": form, "fact": fact, "literal": literal}


def _validate_gate(gate) -> dict | None:
    if gate in (None, {}, []):
        return None
    if isinstance(gate, list):
        if len(gate) > 1:
            _refuse("needs_prohibited_operator",
                    "at most ONE applicability gate (§e.6) — an ELSE is a "
                    "second rule with the complementary gate")
        gate = gate[0]
    if not isinstance(gate, dict) or gate.get("gate") not in GATE_TERMS:
        _refuse("needs_prohibited_operator",
                f"applicability is one {GATE_TERMS} gate over one selector "
                "term — no predicate attached, no value read, no count "
                "compared, no chaining (§e.6)")
    term = gate.get("term")
    validated = _validate_selector([term])
    return {"gate": gate["gate"], "term": validated[0]}


# ---------------------------------------------------------------------------
# The one-sentence render (§e.7's reviewability test, made literal)
# ---------------------------------------------------------------------------

def render_sentence(rule: ValidatedRule) -> str:
    sel = " and ".join(_term_phrase(t) for t in rule.selector)
    scope = ""
    if rule.applicability:
        g = rule.applicability
        verb = ("contains" if g["gate"] == "surface_contains" else "lacks")
        scope = (f", on surfaces where the census attests the page "
                 f"{verb} {_term_phrase(g['term'])},")
    return (f"Every node that {sel}{scope} must satisfy "
            f"{_predicate_phrase(rule.predicate)} "
            f"(population: {rule.population}).")


def _term_phrase(t: dict) -> str:
    return {"role_is": "has role {v}", "component_is": "is a <{v}>",
            "owned_by_bundle": "is owned by bundle {v}",
            "has_attribute": "carries attribute {v}",
            "within": "sits within a {v}",
            "heading_level_is": "is a level-{v} heading",
            }[t["term"]].format(v=t["value"])


def _predicate_phrase(p: dict) -> str:
    f = p["form"]
    if f in COUNT_FORMS:
        op = {"count_at_least": "at least", "count_at_most": "at most",
              "count_equals": "exactly"}[f]
        return f"the surface-wide match count is {op} {p['n']}"
    if f in GEOMETRY_FORMS:
        op = "≥" if f == "at_least" else "≤"
        return f"{p['fact']} {op} {p['px']}px"
    if f in PRESENCE_FORMS:
        return f"{p['fact']} is {'present' if f == 'present' else 'absent'}"
    if f in MEMBERSHIP_FORMS:
        neg = "not " if f == "not_member_of" else ""
        ts = p["token_set"]
        return (f"{p['fact']} is {neg}a member of token set "
                f"{ts['key']} v{ts['version']}")
    neg = "" if f == "equals" else "not "
    return f"{p['fact']} is {neg}{p['literal']!r}"
