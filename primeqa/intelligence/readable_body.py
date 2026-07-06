"""Readable test-case body — a derived read-side projection (Stage 1).

Pure functions, no DB, no LLM: the deterministic SKELETON of a verbose,
plain-language test case a BA/QA recognizes — headline, "what this checks",
preconditions, test data, conceptual steps, expected result — built entirely
from a claim's own stored truth plus its recipes and the org label map. The raw
technical detail (``asserted_truth`` / ``semantic_conditions`` / the real recipe)
stays exactly where it is, one click below in the Technical-details drawer; this
projection is the missing MIDDLE between the one-line title and that dump.

Design (D-206 spine discipline, extended):
  - **Derived, never stored** — like coverage. Computed at read time from inputs
    always on hand (the claim + recipe bodies + the label map). A stored string
    column would go stale across claim/recipe versions (the D-204 SCD lesson).
  - **Deterministic FIRST** — this module carries FACTS ONLY. The optional
    Stage-2 LLM phrasing (``readable_body_phrasing``) only rephrases these facts
    and may introduce nothing absent from :attr:`ReadableBodySkeleton.grounded_tokens`.
  - **Reuse, don't reimplement** — the business-language rules (keep API names,
    schema terms and flow names OUT of the spine) live in
    :mod:`primeqa.intelligence.claim_presentation`; this module composes those
    pure helpers rather than re-deriving labels.
  - **Never fabricate** — an unregistered claim-kind emits only what the claim
    envelope grounds (headline, target/subject, conditions) and OMITS every
    section it cannot ground; it never reads a recipe or invents steps.
  - **Never raises** — presentation must not break a page. Any unexpected body
    shape falls back to a minimal headline-only skeleton.

``grounded_tokens`` is the union of every human label + rendered value the
skeleton actually placed into a string. It is derived as the sections are built
(each builder threads the ``tokens`` set), so it can never drift from what was
rendered — it is the allow-set the Stage-2 grounding validator checks against.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Optional

from primeqa.intelligence.claim_presentation import (
    _expected_value,
    _label,
    _object_label_of,
    _ref_name,
    claim_depth,
    claim_title,
    condition_phrase,
    expected_binding,
)
from primeqa.test_representation.canonicalization import canonical_serialize

# Bumping this invalidates every cached phrasing keyed on a skeleton hash
# (mirrors IDENTITY_HASH_VERSION): a change to the skeleton builder that alters
# any rendered string is a policy change, and the hash must move with it.
# v2: prohibition/acceptance headlines carry the distinguishing "when …"
# conditions clause (D-293 conditions-are-identity made visible).
SKELETON_SHAPE_VERSION = 2

# The seven claim-kinds that get a v1 readable-body shape (existence/property
# are configuration inspections → a light one-line "what this checks"; the rest
# are behavioral → the full body). Any other kind takes the envelope-only path.
_CONFIG_KINDS = frozenset({"existence-claim", "property-claim"})
_BEHAVIORAL_KINDS = frozenset({
    "value-claim", "prohibition-claim", "acceptance-claim",
    "state-transition-claim", "automation-effect-claim",
})
_REGISTERED_KINDS = _CONFIG_KINDS | _BEHAVIORAL_KINDS

# Static "what this checks" glosses. Per-kind, always TRUE of the kind (no
# claim-specific tokens) — the headline carries the specifics.
_CONFIG_GLOSS = (
    "This is a configuration check — it confirms the setup exists in the org "
    "but does not exercise its enforcement."
)
_BEHAVIORAL_CHECKS = {
    "value-claim":
        "Verifies the field saves with the required value after the record is "
        "created.",
    "prohibition-claim":
        "Confirms the org blocks this operation and surfaces the expected error.",
    "acceptance-claim":
        "Confirms the org accepts this operation with the given data — no "
        "validation error.",
    "state-transition-claim":
        "Confirms the record reaches the expected state after the triggering "
        "change.",
    "automation-effect-claim":
        "Confirms the automation fires and produces the expected effect.",
}
_ABSENCE_CHECK = (
    "Confirms the automation correctly stays silent — no follow-up record is "
    "created."
)


# ---------------------------------------------------------------------------
# Typed skeleton
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReadableStep:
    """One conceptual, business-language step narrated from a recipe authoring
    step (the real recipe stays in the technical drawer)."""

    ordinal: int
    narration: str
    grounds: tuple = ()          # tuple[str, ...] — tokens this step introduced


@dataclass(frozen=True)
class ReadableProbe:
    """One bva boundary probe (one data-recipe of a ``strategy_kind='bva'``
    claim): the distinguishing input value + its per-probe expectation."""

    label: str
    input_value: str
    expected: str
    grounds: tuple = ()          # tuple[str, ...]


@dataclass(frozen=True)
class ReadableBodySkeleton:
    """Facts-only skeleton of a readable test case. Every string is grounded in
    the claim/recipe; ``grounded_tokens`` is the Stage-2 validator's allow-set;
    ``skeleton_content_hash`` is the phrasing-cache key input."""

    kind: str
    registered: bool
    headline: str
    depth: str
    checks: Optional[str]
    preconditions: tuple                 # tuple[str, ...]
    test_data: tuple                     # tuple[tuple[str, str], ...] (label, value)
    steps: tuple                         # tuple[ReadableStep, ...]
    expected_result: Optional[str]
    probes: tuple                        # tuple[ReadableProbe, ...]
    grounded_tokens: frozenset           # frozenset[str]
    skeleton_content_hash: str


def to_display_dict(s: "ReadableBodySkeleton") -> dict:
    """The template-facing view of a skeleton — JSON-safe, and omits the internal
    ``grounded_tokens`` + ``skeleton_content_hash`` (the reader attaches this as
    ``claim['readable_body']`` for rendering; the skeleton object itself is kept
    separately for the optional Stage-2 phrasing)."""
    return {
        "kind": s.kind,
        "registered": s.registered,
        "headline": s.headline,
        "depth": s.depth,
        "checks": s.checks,
        "preconditions": list(s.preconditions),
        "test_data": [[label, value] for (label, value) in s.test_data],
        "steps": [{"ordinal": st.ordinal, "narration": st.narration}
                  for st in s.steps],
        "expected_result": s.expected_result,
        "probes": [{"label": p.label, "input_value": p.input_value,
                    "expected": p.expected} for p in s.probes],
    }


# ---------------------------------------------------------------------------
# Grounding tokenizer (the single normalization source; the Stage-2 validator
# reuses extract_named_tokens so both sides normalize identically)
# ---------------------------------------------------------------------------

# Punctuation stripped from token boundaries so '"High"' and High normalize alike.
_PUNCT = "\"'“”‘’`.,;:!?()[]{}<>"
# A "named" span the validator must ground: a multi-word Capitalized label
# (Credit Score, Risk Rating), a quoted literal, or a number.
_CAP_SPAN = re.compile(r"[A-Z][A-Za-z0-9]*(?:\s+[A-Z][A-Za-z0-9]*)+")
_QUOTED = re.compile(r"[\"“'‘]([^\"”'’]+)[\"”'’]")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_NUM_STRIP = re.compile(r"[,\s$£€₹]")
_NUM_FULL = re.compile(r"\d+(?:\.\d+)?$")

# Capitalized words that are sentence-glue, not part of a field/object label
# (Salesforce labels never start/end with these). Trimmed from span boundaries
# so "The Loan" → "Loan" (a single word → not label-checked) while "When Credit
# Score" → "Credit Score" (the real label, grounded). Values are checked via the
# strict number/quote paths, so relaxing spans here can't hide a bad number.
_SPAN_STOP = frozenset({
    "the", "a", "an", "this", "that", "these", "those", "it", "its", "when",
    "if", "then", "and", "or", "but", "no", "not", "to", "with", "for", "of",
    "on", "in", "at", "by", "from", "as", "is", "are", "was", "be", "org",
    "salesforce", "confirm", "create", "read", "update", "delete", "attempt",
    "run", "given", "after", "before", "so", "checks", "verifies", "accepts",
    "rejects", "automatically", "sets",
})


def _core_span(span: Any) -> str:
    """A Capitalized span trimmed of boundary sentence-glue, returned only when
    2+ label words remain — else '' (single/emptied spans are not label-checked
    to avoid false-positives on sentence-initial capitals)."""
    words = [w.strip(_PUNCT) for w in _norm(span).split()]
    while words and words[0] in _SPAN_STOP:
        words.pop(0)
    while words and words[-1] in _SPAN_STOP:
        words.pop()
    return " ".join(words) if len(words) >= 2 else ""


def _norm(s: Any) -> str:
    """Canonical token form: lowercased, whitespace-collapsed, punctuation
    stripped from the boundaries. Non-str coerced via str()."""
    n = " ".join(str(s).split()).strip().lower()
    return n.strip(_PUNCT)


def _num_norm(s: Any) -> str:
    """A value's bare-number form (strip grouping / currency), or '' if not
    numeric — so ``80,00,000`` and ``8000000`` ground identically."""
    t = _NUM_STRIP.sub("", str(s))
    return t if _NUM_FULL.match(t) else ""


def _add(tokens: set, *values: Any) -> None:
    """Ground each value: its full normalized span, each word (so a rephrase of
    a multi-word label still grounds), and its bare-number form."""
    for v in values:
        n = _norm(v)
        if not n:
            continue
        tokens.add(n)
        for w in n.split():
            w = w.strip(_PUNCT)
            if len(w) >= 2 or w.isdigit():
                tokens.add(w)
        num = _num_norm(n)
        if num:
            tokens.add(num)


def _grow_text(tokens: set, text: Any) -> None:
    """Ground the NAMED tokens inside a rendered sentence — Capitalized label
    spans, quoted literals, numbers — so the model may restate the sentence."""
    if not text:
        return
    text = str(text)
    for span in _CAP_SPAN.findall(text):
        _add(tokens, span)
    for q in _QUOTED.findall(text):
        _add(tokens, q)
    for num in _NUMBER.findall(text):
        nn = _num_norm(num)
        if nn:
            tokens.add(nn)


def extract_named_tokens(text: Any) -> list:
    """The 'named' tokens a grounding validator must check in LLM prose: bare
    numbers, quoted literals, and multi-word Capitalized label spans — each
    normalized exactly as :func:`_add` grounds them. Generic prose words are
    intentionally EXCLUDED (only names/values/numbers are grounding-checked, so
    ordinary rephrasing never trips the validator). Public: the Stage-2
    validator imports this to share one normalization source with the builder."""
    text = str(text or "")
    out = []
    for num in _NUMBER.findall(text):
        nn = _num_norm(num)
        if nn:
            out.append(nn)
    for q in _QUOTED.findall(text):
        n = _norm(q)
        if n:
            out.append(n)
    for span in _CAP_SPAN.findall(text):
        core = _core_span(span)
        if core:
            out.append(core)
    return out


def _plain_value(v: Any) -> str:
    """A raw recipe/condition value as a short BA-friendly string (no quotes).
    Unwraps a ``LiteralValue`` dict; ``None`` → ``blank``."""
    if isinstance(v, dict) and "value" in v:
        v = v.get("value")
    if v is None:
        return "blank"
    return str(v)


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _preconditions(semantic_conditions: Any, labels, tokens: set) -> tuple:
    """The AND-composed semantic conditions as business sentences (label-
    resolved). Empty conditions → ``()`` (omitted: "applies unconditionally").
    Wording comes from the shared :func:`condition_phrase` — the SAME source
    the claim title's "when …" clause uses, so the two can never drift."""
    conds = None
    if isinstance(semantic_conditions, dict):
        conds = semantic_conditions.get("conditions")
    if not conds:
        return ()
    out = []
    for c in conds:
        rendered = condition_phrase(c, labels)
        if rendered is None:
            continue
        sentence, grounds = rendered
        _add(tokens, *grounds)
        out.append(sentence)
    return tuple(out)


