"""Repository for the metadata domain.

DB queries scoped to: meta_versions, meta_objects, meta_fields,
                      meta_validation_rules, meta_flows, meta_triggers, meta_record_types
"""

from datetime import datetime, timezone

from sqlalchemy import text

from primeqa.metadata.models import (
    MetaVersion, MetaObject, MetaField,
    MetaValidationRule, MetaFlow, MetaTrigger, MetaRecordType,
)
from primeqa.core.models import Environment


class MetadataRepository:
    def __init__(self, db):
        self.db = db

    # --- Meta Versions ---

    def create_meta_version(self, environment_id, version_label):
        mv = MetaVersion(
            environment_id=environment_id,
            version_label=version_label,
            status="in_progress",
        )
        self.db.add(mv)
        self.db.commit()
        self.db.refresh(mv)
        return mv

    def complete_meta_version(self, version_id, snapshot_hash, counts):
        mv = self.db.query(MetaVersion).filter(MetaVersion.id == version_id).first()
        if not mv:
            return None
        mv.snapshot_hash = snapshot_hash
        mv.status = "complete"
        mv.object_count = counts.get("objects", 0)
        mv.field_count = counts.get("fields", 0)
        mv.vr_count = counts.get("vrs", 0)
        mv.flow_count = counts.get("flows", 0)
        mv.trigger_count = counts.get("triggers", 0)
        mv.completed_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(mv)
        return mv

    def fail_meta_version(self, version_id, error=None):
        mv = self.db.query(MetaVersion).filter(MetaVersion.id == version_id).first()
        if mv:
            mv.status = "failed"
            mv.completed_at = datetime.now(timezone.utc)
            self.db.commit()

    def get_version(self, version_id):
        return self.db.query(MetaVersion).filter(MetaVersion.id == version_id).first()

    def get_current_version(self, environment_id):
        env = self.db.query(Environment).filter(Environment.id == environment_id).first()
        if not env or not env.current_meta_version_id:
            return None
        return self.db.query(MetaVersion).filter(
            MetaVersion.id == env.current_meta_version_id,
        ).first()

    def get_previous_version(self, environment_id):
        versions = self.db.query(MetaVersion).filter(
            MetaVersion.environment_id == environment_id,
            MetaVersion.status == "complete",
            MetaVersion.lifecycle == "active",
        ).order_by(MetaVersion.started_at.desc()).limit(2).all()
        if len(versions) < 2:
            return None
        return versions[1]

    def set_current_version(self, environment_id, version_id):
        env = self.db.query(Environment).filter(Environment.id == environment_id).first()
        if env:
            env.current_meta_version_id = version_id
            env.updated_at = datetime.now(timezone.utc)
            self.db.commit()

    # --- Objects ---

    def store_objects(self, meta_version_id, objects_list):
        created = []
        for obj_data in objects_list:
            obj = MetaObject(
                meta_version_id=meta_version_id,
                api_name=obj_data["api_name"],
                label=obj_data.get("label"),
                key_prefix=obj_data.get("key_prefix"),
                is_custom=obj_data.get("is_custom", False),
                is_queryable=obj_data.get("is_queryable", True),
                is_createable=obj_data.get("is_createable", True),
                is_updateable=obj_data.get("is_updateable", True),
                is_deletable=obj_data.get("is_deletable", True),
            )
            self.db.add(obj)
            created.append(obj)
        self.db.commit()
        for obj in created:
            self.db.refresh(obj)
        return created

    def get_objects(self, meta_version_id):
        return self.db.query(MetaObject).filter(
            MetaObject.meta_version_id == meta_version_id,
        ).all()

    def get_object_by_api_name(self, meta_version_id, api_name):
        return self.db.query(MetaObject).filter(
            MetaObject.meta_version_id == meta_version_id,
            MetaObject.api_name == api_name,
        ).first()

    # --- Fields ---

    def store_fields(self, meta_version_id, object_id, fields_list):
        created = []
        for f in fields_list:
            field = MetaField(
                meta_version_id=meta_version_id,
                meta_object_id=object_id,
                api_name=f["api_name"],
                label=f.get("label"),
                field_type=f["field_type"],
                is_required=f.get("is_required", False),
                is_custom=f.get("is_custom", False),
                is_createable=f.get("is_createable", True),
                is_updateable=f.get("is_updateable", True),
                reference_to=f.get("reference_to"),
                length=f.get("length"),
                precision=f.get("precision"),
                scale=f.get("scale"),
                picklist_values=f.get("picklist_values"),
                default_value=f.get("default_value"),
            )
            self.db.add(field)
            created.append(field)
        self.db.commit()
        return created

    def get_fields(self, meta_version_id, object_id=None):
        q = self.db.query(MetaField).filter(MetaField.meta_version_id == meta_version_id)
        if object_id:
            q = q.filter(MetaField.meta_object_id == object_id)
        return q.all()

    # --- Validation Rules ---

    def store_validation_rules(self, meta_version_id, object_id, rules_list):
        for r in rules_list:
            vr = MetaValidationRule(
                meta_version_id=meta_version_id,
                meta_object_id=object_id,
                rule_name=r["rule_name"],
                error_condition_formula=r.get("error_condition_formula"),
                error_message=r.get("error_message"),
                is_active=r.get("is_active", True),
            )
            self.db.add(vr)
        self.db.commit()

    def get_validation_rules(self, meta_version_id, object_id=None):
        q = self.db.query(MetaValidationRule).filter(
            MetaValidationRule.meta_version_id == meta_version_id,
        )
        if object_id:
            q = q.filter(MetaValidationRule.meta_object_id == object_id)
        return q.all()

    # --- Flows ---

    def store_flows(self, meta_version_id, flows_list):
        for f in flows_list:
            flow = MetaFlow(
                meta_version_id=meta_version_id,
                api_name=f["api_name"],
                label=f.get("label"),
                flow_type=f["flow_type"],
                trigger_object=f.get("trigger_object"),
                trigger_event=f.get("trigger_event"),
                is_active=f.get("is_active", True),
                entry_conditions=f.get("entry_conditions"),
            )
            self.db.add(flow)
        self.db.commit()

    def get_flows(self, meta_version_id):
        return self.db.query(MetaFlow).filter(
            MetaFlow.meta_version_id == meta_version_id,
        ).all()

    # --- Triggers ---

    def store_triggers(self, meta_version_id, object_id, triggers_list):
        for t in triggers_list:
            trigger = MetaTrigger(
                meta_version_id=meta_version_id,
                meta_object_id=object_id,
                trigger_name=t["trigger_name"],
                events=t.get("events"),
                is_active=t.get("is_active", True),
            )
            self.db.add(trigger)
        self.db.commit()

    def get_triggers(self, meta_version_id, object_id=None):
        q = self.db.query(MetaTrigger).filter(
            MetaTrigger.meta_version_id == meta_version_id,
        )
        if object_id:
            q = q.filter(MetaTrigger.meta_object_id == object_id)
        return q.all()

    # --- Record Types ---

    def store_record_types(self, meta_version_id, object_id, record_types_list):
        for rt in record_types_list:
            rec = MetaRecordType(
                meta_version_id=meta_version_id,
                meta_object_id=object_id,
                api_name=rt["api_name"],
                label=rt.get("label"),
                is_active=rt.get("is_active", True),
                is_default=rt.get("is_default", False),
            )
            self.db.add(rec)
        self.db.commit()

    # --- Diffs ---

    def diff_fields(self, old_version_id, new_version_id):
        old_fields = {
            (f.meta_object.api_name, f.api_name): f
            for f in self.get_fields(old_version_id)
        }
        new_fields = {
            (f.meta_object.api_name, f.api_name): f
            for f in self.get_fields(new_version_id)
        }

        old_keys = set(old_fields.keys())
        new_keys = set(new_fields.keys())

        added = [{"object": k[0], "field": k[1]} for k in (new_keys - old_keys)]
        removed = [{"object": k[0], "field": k[1]} for k in (old_keys - new_keys)]

        changed = []
        for key in old_keys & new_keys:
            old_f = old_fields[key]
            new_f = new_fields[key]
            diffs = {}
            for attr in ("field_type", "is_required", "is_custom", "is_createable",
                         "is_updateable", "reference_to", "length", "default_value"):
                old_val = getattr(old_f, attr)
                new_val = getattr(new_f, attr)
                if old_val != new_val:
                    diffs[attr] = {"old": old_val, "new": new_val}
            if diffs:
                changed.append({"object": key[0], "field": key[1], "changes": diffs})

        return {"added": added, "removed": removed, "changed": changed}

    def diff_validation_rules(self, old_version_id, new_version_id):
        old_vrs = {
            (vr.meta_object.api_name if vr.meta_object else "", vr.rule_name): vr
            for vr in self.get_validation_rules(old_version_id)
        }
        new_vrs = {
            (vr.meta_object.api_name if vr.meta_object else "", vr.rule_name): vr
            for vr in self.get_validation_rules(new_version_id)
        }
        old_keys = set(old_vrs.keys())
        new_keys = set(new_vrs.keys())

        added = [{"object": k[0], "rule": k[1]} for k in (new_keys - old_keys)]
        removed = [{"object": k[0], "rule": k[1]} for k in (old_keys - new_keys)]
        changed = []
        for key in old_keys & new_keys:
            o, n = old_vrs[key], new_vrs[key]
            if (o.error_condition_formula != n.error_condition_formula or
                    o.error_message != n.error_message or o.is_active != n.is_active):
                changed.append({"object": key[0], "rule": key[1]})
        return {"added": added, "removed": removed, "changed": changed}

    def diff_flows(self, old_version_id, new_version_id):
        old_flows = {f.api_name: f for f in self.get_flows(old_version_id)}
        new_flows = {f.api_name: f for f in self.get_flows(new_version_id)}
        old_keys = set(old_flows.keys())
        new_keys = set(new_flows.keys())

        added = [{"flow": k} for k in (new_keys - old_keys)]
        removed = [{"flow": k} for k in (old_keys - new_keys)]
        changed = []
        for key in old_keys & new_keys:
            o, n = old_flows[key], new_flows[key]
            if (o.flow_type != n.flow_type or o.trigger_object != n.trigger_object or
                    o.is_active != n.is_active):
                changed.append({"flow": key})
        return {"added": added, "removed": removed, "changed": changed}

    # --- Archival ---

    def archive_old_versions(self, environment_id, keep_count=20):
        versions = self.db.query(MetaVersion).filter(
            MetaVersion.environment_id == environment_id,
            MetaVersion.status == "complete",
            MetaVersion.lifecycle == "active",
        ).order_by(MetaVersion.started_at.desc()).all()

        archived = 0
        for v in versions[keep_count:]:
            v.lifecycle = "archived"
            archived += 1
        if archived:
            self.db.commit()
        return archived
