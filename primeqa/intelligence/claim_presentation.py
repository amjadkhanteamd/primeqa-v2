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
    list ("Tried the forbidden change — Salesforce blocked it");
  - :func:`step_plain` — one S4 evidence step as a plain sentence for the
    run-detail trace ("Created an Opportunity", "Tried the forbidden edit —
    Salesforce blocked it").

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


def _label(name: Any, labels) -> Any:
    """Resolve an sf_api_name to its business label via the (optional) label
    map, falling back to the api name itself when unmapped. Non-str passes
    through unchanged."""
    if labels and isinstance(name, str):
        return labels.get(name, name)
    return name


def _ref_name(ref: Any, labels=None) -> Optional[str]:
    """The business name of a reference dict: its label when the org model knows
    one (the label map), else the raw ``external_id`` (the api name)."""
    if isinstance(ref, dict):
        api = ref.get("external_id") or ref.get("sf_api_name")
        return _label(api, labels)
    return None


def _object_label_of(field_key: Any, labels) -> Optional[str]:
    """The owning object's business label for an object-qualified field key
    (``Case_SLA__c.Status__c`` → ``Case SLA``), or None when the key carries no
    object prefix."""
    if isinstance(field_key, str) and "." in field_key:
        return _label(field_key.split(".", 1)[0], labels)
    return None


def _literal(value: Any) -> str:
    if isinstance(value, dict):                  # LiteralValue {value: ...}
        value = value.get("value")
    return f'"{value}"' if isinstance(value, str) else str(value)


def _expected_value(ev: Any) -> str:
    """Render an ExpectedValue (LiteralValue / NullValue) as a short literal.
    A NullValue is an explicit absence assertion → ``blank`` rather than the
    ``None`` that ``_literal`` would print for a missing ``value`` key."""
    if isinstance(ev, dict) and ev.get("kind") == "null":
        return "blank"
    return _literal(ev)


def _first_field_change(state: Any) -> Optional[tuple]:
    """The first ``(field_external_id, rendered_value)`` of a StateDescriptor's
    ``field_values`` map, or None when there is no field-level binding."""
    if isinstance(state, dict):
        fv = state.get("field_values")
        if isinstance(fv, dict) and fv:
            field, ev = next(iter(fv.items()))
            return field, _expected_value(ev)
    return None


def claim_title(claim_kind: str, asserted_truth: Optional[dict],
                labels=None) -> str:
    """The claim as one plain-English sentence, in BUSINESS language.
    Deterministic; falls back to the humanized kind when the body lacks the
    expected fields. ``labels`` (an optional ``{sf_api_name: display_name}`` map
    from :func:`primeqa.intelligence.entity_labels.label_map`) resolves the
    subject/target api names to their org labels; without it the api names
    render verbatim (the pre-label behavior). API names, flow/recipe names, and
    schema terms (length/precision/scale) stay OUT of the title — they live in
    the technical-details register, never the spine."""
    body = asserted_truth or {}
    try:
        if claim_kind == "prohibition-claim":
            target = _ref_name(body.get("target"), labels) or "the object"
            op = _OPERATION_WORDS.get(body.get("operation"), "the operation")
            return f"Rejects {op} on {target}"
        if claim_kind == "value-claim":
            field = _ref_name(body.get("subject"), labels) or "the field"
            return f"{field} saves as {_literal(body.get('expected_value'))}"
        if claim_kind == "existence-claim":
            subject = _ref_name(body.get("subject"), labels) or "the metadata"
            return f"{subject} exists in the org"
        if claim_kind == "property-claim":
            subject = _ref_name(body.get("subject"), labels) or "the metadata"
            return _property_sentence(subject, body.get("property_name"),
                                      body.get("expected_value"))
        if claim_kind == "metadata-relationship-claim":
            src = _ref_name(body.get("source"), labels) or "the rule"
            tgt = _ref_name(body.get("target"), labels) or "the object"
            edge = (body.get("edge_type") or "applies to").replace("_", " ").lower()
            return f"{src} {edge} {tgt}"
        if claim_kind == "capability-claim":
            # The granting Profile/PermissionSet is stored under
            # ``granting_subject`` (CapabilityClaimBody) — NOT ``grantee``.
            grantee = _ref_name(body.get("granting_subject"), labels) or "the profile"
            target = _ref_name(body.get("target"), labels) or "the object"
            cap = (body.get("granted_capability") or "access").replace("_", " ")
            return f"{grantee} has {cap} on {target}"
        if claim_kind == "layout-claim":
            field = _ref_name(body.get("field"), labels) or "the field"
            layout = _ref_name(body.get("layout"), labels) or "the layout"
            return f"{field} appears on {layout}"
        if claim_kind == "state-transition-claim":
            subject = _ref_name(body.get("subject"), labels) or "the record"
            change = _first_field_change(body.get("to_state"))
            if change:
                fkey, val = change
                return f"{subject}: {_label(fkey, labels)} becomes {val}"
            return f"{subject} reaches the expected state"
        if claim_kind == "automation-effect-claim":
            # The asserted truth is the EFFECT, stated in business terms. The
            # triggering automation (a Flow/recipe API name) is traceability,
            # not the headline — it belongs in the technical details, never the
            # spine (so the title leads with the effect, not the flow name).
            effect = body.get("expected_effect") or {}
            ekind = effect.get("kind") if isinstance(effect, dict) else None
            if ekind == "field_change":
                change = _first_field_change(effect.get("changes"))
                if change:
                    fkey, val = change
                    field = _label(fkey, labels)
                    obj = _object_label_of(fkey, labels)
                    where = f"{obj} {field}" if obj else field
                    return f"Automatically sets {where} to {val}"
                return "An automation updates the record"
            if ekind == "blocked_operation":
                return "An automation blocks the change"
            if ekind == "side_effect":
                return "An automation fires a side effect"
            return "An automation fires"
    except Exception:
        pass
    return claim_kind.replace("-", " ")


# Property-claim readable templates (D-267): the schema term — length /
# precision / scale — must NOT appear in the spine; it states the constraint in
# business words. Other property kinds keep a generic, label-resolved fallback.
def _property_num(ev: Any) -> Any:
    """The magnitude of a property's ExpectedValue (LiteralValue) — the raw
    number, unquoted (a length / precision / scale is always a count)."""
    if isinstance(ev, dict):
        ev = ev.get("value")
    return ev


# Business phrasing for the boolean field-detail properties that can ground (S1
# ``field_details`` columns) — keeps the raw snake_case column name off the spine
# (review D-267). Each entry: (phrase when the asserted value is true, false).
_BOOL_PROPERTY = {
    "is_unique": ("{s} must be unique", "{s} allows duplicate values"),
    "is_nillable": ("{s} can be left blank", "{s} is required"),
    "is_custom": ("{s} is a custom field", "{s} is a standard field"),
    "is_calculated": ("{s} is a formula field", "{s} is not a formula field"),
    "is_external_id": ("{s} is an external ID", "{s} is not an external ID"),
}


def _is_truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "1", "yes")