def _first_data_recipe(recipes: Any) -> Optional[dict]:
    """The first current ``data-recipe`` dict (with an observation body), or
    None. Recipes arrive in the coordinator's deterministic recipe_id order."""
    for r in recipes or ():
        if isinstance(r, dict) and r.get("recipe_kind") == "data-recipe":
            if isinstance(r.get("observation_realization"), dict):
                return r
    return None


def _recipe_steps(recipe: Optional[dict]) -> list:
    if not isinstance(recipe, dict):
        return []
    obs = recipe.get("observation_realization")
    steps = obs.get("steps") if isinstance(obs, dict) else None
    return steps if isinstance(steps, list) else []


def _first_mutation_fields(recipe: Optional[dict]) -> Optional[dict]:
    """The (field → value) map of the recipe's first create/update step that
    carries values — the test-data / boundary-input source. None when absent."""
    for s in _recipe_steps(recipe):
        if not isinstance(s, dict):
            continue
        if s.get("kind") == "create":
            fv = s.get("field_values")
            if isinstance(fv, dict) and fv:
                return fv
        if s.get("kind") == "update":
            fc = s.get("field_changes")
            if isinstance(fc, dict) and fc:
                return fc
    return None


def _test_data(recipe: Optional[dict], labels, tokens: set) -> tuple:
    """(field_label, value) pairs from the recipe's first mutation step —
    label-resolved. This is where a bva boundary INPUT (e.g. 649) physically
    lives (the claim's asserted_truth does not carry it). () when absent."""
    fields = _first_mutation_fields(recipe)
    if not fields:
        return ()
    out = []
    for key, val in fields.items():
        label = _label(key, labels)
        value = _plain_value(val)
        _add(tokens, label, value)
        out.append((str(label), value))
    return tuple(out)


