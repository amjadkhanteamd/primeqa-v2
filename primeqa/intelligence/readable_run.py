"""Readable run RESULT — a derived read-side projection of one S4 run (Stage 1).

Pure functions, no DB, no LLM: the deterministic SKELETON of a QA-readable run
narrative — what the test actually did, with the concrete values it recorded —
built entirely from the run's captured evidence plus the claim's asserted
expectation and the org label map. The mirror of
:mod:`primeqa.intelligence.readable_body` (the test-case plain-language view),
evidence-shaped instead of recipe-shaped:

  - TEST DATA (as staged): the create/update step's actual field values, the
    semantic (recipe-authored) pairs prominent and S4's padding de-emphasized;
  - WHAT HAPPENED: the evidence steps narrated WITH values ("Created an
    Opportunity with Loan Amount = 5,000,000", "Read the record back —
    Loan-to-Value (%) = 50", "Checked the result: expected 50 — matched");
  - RESULT: expected vs actual. The EXPECTED value comes from the claim's
    asserted truth (via the shared ``expected_binding`` dispatch — the same
    source the test-case card's Expected-result section uses); the ACTUAL from
    the read-back rows; **matched/did-not-match comes from the assert step's
    recorded ``held`` — this module never re-judges the outcome**.

Same laws as readable_body: derived never stored; deterministic facts only
(the optional Stage-2 LLM phrasing may only restate them —
``grounded_tokens`` is the validator's allow-set); never fabricate (an errored
run gets a minimal skeleton: the value under test was never evaluated, so no
test-data/steps/expected are narrated); never raises.

v1 scope: the first/primary assertion only (the ``_first_field_change``
convention shared with the test-case card) — multi-assert runs narrate the
first binding and fall back to :func:`step_plain` lines for the rest.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Optional

from primeqa.intelligence.claim_presentation import (
    _label,
    expected_binding,
    fmt_number,
    step_plain,
)
from primeqa.intelligence.readable_body import _add, _grow_text
from primeqa.test_representation.canonicalization import canonical_serialize

# Bumping this invalidates every cached run phrasing keyed on a skeleton hash —
# any builder change that alters a rendered string must move it.
RUN_SKELETON_SHAPE_VERSION = 1

# The most padding pairs the de-emphasized drawer lists (the raw step data one
# click below carries everything regardless).
_MAX_SUPPORTING = 12


@dataclass(frozen=True)
class ReadableRunStep:
    """One evidence step narrated with its recorded values."""

    ordinal: int
    narration: str


@dataclass(frozen=True)
class ReadableRunSkeleton:
    """Facts-only skeleton of a readable run result. Every string is grounded
    in the run's evidence / the claim's assertion; ``grounded_tokens`` is the
    Stage-2 validator's allow-set; ``skeleton_content_hash`` the phrasing-cache
    key input (runs are immutable → the hash is stable forever)."""

    outcome: str                         # passed | failed | errored | skipped
    headline: Optional[str]              # the claim's business title (caller's)
    narrative: Optional[str]             # deterministic Stage-1 paragraph
    test_data: tuple                     # ((label, value), ...) — semantic pairs
    supporting_data: tuple               # ((label, value), ...) — padding pairs
    steps: tuple                         # (ReadableRunStep, ...)
    expected: Optional[str]              # "Loan-to-Value (%) is 50"
    result_sentence: Optional[str]       # "expected 50 — matched" / "…did not match"
    version_caveat: Optional[str]
    grounded_tokens: frozenset
    skeleton_content_hash: str


def to_display_dict(s: "ReadableRunSkeleton") -> dict:
    """The template-facing view — JSON-safe, omits the internal grounding set +
    hash (the skeleton object itself is kept for the Stage-2 phrasing call)."""
    return {
        "outcome": s.outcome,
        "headline": s.headline,
        "narrative": s.narrative,
        "test_data": [[a, b] for (a, b) in s.test_data],
        "supporting_data": [[a, b] for (a, b) in s.supporting_data],
        "steps": [{"ordinal": st.ordinal, "narration": st.narration}
                  for st in s.steps],
        "expected": s.expected,
        "result_sentence": s.result_sentence,
        "version_caveat": s.version_caveat,
    }


# ---------------------------------------------------------------------------
# Evidence readers (dict-shaped: the persisted evidence JSONB steps)
# ---------------------------------------------------------------------------

def _bare(key: Any) -> str:
    """An object-qualified field key → its bare Salesforce name (evidence rows
    and staged payloads speak bare names)."""
    return str(key).split(".", 1)[-1] if key else str(key)


def _field_label(key: Any, sobject: Any, labels) -> str:
    """Business label for an evidence field key: try the object-qualified form
    (the label-map convention for fields), then the key as given."""
    if sobject:
        qualified = f"{sobject}.{_bare(key)}"
        if labels and qualified in (labels or {}):
            return labels[qualified]
    return str(_label(key, labels))


def _first_mutation_step(steps) -> Optional[dict]:
    """The first create/update evidence step carrying staged values."""
    for s in steps or ():
        if not isinstance(s, dict):
            continue
        if s.get("kind") == "create" and isinstance(s.get("field_values"), dict):
            return s
        if s.get("kind") == "update" and isinstance(s.get("field_changes"), dict):
            return s
    return None


def _is_data_read(s: dict) -> bool:
    """A data read-back (DataReadEvidence) vs a metadata inspection read —
    both persist kind='read'; the data read carries rows + fields_captured."""
    return s.get("kind") == "read" and (
        "fields_captured" in s or "soql" in s)


def _first_data_read(steps) -> Optional[dict]:
    for s in steps or ():
        if isinstance(s, dict) and _is_data_read(s):
            return s
    return None


def _first_assert_held(steps) -> Optional[bool]:
    """The first assert step's recorded ``held`` — the run's own judgment."""
    for s in steps or ():
        if isinstance(s, dict) and s.get("kind") == "assert":
            held = s.get("held")
            if isinstance(held, bool):
                return held
    return None


def _staged_pairs(step: dict) -> list:
    fields = step.get("field_values") if step.get("kind") == "create" \
        else step.get("field_changes")
    return list(fields.items()) if isinstance(fields, dict) else []


def _obj_label(step: dict, labels) -> str:
    return str(_label(step.get("sobject"), labels) or "record")


# ---------------------------------------------------------------------------
# Section builders (each threads the tokens set, mirroring readable_body)
# ---------------------------------------------------------------------------

def _split_test_data(mutation: Optional[dict], semantic_field_keys,
                     binding_key: Optional[str], labels, tokens: set):
    """(test_data, supporting_data) label/value pairs from the staged mutation.
    Split by the recipe's semantic keys (bare); fallback: the expected-binding
    field alone; final fallback: everything is test_data (no split — honest)."""
    if mutation is None:
        return (), ()
    sobject = mutation.get("sobject")
    semantic = {(_bare(k)) for k in (semantic_field_keys or ())}
    if not semantic and binding_key:
        semantic = {_bare(binding_key)}
    test_data, supporting = [], []
    for key, val in _staged_pairs(mutation):
        label = _field_label(key, sobject, labels)
        value = fmt_number(val) if val is not None else "blank"
        _add(tokens, label, value)
        pair = (label, value)
        if not semantic or _bare(key) in semantic:
            test_data.append(pair)
        else:
            supporting.append(pair)
    if not test_data and supporting:
        # the split named nothing staged — show everything rather than hide all
        test_data, supporting = supporting, []
    return tuple(test_data), tuple(supporting[:_MAX_SUPPORTING])


def _narrate_mutation(step: dict, semantic_pairs: tuple, labels,
                      tokens: set) -> str:
    """'Created an Opportunity with Loan Amount = 5,000,000, …' — the semantic
    pairs only (padding stays in the supporting drawer); no pairs → the
    established step_plain headline."""
    if step.get("success") is False or step.get("error") or not semantic_pairs:
        return str(step_plain(step, labels))
    obj = _obj_label(step, labels)
    rendered = ", ".join(f"{label} = {value}" for label, value in semantic_pairs)
    verb = "Created" if step.get("kind") == "create" else "Updated"
    article = "an" if obj[:1].lower() in "aeiou" else "a"
    lead = f"{verb} {article} {obj}" if verb == "Created" else f"{verb} the {obj}"
    _add(tokens, obj)
    return f"{lead} with {rendered}"


def _narrate_data_read(step: dict, focus_key: Optional[str], labels,
                       tokens: set, subject_sobject: Optional[str] = None):
    """'Read the record back — Loan-to-Value (%) = 50' (the assertion's field
    when known, else the first captured field). Returns (narration, actual).

    "Read the record back" is only honest when the read targets the SUBJECT
    that was created. A cross-object observation (the read's sobject differs
    from the created subject's — e.g. ProcessInstance after an Opportunity
    create) narrates as the search it is: 0 rows there is the OBSERVATION of
    the missing (or correctly absent) effect, not the created record
    vanishing. Phrasing stays direction-neutral — absence claims (D-307) walk
    the same path, where finding nothing is the pass."""
    rows = step.get("rows")
    row = rows[0] if isinstance(rows, (list, tuple)) and rows \
        and isinstance(rows[0], dict) else None
    read_obj = step.get("sobject")
    cross = bool(read_obj and subject_sobject and read_obj != subject_sobject)
    obj = _obj_label(step, labels) if cross else None
    if row is None:
        if cross:
            return f"Looked for related {obj} records — found none", None
        return "Read the record back — nothing came back", None
    n = len(rows) if isinstance(rows, (list, tuple)) else 1
    lead = (f"Found {n} related {obj} record{'' if n == 1 else 's'}"
            if cross else "Read the record back")
    captured = [f for f in (step.get("fields_captured") or ()) if f != "Id"]
    bare_focus = _bare(focus_key) if focus_key else None
    field = bare_focus if bare_focus and bare_focus in row else (
        captured[0] if captured and captured[0] in row else None)
    if field is None:
        return lead, None
    actual = row.get(field)
    label = _field_label(field, step.get("sobject"), labels)
    value = fmt_number(actual) if actual is not None else "blank"
    _add(tokens, label, value)
    return f"{lead} — {label} = {value}", actual


def _result_pieces(binding, actual, held, expected_absence: bool,
                   labels, tokens: set):
    """(expected_sentence, result_sentence) from the claim's binding + the
    read-back + the assert step's recorded judgment. Never re-judges."""
    if expected_absence:
        expected = "no follow-up record appears"
        if held is True:
            return expected, "no record appeared — as expected"
        if held is False:
            return expected, "a record appeared — it must not have"
        return expected, None
    if not binding:
        return None, None
    fkey, expected_val = binding
    label = _label(fkey, labels)
    if isinstance(label, str) and "." in label:      # unlabeled qualified key
        label = _label(_bare(fkey), labels)
    # A quoted numeric literal ('"50"') displays bare so it reads like the
    # actual value it is compared against; genuinely textual literals keep
    # their quotes.
    if (isinstance(expected_val, str) and len(expected_val) >= 3
            and expected_val.startswith('"') and expected_val.endswith('"')
            and re.fullmatch(r"-?\d+(?:\.\d+)?", expected_val[1:-1])):
        expected_val = expected_val[1:-1]
    expected_val = fmt_number(expected_val)
    _add(tokens, label, expected_val)
    expected = f"{label} is {expected_val}"
    if held is True:
        return expected, f"expected {expected_val} — matched"
    if held is False:
        got = fmt_number(actual) if actual is not None else "nothing"
        _add(tokens, got)
        return expected, f"expected {expected_val}, got {got} — did not match"
    return expected, None


