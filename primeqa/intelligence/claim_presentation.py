"""Plain-English presentation of substrate claims (D-206 — triage-first pages).

Pure functions, no DB, no LLM: deterministic templaters from the claim's own
stored truth. Three surfaces:

  - :func:`claim_title` — the claim AS a sentence ("Rejects editing fields on
    Opportunity"), from ``claim_kind`` + ``asserted_truth``;
  - :func:`claim_depth` — the honesty badge: ``behavioral`` (the test actually
    exercises the org — a data-recipe mutation) vs ``configuration-check``
    (a metadata-recipe inspection: the rule/config exists, enforcement not
    exercised), from the claim's current recipe kinds;
  - :func:`verdict_plain` — an S6 verdict as one plain sentence for the runs
    list ("Tried the forbidden change — Salesforce blocked it").

Computed at READ time, never stored: a derived string column would go stale
across claim/recipe versions (the D-204 SCD lesson), and the inputs are always
on hand at render time. Falls back gracefully on any unexpected body shape —
presentation must never break a page.
"""
from __future__ import annotations

from typing import Any, Optional

# ---------------------------------------------------------------------------
# Titles
# ---------------------------------------------------------------------------

_OPERATION_WORDS = {
    "modify_record": "editing records",
    "modify_field": "editing fields",
    "delete": "deleting records",
    "create_duplicate": "creating duplicates",
    "share": "sharing records",
    "transfer_ownership": "transferring ownership",
}


def _ref_name(ref: Any) -> Optional[str]:
    """The human name of a reference dict (external_id preferred)."""
    if isinstance(ref, dict):
        return ref.get("external_id") or ref.get("sf_api_name")
    return None


def _literal(value: Any) -> str:
    if isinstance(value, dict):                  # LiteralValue {value: ...}
        value = value.get("value")
    return f'"{value}"' if isinstance(value, str) else str(value)


def claim_title(claim_kind: str, asserted_truth: Optional[dict]) -> str:
    """The claim as one plain-English sentence. Deterministic; falls back to
    the humanized kind when the body lacks the expected fields."""
    body = asserted_truth or {}
    try:
        if claim_kind == "prohibition-claim":
            target = _ref_name(body.get("target")) or "the object"
            op = _OPERATION_WORDS.get(body.get("operation"), "the operation")
            return f"Rejects {op} on {target}"
        if claim_kind == "value-claim":
            field = _ref_name(body.get("subject")) or "the field"
            return f"{field} saves as {_literal(body.get('expected_value'))}"
        if claim_kind == "existence-claim":
            subject = _ref_name(body.get("subject")) or "the metadata"
            return f"{subject} exists in the org"
        if claim_kind == "property-claim":
            subject = _ref_name(body.get("subject")) or "the metadata"
            prop = body.get("property_name") or "property"
            return f"{subject}: {prop} is {_literal(body.get('expected_value'))}"
        if claim_kind == "metadata-relationship-claim":
            src = _ref_name(body.get("source")) or "the rule"
            tgt = _ref_name(body.get("target")) or "the object"
            edge = (body.get("edge_type") or "applies to").replace("_", " ").lower()
            return f"{src} {edge} {tgt}"
        if claim_kind == "capability-claim":
            grantee = _ref_name(body.get("grantee")) or "the profile"
            target = _ref_name(body.get("target")) or "the object"
            cap = (body.get("granted_capability") or "access").replace("_", " ")
            return f"{grantee} has {cap} on {target}"
        if claim_kind == "layout-claim":
            field = _ref_name(body.get("field")) or "the field"
            layout = _ref_name(body.get("layout")) or "the layout"
            return f"{field} appears on {layout}"
    except Exception:
        pass
    return claim_kind.replace("-", " ")


# ---------------------------------------------------------------------------
# Depth badge
# ---------------------------------------------------------------------------

def claim_depth(recipe_kinds: Any) -> str:
    """``behavioral`` when any current recipe is a data-recipe (the test
    actually mutates the org and observes the response); otherwise
    ``configuration-check`` (a metadata inspection — the rule/config exists,
    its enforcement is NOT exercised). Empty/unknown → ``configuration-check``
    (never overstate)."""
    kinds = set(recipe_kinds or ())
    if "data-recipe" in kinds:
        return "behavioral"
    return "configuration-check"


