"""Test-case static validator.

Catches the most common AI-generation quality bugs BEFORE a run wastes
a Salesforce API burst:

  - Object doesn't exist in this env's metadata
  - Field doesn't exist on the target object
  - Field not createable on a create step / not updateable on update
  - $var referenced but no prior step set state_ref
  - record_ref points to a state var that was never set
  - SOQL strings that SELECT columns missing on the FROM object
  - (new) Date field value not ISO 8601
  - (new) Picklist value not in the metadata's allowed list
  - (new) Verify-assertion on a field no prior step set (and not a safe formula)

Each issue carries a severity (critical | warning | info) and, for
"field/object not found" kinds, fuzzy suggestions from the actual
metadata so the UI can offer a one-click Apply.

Usage:

    validator = TestCaseValidator(metadata_repo, meta_version_id)
    report = validator.validate(steps)
    if report["status"] == "critical":
        # block execution OR surface banner
        ...
"""

import re
from difflib import get_close_matches
from typing import Any, Dict, Iterable, List, Optional, Tuple


# Severity tiers. The UI maps these 1:1 to banner colors:
#   critical \u2192 red, blocks pre-flight
#   warning  \u2192 yellow, runs but flagged
#   info     \u2192 gray, informational
SEVERITY_CRITICAL = "critical"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

# Fuzzy-match cutoff. 0.6 catches typos ("LastEscaltaion_" vs "LastEscalation_")
# and case variants while rejecting truly wrong names. Tune after usage.
FUZZY_CUTOFF = 0.6
MAX_SUGGESTIONS = 3


# ---- Date format rules ----------------------------------------------------

# ISO 8601 date (YYYY-MM-DD) and datetime (YYYY-MM-DDTHH:MM:SS[Z|\u00b1HH:MM]).
# We accept a date-only value for both Date and DateTime field types \u2014 SF
# tolerates the short form.
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?$"
)

# Field types that must be ISO 8601.
_DATE_FIELD_TYPES = {"date", "datetime"}


# ---- "Safe formula" whitelist ---------------------------------------------
# Fields a verify step may assert on even without a prior step that
# explicitly set them. These are computed / auto-managed in SF, so the
# assertion is meaningful (the test is validating SF's computation).
# Keep intentionally small \u2014 the knowledge provider teaches the AI about
# more over time; adding to this list loosens enforcement globally.
_SAFE_FORMULA_FIELDS = {
    "IsClosed", "IsWon", "IsConverted", "IsDeleted",
    "CreatedDate", "CreatedById",
    "LastModifiedDate", "LastModifiedById",
    "SystemModstamp",
    "Id", "Name",  # Id auto-assigned; Name is the user-supplied create arg
}


def _picklist_values(field) -> Optional[set]:
    """Normalise a MetaField.picklist_values JSON into a set of strings,
    or return None when the metadata didn't capture values (we can't
    validate and shouldn't false-positive). Handles both shapes the
    sync engine emits:

        list of strings:            ["New", "In Progress", "Closed"]
        list of value-objects:      [{"value": "New", ...}, ...]
    """
    pv = getattr(field, "picklist_values", None)
    if not pv:
        return None
    if not isinstance(pv, list) or not pv:
        return None
    out = set()
    for entry in pv:
        if isinstance(entry, str):
            out.add(entry)
        elif isinstance(entry, dict):
            v = entry.get("value") or entry.get("Value")
            if isinstance(v, str):
                out.add(v)
    return out or None


def _suggest(target: str, candidates: Iterable[str],
             n: int = MAX_SUGGESTIONS,
             cutoff: float = FUZZY_CUTOFF) -> List[str]:
    """Return up to `n` best fuzzy matches for `target` from `candidates`,
    or [] when nothing clears `cutoff`. Case-insensitive matching, but the
    returned strings are the original candidate casing so "Apply" works."""
    if not target:
        return []
    # difflib is case-sensitive; do a lower-cased match and map back.
    lc_map = {c.lower(): c for c in candidates}
    matches = get_close_matches(target.lower(), list(lc_map.keys()),
                                n=n, cutoff=cutoff)
    return [lc_map[m] for m in matches]