_ASSERT_PLAIN = {
    "equals": "Confirm the value matches what the test expects",
    "not_equals": "Confirm the value is not the forbidden one",
    "exists": "Confirm the record exists",
    "not_exists": "Confirm no such record exists",
    "is_null": "Confirm the value is blank",
    "not_null": "Confirm the value is set",
    "matches_pattern": "Confirm the value matches the required format",
}


def _narrate_step(step: dict, labels) -> Optional[tuple]:
    """One conceptual sentence + the tokens it grounds, for a recipe authoring
    step. Returns None for an unknown step kind (never fabricate). The step's
    concrete data lives in the Test-data section; steps stay object-level."""
    kind = step.get("kind")
    if kind == "create":
        obj = _ref_name(step.get("target_object"), labels) or "record"
        if step.get("expect_rejection"):
            return (f"Attempt to create a {obj} that the org should reject", (obj,))
        return (f"Create a {obj}", (obj,))
    if kind == "update":
        obj = _ref_name(step.get("target"), labels) or "the record"
        if step.get("expect_rejection"):
            return (f"Attempt an edit to the {obj} that the org should reject", (obj,))
        return (f"Update the {obj}", (obj,))
    if kind == "delete":
        obj = _ref_name(step.get("target"), labels) or "the record"
        if step.get("expect_rejection"):
            return (f"Attempt to delete the {obj} — the org should reject it", (obj,))
        return (f"Delete the {obj}", (obj,))
    if kind == "read":
        obj = _ref_name(step.get("target"), labels) or "the record"
        return (f"Read the {obj} back to check the result", (obj,))
    if kind == "assert":
        pred = step.get("predicate") or {}
        phrase = _ASSERT_PLAIN.get(
            pred.get("predicate") if isinstance(pred, dict) else None,
            "Confirm the result matches what the test expects",
        )
        return (phrase, ())          # subject_ref is recipe-internal → not grounded
    if kind == "apex":
        return ("Run a scripted check", ())
    return None