# ---------------------------------------------------------------------------
# Hash + assembly
# ---------------------------------------------------------------------------

def _skeleton_hash(outcome, headline, narrative, test_data, supporting_data,
                   steps, expected, result_sentence, version_caveat) -> str:
    payload = {
        "v": RUN_SKELETON_SHAPE_VERSION,
        "outcome": outcome,
        "headline": headline,
        "narrative": narrative,
        "test_data": [[a, b] for (a, b) in test_data],
        "supporting_data": [[a, b] for (a, b) in supporting_data],
        "steps": [{"ordinal": s.ordinal, "narration": s.narration} for s in steps],
        "expected": expected,
        "result_sentence": result_sentence,
        "version_caveat": version_caveat,
    }
    return hashlib.sha256(
        canonical_serialize(payload).encode("utf-8")).hexdigest()


def _assemble(*, outcome, headline, narrative, test_data, supporting_data,
              steps, expected, result_sentence, version_caveat,
              tokens) -> ReadableRunSkeleton:
    # Final grounding pass: the allow-set must be a superset of every named
    # token in every rendered string (mirrors readable_body._ground_all).
    for t in (headline, narrative, expected, result_sentence, version_caveat):
        _grow_text(tokens, t)
    for label, value in (*test_data, *supporting_data):
        _add(tokens, label, value)
    for st in steps:
        _grow_text(tokens, st.narration)
    return ReadableRunSkeleton(
        outcome=outcome, headline=headline, narrative=narrative,
        test_data=test_data, supporting_data=supporting_data, steps=steps,
        expected=expected, result_sentence=result_sentence,
        version_caveat=version_caveat,
        grounded_tokens=frozenset(tokens),
        skeleton_content_hash=_skeleton_hash(
            outcome, headline, narrative, test_data, supporting_data, steps,
            expected, result_sentence, version_caveat),
    )