def _property_sentence(subject: str, property_name: Any,
                       expected_value: Any) -> str:
    """One business sentence for a property-claim: ``length`` → "is N
    characters", ``precision`` → "holds up to N digits", ``scale`` → "has N
    decimal place(s)"; the boolean field flags + ``field_type`` get business
    phrasings. Any other property keeps a generic form with the property name
    HUMANIZED (``is_unique`` → "is unique") — a raw schema column never reaches
    the spine."""
    n = _property_num(expected_value)
    if property_name == "length":
        return f"{subject} is {n} characters"
    if property_name == "precision":
        return f"{subject} holds up to {n} digits"
    if property_name == "scale":
        return f"{subject} has {n} decimal place{'' if n == 1 else 's'}"
    if property_name == "field_type" and isinstance(n, str) and n:
        return f"{subject} is a {n} field"
    if property_name in _BOOL_PROPERTY:
        tmpl = _BOOL_PROPERTY[property_name][0 if _is_truthy(n) else 1]
        return tmpl.format(s=subject)
    # generic fallback: humanize the property name so no snake_case column leaks.
    prop = (property_name or "property").replace("_", " ")
    return f"{subject}: {prop} is {_literal(expected_value)}"


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

# D-272 Slice 1: a PERMANENT not_evaluated (the test itself could not be
# built/run — failure_category 'normalization') is honestly NOT a re-run.
_NOT_EVALUATED_PERMANENT = (
    "Could not be evaluated — the test itself could not be built or run "
    "(needs attention, not a retry)")


def verdict_plain(verdict: Optional[str], outcome: Optional[str] = None,
                  failure_category: Optional[str] = None) -> str:
    """One sentence for a run row: the S6 verdict's plain words, falling back
    to the bare outcome when no interpretation was recorded.

    ``failure_category`` (the run's typed error class, D-261) splits the
    ``not_evaluated`` line per D-272 §2.4: a permanent our-side defect
    (``normalization``) reads "needs attention", everything else keeps the
    re-runnable credentials/infrastructure line. Default ``None`` → the
    indeterminate line, so every pre-D-272 caller is unchanged."""
    from primeqa.integrations.failure_taxonomy import is_indeterminate
    if verdict == "not_evaluated" and not is_indeterminate(failure_category):
        return _NOT_EVALUATED_PERMANENT
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
    "behaviour-incomplete":
        "The org has a rule that should reject this, but the engine cannot yet "
        "build a concrete test that provokes the rejection — so it refuses "
        "rather than emit a weaker check that would not actually exercise the rule.",
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