def _steps(recipe: Optional[dict], labels, tokens: set) -> tuple:
    out = []
    ordinal = 1
    for s in _recipe_steps(recipe):
        if not isinstance(s, dict):
            continue
        narrated = _narrate_step(s, labels)
        if narrated is None:
            continue
        narration, grounds = narrated
        _add(tokens, *grounds)
        out.append(ReadableStep(ordinal, narration, tuple(_norm(g) for g in grounds)))
        ordinal += 1
    return tuple(out)


def _expected_result(claim_kind: str, asserted: dict, labels, tokens: set) -> Optional[str]:
    """The claim's asserted outcome as one plain sentence, or None when the
    body carries no groundable outcome (then the section is omitted). The
    field/value extraction is the shared ``expected_binding`` dispatch (also
    the run page's expected-vs-actual source) — only the sentence framing
    lives here."""
    if claim_kind == "value-claim":
        subj = _ref_name(asserted.get("subject"), labels) or "the field"
        binding = expected_binding(claim_kind, asserted)
        ev = binding[1] if binding else _expected_value(asserted.get("expected_value"))
        _add(tokens, subj, ev)
        return f"{subj} saves as {ev}"
    if claim_kind == "prohibition-claim":
        rej = asserted.get("expected_rejection") or {}
        base = "The org rejects the operation"
        code = rej.get("error_code") if isinstance(rej, dict) else None
        if code:
            _add(tokens, code)
            base = f"{base} (error {code})"
        return base
    if claim_kind == "acceptance-claim":
        if asserted.get("operation") == "update":
            change = expected_binding(claim_kind, asserted)
            if change:
                fkey, val = change
                label = _label(fkey, labels)
                _add(tokens, label, val)
                return f"The org accepts setting {label} to {val}"
            return "The org accepts the update"
        return "The org accepts the creation"
    if claim_kind == "state-transition-claim":
        change = expected_binding(claim_kind, asserted)
        if change:
            fkey, val = change
            label = _label(fkey, labels)
            _add(tokens, label, val)
            return f"{label} becomes {val}"
        return None
    if claim_kind == "automation-effect-claim":
        if asserted.get("expected_absence"):
            return "No automation record is produced."
        effect = asserted.get("expected_effect") or {}
        ekind = effect.get("kind") if isinstance(effect, dict) else None
        if ekind == "field_change":
            change = expected_binding(claim_kind, asserted)
            if change:
                fkey, val = change
                label = _label(fkey, labels)
                obj = _object_label_of(fkey, labels)
                where = f"{obj} {label}" if obj else label
                _add(tokens, label, val)
                if obj:
                    _add(tokens, obj)
                return f"{where} becomes {val}"
            return "The automation updates the record"
        if ekind == "blocked_operation":
            return "The change is blocked."
        # side_effect: description is free-form prose → omit rather than echo it.
        return None
    return None


