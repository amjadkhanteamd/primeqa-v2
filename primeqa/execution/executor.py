"""Step execution engine.

Handles adaptive capture, before/after state diffing, PQA_ naming convention,
and step-level execution state tracking.

R1 additions:
  - Emits SSE events on start/finish of each step via primeqa.runs.streams
  - Captures extended log fields: soql_queries, http_status, timings,
    correlation_id (tied via contextvar so we can cross-reference with
    Anthropic/Railway/SF logs)
"""

import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timezone

import requests as http_requests

from primeqa.runs import streams as run_streams

log = logging.getLogger(__name__)

CRITICAL_FIELDS = {"StageName", "Status", "OwnerId", "Amount", "CloseDate", "IsWon", "IsClosed"}


class SalesforceExecutionClient:
    """Salesforce REST API client for step execution."""

    def __init__(self, instance_url, api_version, access_token):
        # rstrip('/') on instance_url \u2014 strict My-Domain orgs reject double
        # slashes in the path with 400 Bad Request.
        self.base_url = f"{instance_url.rstrip('/')}/services/data/v{api_version}"
        self.session = http_requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def create_record(self, sobject, data):
        url = f"{self.base_url}/sobjects/{sobject}/"
        resp = self.session.post(url, json=data, timeout=30)
        return self._build_response(resp, "POST", url, data)

    def update_record(self, sobject, record_id, data):
        url = f"{self.base_url}/sobjects/{sobject}/{record_id}"
        resp = self.session.patch(url, json=data, timeout=30)
        return self._build_response(resp, "PATCH", url, data)

    def delete_record(self, sobject, record_id):
        url = f"{self.base_url}/sobjects/{sobject}/{record_id}"
        resp = self.session.delete(url, timeout=30)
        return self._build_response(resp, "DELETE", url, None)

    def query(self, soql):
        url = f"{self.base_url}/query/"
        resp = self.session.get(url, params={"q": soql}, timeout=30)
        return self._build_response(resp, "GET", url, {"q": soql})

    def get_record(self, sobject, record_id, fields=None):
        url = f"{self.base_url}/sobjects/{sobject}/{record_id}"
        params = {}
        if fields:
            params["fields"] = ",".join(fields)
        resp = self.session.get(url, params=params, timeout=30)
        return self._build_response(resp, "GET", url, params)

    def record_exists(self, sobject, record_id):
        url = f"{self.base_url}/sobjects/{sobject}/{record_id}"
        resp = self.session.get(url, timeout=15)
        return resp.status_code == 200

    @staticmethod
    def _build_response(resp, method, url, body):
        try:
            resp_body = resp.json() if resp.content else None
        except Exception:
            resp_body = resp.text
        return {
            "api_request": {"method": method, "url": url, "body": body},
            "api_response": {
                "status_code": resp.status_code,
                "body": resp_body,
            },
            "http_status": resp.status_code,
            "success": 200 <= resp.status_code < 300,
            "record_id": (resp_body or {}).get("id") if isinstance(resp_body, dict) else None,
        }


