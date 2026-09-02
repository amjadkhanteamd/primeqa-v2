"""S6 evaluation of CUSTOM rules over the census — the worker captured,
this module decides (LLD_PHASE5_AUTHORING §h; D-460; D-471).

D-466 holds unchanged: PASS requires positive attestation, and a custom
rule's attestation IS the census — PASS only when the census attests the
scope was walked (no cap hit, no capture errors), the match set was
non-empty, and every matched node's captured facts satisfy the
predicate. The ratified verdict table (§h) and the HARD INVARIANT
(§e.6, TA 2026-09-01) are implemented literally:

* selector matched nothing            -> NOT_DETERMINED ``no_match_set``
  (the vacuous-pass class D-465/D-466 was burned by; a rule that
  matched nothing tested nothing);
* required fact absent from the census -> NOT_DETERMINED
  ``fact_not_captured`` (absence of data is never ``absent`` — §e.4);
* census schema older than the rule    -> NOT_DETERMINED
  ``census_unattested``;
* traversal mode differs from the rule's declared assumption
                                       -> NOT_DETERMINED
  ``traversal_mode_mismatch``;
* the census is incomplete (cap hit / capture errors / mode missing):
  the applicability gate CANNOT suppress and the full scope cannot be
  attested                             -> NOT_DETERMINED
  ``census_incomplete`` — never ``rule_inapplicable``, and
  ``surface_lacks(X)`` never means "X absent therefore pass". A
  WITNESSED violation still FAILs: a violation found on a partial walk
  is a violation.

Normalisation is specified here, versioned with the census schema, and
applied to RAW captured strings — the census stays evidence:
colours to an sRGB tuple, lengths to px within the pinned epsilon,
font-family to a normalised list.
"""
from __future__ import annotations

import re

PASS = "PASS"
FAIL = "FAIL"
NOT_DETERMINED = "NOT_DETERMINED"

NO_MATCH_SET = "no_match_set"
FACT_NOT_CAPTURED = "fact_not_captured"
CENSUS_UNATTESTED = "census_unattested"
CENSUS_INCOMPLETE = "census_incomplete"
TRAVERSAL_MODE_MISMATCH = "traversal_mode_mismatch"


class EvaluationError(RuntimeError):
    """Caller misuse (a malformed rule content dict) — customer input is
    validated by the grammar long before it reaches here."""


# ---------------------------------------------------------------------------
# Normalisation (census schema v1)
# ---------------------------------------------------------------------------

_RGB = re.compile(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)"
                  r"(?:\s*,\s*([\d.]+))?\s*\)")