def _probes(data_recipe_ids: Any, recipes: Any, expected_result: Optional[str],
            labels, tokens: set) -> tuple:
    """One :class:`ReadableProbe` per authored data-recipe (the bva membership),
    in the given deterministic id order. Each probe's input is its create
    field_values; its expectation is grounded per-probe from the step flags."""
    by_id = {}
    for r in recipes or ():
        if isinstance(r, dict) and r.get("recipe_id"):
            by_id[str(r.get("recipe_id"))] = r
    out = []
    for i, rid in enumerate(data_recipe_ids or ()):
        recipe = by_id.get(str(rid))
        if recipe is None:
            continue
        fields = _first_mutation_fields(recipe)
        pairs = []
        grounds = []
        if fields:
            for key, val in fields.items():
                label = _label(key, labels)
                value = _plain_value(val)
                pairs.append(f"{label} = {value}")
                grounds.extend([label, value])
        input_value = ", ".join(pairs) if pairs else "the default data"
        # Per-probe expectation, grounded in the recipe's own mutation step.
        create = None
        for s in _recipe_steps(recipe):
            if isinstance(s, dict) and s.get("kind") in ("create", "update"):
                create = s
                break
        if create is not None and create.get("expect_rejection"):
            expected = "the org rejects it"
        elif create is not None and create.get("expect_acceptance"):
            expected = "the org accepts it"
        else:
            expected = expected_result or "the expected result"
        _add(tokens, *grounds)
        out.append(ReadableProbe(
            label=f"Boundary case {i + 1}",
            input_value=input_value,
            expected=expected,
            grounds=tuple(_norm(g) for g in grounds),
        ))
    return tuple(out)


