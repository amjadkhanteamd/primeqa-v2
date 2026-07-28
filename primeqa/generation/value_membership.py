"""Value-membership validation for enumerated fields (D-399 / D-412).

Deterministic, LLM-free, read-only post-emission check: every LITERAL a claim
or its recipes assert/stage against a picklist or multipicklist field is
checked for MEMBERSHIP in S1's captured value set. The live defect it exists
for: ``31eaa21e`` staged ``Opportunity.Loan_Type__c = "Home Loan"`` against an
org whose set is ``{Home, Personal, Business}`` — a claim that manufactures a
wrong-red by construction, burning a live org run + an S6 investigation on
every execution (D-399). The grounding validator checks FIELD grounding; this
checks VALUE membership — the gap it closes is exactly the one D-399 named.

**Three verdicts, never two (D-399.1 — the binding constraint).**

    VALID            the literal is in the field's captured ACTIVE set
    INVALID          it provably is not (authoritative capture only)
    CANNOT_VALIDATE  capture is not authoritative for this field —
                     no conclusion may be drawn, and refusal is FORBIDDEN

A two-verdict design (valid/invalid) silently converts every capture gap into
a refusal. A false refusal is SILENT — the claim is never emitted, coverage
has a hole, and nothing signals it; a wrong-red is LOUD — it fails on a live
run where a human sees it. Collapsing to two verdicts therefore inverts the
fail-loud posture: it trades the loud failure for the silent one. That
inversion is what made this validator unsafe to build before D-408 recorded
the capture OUTCOME per field; the gate below is the whole reason
``field_details.picklist_capture`` exists.

**The capture gate** (marks from D-408):

    gvs / inline / inline_standard  authoritative — may refuse. (``gvs`` is
                                    absent on env-59 today but asserts a fully
                                    captured GlobalValueSet per D-408; it
                                    gates identically to ``inline``.)
    inline_truncated                CANNOT_VALIDATE always. The stored set is
                                    a disclosed SUBSET (200-value cap, D-407);
                                    "not in the stored 200" cannot distinguish
                                    "org lacks it" from "we truncated it away".
    no_values                       the org's set is known and EMPTY — refusing
                                    any literal is correct. Reasoning: this
                                    mark is written only when the describe
                                    payload itself carried zero values, and all
                                    8 such fields on env-59 were re-verified
                                    against the live org describe (0 values
                                    each, D-408) — honest absence, not a
                                    capture gap. A member of the empty set does
                                    not exist, so every literal is INVALID
                                    (reason ``absent``).
    NULL                            PRE-MIGRATION capture (nothing wrote the
                                    mark) — "draw no conclusion" (D-408), so
                                    CANNOT_VALIDATE.
    anything else                   a future capture source this code does not
                                    know — CANNOT_VALIDATE (fail toward the
                                    loud path, never toward silent refusal).

**Matching** (D-412):

  * Compared against ``value_api_name`` AND ``value_label`` — the transport
    writes api-names, but generation quotes what it read, which may be the
    label (api ``BestCase`` / label ``Best Case``). Which one matched is
    carried on the check (``matched_on``) so a label-only match stays visible:
    it is VALID for membership but the transport payload may still need the
    api-name spelling.
  * ACTIVE values only for VALID. A literal present in the set but inactive is
    org DRIFT — ``INVALID`` with reason ``inactive`` — a different owner from
    ``absent`` (hallucination), and the two are never collapsed.
  * Exact string match, no case-folding: this is a test-authoring surface and
    a case mismatch in an assertion is a real defect, not noise. Multipicklist
    literals are split on ``;`` with whitespace trim (the SF wire format).

**Extraction is structured-only.** Literals come from ``field_values`` /
``field_changes`` maps (claim ``from_state``/``to_state``/``expected_effect``
and recipe ``steps[]``), ``{kind: "literal", value: …}`` wrappers, and
condition nodes carrying a Field ``subject`` + ``value``. The walker never
parses free strings, so prose like ``triggering_action.description`` ("creating
a Opportunity with Loan_Type__c='Home Loan'") is excluded by construction —
sentences are not validatable and false extraction there would mint false
refusals.

**Fail loud on its own errors** (D-399.1 corollary): a validator that cannot
read the capture state must ERROR, never pass the claim through — a pass it
did not compute is a silent wrong-green of its own. Concretely:
``FieldCaptureIndex.load`` raises :class:`ValueMembershipError` when the
capture column cannot be read or when the org resolves to zero picklist
fields (a wrong org id would otherwise validate everything vacuously).

**NOT WIRED IN.** This module is an offline/callable check. Inserting it into
the emission path / approval gate is a generation-semantics change and a
product decision (what does a refusal DO?) — deliberately out of scope here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from enum import Enum
from typing import Any, Iterable, Optional

from sqlalchemy import text


class ValueMembershipError(RuntimeError):
    """The validator itself failed (capture unreadable, org empty). Never
    swallow this into a pass — see the fail-loud contract above."""


class Verdict(str, Enum):
    VALID = "valid"
    INVALID = "invalid"
    CANNOT_VALIDATE = "cannot_validate"


# Capture marks that assert a COMPLETE captured set (D-408 vocabulary).
AUTHORITATIVE_MARKS = frozenset({"gvs", "inline", "inline_standard"})
# The mark asserting a known-EMPTY set (still authoritative — for emptiness).
EMPTY_MARK = "no_values"
# The mark asserting a disclosed SUBSET (200-value storage cap, D-407).
TRUNCATED_MARK = "inline_truncated"

_FIELDNAME = re.compile(r"^[A-Za-z0-9_]+\.[A-Za-z0-9_]+$")


@dataclass(frozen=True)
class _FieldCapture:
    field_type: str                 # 'picklist' | 'multipicklist'
    capture: Optional[str]          # picklist_capture mark (None = pre-migration)
    # (api_name, label, is_active) triples for the field's captured set.
    values: tuple = ()


@dataclass(frozen=True)
class LiteralCheck:
    """One (field, literal) membership check."""
    field: str                      # Object.Field
    value: str                      # the literal part checked (post ';'-split)
    verdict: Verdict
    # VALID  -> 'api_name' | 'label' (which column matched)
    # INVALID -> 'absent' | 'inactive'
    # CANNOT_VALIDATE -> the capture mark ('inline_truncated' | 'null_capture'
    #                    | the unknown mark)
    detail: str
    capture: Optional[str]          # the field's capture mark, for the record


@dataclass
class ClaimValidation:
    """All checks for one claim (its bodies + its recipes)."""
    checks: list = dc_field(default_factory=list)

    @property
    def verdict(self) -> Verdict:
        """Claim-level rollup. INVALID dominates (one provably-wrong literal
        flags the claim); CANNOT_VALIDATE next (the claim is not fully
        validated); VALID only when every checked literal is valid. A claim
        with no enumerated literals at all is vacuously VALID — there is
        nothing this validator is competent to say about it."""
        vs = {c.verdict for c in self.checks}
        if Verdict.INVALID in vs:
            return Verdict.INVALID
        if Verdict.CANNOT_VALIDATE in vs:
            return Verdict.CANNOT_VALIDATE
        return Verdict.VALID

    @property
    def invalid_checks(self) -> list:
        return [c for c in self.checks if c.verdict == Verdict.INVALID]


def extract_field_literals(*bodies: Any) -> list[tuple[str, Any]]:
    """(Object.Field, literal) pairs from STRUCTURED nodes only, deduplicated
    on (field, str(literal)) preserving first-seen order.

    Recognized shapes (all real, taken from the live corpus):
      * any dict key matching ``Object.Field`` whose value is a scalar —
        ``field_values`` / ``field_changes`` maps wherever they appear
        (claim ``from_state``/``to_state``/``expected_effect.changes``,
        recipe ``steps[]``);
      * the same key with a ``{"kind": "literal", "value": …}`` wrapper;
      * condition nodes ``{"subject": {"entity_type": "Field",
        "external_id": F, …}, "value": V}`` (V scalar or list).

    Free strings are NEVER parsed — prose is not a literal source.
    """
    out: list[tuple[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def _add(f: str, v: Any) -> None:
        key = (f, str(v))
        if key not in seen:
            seen.add(key)
            out.append((f, v))

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            subj = node.get("subject")
            if (isinstance(subj, dict)
                    and subj.get("entity_type") == "Field"
                    and subj.get("external_id") and "value" in node):
                v = node["value"]
                for item in (v if isinstance(v, list) else [v]):
                    _add(subj["external_id"], item)
            for k, v in node.items():
                if isinstance(k, str) and _FIELDNAME.match(k):
                    if isinstance(v, dict) and v.get("kind") == "literal":
                        _add(k, v.get("value"))
                    elif isinstance(v, (str, int, float, bool)) or v is None:
                        _add(k, v)
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    for body in bodies:
        _walk(body)
    return out


class FieldCaptureIndex:
    """The org's enumerated fields + captured value sets, loaded once.

    Construct directly for tests; use :meth:`load` against a tenant-schema
    connection for the real thing.
    """

    def __init__(self, fields: dict[str, _FieldCapture]):
        self._fields = fields

    @classmethod
    def load(cls, conn, connected_org_id: str) -> "FieldCaptureIndex":
        """One pass over ``field_details`` + ``picklist_value_details`` at
        CURRENT versions, scoped to ``connected_org_id``.

        Fail-loud (never degrade to a permissive index):
          * an unreadable ``picklist_capture`` column raises — a validator
            that cannot see capture must not run;
          * zero picklist fields raises — every org with a model has some,
            so an empty result means a wrong org id / schema, and validating
            against it would vacuously pass everything.
        """
        try:
            field_rows = conn.execute(text("""
                SELECT e.sf_api_name AS f, d.field_type AS ft,
                       d.picklist_capture AS cap,
                       CAST(d.picklist_value_set_entity_id AS text) AS pvs
                FROM entities e
                JOIN field_details d ON d.entity_id = e.id
                WHERE e.connected_org_id = CAST(:o AS uuid)
                  AND e.entity_type = 'Field'
                  AND e.valid_to_seq IS NULL
                  AND d.field_type IN ('picklist', 'multipicklist')
            """), {"o": connected_org_id}).mappings().all()
            value_rows = conn.execute(text("""
                SELECT CAST(pvd.picklist_value_set_entity_id AS text) AS pvs,
                       pvd.value_api_name AS api, pvd.value_label AS lbl,
                       pvd.is_active AS act
                FROM picklist_value_details pvd
                JOIN entities pe ON pe.id = pvd.entity_id
                WHERE pe.valid_to_seq IS NULL
            """)).mappings().all()
        except ValueMembershipError:
            raise
        except Exception as e:  # noqa: BLE001 — wrap, never pass-through-as-pass
            raise ValueMembershipError(
                f"cannot read capture state for org {connected_org_id}: {e}"
            ) from e

        if not field_rows:
            raise ValueMembershipError(
                f"org {connected_org_id} resolves to ZERO picklist fields — "
                "wrong org id or schema; refusing to validate vacuously")

        by_pvs: dict[str, list] = {}
        for r in value_rows:
            by_pvs.setdefault(r["pvs"], []).append(
                (r["api"], r["lbl"], bool(r["act"])))
        fields = {
            r["f"]: _FieldCapture(
                field_type=r["ft"], capture=r["cap"],
                values=tuple(by_pvs.get(r["pvs"], [])) if r["pvs"] else (),
            )
            for r in field_rows
        }
        return cls(fields)

    @classmethod
    def from_s1(cls, s1, at_seq: int,
                field_names: Iterable[str]) -> "FieldCaptureIndex":
        """Build the index for ONLY ``field_names``, through the S1 model
        interface (``get_entities`` → ``get_entity_details`` →
        ``get_picklist_values``) — the same seam every other S3 grounding read
        uses, at the batch's pinned ``at_seq``. This is the EMISSION-path
        builder (D-413): it resolves just the fields the authored bundles
        actually reference, so a batch with no enumerated literals does no
        value reads at all.

        Contract differences from :meth:`load` (both deliberate):
          * a field that does not resolve, or is not picklist-typed, is simply
            NOT in the index — out of this validator's jurisdiction (field
            grounding already ran);
          * an empty result is fine here (the referenced fields may contain no
            picklists) — the zero-fields fail-loud belongs to the whole-org
            audit mode only;
          * a detail row MISSING the ``picklist_capture`` key reads as NULL
            capture → CANNOT_VALIDATE. Against the real schema the key always
            exists (``get_entity_details`` is ``SELECT *`` over a table that
            has the column); only synthetic S1 doubles lack it, and refusing
            on a double's field would invert fail-loud in every fixture-based
            test. The column-missing fail-loud stays enforced in
            :meth:`load`, whose SQL names the column explicitly.
        """
        fields: dict[str, _FieldCapture] = {}
        for name in field_names:
            ents = s1.get_entities("Field", at_seq=at_seq,
                                   filters={"sf_api_name": name})
            if not ents:
                continue
            details = s1.get_entity_details(ents[0].id, at_seq=at_seq) or {}
            ftype = (details.get("field_type") or "").lower()
            if ftype not in ("picklist", "multipicklist"):
                continue
            values: tuple = ()
            pvs_id = details.get("picklist_value_set_entity_id")
            if pvs_id:
                values = tuple(
                    (r["value_api_name"], r["value_label"],
                     bool(r["is_active"]))
                    for r in s1.get_picklist_values(pvs_id, at_seq=at_seq))
            fields[name] = _FieldCapture(
                field_type=ftype, capture=details.get("picklist_capture"),
                values=values)
        return cls(fields)

    def active_values(self, field: str) -> list[str]:
        """The field's captured ACTIVE api-names, sorted — the "org accepts"
        list a declination shows the buyer. [] for unknown fields."""
        fc = self._fields.get(field)
        if fc is None:
            return []
        return sorted(a for a, _l, act in fc.values if act)

    # -- the check ---------------------------------------------------------

    def check_literal(self, field: str, value: Any) -> list[LiteralCheck]:
        """Membership checks for one (field, literal). Returns [] when the
        field is not an enumerated field this validator is competent on
        (unknown fields are FIELD-grounding's jurisdiction, not membership's).
        Multipicklist literals fan out to one check per ``;``-part."""
        fc = self._fields.get(field)
        if fc is None:
            return []
        if value is None or isinstance(value, bool):
            return []

        cap = fc.capture
        parts = ([p.strip() for p in str(value).split(";") if p.strip()]
                 if fc.field_type == "multipicklist" else [str(value)])

        checks: list[LiteralCheck] = []
        for part in parts:
            if cap == TRUNCATED_MARK:
                checks.append(LiteralCheck(field, part,
                                           Verdict.CANNOT_VALIDATE,
                                           TRUNCATED_MARK, cap))
                continue
            if cap is None:
                checks.append(LiteralCheck(field, part,
                                           Verdict.CANNOT_VALIDATE,
                                           "null_capture", cap))
                continue
            if cap == EMPTY_MARK:
                # Known-empty set (org-verified, D-408): nothing is a member.
                checks.append(LiteralCheck(field, part, Verdict.INVALID,
                                           "absent", cap))
                continue
            if cap not in AUTHORITATIVE_MARKS:
                # A capture source this code does not know: no conclusion.
                checks.append(LiteralCheck(field, part,
                                           Verdict.CANNOT_VALIDATE, cap, cap))
                continue

            api_active = {a for a, _l, act in fc.values if act}
            lbl_active = {l for _a, l, act in fc.values if act and l}
            api_all = {a for a, _l, _act in fc.values}
            lbl_all = {l for _a, l, _act in fc.values if l}

            if part in api_active:
                checks.append(LiteralCheck(field, part, Verdict.VALID,
                                           "api_name", cap))
            elif part in lbl_active:
                checks.append(LiteralCheck(field, part, Verdict.VALID,
                                           "label", cap))
            elif part in api_all or part in lbl_all:
                # Present but inactive — org DRIFT, not hallucination.
                checks.append(LiteralCheck(field, part, Verdict.INVALID,
                                           "inactive", cap))
            else:
                checks.append(LiteralCheck(field, part, Verdict.INVALID,
                                           "absent", cap))
        return checks

    def validate(self, *bodies: Any) -> ClaimValidation:
        """Extract + check every enumerated-field literal in ``bodies``
        (claim ``asserted_truth`` / ``semantic_conditions`` + recipe bodies —
        pass whichever exist; the walker is shape-tolerant)."""
        result = ClaimValidation()
        for f, v in extract_field_literals(*bodies):
            result.checks.extend(self.check_literal(f, v))
        return result