_HEX = re.compile(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_PX = re.compile(r"^(-?[\d.]+)px$")


def normalise_color(raw: str):
    """Canonical (r, g, b, a) ints/float, or None when the string is not
    a colour this schema recognises. getComputedStyle resolves keyword/
    hsl/color-mix/currentColor to rgb()/rgba() already; hex accepted for
    token-set literals."""
    s = (raw or "").strip()
    m = _RGB.match(s)
    if m:
        r, g, b = (int(float(m.group(i))) for i in (1, 2, 3))
        a = float(m.group(4)) if m.group(4) is not None else 1.0
        return (r, g, b, round(a, 3))
    m = _HEX.match(s)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        a = round(int(h[6:8], 16) / 255, 3) if len(h) == 8 else 1.0
        return (r, g, b, a)
    return None


def normalise_length(raw) -> float | None:
    """px float, or None. Comparison uses the pinned epsilon — browsers
    return 13.9993px and an exact match would manufacture reds."""
    if isinstance(raw, (int, float)):
        return float(raw)
    m = _PX.match((raw or "").strip())
    return float(m.group(1)) if m else None


def normalise_font(raw: str) -> list:
    return [f.strip().strip('"\'').lower()
            for f in (raw or "").split(",") if f.strip()]


def _values_equal(fact: str, a, b, epsilon_px: float) -> bool | None:
    """Schema-v1 equality for one fact slot. None = not comparable in
    this schema (treated as unsatisfied, never as satisfied)."""
    if fact.startswith("style:"):
        ca, cb = normalise_color(str(a)), normalise_color(str(b))
        if ca is not None or cb is not None:
            return ca is not None and cb is not None and ca == cb
        la, lb = normalise_length(str(a)), normalise_length(str(b))
        if la is not None or lb is not None:
            return (la is not None and lb is not None
                    and abs(la - lb) <= epsilon_px)
        if "font-family" in fact:
            return normalise_font(str(a)) == normalise_font(str(b))
        return str(a).strip().lower() == str(b).strip().lower()
    if a is True or b is True:            # bare attribute presence
        return a == b
    return str(a) == str(b)


# ---------------------------------------------------------------------------
# Selector matching over census nodes
# ---------------------------------------------------------------------------

def _node_matches(term: dict, node: dict, resolve_bundle_tag) -> bool | None:
    """True/False, or None when the term is undecidable for this node
    (owned_by_bundle without a resolver = fact not captured)."""
    t, v = term["term"], term.get("value")
    if t == "role_is":
        return node.get("role") == v
    if t == "component_is":
        return node.get("tag") == v
    if t == "has_attribute":
        return v in (node.get("attrs") or {})
    if t == "within":
        return v in (node.get("anc") or [])
    if t == "heading_level_is":
        return node.get("heading") == v
    if t == "owned_by_bundle":
        tag = node.get("tag")
        if not tag or resolve_bundle_tag is None:
            return None
        owner = resolve_bundle_tag(tag)
        return None if owner is None else (owner == v)
    raise EvaluationError(f"unknown selector term {t!r}")


def _match_set(selector: list, nodes: list, resolve_bundle_tag):
    matched, undecidable = [], 0
    for n in nodes:
        verdicts = [_node_matches(t, n, resolve_bundle_tag)
                    for t in selector]
        if any(v is None for v in verdicts):
            undecidable += 1
        elif all(verdicts):
            matched.append(n)
    return matched, undecidable


# ---------------------------------------------------------------------------
# The census trust gate (§e.6 — the five measurable conditions)
# ---------------------------------------------------------------------------

def census_conditions(observation: dict, required_schema: int) -> dict:
    """Each condition observable and named; any False and the census can
    suppress nothing. Surface status OK implies structural-quiet reached
    (the scan returns NOT_REACHED otherwise, by construction)."""
    census = observation.get("census") or {}
    return {
        "surface_ok_and_quiet": observation.get("status") == "OK",
        "node_cap_not_hit": census.get("cap_hit") is False,
        "traversal_mode_recorded": bool(census.get("traversal_mode")),
        "schema_version_sufficient":
            int(census.get("schema_version") or 0) >= required_schema,
        "zero_capture_errors": census.get("capture_errors") == 0,
    }


# ---------------------------------------------------------------------------
# The evaluator
# ---------------------------------------------------------------------------

def evaluate_rule(content: dict, observation: dict, *,
                  token_sets: dict | None = None,
                  resolve_bundle_tag=None,
                  epsilon_px: float = 0.5) -> tuple:
    """One custom rule over one surface's observation ->
    ``(verdict, basis)``. ``content`` is the ratified rule content
    (grammar-validated at authoring); ``token_sets`` maps
    ``(key, version)`` -> list of tokens, resolved by the caller from
    the tenant store (this module reads no database)."""
    census = observation.get("census")
    required = int(content.get("census_schema_version") or 1)
    basis: dict = {"rule_content_facts": {
        "population": content.get("population"),
        "selector": content.get("selector"),
        "predicate": content.get("predicate"),
        "applicability": content.get("applicability")}}

    if not census:
        return NOT_DETERMINED, {**basis, "reason": CENSUS_UNATTESTED,
                                "detail": "no census in the observation — "
                                          "the manifest predates the census "
                                          "pin or the capture was skipped"}
    conditions = census_conditions(observation, required)
    basis["census_conditions"] = conditions
    basis["census_meta"] = {k: census.get(k) for k in (
        "schema_version", "traversal_mode", "cap_hit", "capture_errors",
        "n", "node_cap")}

    if not conditions["schema_version_sufficient"]:
        return NOT_DETERMINED, {**basis, "reason": CENSUS_UNATTESTED,
                                "detail": f"census schema "
                                          f"{census.get('schema_version')} < "
                                          f"required {required}"}
    assumption = content.get("traversal_mode_assumption") or "any"
    if assumption != "any" and census.get("traversal_mode") != assumption:
        return NOT_DETERMINED, {
            **basis, "reason": TRAVERSAL_MODE_MISMATCH,
            "detail": f"rule assumes {assumption}, census recorded "
                      f"{census.get('traversal_mode')!r} — evaluated under "
                      "a different shadow-DOM traversal than the rule's "
                      "baseline (§h: NOT_COMPARABLE, never a transition)"}

    complete = all(conditions.values())
    nodes = census.get("nodes") or []

    # -- applicability: one gate, decidable ONLY over a COMPLETE census.
    # §e.6 (ratified): "surface_lacks(X) ... is a scope gate over a
    # complete census, and on an incomplete one it decides NOTHING" —
    # neither suppression nor application, so the verdict is
    # census_incomplete outright, never rule_inapplicable and never a
    # merits evaluation under an unproven scope.
    gate = content.get("applicability")
    if gate:
        if not complete:
            failed = [k for k, v in conditions.items() if not v]
            return NOT_DETERMINED, {
                **basis, "reason": CENSUS_INCOMPLETE,
                "failed_conditions": failed,
                "detail": "the applicability gate decides nothing over an "
                          "incomplete census (§e.6 hard invariant) — a "
                          "would-be NOT_APPLICABLE becomes NOT_DETERMINED, "
                          "and surface_lacks never means 'X absent "
                          "therefore pass'"}
        gate_matched, gate_und = _match_set([gate["term"]], nodes,
                                            resolve_bundle_tag)
        if gate_und:
            return NOT_DETERMINED, {
                **basis, "reason": CENSUS_INCOMPLETE,
                "failed_conditions": ["gate term undecidable"],
                "detail": "the gate's term is undecidable for at least "
                          "one node — the scope condition is unproven"}
        present = bool(gate_matched)
        applies = (present if gate["gate"] == "surface_contains"
                   else not present)
        if not applies:
            return NOT_DETERMINED, {
                **basis, "reason": "rule_inapplicable",
                "detail": f"{gate['gate']} gate: the complete census "
                          "attests the scope condition is not met"}

    # -- the match set
    matched, undecidable = _match_set(content["selector"], nodes,
                                      resolve_bundle_tag)
    basis["match_count"] = len(matched)
    basis["undecidable_nodes"] = undecidable
    predicate = content["predicate"]
    form = predicate["form"]

    # -- count forms: the match-set cardinality, surface-scoped
    if form.startswith("count_"):
        n, k = predicate["n"], len(matched)
        if form == "count_at_least":
            if k >= n:
                return PASS, {**basis, "attested_by": "census cardinality",
                              "observed": k}
            if not complete:
                return NOT_DETERMINED, {**basis,
                                        "reason": CENSUS_INCOMPLETE,
                                        "observed": k,
                                        "detail": "fewer matches than "
                                                  "required on a walk that "
                                                  "did not finish"}
            return FAIL, {**basis, "observed": k, "required": n,
                          "nodes": _summaries(matched)}
        if not complete:
            return NOT_DETERMINED, {**basis, "reason": CENSUS_INCOMPLETE,
                                    "observed": k,
                                    "detail": f"{form} cannot be attested "
                                              "by a walk that did not "
                                              "finish"}
        ok = k <= n if form == "count_at_most" else k == n
        if ok:
            return PASS, {**basis, "attested_by": "census cardinality",
                          "observed": k}
        return FAIL, {**basis, "observed": k, "required": n,
                      "nodes": _summaries(matched)}

    if not matched:
        if not complete:
            return NOT_DETERMINED, {**basis, "reason": CENSUS_INCOMPLETE,
                                    "detail": "empty match set on a walk "
                                              "that did not finish proves "
                                              "nothing"}
        return NOT_DETERMINED, {**basis, "reason": NO_MATCH_SET,
                                "detail": "the selector matched nothing — "
                                          "a rule that matched nothing "
                                          "tested nothing (§h)"}

    # -- per-node predicate: FORALL over the declared population
    violating, uncaptured = [], []
    for node in matched:
        sat = _satisfies(predicate, node, token_sets or {}, epsilon_px)
        if sat is None:
            uncaptured.append(node)
        elif not sat:
            violating.append(node)
    if violating:
        return FAIL, {**basis, "nodes": _summaries(violating),
                      "violations": len(violating)}
    if uncaptured:
        return NOT_DETERMINED, {**basis, "reason": FACT_NOT_CAPTURED,
                                "nodes": _summaries(uncaptured),
                                "detail": "the predicate's fact slot is "
                                          "not recorded for these matched "
                                          "nodes — absence of data is "
                                          "never 'absent' (§e.4)"}
    if not complete:
        return NOT_DETERMINED, {**basis, "reason": CENSUS_INCOMPLETE,
                                "detail": "every matched node satisfies "
                                          "the predicate, but the walk did "
                                          "not finish — PASS requires the "
                                          "census to attest the scope was "
                                          "walked (D-466)"}
    return PASS, {**basis,
                  "attested_by": f"census schema "
                                 f"{census.get('schema_version')}, "
                                 f"{len(matched)} matched nodes, all "
                                 "satisfying"}


def _satisfies(predicate: dict, node: dict, token_sets: dict,
               epsilon_px: float):
    """True / False / None (= the fact slot is not recorded)."""
    form, fact = predicate["form"], predicate["fact"]

    if form in ("at_least", "at_most"):
        dim = fact[5:]
        box = node.get("box")
        if not box or len(box) != 4:
            return None
        value = dict(zip(("x", "y", "width", "height"),
                         (box[0], box[1], box[2], box[3])))[dim]
        px = predicate["px"]
        if form == "at_least":
            return value >= px - epsilon_px
        return value <= px + epsilon_px

    observed, recorded = _fact_value(fact, node)
    if not recorded:
        return None

    if form == "present":
        return observed is not None
    if form == "absent":
        # the slot IS recorded (the attrs bag / role field exists for
        # this node); its recorded content witnesses the negation (§e.4)
        return observed is None

    if form in ("member_of", "not_member_of"):
        ts = predicate["token_set"]
        tokens = token_sets.get((ts["key"], ts["version"]))
        if tokens is None or observed is None:
            return None
        member = any(_values_equal(fact, observed, t, epsilon_px)
                     for t in tokens)
        return member if form == "member_of" else not member

    if observed is None:
        return None
    equal = _values_equal(fact, observed, predicate["literal"], epsilon_px)
    return equal if form == "equals" else not equal


def _fact_value(fact: str, node: dict):
    """(value, slot_recorded). value None + recorded True = a POSITIVE
    record of absence; recorded False = the census never captured the
    slot for this node (-> fact_not_captured)."""
    if fact == "role":
        return (node.get("role") or None), ("role" in node)
    if fact == "component:tag":
        return (node.get("tag") or None), ("tag" in node)
    if fact.startswith("attr:"):
        attrs = node.get("attrs")
        if attrs is None:
            return None, False
        return attrs.get(fact[5:]), True
    if fact.startswith("style:"):
        style = node.get("style") or {}
        prop = fact[6:]
        if prop not in style:
            return None, False
        return style[prop], True
    raise EvaluationError(f"unknown fact {fact!r}")


def _summaries(nodes: list, cap: int = 10) -> list:
    return [{"role": n.get("role"), "tag": n.get("tag"),
             "name": (n.get("name") or "")[:80], "box": n.get("box")}
            for n in nodes[:cap]]