# ---------------------------------------------------------------------------
# Hash + assembly
# ---------------------------------------------------------------------------

def _skeleton_hash(kind, registered, headline, depth, checks, preconditions,
                   test_data, steps, expected_result, probes) -> str:
    """Byte-stable content hash over the rendered facts (excludes the derived
    grounded_tokens + the hash itself). Uses ``canonical_serialize`` — the same
    sorted-keys / no-whitespace discipline as the substrate's identity_hash."""
    payload = {
        "v": SKELETON_SHAPE_VERSION,
        "kind": kind,
        "registered": registered,
        "headline": headline,
        "depth": depth,
        "checks": checks,
        "preconditions": list(preconditions),
        "test_data": [[a, b] for (a, b) in test_data],
        "steps": [{"ordinal": s.ordinal, "narration": s.narration} for s in steps],
        "expected_result": expected_result,
        "probes": [{"label": p.label, "input_value": p.input_value,
                    "expected": p.expected} for p in probes],
    }
    return hashlib.sha256(
        canonical_serialize(payload).encode("utf-8"),
    ).hexdigest()


def _ground_all(headline, checks, preconditions, test_data, steps,
                expected_result, probes, tokens) -> None:
    """Final grounding pass: ensure ``grounded_tokens`` is a SUPERSET of every
    named token in every string sent to the model — so a faithful restatement
    can never be flagged; only a genuine fabrication (a value/label the skeleton
    never rendered) fails the Stage-2 validator."""
    for t in (headline, checks, expected_result):
        _grow_text(tokens, t)
    for p in preconditions:
        _grow_text(tokens, p)
    for label, value in test_data:
        _add(tokens, label, value)
    for st in steps:
        _grow_text(tokens, st.narration)
    for pr in probes:
        _add(tokens, pr.label)
        _grow_text(tokens, pr.input_value)
        _grow_text(tokens, pr.expected)