# ---------------------------------------------------------------------------
# Plain-words evidence-step lines (D-233 — readable run detail)
# ---------------------------------------------------------------------------

# One plain sentence per S4 evidence step so the run-detail trace reads as a
# story instead of a raw JSON dump. The raw step tree stays one click away; this
# is the headline. Dispatches on ``kind`` (read / assert / create / update /
# delete) + the outcome fields (success / held / matched / error). A mutation
# with ``matched`` set is a NEGATIVE test (a rejection was expected); ``matched``
# None is a positive/setup mutation.

# Negative-test mutation phrasings (a rejection was expected), keyed by kind.
_MUT_BLOCKED = {
    "create": "Tried to create a forbidden {obj} — Salesforce blocked it{code}",
    "update": "Tried the forbidden edit on {obj} — Salesforce blocked it{code}",
    "delete": "Tried the forbidden delete of {obj} — Salesforce blocked it{code}",
}
_MUT_LEAKED = {     # the org ALLOWED what should have been rejected — a defect
    "create": "Created a {obj} that should have been rejected — a real defect",
    "update": "Edited the {obj} when it should have been blocked — a real defect",
    "delete": "Deleted the {obj} when it should have been blocked — a real defect",
}
_MUT_OTHERRULE = {  # blocked, but not by the rule under test
    "create": "Creation was blocked, but by a different rule{code}",
    "update": "The edit was blocked, but by a different rule{code}",
    "delete": "The delete was blocked, but by a different rule{code}",
}
# Positive / setup mutation phrasings (no rejection expected).
_MUT_OK = {
    "create": "Created a {obj}",
    "update": "Edited the {obj}",
    "delete": "Deleted the {obj}",
}
_MUT_FAIL = {
    "create": "Couldn't create the {obj}{code}",
    "update": "Couldn't edit the {obj}{code}",
    "delete": "Couldn't delete the {obj}{code}",
}


def _err_msg(err: Any) -> str:
    if isinstance(err, dict):
        return err.get("message") or err.get("error_type") or "an error occurred"
    return "an error occurred"


# Salesforce metadata / Tooling sobjects that surface as the read target on an
# inspection run — schema terms that must not leak into the spine. Mapped to a
# plain phrase; a custom data sobject resolves via the org label map; anything
# else falls back to the raw name (already business-ish for data objects).
_SYSTEM_SOBJECT_PLAIN = {
    "EntityDefinition": "object definition",
    "FieldDefinition": "field definition",
    "CustomField": "field definition",
    "CustomObject": "object definition",
    "ValidationRule": "validation rule",
    "FlowDefinitionView": "flow definition",
    "RecordType": "record type",
    "PermissionSet": "permission set",
    "Profile": "profile",
}


def _sobj(step: dict, labels=None) -> str:
    raw = step.get("sobject")
    if not raw:
        return "record"
    if raw in _SYSTEM_SOBJECT_PLAIN:                  # a schema/Tooling sobject
        return _SYSTEM_SOBJECT_PLAIN[raw]
    if labels and raw in labels:                      # a custom data sobject
        return labels[raw]
    return raw


def _mutation_plain(kind: str, step: dict, err: Any, labels=None) -> str:
    obj = _sobj(step, labels)
    if err:
        return f"Couldn't attempt the {obj} {kind} — {_err_msg(err)}"
    success = bool(step.get("success"))
    matched = step.get("matched")
    code = step.get("error_code")
    code_sfx = f" ({code})" if code else ""
    if matched is not None:                # negative test — a rejection expected
        tmpl = (_MUT_LEAKED[kind] if success
                else _MUT_BLOCKED[kind] if matched
                else _MUT_OTHERRULE[kind])
    else:                                  # positive / setup mutation
        tmpl = _MUT_OK[kind] if success else _MUT_FAIL[kind]
    return tmpl.format(obj=obj, code=code_sfx)


def step_plain(step: Any, labels=None) -> str:
    """One plain-English sentence for an S4 evidence step (the run-detail trace).
    Pure + never-raises: dispatches on ``step['kind']`` and the outcome fields,
    falling back to the kind word on any unexpected shape so the run page never
    breaks. ``labels`` (the org label map) de-jargons the read target —
    ``EntityDefinition`` → "object definition" (a schema sobject) or a custom
    sobject → its business label."""
    if not isinstance(step, dict):
        return "Step"
    kind = step.get("kind")
    try:
        err = step.get("error")
        if kind == "read":
            raw = step.get("sobject")
            obj = _sobj(step, labels)
            if err:
                return f"Couldn't read {obj} — {_err_msg(err)}"
            n = step.get("row_count")
            if raw in _SYSTEM_SOBJECT_PLAIN:    # metadata/config inspection read
                if n is None:
                    return f"Read the {obj}"
                return f"Read the {obj} ({n} row{'' if n == 1 else 's'})"
            if n is None:                       # data read — the established form
                return f"Read {obj}"
            return f"Read {n} {obj} row{'' if n == 1 else 's'}"
        if kind == "assert":
            if err:
                return f"Couldn't run the check — {_err_msg(err)}"
            held = step.get("held")
            if held is True:
                return "Checked the records — the assertion held"
            if held is False:
                return "Checked the records — the assertion did NOT hold"
            return "Checked the records"
        if kind in _MUT_OK:
            return _mutation_plain(kind, step, err, labels)
    except Exception:
        pass
    return kind.replace("_", " ") if isinstance(kind, str) else "step"