class StepExecutor:
    """Executes individual test steps against Salesforce."""

    def __init__(self, sf_client, run_id, capture_mode, step_result_repo,
                 entity_repo, idempotency_mgr, meta_vr_lookup=None,
                 tenant_id=None):
        self.sf = sf_client
        self.run_id = run_id
        self.tenant_id = tenant_id  # passed through to emit_step_* for durable event log
        self.capture_mode = capture_mode
        self.step_result_repo = step_result_repo
        self.entity_repo = entity_repo
        self.idempotency = idempotency_mgr
        self.meta_vr_lookup = meta_vr_lookup or (lambda obj: False)
        self.state_vars = {}

    def execute_step(self, run_test_result_id, step_def, test_case_id=None,
                     correlation_id=None):
        step_order = step_def.get("step_order", 0)
        action = step_def.get("action", "")
        target_object = step_def.get("target_object", "")
        logical_id = step_def.get("state_ref", f"step_{step_order}")
        if logical_id.startswith("$"):
            logical_id = logical_id[1:]

        # Correlation ID: one per step (within a test) for cross-system log
        # join. Propagated into api_request so SF-side logs can carry it too.
        correlation_id = correlation_id or uuid.uuid4().hex[:16]

        step_result = self.step_result_repo.create_step_result(
            run_test_result_id=run_test_result_id,
            step_order=step_order,
            step_action=action,
            target_object=target_object,
            status="passed",
            execution_state="not_started",
        )

        self.step_result_repo.update_step_result(step_result.id, {
            "execution_state": "in_progress",
            "correlation_id": correlation_id,
        })

        # SSE event \u2014 step starting
        run_streams.emit_step_started(
            self.run_id, test_case_id or 0, step_order,
            tenant_id=self.tenant_id,
            action=action, target_object=target_object,
            correlation_id=correlation_id,
        )

        start_time = time.time()
        t_setup_done = start_time
        t_sf_done = start_time
        before_state = None
        after_state = None
        field_diff = None
        api_request = None
        api_response = None
        http_status = None
        soql_queries = None
        error_message = None
        status = "passed"
        target_record_id = None

        try:
            resolved = self._resolve_refs(step_def.get("field_values", {}))
            record_ref = self._resolve_ref(step_def.get("record_ref"))

            # Fail-fast on unresolved $vars \u2014 sending a literal "$foo" to
            # Salesforce produces a cryptic MALFORMED_ID response; catch it
            # here and surface an actionable error that points at the step
            # definition. This usually means the AI generator emitted a
            # reference without giving the prior create step a matching
            # state_ref.
            unresolved = [v for v in list(resolved.values()) + [record_ref]
                          if isinstance(v, str) and v.startswith("$")]
            if unresolved:
                raise ValueError(
                    "Unresolved reference variable(s): " + ", ".join(sorted(set(unresolved))) +
                    f" \u2014 no prior step stored them. Available vars: {sorted(self.state_vars.keys()) or '(none)'}. "
                    "Fix the test case so a prior create step sets `state_ref` to the matching $var."
                )

            if self.capture_mode == "full" and action in ("update", "delete") and record_ref:
                before_state = self._capture_state(target_object, record_ref)

            t_setup_done = time.time()

            if action == "create":
                result = self._execute_create(
                    target_object, resolved, step_order, logical_id,
                    run_test_result_id, step_result.id,
                )
            elif action == "update":
                result = self.sf.update_record(target_object, record_ref, resolved)
            elif action == "query":
                soql = step_def.get("soql", f"SELECT Id FROM {target_object} LIMIT 1")
                soql = self._resolve_soql_refs(soql)
                soql_queries = [soql]
                result = self.sf.query(soql)
            elif action == "verify":
                result = self._execute_verify(target_object, record_ref, step_def.get("assertions", {}))
            elif action == "delete":
                result = self.sf.delete_record(target_object, record_ref)
            elif action == "wait":
                time.sleep(step_def.get("duration", 1))
                result = {"api_request": None, "api_response": None, "success": True, "record_id": None}
            elif action == "convert":
                result = self._execute_convert(target_object, record_ref, resolved)
            else:
                raise ValueError(f"Unknown action: {action}")

            t_sf_done = time.time()

            api_request = result.get("api_request")
            api_response = result.get("api_response")
            http_status = result.get("http_status")
            target_record_id = result.get("record_id") or record_ref

            if not result.get("success"):
                status = "failed"
                error_message = str(api_response.get("body") if api_response else "Unknown error")

            if action == "create" and result.get("record_id"):
                state_ref = step_def.get("state_ref")
                if state_ref and state_ref.startswith("$"):
                    self.state_vars[state_ref[1:]] = result["record_id"]
                target_record_id = result["record_id"]

            should_capture = self._should_capture(
                action, target_object, status, step_def.get("field_values", {}),
            )
            if should_capture and target_record_id and action in ("create", "update"):
                after_state = self._capture_state(target_object, target_record_id)
                if before_state and after_state:
                    field_diff = self._compute_diff(before_state, after_state)

        except Exception as e:
            status = "error"
            error_message = str(e)

        end_time = time.time()
        duration_ms = int((end_time - start_time) * 1000)
        execution_state = "completed" if status != "error" else "partially_completed"

        timings = {
            "total_ms": duration_ms,
            "setup_ms": int((t_setup_done - start_time) * 1000),
            "sf_ms": int((t_sf_done - t_setup_done) * 1000),
            "capture_ms": int((end_time - t_sf_done) * 1000),
        }

        self.step_result_repo.update_step_result(step_result.id, {
            "status": status,
            "execution_state": execution_state,
            "target_record_id": target_record_id,
            "before_state": before_state,
            "after_state": after_state,
            "field_diff": field_diff,
            "api_request": api_request,
            "api_response": api_response,
            "http_status": http_status,
            "soql_queries": soql_queries,
            "timings": timings,
            "error_message": error_message,
            "duration_ms": duration_ms,
        })

        # SSE event \u2014 step finished. Include enough for the UI timeline to
        # render without fetching the row back over REST.
        run_streams.emit_step_finished(
            self.run_id, test_case_id or 0, step_order,
            status,
            tenant_id=self.tenant_id,
            action=action,
            target_object=target_object,
            http_status=http_status,
            duration_ms=duration_ms,
            error_summary=(error_message[:140] if error_message else None),
            correlation_id=correlation_id,
        )

        return step_result, status

    def _execute_create(self, sobject, field_values, step_order, logical_id,
                        run_test_result_id, step_result_id):
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        pqa_name = f"PQA_{self.run_id}_{logical_id} {timestamp}"
        if "Name" not in field_values:
            field_values["Name"] = pqa_name
        else:
            field_values["Name"] = pqa_name

        idem_key = self.idempotency.generate_key(
            self.run_id, step_order, sobject, logical_id,
        )
        existing = self.idempotency.check_existing(idem_key)
        if existing:
            if self.sf.record_exists(sobject, existing.sf_record_id):
                return {
                    "api_request": None,
                    "api_response": {"body": {"id": existing.sf_record_id, "reused": True}},
                    "success": True,
                    "record_id": existing.sf_record_id,
                }

        result = self.sf.create_record(sobject, field_values)
        if result["success"] and result["record_id"]:
            fingerprint = self.idempotency.compute_fingerprint(sobject, field_values)
            self.entity_repo.create_entity(
                run_id=self.run_id,
                run_step_result_id=step_result_id,
                entity_type=sobject,
                sf_record_id=result["record_id"],
                creation_source="direct",
                logical_identifier=logical_id,
                primeqa_idempotency_key=idem_key,
                creation_fingerprint=fingerprint,
            )
        return result

    def _execute_verify(self, sobject, record_id, assertions):
        result = self.sf.get_record(sobject, record_id, list(assertions.keys()))
        if not result["success"]:
            return result
        record_data = result["api_response"]["body"]
        failures = []
        for field, expected in assertions.items():
            actual = record_data.get(field)
            if actual != expected:
                failures.append(f"{field}: expected {expected}, got {actual}")
        if failures:
            result["success"] = False
            result["api_response"]["body"]["assertion_failures"] = failures
        return result

    def _execute_convert(self, sobject, record_id, field_values):
        return self.sf.create_record(f"Lead/{record_id}/convert", field_values or {})

    def _should_capture(self, action, target_object, status, field_values):
        if self.capture_mode == "full":
            return True
        if self.capture_mode == "minimal":
            return False
        if status == "failed":
            return True
        touched_fields = set(field_values.keys()) if field_values else set()
        if touched_fields & CRITICAL_FIELDS:
            return True
        if self.meta_vr_lookup(target_object):
            return True
        return False

    def _capture_state(self, sobject, record_id):
        result = self.sf.get_record(sobject, record_id)
        if result["success"]:
            body = result["api_response"]["body"]
            return {k: v for k, v in body.items()
                    if not k.startswith("attributes") and k != "attributes"}
        return None

    @staticmethod
    def _compute_diff(before, after):
        if not before or not after:
            return None
        diff = {}
        all_keys = set(list(before.keys()) + list(after.keys()))
        for key in all_keys:
            old_val = before.get(key)
            new_val = after.get(key)
            if old_val != new_val:
                diff[key] = {"old": old_val, "new": new_val}
        return diff if diff else None

    def _resolve_refs(self, field_values):
        resolved = {}
        for k, v in field_values.items():
            resolved[k] = self._resolve_ref(v) if isinstance(v, str) else v
        return resolved

    def _resolve_ref(self, value):
        if isinstance(value, str) and value.startswith("$"):
            var_name = value[1:]
            return self.state_vars.get(var_name, value)
        return value

    def _resolve_soql_refs(self, soql):
        for var_name, var_value in self.state_vars.items():
            soql = soql.replace(f"${var_name}", f"'{var_value}'")
        return soql