def _assemble(*, kind, registered, headline, depth, checks, preconditions,
              test_data, steps, expected_result, probes, tokens) -> ReadableBodySkeleton:
    _ground_all(headline, checks, preconditions, test_data, steps,
                expected_result, probes, tokens)
    return ReadableBodySkeleton(
        kind=kind,
        registered=registered,
        headline=headline,
        depth=depth,
        checks=checks,
        preconditions=preconditions,
        test_data=test_data,
        steps=steps,
        expected_result=expected_result,
        probes=probes,
        grounded_tokens=frozenset(tokens),
        skeleton_content_hash=_skeleton_hash(
            kind, registered, headline, depth, checks, preconditions,
            test_data, steps, expected_result, probes),
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_readable_body(*, claim_kind, archetype, asserted_truth,
                        semantic_conditions, recipes, strategy_kind,
                        data_recipe_ids, labels) -> ReadableBodySkeleton:
    """Build the facts-only readable-body skeleton for a claim.

    Pure, never raises (any unexpected shape falls back to a headline-only
    envelope). All inputs are already loaded by the claim-detail read; the only
    org-model dependency is ``labels`` (the :func:`entity_labels.label_map`
    result) for business-language resolution.

    Args:
      claim_kind: the claim's kind (e.g. ``"automation-effect-claim"``).
      archetype: the claim's archetype (unused for section logic in v1;
        accepted so the caller can pass the whole detail dict through).
      asserted_truth: the dumped claim body dict.
      semantic_conditions: the dumped conditions body dict.
      recipes: the list of dumped recipe dicts (as ``_read_claim_detail``
        returns them — each with observation_realization etc.).
      strategy_kind: ``'single'`` / ``'bva'`` / None (the claim's evaluation
        strategy; ``'bva'`` with >1 data-recipe surfaces the probe set).
      data_recipe_ids: the current data-recipe ids (the bva probe membership,
        coordinator order).
      labels: the ``{sf_api_name: display_name}`` map.

    Returns:
      A :class:`ReadableBodySkeleton`.
    """
    asserted = asserted_truth if isinstance(asserted_truth, dict) else {}
    recipes = recipes if isinstance(recipes, list) else []
    tokens: set = set()
    registered = claim_kind in _REGISTERED_KINDS
    # never raises; conditions render the distinguishing "when …" clause on the
    # condition-identified kinds (prohibition/acceptance)
    headline = claim_title(claim_kind, asserted, labels,
                           semantic_conditions=semantic_conditions)

    try:
        depth = claim_depth([r.get("recipe_kind") for r in recipes
                             if isinstance(r, dict)])

        # Configuration inspections: headline + a light one-line gloss only.
        if claim_kind in _CONFIG_KINDS:
            return _assemble(
                kind=claim_kind, registered=True, headline=headline, depth=depth,
                checks=_CONFIG_GLOSS, preconditions=(), test_data=(), steps=(),
                expected_result=None, probes=(), tokens=tokens)

        # Unregistered kinds: envelope-only. Ground ONLY the claim envelope
        # (headline + a target/subject "checks" line + conditions). Never read a
        # recipe, never emit steps/test-data/expected — never fabricate.
        if not registered:
            ref = asserted.get("target") or asserted.get("subject")
            name = _ref_name(ref, labels)
            checks = None
            if name:
                _add(tokens, name)
                checks = f"Checks {name}."
            preconditions = _preconditions(semantic_conditions, labels, tokens)
            return _assemble(
                kind=claim_kind, registered=False, headline=headline, depth=depth,
                checks=checks, preconditions=preconditions, test_data=(), steps=(),
                expected_result=None, probes=(), tokens=tokens)

        # Behavioral kinds: the full body.
        if claim_kind == "automation-effect-claim" and asserted.get("expected_absence"):
            checks = _ABSENCE_CHECK
        else:
            checks = _BEHAVIORAL_CHECKS.get(claim_kind)
        preconditions = _preconditions(semantic_conditions, labels, tokens)
        data_recipe = _first_data_recipe(recipes)
        test_data = _test_data(data_recipe, labels, tokens) if data_recipe else ()
        steps = _steps(data_recipe, labels, tokens) if data_recipe else ()
        expected_result = _expected_result(claim_kind, asserted, labels, tokens)
        probes = ()
        if strategy_kind == "bva" and len(data_recipe_ids or []) > 1:
            probes = _probes(data_recipe_ids, recipes, expected_result, labels, tokens)
        return _assemble(
            kind=claim_kind, registered=True, headline=headline, depth=depth,
            checks=checks, preconditions=preconditions, test_data=test_data,
            steps=steps, expected_result=expected_result, probes=probes, tokens=tokens)
    except Exception:
        # Presentation must never break a page: fall back to a headline-only
        # envelope (headline came from claim_title, which never raises).
        return _assemble(
            kind=claim_kind, registered=False, headline=headline,
            depth="configuration-check", checks=None, preconditions=(),
            test_data=(), steps=(), expected_result=None, probes=(), tokens=set())