# ---------------------------------------------------------------------------
# Plain-words run attribution (D-267 — business language in the S6 attribution)
# ---------------------------------------------------------------------------

def _read_step_subject(steps: Any):
    """The ``(entity_type, external_id)`` of an inspection run's READ step — the
    subject the S6 attribution names. Mirrors ``interpreter._read_subject``'s
    source (the read step carries ``subject_entity_type`` /
    ``subject_external_id``). Returns ``(None, None)`` with no read step."""
    try:
        for s in steps or ():
            if isinstance(s, dict) and s.get("kind") == "read":
                return s.get("subject_entity_type"), s.get("subject_external_id")
    except Exception:
        pass
    return None, None


def humanize_attribution(attribution: Any, steps: Any, labels=None) -> Any:
    """Relabel a STORED S6 attribution sentence into business language at
    DISPLAY time. The attribution prose was persisted with the raw api name
    (``"The asserted metadata for Object Case_SLA__c is present …"``); this
    rewrites the subject phrase ``"<EntityType> <api_name>"`` →
    ``"the <label> <entity-type>"`` (``"the Case SLA object"``) using the org
    label map + the run's read-step subject — fixing OLD and new runs alike
    without mutating the substrate's store. Pure, never raises; returns the
    input unchanged when there is nothing to resolve."""
    if not attribution or not labels:
        return attribution
    try:
        entity_type, external_id = _read_step_subject(steps)
        if not entity_type or not external_id:
            return attribution
        label = labels.get(external_id)
        if not label:
            return attribution
        old = f"{entity_type} {external_id}"
        new = f"the {label} {entity_type.lower()}"
        return attribution.replace(old, new)
    except Exception:
        return attribution


# ---------------------------------------------------------------------------
# Plain-words cause + friendly time / duration (Results page — triage at scale)
# ---------------------------------------------------------------------------

# One plain sentence per S6 cause_kind — the failure-cluster headline on the
# Results page. The "real defect vs fixable" split is NOT here (that's
# repair_agent.proposal_for); this is only the human-readable name of the cause.
_CAUSE_PLAIN = {
    "enforcement_gap":
        "A forbidden action was allowed — the rule didn't fire",
    "vr_formula_drift":
        "The validation rule changed — it no longer blocks this",
    "vr_inactive":
        "The validation rule is turned off",
    "no_active_vr":
        "No active validation rule backs this anymore",
    "vr_formula_indeterminate":
        "The validation rule couldn't be evaluated on this record",
    "platform_constraint":
        "Blocked by a different rule than the one under test",
    "automation_inactive":
        "The automation is turned off — no active flow fires",
    "automation_effect_absent":
        "The automation ran but didn't make the expected change",
    "field_not_createable":
        "The test set a field the org won't accept on create",
}


def cause_plain(cause_kind: Optional[str]) -> Optional[str]:
    """One plain sentence for an S6 ``cause_kind`` (the failure-cluster
    headline). Falls back to the humanized kind; None → None."""
    if not cause_kind:
        return None
    return _CAUSE_PLAIN.get(cause_kind, cause_kind.replace("_", " ").capitalize())


def duration_human(ms: Any) -> str:
    """A run duration in friendly units: sub-second stays ``ms``, >= 1s →
    seconds with one decimal. None / non-numeric → ''."""
    if ms is None:
        return ""
    try:
        ms = int(ms)
    except (TypeError, ValueError):
        return ""
    if ms < 1000:
        return f"{ms} ms"
    return f"{ms / 1000:.1f}s"


def time_ago(iso: Optional[str], now=None) -> str:
    """A friendly relative time for an ISO timestamp string: 'just now',
    'Nm ago', 'Nh ago', 'Nd ago', then an absolute 'Mon DD' beyond a week.
    ``now`` is injectable so tests are deterministic. Never raises → '' on a
    bad/empty value."""
    if not iso:
        return ""
    from datetime import datetime, timezone
    try:
        ts = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    if now is None:
        now = datetime.now(timezone.utc)
    secs = (now - ts).total_seconds()
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    if secs < 7 * 86400:
        return f"{int(secs // 86400)}d ago"
    return ts.strftime("%b %d")
