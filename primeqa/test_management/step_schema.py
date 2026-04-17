"""Step action grammar for test cases.

Defines the structured actions available in test steps and validation logic.
The UI renders form fields based on this schema; the generator produces matching
steps; the executor runs them.
"""

# Each action's schema: which fields it requires, their type hint, and UI widget
STEP_ACTIONS = {
    "create": {
        "label": "Create record",
        "description": "Create a new Salesforce record",
        "fields": {
            "target_object": {"type": "object_ref", "required": True, "label": "Object"},
            "field_values": {"type": "field_map", "required": True, "label": "Field values"},
            "logical_id": {"type": "string", "required": False, "label": "Logical ID (for reference)"},
        },
    },
    "update": {
        "label": "Update record",
        "description": "Update an existing record",
        "fields": {
            "target_object": {"type": "object_ref", "required": True, "label": "Object"},
            "record_ref": {"type": "record_ref", "required": True, "label": "Record reference"},
            "field_values": {"type": "field_map", "required": True, "label": "Field values"},
        },
    },
    "query": {
        "label": "Query records",
        "description": "Run a SOQL query",
        "fields": {
            "target_object": {"type": "object_ref", "required": True, "label": "Object"},
            "soql": {"type": "text", "required": True, "label": "SOQL query"},
            "store_as": {"type": "string", "required": False, "label": "Store result as"},
        },
    },
    "verify": {
        "label": "Verify field values",
        "description": "Assert field values on a record",
        "fields": {
            "target_object": {"type": "object_ref", "required": True, "label": "Object"},
            "record_ref": {"type": "record_ref", "required": True, "label": "Record reference"},
            "assertions": {"type": "field_map", "required": True, "label": "Expected field values"},
        },
    },
    "delete": {
        "label": "Delete record",
        "description": "Delete a record",
        "fields": {
            "target_object": {"type": "object_ref", "required": True, "label": "Object"},
            "record_ref": {"type": "record_ref", "required": True, "label": "Record reference"},
        },
    },
    "convert": {
        "label": "Convert lead",
        "description": "Convert a Lead to Account/Contact/Opportunity",
        "fields": {
            "target_object": {"type": "string", "required": True, "label": "Object", "default": "Lead"},
            "record_ref": {"type": "record_ref", "required": True, "label": "Lead reference"},
            "convert_to": {"type": "multi_select", "required": True, "label": "Convert to",
                          "options": ["Account", "Contact", "Opportunity"]},
        },
    },
    "wait": {
        "label": "Wait",
        "description": "Pause execution",
        "fields": {
            "duration_sec": {"type": "integer", "required": True, "label": "Duration (seconds)"},
            "reason": {"type": "string", "required": False, "label": "Reason"},
        },
    },
}


class StepValidator:
    """Validates steps against the schema and (optionally) against metadata."""

    def __init__(self, metadata_repo=None, meta_version_id=None):
        self.metadata_repo = metadata_repo
        self.meta_version_id = meta_version_id
        self._object_cache = None
        self._field_cache = {}

    def validate(self, steps):
        """Validate a list of steps. Returns (ok, errors)."""
        errors = []
        logical_ids = set()

        for i, step in enumerate(steps):
            prefix = f"Step {step.get('step_order', i+1)}"
            action = step.get("action")
            if action not in STEP_ACTIONS:
                errors.append(f"{prefix}: unknown action '{action}'")
                continue

            schema = STEP_ACTIONS[action]
            for field_name, field_spec in schema["fields"].items():
                if field_spec["required"] and not step.get(field_name):
                    errors.append(f"{prefix}: missing required field '{field_name}'")

            target = step.get("target_object")
            if target and self.metadata_repo and self.meta_version_id and action != "wait":
                if not self._object_exists(target):
                    errors.append(f"{prefix}: object '{target}' not found in metadata")

            record_ref = step.get("record_ref")
            if record_ref and action in ("update", "verify", "delete", "convert"):
                ref_id = record_ref.lstrip("$")
                if ref_id not in logical_ids:
                    errors.append(f"{prefix}: record_ref '{record_ref}' not defined by earlier step")

            field_values = step.get("field_values") or step.get("assertions") or {}
            if field_values and target and self.metadata_repo and self.meta_version_id:
                for fname in field_values.keys():
                    if not self._field_exists(target, fname):
                        errors.append(f"{prefix}: field '{target}.{fname}' not found in metadata")

            logical_id = step.get("logical_id")
            if logical_id:
                logical_ids.add(logical_id)

        return len(errors) == 0, errors

    def _object_exists(self, api_name):
        if self._object_cache is None:
            objs = self.metadata_repo.get_objects(self.meta_version_id)
            self._object_cache = {o.api_name for o in objs}
        return api_name in self._object_cache

    def _field_exists(self, object_name, field_name):
        if object_name not in self._field_cache:
            obj = self.metadata_repo.get_object_by_api_name(self.meta_version_id, object_name)
            if not obj:
                self._field_cache[object_name] = set()
            else:
                fields = self.metadata_repo.get_fields(self.meta_version_id, obj.id)
                self._field_cache[object_name] = {f.api_name for f in fields}
        return field_name in self._field_cache[object_name]