_VERSION_CAVEAT = (
    "The test case has been edited since this run — the expectation shown is "
    "the one this run actually checked."
)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_readable_run(*, claim_kind, asserted_truth, outcome, verdict_plain,
                       steps, semantic_field_keys, labels, headline=None,
                       version_drift: bool = False) -> ReadableRunSkeleton:
    """Build the facts-only readable-run skeleton from one run's evidence.

    Pure, never raises (any unexpected shape falls back to a minimal
    outcome-only skeleton). ``steps`` are the persisted evidence step dicts;
    ``semantic_field_keys`` the recipe-authored staged field keys (bare or
    qualified; the k16 semantic set) used to split TEST DATA from padding —
    None degrades to the expected-binding field, then to no split;
    ``asserted_truth`` should be the claim version PINNED by the run when
    available (``version_drift=True`` adds the caveat line).

    An errored run gets the minimal skeleton — the value under test was never
    evaluated, so narrating staged data as "what the test did" would overstate.
    """
    outcome = str(outcome or "")
    tokens: set = set()
    minimal = dict(
        outcome=outcome, headline=headline, narrative=None, test_data=(),
        supporting_data=(), steps=(), expected=None, result_sentence=None,
        version_caveat=None, tokens=tokens)
    try:
        if outcome == "errored" or not steps:
            return _assemble(**minimal)

        asserted = asserted_truth if isinstance(asserted_truth, dict) else {}
        binding = expected_binding(claim_kind, asserted)
        expected_absence = bool(asserted.get("expected_absence")) \
            if claim_kind == "automation-effect-claim" else False

        mutation = _first_mutation_step(steps)
        test_data, supporting = _split_test_data(
            mutation, semantic_field_keys, binding[0] if binding else None,
            labels, tokens)

        # Narrate the evidence steps in order, with values.
        run_steps = []
        ordinal = 1
        actual = None
        data_read = _first_data_read(steps)
        for s in steps:
            if not isinstance(s, dict):
                continue
            kind = s.get("kind")
            if s is mutation and kind in ("create", "update"):
                narration = _narrate_mutation(s, test_data, labels, tokens)
            elif isinstance(s, dict) and _is_data_read(s):
                narration, got = _narrate_data_read(
                    s, binding[0] if binding else None, labels, tokens,
                    subject_sobject=(mutation or {}).get("sobject"))
                if s is data_read:
                    actual = got
            elif kind == "assert":
                held = s.get("held")
                if held is True:
                    narration = "Checked the result — it matched"
                elif held is False:
                    narration = "Checked the result — it did not match"
                else:
                    narration = str(step_plain(s, labels))
            elif kind:
                narration = str(step_plain(s, labels))
            else:
                continue                                  # never fabricate
            run_steps.append(ReadableRunStep(ordinal, narration))
            ordinal += 1

        held = _first_assert_held(steps)
        expected, result_sentence = _result_pieces(
            binding, actual, held, expected_absence, labels, tokens)

        # Enrich the bare assert narration with the expected-vs-actual line.
        if result_sentence:
            run_steps = tuple(
                ReadableRunStep(st.ordinal,
                                f"Checked the result: {result_sentence}")
                if st.narration.startswith("Checked the result") else st
                for st in run_steps)
        else:
            run_steps = tuple(run_steps)

        # Deterministic Stage-1 paragraph from already-grounded pieces.
        bits = [st.narration for st in run_steps
                if not st.narration.startswith("Checked the result")]
        if result_sentence and expected:
            outcome_word = "this matched" if held else "this did not match"
            bits.append(f"Expected: {expected} — {outcome_word}")
        elif result_sentence:
            bits.append(result_sentence)
        if verdict_plain:
            bits.append(str(verdict_plain))
        narrative = (". ".join(b.rstrip(".") for b in bits if b) + ".") \
            if bits else None

        return _assemble(
            outcome=outcome, headline=headline, narrative=narrative,
            test_data=test_data, supporting_data=supporting,
            steps=tuple(run_steps), expected=expected,
            result_sentence=result_sentence,
            version_caveat=_VERSION_CAVEAT if version_drift else None,
            tokens=tokens)
    except Exception:
        # Presentation must never break a page.
        return _assemble(**minimal)
