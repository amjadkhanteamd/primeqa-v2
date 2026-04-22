"""GenerationLinter — structural checks on AI-generated flow steps.

Runs after the LLM returns a plan and BEFORE the steps persist. Uses
org metadata (no knowledge rules, no LLM) so it's deterministic, fast,
and safe to run on every generation.

Three outcomes per issue:
  - FIX   — auto-corrected in place (Id removed, formula field dropped,
            date reformatted). auto_fix mode applies; strict mode
            converts to a block.
  - WARN  — suspect but not auto-correctable (picklist value not in
            synced metadata — might be a legit new value).
  - BLOCK — unfixable; the step would error at execution time. Always
            raises; even auto_fix can't recover (e.g. `$user_id`
            placeholders needing runtime resolution).

All checks are pure functions on (steps, metadata). Steps are mutated
in place when a fix is applied; callers that want an untouched copy
should pass a deep-copy.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


# ---- Result types --------------------------------------------------------

@dataclass
class LintFix:
    step_id: int
    step_name: str
    field: Optional[str]
    check: str
    action: str      # "removed" | "reformatted" | "flagged"
    detail: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class LintWarning:
    step_id: int
    step_name: str
    field: Optional[str]
    check: str
    message: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class LintBlock:
    step_id: int
    step_name: str
    field: Optional[str]
    check: str
    reason: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class LintResult:
    passed: bool
    fixes_applied: list[LintFix] = field(default_factory=list)
    warnings: list[LintWarning] = field(default_factory=list)
    blocked: list[LintBlock] = field(default_factory=list)

    def summary_dict(self) -> dict:
        return {
            "passed": self.passed,
            "fixes_count": len(self.fixes_applied),
            "warnings_count": len(self.warnings),
            "blocked_count": len(self.blocked),
            "fixes": [f.as_dict() for f in self.fixes_applied],
            "warnings": [w.as_dict() for w in self.warnings],
            "blocked": [b.as_dict() for b in self.blocked],
        }


# ---- Constants -----------------------------------------------------------

# Canonical set of always-read-only formula fields that show up often
# enough to hard-code rather than rely on metadata.calculated alone.
_KNOWN_FORMULA_FIELDS: set[tuple[Optional[str], str]] = {
    ("Case",        "IsClosed"),
    ("Opportunity", "IsClosed"),
    ("Opportunity", "IsWon"),
    ("Lead",        "IsConverted"),
    (None,          "IsDeleted"),  # every sObject
}

# System-managed fields that should never appear in a create/update.
_SYSTEM_FIELDS: set[str] = {
    "CreatedDate", "CreatedById",
    "LastModifiedDate", "LastModifiedById",
    "SystemModstamp",
}

# Fields we don't flag when they show up in a verify-expected block,
# even without a matching prior create/update.
_ALWAYS_VERIFIABLE: set[str] = {
    "Id", "CreatedDate", "LastModifiedDate", "CreatedById",
    "LastModifiedById", "SystemModstamp",
}

# $var pattern used in raw payloads (before runtime resolution).
_VAR_RE = re.compile(r"\$([a-zA-Z_][a-zA-Z0-9_]*)")

# Built-in variables resolved by the pipeline — these are OK at lint time.
_KNOWN_VARS: set[str] = {
    "today", "now", "user_id", "record_type_id", "queue_id",
    "profile_id", "tenant_id",
}

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_DT_RE   = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$")

# Two common non-ISO formats we can auto-reformat: mm/dd/yyyy and dd/mm/yyyy.
# We pick US-style (mm/dd/yyyy) because that's what the LLM produces most
# often in English. Ambiguous cases are left alone.
_US_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


# ---- Helpers -------------------------------------------------------------

def _field_meta(metadata: Any, object_name: str, field_name: str) -> Optional[dict]:
    """Dig a single field's metadata out of whatever shape `metadata` is.

    The linter accepts two shapes:
      - dict {object_name: {"fields": {field_name: {...}}}} (test fixture)
      - TestCaseValidator-style {object_name: {field_name: MetaField}}
        where MetaField carries .type / .createable / .updateable /
        .calculated / .picklistValues attributes.

    Returns a normalised dict {type, createable, updateable, calculated,
    picklistValues} or None if the field isn't indexed yet.
    """
    if metadata is None:
        return None
    obj = None
    if isinstance(metadata, dict):
        obj = metadata.get(object_name) or metadata.get(object_name.lower())
    else:
        # Object-style: metadata.get_object / metadata[object_name]
        try:
            obj = metadata.get(object_name)  # type: ignore[attr-defined]
        except Exception:
            obj = None
    if obj is None:
        return None
    fields = obj.get("fields") if isinstance(obj, dict) else getattr(obj, "fields", None)
    if fields is None:
        return None
    f = None
    if isinstance(fields, dict):
        f = fields.get(field_name) or fields.get(field_name.lower())
    if f is None:
        return None
    # Normalise: support both raw dict + object with attrs.
    return {
        "type": (f.get("type") if isinstance(f, dict) else getattr(f, "type", None)),
        "createable": (f.get("createable") if isinstance(f, dict)
                       else getattr(f, "createable", True)),
        "updateable": (f.get("updateable") if isinstance(f, dict)
                       else getattr(f, "updateable", True)),
        "calculated": (f.get("calculated") if isinstance(f, dict)
                       else getattr(f, "calculated", False)),
        "picklistValues": (f.get("picklistValues") if isinstance(f, dict)
                           else getattr(f, "picklistValues", None)),
    }


def _step_name(step: dict) -> str:
    """Friendly step label: step_name field or action+object fallback."""
    return (step.get("name") or step.get("step_name")
            or f"{step.get('action', '?')} {step.get('target_object', '')}").strip()


def _payload(step: dict) -> dict:
    """Return the editable field-values dict for a create/update step."""
    # Historical field names: field_values, fields, payload. Return the
    # first non-None one; callers mutate in place.
    for key in ("field_values", "fields", "payload"):
        p = step.get(key)
        if isinstance(p, dict):
            return p
    return {}


def _expected(step: dict) -> dict:
    for key in ("expected", "expected_values", "assertions"):
        p = step.get(key)
        if isinstance(p, dict):
            return p
    return {}


# ---- Linter --------------------------------------------------------------

class GenerationLinter:
    """Structural checks. `mode` is "auto_fix" or "strict"."""

    def __init__(self, metadata: Any):
        self.metadata = metadata

    def lint(self, steps: list[dict], *, mode: str = "auto_fix") -> LintResult:
        if mode not in ("auto_fix", "strict"):
            raise ValueError(f"mode must be auto_fix or strict, got {mode!r}")
        result = LintResult(passed=True)

        for idx, step in enumerate(steps):
            self._check_unresolved_variables(idx, step, result)
            self._check_id_in_create(idx, step, result, mode)
            self._check_readonly_or_formula(idx, step, result, mode)
            self._check_date_formats(idx, step, result, mode)
            self._check_picklist_values(idx, step, result)

        # Cross-step analysis happens last: needs complete post-fix picture
        # so we don't flag assertions that we just auto-fixed into the
        # payload.
        self._check_untraced_assertions(steps, result, mode)

        if mode == "strict" and (result.fixes_applied or result.blocked):
            # Promote any fix to a block.
            for f in list(result.fixes_applied):
                result.blocked.append(LintBlock(
                    step_id=f.step_id, step_name=f.step_name,
                    field=f.field, check=f.check,
                    reason=f"strict mode: {f.detail}",
                ))
            result.fixes_applied = []

        if result.blocked:
            result.passed = False
        return result

    # --- 1. Unresolved $variables ----------------------------------------

    def _check_unresolved_variables(self, idx: int, step: dict,
                                     result: LintResult) -> None:
        combined: dict = {**_payload(step), **_expected(step)}
        for fname, val in list(combined.items()):
            if not isinstance(val, str):
                continue
            for m in _VAR_RE.finditer(val):
                var = m.group(1)
                if var in _KNOWN_VARS:
                    continue
                result.blocked.append(LintBlock(
                    step_id=idx, step_name=_step_name(step),
                    field=fname, check="unresolved_variable",
                    reason=(f"${var} referenced but not resolvable at lint time. "
                            f"Replace with a real value or use a known var "
                            f"({', '.join(sorted(_KNOWN_VARS))})."),
                ))

    # --- 2. Id in create ------------------------------------------------

    def _check_id_in_create(self, idx: int, step: dict,
                             result: LintResult, mode: str) -> None:
        if step.get("action") != "create":
            return
        payload = _payload(step)
        if "Id" in payload:
            detail = payload.get("Id")
            payload.pop("Id", None)
            result.fixes_applied.append(LintFix(
                step_id=idx, step_name=_step_name(step),
                field="Id", check="id_in_create",
                action="removed",
                detail=f"Removed Id={detail!r} from create payload "
                       f"(Salesforce assigns it).",
            ))

    # --- 3. Read-only OR formula field in create/update -----------------

    def _check_readonly_or_formula(self, idx: int, step: dict,
                                    result: LintResult, mode: str) -> None:
        action = step.get("action")
        if action not in ("create", "update"):
            return
        obj = step.get("target_object")
        payload = _payload(step)

        for fname in list(payload.keys()):
            # System audit fields
            if fname in _SYSTEM_FIELDS:
                payload.pop(fname, None)
                result.fixes_applied.append(LintFix(
                    step_id=idx, step_name=_step_name(step),
                    field=fname, check="readonly_field",
                    action="removed",
                    detail=f"System-managed field {fname} cannot be set via DML.",
                ))
                continue

            # Known formula fields
            if (obj, fname) in _KNOWN_FORMULA_FIELDS or (None, fname) in _KNOWN_FORMULA_FIELDS:
                payload.pop(fname, None)
                result.fixes_applied.append(LintFix(
                    step_id=idx, step_name=_step_name(step),
                    field=fname, check="formula_field",
                    action="removed",
                    detail=f"{obj}.{fname} is a formula field; read-only.",
                ))
                continue

            # Metadata-declared
            meta = _field_meta(self.metadata, obj, fname)
            if meta is None:
                continue  # unknown field — skip; validator covers existence
            if meta.get("calculated"):
                payload.pop(fname, None)
                result.fixes_applied.append(LintFix(
                    step_id=idx, step_name=_step_name(step),
                    field=fname, check="formula_field",
                    action="removed",
                    detail=f"{obj}.{fname} is calculated/formula; read-only.",
                ))
                continue
            if action == "create" and meta.get("createable") is False:
                payload.pop(fname, None)
                result.fixes_applied.append(LintFix(
                    step_id=idx, step_name=_step_name(step),
                    field=fname, check="readonly_field",
                    action="removed",
                    detail=f"{obj}.{fname} is not createable.",
                ))
            elif action == "update" and meta.get("updateable") is False:
                payload.pop(fname, None)
                result.fixes_applied.append(LintFix(
                    step_id=idx, step_name=_step_name(step),
                    field=fname, check="readonly_field",
                    action="removed",
                    detail=f"{obj}.{fname} is not updateable.",
                ))

    # --- 4. Invalid date format -----------------------------------------

    def _check_date_formats(self, idx: int, step: dict,
                             result: LintResult, mode: str) -> None:
        obj = step.get("target_object")
        payload = _payload(step)
        for fname, val in list(payload.items()):
            if not isinstance(val, str):
                continue
            meta = _field_meta(self.metadata, obj, fname)
            ftype = (meta or {}).get("type") if meta else None
            if ftype not in ("date", "datetime"):
                continue
            if ftype == "date" and _ISO_DATE_RE.match(val):
                continue
            if ftype == "datetime" and _ISO_DT_RE.match(val):
                continue

            # Try US-date auto-reformat: mm/dd/yyyy -> yyyy-mm-dd
            m = _US_DATE_RE.match(val)
            if m and ftype == "date":
                mo, da, yr = m.group(1).zfill(2), m.group(2).zfill(2), m.group(3)
                new_val = f"{yr}-{mo}-{da}"
                payload[fname] = new_val
                result.fixes_applied.append(LintFix(
                    step_id=idx, step_name=_step_name(step),
                    field=fname, check="date_format",
                    action="reformatted",
                    detail=f"Reformatted {val!r} -> {new_val!r} (ISO-8601).",
                ))
            else:
                result.warnings.append(LintWarning(
                    step_id=idx, step_name=_step_name(step),
                    field=fname, check="date_format",
                    message=(f"Value {val!r} on {obj}.{fname} ({ftype}) is "
                             f"not ISO-8601 and couldn't be auto-reformatted."),
                ))

    # --- 5. Picklist value validation (warn only) -----------------------

    def _check_picklist_values(self, idx: int, step: dict,
                                result: LintResult) -> None:
        obj = step.get("target_object")
        payload = _payload(step)
        for fname, val in payload.items():
            if not isinstance(val, str):
                continue
            meta = _field_meta(self.metadata, obj, fname)
            if meta is None:
                continue
            if meta.get("type") != "picklist":
                continue
            allowed = meta.get("picklistValues") or []
            if not allowed:
                continue
            # Allowed values may be list[str] or list[{value: ...}]
            allowed_set: set[str] = set()
            for a in allowed:
                if isinstance(a, str):
                    allowed_set.add(a)
                elif isinstance(a, dict):
                    v = a.get("value") or a.get("label")
                    if v:
                        allowed_set.add(v)
            if val in allowed_set:
                continue
            result.warnings.append(LintWarning(
                step_id=idx, step_name=_step_name(step),
                field=fname, check="picklist_value",
                message=(f"{obj}.{fname}={val!r} is not in the synced "
                         f"picklist values. Might be a new value — verify "
                         f"manually before running."),
            ))

    # --- 6. Untraced verify assertions ---------------------------------

    def _check_untraced_assertions(self, steps: list[dict],
                                    result: LintResult, mode: str) -> None:
        # Walk the flow once and record which (object, field) pairs each
        # prior create/update set. A verify assertion whose (object, field)
        # was never written earlier is "untraced".
        seen_writes: set[tuple[str, str]] = set()
        for idx, step in enumerate(steps):
            action = step.get("action")
            obj = step.get("target_object") or ""
            if action in ("create", "update"):
                for fname in _payload(step).keys():
                    seen_writes.add((obj, fname))
                continue
            if action != "verify":
                continue
            expected = _expected(step)
            for fname in list(expected.keys()):
                if fname in _ALWAYS_VERIFIABLE:
                    continue
                if (obj, fname) in seen_writes:
                    continue
                # Untraced — remove in auto_fix, or flag.
                if mode == "auto_fix":
                    expected.pop(fname, None)
                    result.fixes_applied.append(LintFix(
                        step_id=idx, step_name=_step_name(step),
                        field=fname, check="untraced_assertion",
                        action="removed",
                        detail=f"Assertion {obj}.{fname} was never set by a "
                               f"prior step.",
                    ))
                else:
                    result.blocked.append(LintBlock(
                        step_id=idx, step_name=_step_name(step),
                        field=fname, check="untraced_assertion",
                        reason=f"Untraced assertion: {obj}.{fname} not set "
                               f"by any prior step.",
                    ))


__all__ = [
    "LintFix", "LintWarning", "LintBlock", "LintResult",
    "GenerationLinter",
]
