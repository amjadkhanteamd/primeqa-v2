"""Service layer for the metadata domain.

Business logic: refresh from Salesforce, diff, impact analysis, lifecycle archival.
"""

import hashlib
import json
import logging

import requests as http_requests

from primeqa.test_management.models import TestCaseVersion, TestCase
from primeqa.metadata.models import MetaVersion

log = logging.getLogger(__name__)

SYSTEM_OBJECTS_EXCLUDE = {
    "ApexLog", "AsyncApexJob", "BatchApexErrorEvent", "CronJobDetail",
    "CronTrigger", "DataStatistics", "EmailServicesFunction", "EventLogFile",
    "FieldPermissions", "LoginHistory", "ObjectPermissions", "PermissionSet",
    "PermissionSetAssignment", "SetupAuditTrail", "UserLogin",
}


class MetadataService:
    def __init__(self, metadata_repo, env_repo):
        self.metadata_repo = metadata_repo
        self.env_repo = env_repo

    def refresh_metadata(self, environment_id, tenant_id):
        env = self.env_repo.get_environment(environment_id, tenant_id)
        if not env:
            raise ValueError("Environment not found")

        creds = self.env_repo.get_credentials_decrypted(environment_id)
        if not creds or not creds.get("access_token"):
            raise ValueError("No credentials stored for this environment")

        prev_version = self.metadata_repo.get_current_version(environment_id)

        version_num = 1
        if prev_version:
            try:
                version_num = int(prev_version.version_label.lstrip("v")) + 1
            except (ValueError, AttributeError):
                version_num = prev_version.id + 1

        mv = self.metadata_repo.create_meta_version(
            environment_id, f"v{version_num}",
        )

        try:
            sf = SalesforceClient(env.sf_instance_url, env.sf_api_version, creds["access_token"])

            sobjects = sf.get_objects()
            filtered = [
                o for o in sobjects
                if o["name"] not in SYSTEM_OBJECTS_EXCLUDE
                and (o.get("createable") or o.get("queryable"))
                and not o["name"].endswith("ChangeEvent")
                and not o["name"].endswith("Feed")
                and not o["name"].endswith("Share")
                and not o["name"].endswith("History")
            ]

            stored_objects = self.metadata_repo.store_objects(mv.id, [
                {
                    "api_name": o["name"],
                    "label": o.get("label"),
                    "key_prefix": o.get("keyPrefix"),
                    "is_custom": o.get("custom", False),
                    "is_queryable": o.get("queryable", True),
                    "is_createable": o.get("createable", True),
                    "is_updateable": o.get("updateable", True),
                    "is_deletable": o.get("deletable", True),
                }
                for o in filtered
            ])

            total_fields = 0
            obj_map = {o.api_name: o for o in stored_objects}

            for obj in stored_objects:
                try:
                    describe = sf.describe_object(obj.api_name)
                except Exception as e:
                    log.warning(f"Failed to describe {obj.api_name}: {e}")
                    continue

                fields_data = [
                    {
                        "api_name": f["name"],
                        "label": f.get("label"),
                        "field_type": f["type"],
                        "is_required": not f.get("nillable", True) and not f.get("defaultedOnCreate", False),
                        "is_custom": f.get("custom", False),
                        "is_createable": f.get("createable", True),
                        "is_updateable": f.get("updateable", True),
                        "reference_to": f["referenceTo"][0] if f.get("referenceTo") else None,
                        "length": f.get("length"),
                        "precision": f.get("precision"),
                        "scale": f.get("scale"),
                        "picklist_values": (
                            [{"value": pv["value"], "label": pv.get("label")}
                             for pv in f.get("picklistValues", [])]
                            if f.get("picklistValues") else None
                        ),
                        "default_value": str(f["defaultValue"]) if f.get("defaultValue") is not None else None,
                    }
                    for f in describe.get("fields", [])
                ]
                self.metadata_repo.store_fields(mv.id, obj.id, fields_data)
                total_fields += len(fields_data)

                record_types = [
                    {
                        "api_name": rt.get("developerName", rt.get("name", "")),
                        "label": rt.get("name"),
                        "is_active": rt.get("active", True),
                        "is_default": rt.get("defaultRecordTypeMapping", False),
                    }
                    for rt in describe.get("recordTypeInfos", [])
                    if rt.get("developerName") != "Master"
                ]
                if record_types:
                    self.metadata_repo.store_record_types(mv.id, obj.id, record_types)

            vrs = sf.query_tooling(
                "SELECT Id, ValidationName, Active, "
                "EntityDefinition.QualifiedApiName, "
                "ErrorConditionFormula, ErrorMessage "
                "FROM ValidationRule"
            )
            vr_count = 0
            for vr in vrs:
                obj_name = vr.get("EntityDefinition", {}).get("QualifiedApiName", "")
                obj = obj_map.get(obj_name)
                if obj:
                    self.metadata_repo.store_validation_rules(mv.id, obj.id, [{
                        "rule_name": vr.get("ValidationName", ""),
                        "error_condition_formula": vr.get("ErrorConditionFormula"),
                        "error_message": vr.get("ErrorMessage"),
                        "is_active": vr.get("Active", True),
                    }])
                    vr_count += 1

            flow_records = sf.query_tooling(
                "SELECT Id, ApiName, Label, ProcessType, "
                "TriggerType, "
                "TriggerObjectOrEvent.QualifiedApiName "
                "FROM Flow WHERE Status = 'Active'"
            )
            flow_type_map = {
                "AutoLaunchedFlow": "autolaunched",
                "Flow": "screen",
                "Workflow": "record_triggered",
                "CustomEvent": "record_triggered",
                "InvocableProcess": "process_builder",
            }
            trigger_event_map = {
                "RecordAfterSave": "create_or_update",
                "RecordBeforeSave": "create_or_update",
                "RecordBeforeDelete": "delete",
            }
            flows_data = []
            for f in flow_records:
                pt = f.get("ProcessType", "")
                flows_data.append({
                    "api_name": f.get("ApiName", ""),
                    "label": f.get("Label"),
                    "flow_type": flow_type_map.get(pt, "autolaunched"),
                    "trigger_object": (f.get("TriggerObjectOrEvent") or {}).get("QualifiedApiName"),
                    "trigger_event": trigger_event_map.get(f.get("TriggerType")),
                    "is_active": True,
                })
            if flows_data:
                self.metadata_repo.store_flows(mv.id, flows_data)

            trigger_records = sf.query_tooling(
                "SELECT Id, Name, TableEnumOrId, "
                "UsageBeforeInsert, UsageAfterInsert, "
                "UsageBeforeUpdate, UsageAfterUpdate, "
                "UsageBeforeDelete, UsageAfterDelete "
                "FROM ApexTrigger"
            )
            trigger_count = 0
            for t in trigger_records:
                obj_name = t.get("TableEnumOrId", "")
                obj = obj_map.get(obj_name)
                if obj:
                    events = []
                    if t.get("UsageBeforeInsert") or t.get("UsageAfterInsert"):
                        events.append("insert")
                    if t.get("UsageBeforeUpdate") or t.get("UsageAfterUpdate"):
                        events.append("update")
                    if t.get("UsageBeforeDelete") or t.get("UsageAfterDelete"):
                        events.append("delete")
                    self.metadata_repo.store_triggers(mv.id, obj.id, [{
                        "trigger_name": t.get("Name", ""),
                        "events": ",".join(events),
                        "is_active": True,
                    }])
                    trigger_count += 1

            hash_input = sorted([o.api_name for o in stored_objects])
            all_fields = self.metadata_repo.get_fields(mv.id)
            hash_input.extend(sorted([f"{f.meta_object_id}:{f.api_name}" for f in all_fields]))
            snapshot_hash = hashlib.sha256(json.dumps(hash_input).encode()).hexdigest()

            counts = {
                "objects": len(stored_objects),
                "fields": total_fields,
                "vrs": vr_count,
                "flows": len(flows_data),
                "triggers": trigger_count,
            }
            self.metadata_repo.complete_meta_version(mv.id, snapshot_hash, counts)
            self.metadata_repo.set_current_version(environment_id, mv.id)

            diff_summary = None
            changes_detected = False
            if prev_version and prev_version.snapshot_hash != snapshot_hash:
                changes_detected = True
                diff_summary = self._compute_diffs(prev_version.id, mv.id)

            self.metadata_repo.archive_old_versions(environment_id)

            return {
                "version_id": mv.id,
                "version_label": f"v{version_num}",
                "objects_count": counts["objects"],
                "fields_count": counts["fields"],
                "vr_count": counts["vrs"],
                "flow_count": counts["flows"],
                "trigger_count": counts["triggers"],
                "snapshot_hash": snapshot_hash,
                "changes_detected": changes_detected,
                "diff_summary": diff_summary,
            }

        except Exception as e:
            self.metadata_repo.fail_meta_version(mv.id)
            raise

    def _compute_diffs(self, old_version_id, new_version_id):
        field_diff = self.metadata_repo.diff_fields(old_version_id, new_version_id)
        vr_diff = self.metadata_repo.diff_validation_rules(old_version_id, new_version_id)
        flow_diff = self.metadata_repo.diff_flows(old_version_id, new_version_id)
        return {
            "fields": field_diff,
            "validation_rules": vr_diff,
            "flows": flow_diff,
        }

    def run_impact_analysis(self, environment_id, new_version_id, old_version_id):
        from primeqa.test_management.models import MetadataImpact

        diffs = self._compute_diffs(old_version_id, new_version_id)
        db = self.metadata_repo.db
        affected_count = 0

        changed_entities = set()
        for f in diffs["fields"]["added"] + diffs["fields"]["removed"] + diffs["fields"]["changed"]:
            changed_entities.add(f["object"])
        for vr in diffs["validation_rules"]["added"] + diffs["validation_rules"]["removed"] + diffs["validation_rules"]["changed"]:
            changed_entities.add(vr["object"])
        for fl in diffs["flows"]["added"] + diffs["flows"]["removed"] + diffs["flows"]["changed"]:
            changed_entities.add(fl["flow"])

        for entity_ref in changed_entities:
            affected_versions = db.query(TestCaseVersion).filter(
                TestCaseVersion.referenced_entities.op("@>")(json.dumps([entity_ref])),
            ).all()

            for tcv in affected_versions:
                tc = db.query(TestCase).filter(TestCase.id == tcv.test_case_id).first()
                if not tc:
                    continue

                impact_type = "field_changed"
                if any(f["object"] == entity_ref for f in diffs["fields"]["removed"]):
                    impact_type = "field_removed"
                elif any(f["object"] == entity_ref for f in diffs["fields"]["added"]):
                    impact_type = "field_added"
                elif any(vr.get("object") == entity_ref for vr in
                         diffs["validation_rules"]["added"] + diffs["validation_rules"]["removed"] + diffs["validation_rules"]["changed"]):
                    impact_type = "vr_changed"
                elif any(fl.get("flow") == entity_ref for fl in
                         diffs["flows"]["added"] + diffs["flows"]["removed"] + diffs["flows"]["changed"]):
                    impact_type = "flow_changed"

                impact = MetadataImpact(
                    new_meta_version_id=new_version_id,
                    prev_meta_version_id=old_version_id,
                    test_case_id=tc.id,
                    impact_type=impact_type,
                    entity_ref=entity_ref,
                    change_details=diffs,
                )
                db.add(impact)
                affected_count += 1

        db.commit()
        return affected_count

    def get_current_version_summary(self, environment_id):
        mv = self.metadata_repo.get_current_version(environment_id)
        if not mv:
            return None
        return {
            "version_id": mv.id,
            "version_label": mv.version_label,
            "status": mv.status,
            "lifecycle": mv.lifecycle,
            "object_count": mv.object_count,
            "field_count": mv.field_count,
            "vr_count": mv.vr_count,
            "flow_count": mv.flow_count,
            "trigger_count": mv.trigger_count,
            "snapshot_hash": mv.snapshot_hash,
            "started_at": mv.started_at.isoformat() if mv.started_at else None,
            "completed_at": mv.completed_at.isoformat() if mv.completed_at else None,
        }

    def get_diff(self, environment_id):
        current = self.metadata_repo.get_current_version(environment_id)
        previous = self.metadata_repo.get_previous_version(environment_id)
        if not current or not previous:
            return None
        return self._compute_diffs(previous.id, current.id)

    def list_pending_impacts(self, environment_id):
        from primeqa.test_management.models import MetadataImpact
        current = self.metadata_repo.get_current_version(environment_id)
        if not current:
            return []
        impacts = self.metadata_repo.db.query(MetadataImpact).filter(
            MetadataImpact.new_meta_version_id == current.id,
            MetadataImpact.resolution == "pending",
        ).all()
        return [
            {
                "id": i.id,
                "test_case_id": i.test_case_id,
                "impact_type": i.impact_type,
                "entity_ref": i.entity_ref,
                "resolution": i.resolution,
            }
            for i in impacts
        ]


class SalesforceClient:
    """Thin wrapper around Salesforce REST + Tooling APIs."""

    def __init__(self, instance_url, api_version, access_token):
        self.base_url = f"{instance_url}/services/data/v{api_version}"
        self.session = http_requests.Session()
        self.session.headers["Authorization"] = f"Bearer {access_token}"
        self.session.headers["Accept"] = "application/json"

    def _get(self, url):
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def get_objects(self):
        data = self._get(f"{self.base_url}/sobjects/")
        return data.get("sobjects", [])

    def describe_object(self, object_name):
        return self._get(f"{self.base_url}/sobjects/{object_name}/describe/")

    def query_tooling(self, soql):
        url = f"{self.base_url}/tooling/query/"
        resp = self.session.get(url, params={"q": soql}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        records = data.get("records", [])

        while data.get("nextRecordsUrl"):
            next_url = f"{self.base_url.rsplit('/services/', 1)[0]}{data['nextRecordsUrl']}"
            data = self._get(next_url)
            records.extend(data.get("records", []))

        return records