# ---------------------------------------------------------------------------
# Plain-words run lines
# ---------------------------------------------------------------------------

_VERDICT_PLAIN = {
    "prohibition_enforced":
        "Tried the forbidden change — Salesforce blocked it",
    "prohibition_not_enforced":
        "Tried the forbidden change — Salesforce ALLOWED it (a real defect)",
    "rejected_unasserted_reason":
        "Blocked, but by a different rule than the one under test",
    "value_persisted":
        "Created the record — the value saved exactly as required",
    "value_not_persisted":
        "Created the record — but the value did not persist as required",
    "state_transitioned":
        "Created the record — Salesforce moved it to the expected state",
    "state_not_transitioned":
        "Created the record — but Salesforce did NOT move it to the expected state",
    "automation_triggered":
        "Triggered the automation — it produced the expected result",
    "automation_not_triggered":
        "Triggered the automation — but the expected result never appeared",
    "asserted_metadata_present":
        "The configuration exists in the org (existence only — enforcement "
        "not exercised)",
    "asserted_metadata_absent":
        "The expected configuration is MISSING from the org",
    "asserted_value_matches":
        "The configured value matches what the test asserts",
    "asserted_value_differs":
        "The configured value DIFFERS from what the test asserts",
    "not_evaluated":
        "Could not be evaluated (credentials/infrastructure) — re-run",
}

_OUTCOME_PLAIN = {
    "passed": "Passed",
    "failed": "Failed",
    "errored": "Could not run to completion",
}


def verdict_plain(verdict: Optional[str], outcome: Optional[str] = None) -> str:
    """One sentence for a run row: the S6 verdict's plain words, falling back
    to the bare outcome when no interpretation was recorded."""
    if verdict and verdict in _VERDICT_PLAIN:
        return _VERDICT_PLAIN[verdict]
    if outcome and outcome in _OUTCOME_PLAIN:
        return _OUTCOME_PLAIN[outcome]
    return "No result recorded"


# ---------------------------------------------------------------------------
# Plain-words refusal note (D-206.1)
# ---------------------------------------------------------------------------

# One plain sentence per S3 refusal kind — WHY a generation produced no tests.
# Without this a refusal renders as "No test plan yet" and reads as a silent
# failure (the SQ-205 confusion).
_REFUSAL_PLAIN = {
    "underspecified-requirement":
        "The requirement is too vague to anchor a test — it does not name a "
        "concrete behavior or target.",
    "no-relevant-context":
        "The requirement references things that do not exist in the synced "
        "org model.",
    "ambiguous-reference":
        "The requirement matched more than one thing in the org — it could "
        "not be resolved to a single target.",
    "ungrounded-claim":
        "The org has no rule or configuration backing this — there is "
        "nothing real to test against.",
    "structural-validation-failure":
        "The AI could not produce a valid proposal for this requirement "
        "(often: it names objects or fields that do not exist in the org).",
    "low-generation-confidence":
        "The engine was not confident enough in any interpretation to "
        "commit to a test.",
    "no-admissible-negative-scenario-found":
        "No org rule exists that would reject this — there is no real "
        "enforcement to test.",
    "operational-budget-exhausted":
        "Generation ran out of its processing budget before finishing — "
        "try again.",
    "emission-deferred":
        "The engine understood the requirement and found it testable, but "
        "authoring this kind of test is not built yet.",
}


def refusal_plain(refusal_kind: Optional[str],
                  refusals: Any = None) -> Optional[str]:
    """The refusal as one plain sentence, with the substrate's recorded detail
    appended when present (e.g. WHICH claim kind was deferred, WHICH refs were
    missing). ``refusals`` is the outcome's typed refusal list
    ``[{refusal_kind, payload}, ...]``. None when there is no refusal."""
    if not refusal_kind:
        return None
    base = _REFUSAL_PLAIN.get(refusal_kind,
                              refusal_kind.replace("-", " ").capitalize())
    detail = None
    try:
        for entry in refusals or ():
            payload = (entry or {}).get("payload") or {}
            detail = (payload.get("detail")
                      or payload.get("missing_refs")
                      or payload.get("reason"))
            if detail:
                break
    except Exception:
        detail = None
    if detail:
        if isinstance(detail, (list, tuple)):
            detail = ", ".join(str(d) for d in detail)
        return f"{base} ({detail})"
    return base
