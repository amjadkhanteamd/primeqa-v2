"""Test-case static validator.

Catches the most common AI-generation quality bugs BEFORE a run wastes
a Salesforce API burst:

  - Object doesn't exist in this env's metadata
  - Field doesn't exist on the target object
  - Field not createable on a create step / not updateable on update
  - $var referenced but no prior step set state_ref
  - record_ref points to a state var that was never set

Each issue carries a severity (critical | warning | info) and, for
"field/object not found" kinds, fuzzy suggestions from the actual
metadata so the UI can offer a one-click Apply.

Usage:

    validator = TestCaseValidator(metadata_repo, meta_version_id)
    report = validator.validate(steps)
    if report["status"] == "critical":
        # block execution OR surface banner
        ...

The validator does NOT parse SOQL strings yet \u2014 that's Commit 2.
"""

from difflib import get_close_matches
from typing import Any, Dict, Iterable, List, Optional


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

        for step in steps or []:
            issues.extend(self._validate_step(step, seen_state_refs))

            # A create step with state_ref registers its var as "available"
            # for all subsequent steps. We do this AFTER validating the
            # current step so "this step refs a var this step also sets"
            # still fails (tight ordering enforced).
            action = step.get("action")
            state_ref = step.get("state_ref")
            if action == "create" and isinstance(state_ref, str) and state_ref.startswith("$"):
                seen_state_refs.add(state_ref[1:])

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
        elif rule == "unresolved_state_ref":
            # replacement is the step_order of the creator to mutate, not
            # a string in this step; skip \u2014 UI should direct the user
            # to the creator step's state_ref field instead.
            pass

        return new_steps

    # ---- Rules ---------------------------------------------------------

    def _validate_step(self, step: Dict[str, Any],
                       seen_state_refs: set) -> List[Dict[str, Any]]:
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

        return issues

    # ---- Helpers -------------------------------------------------------

    @staticmethod
    def _collect_refs(step: Dict[str, Any]) -> List[str]:
        """Return every $var name referenced by this step (without the $)."""
        refs = []

        def visit(value):
            if isinstance(value, str) and value.startswith("$"):
                refs.append(value[1:])
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