class TestCaseValidator:
    """Validates a sequence of test-case steps against the metadata of a
    given `meta_version_id`. Cheap to construct \u2014 hydrates object +
    field indexes eagerly so repeated `.validate()` calls don't re-hit
    the DB."""

    def __init__(self, metadata_repo, meta_version_id: int):
        self.metadata_repo = metadata_repo
        self.meta_version_id = meta_version_id

        # Hydrate indexes up-front. Tenants rarely have > 500 objects or
        # > 10k fields per meta version; this is a single query per domain.
        objs = metadata_repo.get_objects(meta_version_id) if meta_version_id else []
        self._obj_by_name: Dict[str, Any] = {o.api_name: o for o in objs}
        self._obj_names: List[str] = list(self._obj_by_name.keys())

        fields = metadata_repo.get_fields(meta_version_id) if meta_version_id else []
        # {object_id: {field_api: MetaField}}
        self._fields_by_obj: Dict[int, Dict[str, Any]] = {}
        for f in fields:
            self._fields_by_obj.setdefault(f.meta_object_id, {})[f.api_name] = f

    # ---- Public API ----------------------------------------------------

    def validate(self, steps: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Run all rules over a list of step dicts. Returns a report
        dict suitable for storing on test_case_versions.validation_report."""
        issues: List[Dict[str, Any]] = []
        # Track state vars set by prior steps so we can flag unresolved $vars.
        seen_state_refs: set = set()
        # Track which fields each $var has had explicitly set by prior
        # create/update steps. Used by assertion_not_traced.
        traced_fields_by_var: Dict[str, set] = {}

        for step in steps or []:
            issues.extend(self._validate_step(step, seen_state_refs, traced_fields_by_var))

            # After validating the current step, record its field writes
            # so later verify steps can check against them.
            action = step.get("action")
            fv = step.get("field_values") or {}
            # For create: the var being declared is state_ref.
            # For update: the var being written is record_ref.
            target_var = None
            if action == "create":
                sr = step.get("state_ref")
                if isinstance(sr, str) and sr.startswith("$"):
                    target_var = sr[1:]
            elif action == "update":
                rr = step.get("record_ref")
                if isinstance(rr, str) and rr.startswith("$"):
                    # Strip .Id suffix so $foo.Id writes are attributed to $foo
                    target_var = rr[1:]
                    if target_var.endswith(".Id"):
                        target_var = target_var[:-3]
            if target_var is not None and isinstance(fv, dict):
                traced_fields_by_var.setdefault(target_var, set()).update(fv.keys())

            # A create step with state_ref registers its var as "available"
            # for all subsequent steps. We do this AFTER validating the
            # current step so "this step refs a var this step also sets"
            # still fails (tight ordering enforced).
            state_ref = step.get("state_ref")
            if action == "create" and isinstance(state_ref, str) and state_ref.startswith("$"):
                seen_state_refs.add(state_ref[1:])

            # A convert step produces three implicit record ids that later
            # steps reference as $<lead>.ConvertedAccountId /
            # .ConvertedContactId / .ConvertedOpportunityId. Register them
            # here so those references don\u2019t trip unresolved_state_ref.
            # Runtime counterpart: executor stores the same keys in
            # state_vars from the convert response body.
            if action == "convert":
                lead_var = None
                rr = step.get("record_ref")
                if isinstance(rr, str) and rr.startswith("$"):
                    lead_var = rr[1:]
                if isinstance(state_ref, str) and state_ref.startswith("$"):
                    lead_var = state_ref[1:]
                if lead_var:
                    for suffix in ("ConvertedAccountId",
                                   "ConvertedContactId",
                                   "ConvertedOpportunityId"):
                        seen_state_refs.add(f"{lead_var}.{suffix}")

        summary = {SEVERITY_CRITICAL: 0, SEVERITY_WARNING: 0, SEVERITY_INFO: 0}
        for i in issues:
            summary[i["severity"]] = summary.get(i["severity"], 0) + 1

        if summary[SEVERITY_CRITICAL]:
            status = SEVERITY_CRITICAL
        elif summary[SEVERITY_WARNING]:
            status = "warnings"
        else:
            status = "ok"

        return {
            "status": status,
            "issues": issues,
            "summary": summary,
            "meta_version_id": self.meta_version_id,
        }

    def apply_fix(self, steps: List[Dict[str, Any]], issue: Dict[str, Any],
                  replacement: str) -> List[Dict[str, Any]]:
        """Return a new steps list with a single fix applied. `issue` is
        the dict from the validation report; `replacement` is the chosen
        suggestion. Pure function \u2014 does not persist anywhere."""
        import copy
        new_steps = copy.deepcopy(steps or [])
        step_order = issue.get("step_order")
        target = next((s for s in new_steps if s.get("step_order") == step_order), None)
        if not target:
            return new_steps

        rule = issue.get("rule")
        if rule == "field_not_found":
            field = issue.get("field")
            fv = target.get("field_values") or {}
            if field in fv:
                fv[replacement] = fv.pop(field)
                target["field_values"] = fv
            # Fix in assertions too (verify steps)
            asserts = target.get("assertions") or {}
            if field in asserts:
                asserts[replacement] = asserts.pop(field)
                target["assertions"] = asserts
        elif rule == "object_not_found":
            target["target_object"] = replacement
        elif rule == "soql_from_object_not_found":
            # Rewrite the FROM clause of the SOQL string. Only the first
            # match is replaced (SOQL rarely has two FROMs outside
            # subqueries, which we wouldn't touch anyway).
            soql = target.get("soql") or ""
            bad = issue.get("object_name")
            if soql and bad:
                target["soql"] = re.sub(
                    r"(\bFROM\b\s+)" + re.escape(bad) + r"\b",
                    r"\1" + replacement,
                    soql, count=1, flags=re.IGNORECASE,
                )
        elif rule == "soql_column_not_found":
            # Rewrite the bad column in the SELECT list. Whole-word match
            # so "Name" doesn't replace "AccountName" accidentally.
            soql = target.get("soql") or ""
            bad = issue.get("field")
            if soql and bad:
                target["soql"] = re.sub(
                    r"\b" + re.escape(bad) + r"\b",
                    replacement,
                    soql, count=1,
                )
        elif rule == "unresolved_state_ref":
            # replacement is the step_order of the creator to mutate, not
            # a string in this step; skip \u2014 UI should direct the user
            # to the creator step's state_ref field instead.
            pass

        return new_steps

    # ---- Rules ---------------------------------------------------------

    def _validate_step(self, step: Dict[str, Any],
                       seen_state_refs: set,
                       traced_fields_by_var: Optional[Dict[str, set]] = None,
                       ) -> List[Dict[str, Any]]:
        issues: List[Dict[str, Any]] = []
        order = step.get("step_order", 0)
        action = step.get("action", "")
        obj_name = step.get("target_object", "")

        # ---- Rule: object exists ----
        if action in ("create", "update", "delete", "query", "verify", "convert"):
            if not obj_name:
                issues.append(self._issue(order, SEVERITY_CRITICAL,
                    "object_missing",
                    f"Step {order} has no target_object",
                    object_name=None))
                return issues
            if obj_name not in self._obj_by_name:
                issues.append(self._issue(order, SEVERITY_CRITICAL,
                    "object_not_found",
                    f"Object '{obj_name}' does not exist in the environment's metadata",
                    object_name=obj_name,
                    suggestions=_suggest(obj_name, self._obj_names)))
                return issues  # further field checks would be nonsense

        obj = self._obj_by_name.get(obj_name)
        obj_fields = self._fields_by_obj.get(obj.id, {}) if obj else {}
        field_names = list(obj_fields.keys())

        # Metadata in this tenant may be partial \u2014 only objects in the
        # "fields" category sync have their fields loaded. Without any
        # field rows for this object we can't confidently say a field
        # doesn't exist. Fall back to a single "object not fully synced"
        # info-level nudge per object and skip detailed field checks.
        has_field_data = bool(obj_fields)

        fv = step.get("field_values") or {}
        asserts = step.get("assertions") or {}

        if obj and not has_field_data and (fv or asserts):
            issues.append(self._issue(order, SEVERITY_INFO,
                "fields_not_synced",
                f"Fields for {obj_name} are not in this metadata version; "
                "cannot validate individual fields. Refresh the 'fields' "
                "category in Settings \u2192 Environments to enable deep checks.",
                object_name=obj_name))
        elif obj and has_field_data:
            # ---- Rule: fields in field_values ----
            if isinstance(fv, dict):
                for fname in list(fv.keys()):
                    if fname not in obj_fields:
                        issues.append(self._issue(order, SEVERITY_CRITICAL,
                            "field_not_found",
                            f"Field '{fname}' does not exist on {obj_name}",
                            object_name=obj_name, field=fname,
                            suggestions=_suggest(fname, field_names)))
                    else:
                        f = obj_fields[fname]
                        if action == "create" and not f.is_createable:
                            issues.append(self._issue(order, SEVERITY_CRITICAL,
                                "field_not_createable",
                                f"Field '{fname}' on {obj_name} is not writeable on create",
                                object_name=obj_name, field=fname))
                        elif action == "update" and not f.is_updateable:
                            issues.append(self._issue(order, SEVERITY_WARNING,
                                "field_not_updateable",
                                f"Field '{fname}' on {obj_name} is not updateable",
                                object_name=obj_name, field=fname))

            # ---- Rule: fields in assertions (verify) ----
            if isinstance(asserts, dict):
                for fname in asserts.keys():
                    if fname not in obj_fields:
                        issues.append(self._issue(order, SEVERITY_CRITICAL,
                            "field_not_found",
                            f"Field '{fname}' does not exist on {obj_name} (assertion)",
                            object_name=obj_name, field=fname,
                            suggestions=_suggest(fname, field_names)))

        # ---- Rule: $var references have a prior state_ref ----
        # Scan field_values, record_ref for $foo references.
        refs = self._collect_refs(step)
        for ref in refs:
            if ref not in seen_state_refs:
                issues.append(self._issue(order, SEVERITY_CRITICAL,
                    "unresolved_state_ref",
                    f"${ref} is not set by any prior step. "
                    f"The earlier 'create' step that produces this record "
                    f"must set state_ref: '${ref}'.",
                    state_ref=ref,
                    suggestions=_suggest(ref, list(seen_state_refs))))

        # ---- Rule: SOQL validates against the FROM object ----
        # query steps carry a full SOQL string; the target_object field
        # may or may not match the FROM clause, and the SELECT field
        # list is where the Last_Escalation_Date__c / wrong-column
        # hallucinations usually hide. We parse what we can and flag
        # columns the SELECT references that don't exist on FROM.
        if action == "query":
            soql = step.get("soql") or ""
            if soql.strip():
                issues.extend(self._validate_soql(order, soql))

        # ---- Rule (new): date-field values are ISO 8601 ----
        if obj and has_field_data and action in ("create", "update"):
            for fname, value in (fv.items() if isinstance(fv, dict) else []):
                if not isinstance(value, str) or not value:
                    continue
                # Skip $ref values \u2014 they resolve at runtime.
                if value.startswith("$"):
                    continue
                f = obj_fields.get(fname)
                if not f or (f.field_type or "").lower() not in _DATE_FIELD_TYPES:
                    continue
                ftype = (f.field_type or "").lower()
                ok = (_ISO_DATE_RE.match(value) is not None) or \
                     (ftype == "datetime" and _ISO_DATETIME_RE.match(value) is not None)
                if not ok:
                    issues.append(self._issue(order, SEVERITY_WARNING,
                        "date_format_invalid",
                        f"'{value}' on {obj_name}.{fname} is not ISO 8601. "
                        "Use YYYY-MM-DD (Date) or YYYY-MM-DDTHH:MM:SSZ (DateTime).",
                        object_name=obj_name, field=fname,
                        value=value))

        # ---- Rule (new): picklist value in allowed list ----
        if obj and has_field_data and action in ("create", "update"):
            for fname, value in (fv.items() if isinstance(fv, dict) else []):
                if not isinstance(value, str) or not value or value.startswith("$"):
                    continue
                f = obj_fields.get(fname)
                if not f:
                    continue
                ftype = (f.field_type or "").lower()
                if ftype not in ("picklist", "multipicklist"):
                    continue
                allowed = _picklist_values(f)
                if allowed is None:
                    continue  # metadata didn't capture the values
                if value not in allowed:
                    issues.append(self._issue(order, SEVERITY_WARNING,
                        "picklist_value_not_allowed",
                        f"'{value}' is not in the metadata's allowed "
                        f"picklist values for {obj_name}.{fname}. "
                        f"Allowed: {', '.join(sorted(allowed)[:8])}"
                        + ("\u2026" if len(allowed) > 8 else ""),
                        object_name=obj_name, field=fname, value=value,
                        suggestions=_suggest(value, allowed)))

        # ---- Rule (new): verify assertions were set by a prior step ----
        if action == "verify" and isinstance(asserts, dict) and traced_fields_by_var is not None:
            rr = step.get("record_ref")
            var_name = None
            if isinstance(rr, str) and rr.startswith("$"):
                var_name = rr[1:]
                if var_name.endswith(".Id"):
                    var_name = var_name[:-3]
            traced = traced_fields_by_var.get(var_name, set()) if var_name else set()
            for fname in asserts.keys():
                if fname in _SAFE_FORMULA_FIELDS:
                    continue
                if fname in traced:
                    continue
                issues.append(self._issue(order, SEVERITY_WARNING,
                    "assertion_not_traced",
                    f"Assertion on {obj_name}.{fname} \u2014 no prior step "
                    f"set this field on {rr or '?'} and it's not a known "
                    "auto-computed field. The assertion may be unfounded.",
                    object_name=obj_name, field=fname,
                    state_ref=var_name))

        return issues

    # ---- SOQL parsing ---------------------------------------------------
    #
    # Full SOQL grammar is bigger than a regex \u2014 we don't try to be a
    # parser, just pull out SELECT field list + FROM object. Relationship
    # traversal (Account.Industry) is acknowledged but validated only at
    # the top-level column segment. Subqueries, aggregates, typeof are
    # best-effort: we skip columns we can't confidently identify.
    #
    # This is intentionally permissive: false negatives are acceptable
    # (the SF runtime still catches them), false positives are not.

    _SOQL_SELECT_RE = re.compile(
        r"\bSELECT\b(?P<fields>.+?)\bFROM\b\s+(?P<obj>[A-Za-z0-9_]+)",
        re.IGNORECASE | re.DOTALL,
    )
    _SOQL_RESERVED = {
        # Functions / keywords that aren't object fields
        "count", "count_distinct", "sum", "avg", "min", "max",
        "format", "tolabel", "convertcurrency", "calendar_year",
        "calendar_quarter", "calendar_month", "day_in_month",
        "distance", "geolocation", "typeof", "when", "then", "else", "end",
    }

    @classmethod
    def _extract_soql_targets(cls, soql: str) -> Optional[Tuple[str, List[str]]]:
        """Return (object_api_name, [top_level_field, ...]) or None if we
        can't make sense of the query (subqueries, aggregates everywhere,
        etc). Relationship paths are trimmed to their first segment so
        'Account.Industry' becomes 'Account' at the object level \u2014 the
        caller validates if it's a real relationship."""
        m = cls._SOQL_SELECT_RE.search(soql)
        if not m:
            return None
        obj = m.group("obj")
        fields_src = m.group("fields")
        # Strip comments
        fields_src = re.sub(r"/\*.*?\*/", "", fields_src, flags=re.DOTALL)
        # Split by commas that aren't inside parens (naive but good enough)
        # Walk the string, track paren depth.
        parts = []
        buf = []
        depth = 0
        for ch in fields_src:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth = max(0, depth - 1)
            if ch == "," and depth == 0:
                parts.append("".join(buf).strip())
                buf = []
            else:
                buf.append(ch)
        if buf:
            parts.append("".join(buf).strip())

        fields = []
        for p in parts:
            # Skip subqueries: (SELECT ...) \u2014 parens at column position
            if p.startswith("("):
                continue
            # Strip aliases: "Name alias" \u2192 "Name"
            tokens = p.split()
            if not tokens:
                continue
            token = tokens[0]
            # Strip function wrappers: "COUNT(Id)" \u2192 "Id"
            func_m = re.match(r"^([A-Za-z_]+)\s*\(\s*([^\)]+)\s*\)\s*$", token)
            if func_m:
                inner = func_m.group(2).strip()
                token = inner if inner and inner != "*" else ""
                if not token:
                    continue
            # Drop *
            if token == "*":
                continue
            # First-segment only for relationship traversal
            if "." in token:
                token = token.split(".", 1)[0]
            if token.lower() in cls._SOQL_RESERVED:
                continue
            fields.append(token)
        return obj, fields

    def _validate_soql(self, step_order: int, soql: str) -> List[Dict[str, Any]]:
        """Return issues for a single SOQL string. Handles: FROM object
        exists; SELECT columns exist on that object (with fuzzy suggestions).
        Silently returns [] when the SOQL can't be parsed \u2014 we never
        want a parser failure to block an otherwise-valid test."""
        extracted = self._extract_soql_targets(soql)
        if not extracted:
            return []
        obj_name, fields = extracted
        issues: List[Dict[str, Any]] = []

        if obj_name not in self._obj_by_name:
            issues.append(self._issue(step_order, SEVERITY_CRITICAL,
                "soql_from_object_not_found",
                f"SOQL FROM '{obj_name}' does not exist in the environment's metadata",
                object_name=obj_name,
                suggestions=_suggest(obj_name, self._obj_names)))
            # No further field checks without the object
            return issues

        obj = self._obj_by_name[obj_name]
        obj_fields = self._fields_by_obj.get(obj.id, {})
        if not obj_fields:
            # Fields for this object not synced; cannot validate columns.
            # One info-level nudge rather than a flurry of false positives.
            issues.append(self._issue(step_order, SEVERITY_INFO,
                "fields_not_synced",
                f"SOQL columns on {obj_name} cannot be validated because "
                f"that object's fields are not in this metadata version. "
                f"Refresh the 'fields' category.",
                object_name=obj_name))
            return issues

        field_names = list(obj_fields.keys())
        for f in fields:
            if f in obj_fields:
                continue
            # Salesforce relationship convention: a lookup field named
            # FooId is traversed in SOQL as `Foo.Bar`. When the token
            # we see is "Foo", check whether "FooId" exists as a
            # reference-type field. If so, it's a valid relationship
            # traversal \u2014 skip the complaint. "__r" suffix is the
            # custom-relationship equivalent (Account__c \u2192 Account__r).
            relationship_fk = f + "Id"
            if relationship_fk in obj_fields:
                continue
            if f.endswith("__r"):
                custom_fk = f[:-3] + "__c"
                if custom_fk in obj_fields:
                    continue
            issues.append(self._issue(step_order, SEVERITY_CRITICAL,
                "soql_column_not_found",
                f"SOQL column '{f}' does not exist on {obj_name}",
                object_name=obj_name, field=f,
                suggestions=_suggest(f, field_names)))
        return issues

    # ---- Helpers -------------------------------------------------------

    @staticmethod
    def _collect_refs(step: Dict[str, Any]) -> List[str]:
        """Return every $var name referenced by this step (without the $).

        Tolerates the dotted .Id accessor (e.g. "$account.Id") which the
        AI generator naturally produces when writing foreign keys. The
        executor also collapses .Id to the bare var on resolve; keeping
        both in sync means the validator matches real runtime behavior.
        """
        refs = []

        def _strip_id_suffix(name: str) -> str:
            return name[:-3] if name.endswith(".Id") else name

        def visit(value):
            if isinstance(value, str) and value.startswith("$"):
                refs.append(_strip_id_suffix(value[1:]))
            elif isinstance(value, dict):
                for v in value.values():
                    visit(v)
            elif isinstance(value, list):
                for v in value:
                    visit(v)

        # record_ref, field_values, assertions
        rr = step.get("record_ref")
        if rr:
            visit(rr)
        visit(step.get("field_values") or {})
        visit(step.get("assertions") or {})
        # Do NOT visit `state_ref` itself \u2014 that's a declaration not a reference.
        return refs

    @staticmethod
    def _issue(step_order: int, severity: str, rule: str, message: str,
               **context) -> Dict[str, Any]:
        issue = {
            "step_order": step_order,
            "severity": severity,
            "rule": rule,
            "message": message,
        }
        # Drop None values from context for a cleaner JSON payload
        for k, v in context.items():
            if v is not None:
                issue[k] = v
        return issue
