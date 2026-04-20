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

    def convert_lead(self, lead_id, converted_status=None,
                      do_not_create_opportunity=False,
                      opportunity_name=None):
        """Convert a Lead via the Invocable Standard Actions REST endpoint.

        POST /actions/standard/convertLead
        Body: {"inputs": [{leadId, convertedStatus, doNotCreateOpportunity, opportunityName}]}
        Response (list, one entry per input):
          [{ isSuccess, errors, outputValues: {accountId, contactId, opportunityId} }]

        First attempt pointed at /sobjects/LeadConvert which Salesforce
        does not expose in the standard REST namespace (returned 404 on
        every call). The /actions/standard/convertLead path is GA since
        API v32.0 and is the documented way to trigger Lead conversion
        over REST without Apex.

        Returns a normalised envelope (same shape create_record uses) so
        the caller doesn't care that the upstream shape differs.
        """
        url = f"{self.base_url}/actions/standard/convertLead"
        single_input = {
            "leadId": lead_id,
            "doNotCreateOpportunity": bool(do_not_create_opportunity),
        }
        if converted_status:
            single_input["convertedStatus"] = converted_status
        if opportunity_name:
            single_input["opportunityName"] = opportunity_name
        body = {"inputs": [single_input]}

        resp = self.session.post(url, json=body, timeout=30)
        envelope = self._build_response(resp, "POST", url, body)

        # SF returns TWO shapes depending on whether the endpoint exists:
        #   - Endpoint exists + action succeeded  : list [{ isSuccess, outputValues, errors }]
        #   - Endpoint 404 / auth error           : list [{ errorCode, message }]
        # Flatten either into a predictable shape; preserve original error
        # info so the step log stays actionable (prior flattening mapped
        # missing keys to None/[], hiding the real 404 message).
        raw = envelope.get("api_response", {}).get("body")
        if isinstance(raw, list) and raw:
            first = raw[0] or {}
            if "isSuccess" in first:
                # Normal invocable-action response shape.
                is_ok = bool(first.get("isSuccess"))
                out = first.get("outputValues") or {}
                flat = {
                    "accountId":     out.get("accountId"),
                    "contactId":     out.get("contactId"),
                    "opportunityId": out.get("opportunityId"),
                    "isSuccess":     is_ok,
                    "errors":        first.get("errors") or [],
                }
                envelope["api_response"]["body"] = flat
                envelope["success"] = bool(envelope.get("success")) and is_ok
                if is_ok:
                    envelope["record_id"] = flat["accountId"] or envelope.get("record_id")
            else:
                # Error shape \u2014 e.g. [{errorCode:"NOT_FOUND", message:"The
                # requested resource does not exist"}]. Preserve the real
                # SF message so the step log shows something useful
                # instead of "isSuccess: false, errors: []".
                envelope["success"] = False
                # Leave api_response.body as-is (the list with the
                # original errorCode/message) so operators can read it.
        return envelope

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
                 name_createable_lookup=None,
                 tenant_id=None):
        self.sf = sf_client
        self.run_id = run_id
        self.tenant_id = tenant_id  # passed through to emit_step_* for durable event log
        self.capture_mode = capture_mode
        self.step_result_repo = step_result_repo
        self.entity_repo = entity_repo
        self.idempotency = idempotency_mgr
        self.meta_vr_lookup = meta_vr_lookup or (lambda obj: False)
        # Metadata-backed lookup: does this SObject accept writes to its
        # `Name` field? Needed because `_execute_create` auto-injects a
        # PQA_ prefix into Name for run-tracking, which Salesforce rejects
        # on objects where Name is a read-only formula (Lead, Contact) or
        # auto-generated (Case uses CaseNumber). Return tri-state:
        #   True  \u2014 metadata confirms Name is createable, inject safely
        #   False \u2014 metadata confirms Name is NOT createable, skip inject
        #   None  \u2014 metadata unknown / not synced for this object, skip
        #           inject (err on the safe side; lose the PQA_ tag rather
        #           than break the create with INVALID_FIELD_FOR_INSERT_UPDATE)
        # Default callback returns None \u2014 safe skip \u2014 for tests that don't
        # wire a lookup. Worker wires the real DB-backed one.
        self.name_createable_lookup = name_createable_lookup or (lambda obj: None)
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
            # Resolve assertion refs up-front too so the verify branch
            # can use them AND so the unresolved-fail-fast catches
            # assertion-side mistakes (previously verify silently
            # compared literal "$foo" strings against real record
            # values, producing baffling assertion_failures).
            resolved_assertions = self._resolve_refs(step_def.get("assertions", {}))

            # Fail-fast on unresolved $vars \u2014 sending a literal "$foo" to
            # Salesforce produces a cryptic MALFORMED_ID response; catch it
            # here and surface an actionable error that points at the step
            # definition. This usually means the AI generator emitted a
            # reference without giving the prior create step a matching
            # state_ref.
            unresolved = [v for v in list(resolved.values())
                                    + list(resolved_assertions.values())
                                    + [record_ref]
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
                # Assertions already resolved above (same pass as
                # field_values) so the unresolved-ref fail-fast catches
                # assertion-side typos consistently with create/update.
                result = self._execute_verify(target_object, record_ref, resolved_assertions)
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

            # Lead convert produces THREE record ids (Account, Contact,
            # Opportunity) in the response body. Later steps reference them
            # as $lead.ConvertedAccountId / .ConvertedContactId /
            # .ConvertedOpportunityId. Stash each dotted key in state_vars
            # so _resolve_ref finds them without any special casing.
            if action == "convert" and result.get("success"):
                body = (result.get("api_response") or {}).get("body") or {}
                ref_name = None
                if isinstance(record_ref, str) and record_ref.startswith("$"):
                    ref_name = record_ref[1:]
                # Also honor state_ref on the convert step itself, if set.
                sr = step_def.get("state_ref")
                if sr and sr.startswith("$"):
                    ref_name = sr[1:]
                if ref_name:
                    # Anthropic / simple-salesforce-style response may use
                    # lowerCamel or PascalCase; cover both without parsing.
                    out_map = {
                        "ConvertedAccountId":      body.get("accountId")      or body.get("AccountId"),
                        "ConvertedContactId":      body.get("contactId")      or body.get("ContactId"),
                        "ConvertedOpportunityId":  body.get("opportunityId")  or body.get("OpportunityId"),
                    }
                    for suffix, val in out_map.items():
                        if val:
                            self.state_vars[f"{ref_name}.{suffix}"] = val

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

        # ---- expect_fail handling ------------------------------------------
        # Negative-validation / boundary TCs often contain a step that SHOULD
        # be blocked by a Salesforce validation rule (e.g. Closing Won an
        # Opportunity with Contract Value = 0). The test proves the rule is
        # enforced; a Salesforce-side error IS the passing outcome. Flip the
        # status here so the test result reflects intent rather than raw
        # SF response.
        #
        # Unresolved $var errors are generator bugs, not real negative paths;
        # they always fail regardless of expect_fail so we don't silently
        # mask broken test plans.
        expect_fail = bool(step_def.get("expect_fail"))
        unresolved_ref_error = isinstance(error_message, str) and "Unresolved reference variable" in error_message
        expect_fail_class = None
        if expect_fail and not unresolved_ref_error:
            if status in ("failed", "error"):
                # Expected failure happened \u2014 this is a pass. Preserve the
                # original error in a dedicated field so the UI can render
                # "Expected fail: <msg>" as an info note rather than an error.
                error_message = f"Expected failure (verified): {error_message}"[:500] if error_message else "Expected failure (verified)"
                status = "passed"
                expect_fail_class = "expected_fail_verified"
            else:
                # Step succeeded when we expected it to fail \u2014 that's a real
                # test failure. The validation rule may have been weakened or
                # the test setup didn't exercise it.
                error_message = "Expected this step to fail (expect_fail=true), but it succeeded. Validation rule may not be in effect."
                status = "failed"
                expect_fail_class = "expected_fail_unverified"

        end_time = time.time()
        duration_ms = int((end_time - start_time) * 1000)
        execution_state = "completed" if status != "error" else "partially_completed"

        timings = {
            "total_ms": duration_ms,
            "setup_ms": int((t_setup_done - start_time) * 1000),
            "sf_ms": int((t_sf_done - t_setup_done) * 1000),
            "capture_ms": int((end_time - t_sf_done) * 1000),
        }

        update_payload = {
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
        }
        if expect_fail_class:
            update_payload["failure_class"] = expect_fail_class
        self.step_result_repo.update_step_result(step_result.id, update_payload)

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
            # 300-char cap \u2014 enough for the "Fix the test case so a prior
            # create step sets state_ref..." actionable tail of our custom
            # error messages while still keeping the log line readable.
            error_summary=(error_message[:300] if error_message else None),
            correlation_id=correlation_id,
        )

        return step_result, status

    def _execute_create(self, sobject, field_values, step_order, logical_id,
                        run_test_result_id, step_result_id):
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # Include test_case_id in the generated record Name so multiple
        # test cases from the same requirement don't collide on
        # Name uniqueness. We look up the TC id via the run_test_result
        # row, which we've been passing the id of throughout the chain.
        tc_suffix = ""
        try:
            tc_row = self.step_result_repo.db.query(
                __import__('primeqa.execution.models', fromlist=['RunTestResult']).RunTestResult
            ).filter_by(id=run_test_result_id).first()
            if tc_row and tc_row.test_case_id:
                tc_suffix = f"_{tc_row.test_case_id}"
        except Exception:
            pass
        pqa_name = f"PQA_{self.run_id}{tc_suffix}_{logical_id} {timestamp}"
        # Only inject / overwrite Name when we know it's a writable field
        # on this object. Metadata lookup returns True / False / None and
        # we only act on True. On Lead / Contact / Case etc. Name is a
        # formula or auto-number \u2014 setting it used to produce
        # INVALID_FIELD_FOR_INSERT_UPDATE on every create, which surfaced
        # when the AI started generating Lead-conversion and Case TCs.
        name_writable = self.name_createable_lookup(sobject)
        if name_writable is True:
            # Respect AI intent: if the AI supplied a Name, the next verify
            # step probably asserts against that exact value (TC 146 broke
            # on this: AI asserted Name="SQ-205 Regression Acct" but we
            # overwrote with PQA_117_146_... so assertion failed). Only
            # inject the PQA tag if AI didn't choose a Name itself.
            if "Name" not in field_values or not field_values.get("Name"):
                field_values["Name"] = pqa_name
        elif name_writable is False:
            # AI accidentally provided Name on a non-createable-Name object;
            # strip it so SF doesn't reject the whole create.
            field_values.pop("Name", None)
        # else (None / unknown): don't touch Name. Neither inject nor strip.
        # On well-synced metadata this branch never fires; on partial
        # metadata we lose the PQA_ tag for that object but creates succeed.

        # Idempotency key also gains the test_case_id so two TCs creating
        # the same logical Account don't reuse each other's record.
        idem_key = self.idempotency.generate_key(
            self.run_id, step_order, sobject,
            f"{logical_id}{tc_suffix}" if tc_suffix else logical_id,
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
        # Lead convert \u2014 uses the standalone sobjects/LeadConvert endpoint.
        # field_values can override converted_status / opportunity_name /
        # do_not_create_opportunity. convert_to (the step's
        # array of "Account"/"Contact"/"Opportunity") is advisory here:
        # Account + Contact are always produced; Opportunity presence is
        # controlled by do_not_create_opportunity. If the step's
        # convert_to omits "Opportunity", flip the flag.
        fv = field_values or {}
        do_not_create_opp = bool(fv.get("doNotCreateOpportunity",
                                        fv.get("do_not_create_opportunity", False)))
        return self.sf.convert_lead(
            lead_id=record_id,
            converted_status=fv.get("convertedStatus") or fv.get("converted_status"),
            do_not_create_opportunity=do_not_create_opp,
            opportunity_name=fv.get("opportunityName") or fv.get("opportunity_name"),
        )

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
            # Tolerate dotted accessors on the record Id: $foo.Id is
            # semantically the same as $foo (both resolve to the record
            # ID stored by the earlier create step). The AI generator
            # naturally writes "AccountId": "$account.Id" because that
            # reads well in English; we accept it.
            if var_name.endswith(".Id"):
                var_name = var_name[:-3]
            return self.state_vars.get(var_name, value)
        return value

    def _resolve_soql_refs(self, soql):
        """Expand $foo and $foo.Id tokens inside a SOQL template.

        The naive `soql.replace(f"${var_name}", f"'{var_value}'")` that
        shipped previously produced malformed queries when the AI wrote
        the dotted-accessor form in a SOQL string:

            template:  SELECT CloseDate FROM Opportunity WHERE Id = '$opp.Id'
            state:     {"opp": "006Ip000003Kc95IAC"}
            resolved:  ...WHERE Id = ''006Ip000003Kc95IAC'.Id'   <-- MALFORMED_QUERY

        `.replace` substituted `$opp` inside `'$opp.Id'` and left the
        trailing `.Id'` as garbage. TC 136 failed on exactly this bug.

        Fix: regex-match either the quoted `'$foo.Id'` form OR the bare
        `$foo` token (with a word-boundary lookahead so `$opp` doesn't
        swallow `$opportunity`). Replace both with `'<resolved_id>'`.
        """
        import re
        for var_name, var_value in self.state_vars.items():
            # Dotted accessor first (the bug we\u2019re fixing). Tolerates
            # optional surrounding single-quotes in the template so
            # either `'$foo.Id'` or bare `$foo.Id` normalize to the same
            # quoted id literal.
            soql = re.sub(
                rf"'?\${re.escape(var_name)}\.Id'?",
                f"'{var_value}'",
                soql,
            )
            # Then bare $foo, with a negative lookahead to keep $opp
            # from clobbering $opportunity / $oppLine / $foo.Name.
            soql = re.sub(
                rf"'?\${re.escape(var_name)}'?(?![.A-Za-z0-9_])",
                f"'{var_value}'",
                soql,
            )
        return soql
