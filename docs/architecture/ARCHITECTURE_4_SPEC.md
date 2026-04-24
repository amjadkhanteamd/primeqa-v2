# PrimeQA Architecture 4 — Tool-Use Test Plan Generation

**Status:** DRAFT — pending review by Amjad, then Claude Code sanity check  
**Supersedes:** the one-shot JSON generation path in `primeqa/intelligence/llm/prompts/test_plan_generation.py`  
**Scope:** Test plan generation for Salesforce requirements. Execution, review, and dashboard layers are unchanged.  
**Goal:** Make whole classes of bugs (unresolved `$vars`, invalid fields, out-of-order steps) impossible by construction.

---

## 1. Design Principles — Locked

These are the constitution. Every downstream decision answers to them.

1. **LLM owns intent. System owns structure.**  
   The LLM decides *what* to test and *what to verify*. The system decides *how* to execute, *how* to track state, and *how* to validate. If these ever blur, the blur side loses.

2. **State is handed out, never invented.**  
   State refs (`case_1`, `acc_1`, etc.) are returned by tools, not guessed by the LLM. Referencing an undeclared state_ref is a tool error, not a runtime failure.

3. **Scenario binds execution.**  
   When a test case declares `actors = ["Case", "Account"]`, the system enforces that declaration. Attempts to operate on undeclared objects are tool errors.

4. **Strict > convenient.**  
   Duplicate state_refs error. Invalid field names error. Retries happen with narrowed context, not resubmitted plans. No silent recovery.

5. **11 tools. No more.**  
   The tool vocabulary is locked. Expansion requires real-customer evidence, not speculative capability.

6. **Domain Packs influence, they don't enforce.**  
   Packs remain prompt-layer text. They shape LLM behavior. They do not gate tool validation. Enforcement-via-knowledge is a v2 topic.

---

## 2. Tool Vocabulary — 11 Tools, Locked

### 2.1 Scenario control (3)

**`start_test_case`**

Begins a test case. Must be called before any given/when/then tools. The declared `actors` list binds execution — subsequent tool calls referencing objects outside this list are errors.

```json
{
  "name": "start_test_case",
  "input_schema": {
    "title": "string, max 200",
    "intent": "string, one sentence: what is being tested",
    "actors": ["string"],  // Salesforce object API names the test touches
    "relationships": ["string"],  // optional, e.g. "Escalation__c.CaseId -> Case.Id"
    "conditions": ["string"],  // preconditions in natural language
    "expected_outcome": "string, one sentence: what should be true at the end",
    "coverage_type": "positive_flow | negative_validation | boundary | edge_case | regression"
  }
}
```

Returns: `{"status": "ok", "test_case_id": "tc_1"}`. The system assigns `tc_1` / `tc_2` / etc. sequentially within a plan.

---

**`end_test_case`**

Finalizes the current test case. State refs declared during this test case are dropped from scope. The next `start_test_case` begins a fresh scope.

```json
{
  "name": "end_test_case",
  "input_schema": {}
}
```

Returns: `{"status": "ok", "test_case_id": "tc_1", "steps_recorded": 8}`.

Error if called without an open test case, or if the test case has zero `when_*` tool calls (a test case must exercise something).

---

**`end_test_plan`**

Signals no more test cases. The system emits the accumulated plan for persistence. The LLM's job is done.

```json
{
  "name": "end_test_plan",
  "input_schema": {}
}
```

Returns: `{"status": "ok", "test_case_count": 5, "plan_id": "pending"}`. The system then persists the plan via existing test-case-version pipeline and returns the persisted ids out-of-band (to the caller of the generation pipeline, not back to the LLM).

---

### 2.2 Given — preconditions (1)

**`given_record`**

Declares a record that must exist before the action under test. The system creates it as a real Salesforce record (respecting the namespace for cleanup). Returns a state_ref the LLM can use.

```json
{
  "name": "given_record",
  "input_schema": {
    "object": "string, Salesforce API name",
    "state_ref": "string, must be unique within test case, snake_case",
    "field_values": "object, field API names as keys",
    "notes": "string, optional natural-language explanation of why this record is needed"
  }
}
```

Returns on success: `{"status": "ok", "state_ref": "acc_1", "salesforce_id": "001xx00000AAAAA"}`.

Returns on failure:
```json
{
  "status": "error",
  "error_code": "OBJECT_NOT_IN_ACTORS" | "INVALID_FIELD" | "DUPLICATE_STATE_REF" | "SALESFORCE_CREATE_FAILED",
  "error_message": "Account is not in declared actors [Case, Contact]. Add it via start_test_case or use an in-scope object.",
  "details": {...}
}
```

Field references: values may reference earlier state_refs via `$<state_ref>.<field>` syntax. E.g. `{"AccountId": "$acc_1.Id"}`. The system resolves these at execution time.

---

### 2.3 When — actions under test (3)

**`when_create_record`**

The action being tested is record creation. This differs from `given_record` semantically — `given_record` is "setup," `when_create_record` is "the thing we're verifying Salesforce handles correctly."

Same schema as `given_record` plus:
```json
{
  "expect_failure": "object, optional — { error_code: string, message_pattern: string }"
}
```

If `expect_failure` is provided, the tool validates that the create attempt failed with a matching error. If the create succeeds instead, the tool returns `UNEXPECTED_SUCCESS`.

---

**`when_update_record`**

```json
{
  "name": "when_update_record",
  "input_schema": {
    "target_ref": "string, state_ref of a previously-declared record",
    "field_values": "object",
    "expect_failure": "object, optional",
    "notes": "string, optional"
  }
}
```

Target must be in scope (declared via `given_record` or `when_create_record` in the current test case). Referencing an out-of-scope ref returns `STATE_REF_NOT_FOUND`.

---

**`when_delete_record`**

```json
{
  "name": "when_delete_record",
  "input_schema": {
    "target_ref": "string",
    "expect_failure": "object, optional",
    "notes": "string, optional"
  }
}
```

Same validation as `when_update_record`.

---

### 2.4 Then — assertions (3)

**`then_verify`**

Verifies fields on a specific record.

```json
{
  "name": "then_verify",
  "input_schema": {
    "state_ref": "string",
    "assertions": "object, field API name → expected value",
    "notes": "string, optional"
  }
}
```

The system re-queries the record (fresh fetch, not cached from the create/update response) and asserts each field matches. Mismatches return `ASSERTION_FAILED` with `comparison_details` (same structure as current `verify_comparison_details`).

Special values in assertions:
- `"$NOT_NULL"` — field must be populated
- `"$NULL"` — field must be empty
- `"$GREATER_THAN:<value>"` / `"$LESS_THAN:<value>"` — comparison
- regular values — exact match

---

**`then_verify_related`**

Verifies that a related record exists in response to a prior action (Flow output, trigger-created record, etc.).

```json
{
  "name": "then_verify_related",
  "input_schema": {
    "parent_ref": "string, state_ref of the parent",
    "related_object": "string, object API name",
    "filters": "object, field → value (in addition to the parent relationship)",
    "assertions": "object, fields the related record must satisfy",
    "notes": "string, optional"
  }
}
```

The system resolves the parent-child relationship from the `relationships` declared in `start_test_case` if present, otherwise attempts to infer from standard Salesforce relationship naming (`<Parent>Id` on the child). If relationship can't be resolved, returns `RELATIONSHIP_NOT_DECLARED` — the LLM should call `start_test_case` again with explicit relationships, or restructure the test.

---

**`then_verify_absence`**

Verifies that NO related record exists. Used for "the Flow should NOT fire under these conditions" tests.

```json
{
  "name": "then_verify_absence",
  "input_schema": {
    "parent_ref": "string",
    "related_object": "string",
    "filters": "object",
    "notes": "string, optional"
  }
}
```

Returns success if the query returns zero matches, error otherwise.

---

### 2.5 Query (1)

**`then_query_and_assert`**

Arbitrary record-set assertions. For when you need to verify count, or verify properties of records the test didn't create directly.

```json
{
  "name": "then_query_and_assert",
  "input_schema": {
    "object": "string",
    "filters": "object",
    "expected_count": "integer, optional — if provided, asserts exact count",
    "assertions": "object, optional — if provided, asserts all matching records satisfy these fields",
    "notes": "string, optional"
  }
}
```

At least one of `expected_count` or `assertions` must be provided. Both can be provided.

---

### 2.6 Async (1)

**`wait_until`**

Polls a condition tool until it succeeds or times out. Replaces fixed sleeps for async-triggered assertions (Flow creating a record, batch job completing, etc.).

```json
{
  "name": "wait_until",
  "input_schema": {
    "condition_tool": "string, one of: then_verify | then_verify_related | then_query_and_assert",
    "condition_args": "object, arguments to pass to the condition tool",
    "timeout_seconds": "integer, default 10, max 60",
    "poll_interval_seconds": "integer, default 1, min 1",
    "notes": "string, optional"
  }
}
```

Returns success when the condition tool returns success. Returns `TIMEOUT` if the condition never satisfies within `timeout_seconds`. The error message includes the last condition-tool error to aid debugging.

`wait_until` is a meta-tool: it doesn't validate Salesforce state directly, it invokes another tool repeatedly. The invoked tool's usual validations apply.

---

## 3. State Registry

### 3.1 Scope

State refs live at **test case scope**. When `start_test_case` is called, a fresh state registry is created. When `end_test_case` is called, the registry is cleared. State refs do NOT persist across test cases within a plan.

This is intentional. It prevents test cases from coupling via shared state. Each test case is independent.

### 3.2 Registry contents

For each state_ref:
```
{
  "state_ref": "acc_1",
  "object": "Account",
  "salesforce_id": "001xx00000AAAAA",
  "created_by_tool": "given_record",
  "created_at_step": 1,
  "field_values_set": {"Name": "Test Account", ...},
  "namespace": "pqa_r847_tc1_ab3f"
}
```

The registry is in-memory per generation call, not persisted. Once `end_test_case` is called, the tools that created real Salesforce records add entries to the cleanup queue (Section 5).

### 3.3 Reference resolution

State ref references in `field_values` use `$<state_ref>.<field>` syntax. Resolution happens at tool-execution time, not at LLM-emission time.

- `{"AccountId": "$acc_1.Id"}` → resolved to `{"AccountId": "001xx00000AAAAA"}`
- `{"Name": "Child of $acc_1.Name"}` → resolved to `{"Name": "Child of Test Account"}`
- `{"Description": "This references $acc_1"}` → resolved to literal "This references $acc_1" (no `.field` suffix = no resolution, treated as literal text)

If a `$ref.field` references a state_ref not in the current scope, `given_record` / `when_*_record` returns `STATE_REF_NOT_FOUND`. If the field doesn't exist on the referenced record, returns `INVALID_REFERENCED_FIELD`.

### 3.4 Standard env references

Some references resolve to system values, not state:
- `$CURRENT_USER.Id` → the executing user's Salesforce Id
- `$TODAY` → today's date in ISO format
- `$NOW` → current datetime in ISO format

These work everywhere `$<state_ref>.<field>` works and don't require declaration.

---

## 4. Scenario Enforcement

When `start_test_case` declares `actors`, subsequent tool calls are validated against that list:

- `given_record(object=X)` — X must be in actors
- `when_create_record(object=X)` — X must be in actors
- `when_update_record(target_ref=R)` — R's object (looked up in registry) must be in actors
- `when_delete_record(target_ref=R)` — same
- `then_verify_related(related_object=X)` — X must be in actors
- `then_verify_absence(related_object=X)` — X must be in actors
- `then_query_and_assert(object=X)` — X must be in actors

When `relationships` is declared, `then_verify_related` / `then_verify_absence` validate the parent-child relationship is declared. If relationships is empty, these tools fall back to Salesforce standard-naming inference (`<Parent>Id` field on child). If that inference fails, they error and the LLM must either restructure or update `actors`/`relationships`.

If the LLM needs to broaden scope mid-test-case, it can call `start_test_case` again — this is treated as updating the current scope, not starting a new test case, PROVIDED `end_test_case` hasn't been called. Post-`end_test_case`, `start_test_case` begins a new test case.

---

## 5. Cleanup — Dual Layer

### 5.1 Namespace

Every record created via `given_record` or `when_create_record` gets a namespace stamp. The system applies this automatically — the LLM doesn't see it.

**Implementation:** the system prefixes or appends a namespace marker to a standard identifier field. Default strategy:

1. If the object has an `External_Id__c` field (convention), set it to `pqa_<run_id>_<tc_index>_<random>`.
2. Else if the object's Name field is writable and not subject to validation rules about format, prefix the Name with `[PQA-<run_id>-<tc_index>] `.
3. Else add the namespace as a Description or Notes field suffix if available.
4. If no stamping surface exists, log a warning and skip stamping for that record — this record relies on cleanup-queue only.

**Decision to flag:** the "which field to stamp" decision is implementation detail. The spec requires that SOME stamping happens per record. Claude Code will decide the exact per-object strategy during implementation, ideally via metadata inspection.

### 5.2 Cleanup queue

The state registry tracks everything created. When `end_test_case` fires (success or failure), the system adds all created records to a cleanup queue in LIFO order (newest first — handles dependencies naturally, since children are typically created after parents).

Cleanup runs post-generation, after the full plan is persisted. It runs async in a worker job, not blocking the generation response. The cleanup job:

1. Reads cleanup queue for this generation run
2. Attempts to delete records in order
3. On delete failure (cascading constraint, managed-package block, etc.), logs the failure and continues
4. Records surviving cleanup remain in Salesforce but are namespace-stamped — a separate periodic sweep can purge them later

### 5.3 Periodic namespace purge

A scheduled job (daily) queries each relevant object for records with PQA namespace stamps older than 48 hours and deletes them. This is the backstop for the cleanup-queue's partial-failure mode.

**Decision to flag:** the 48-hour window is arbitrary. Claude Code can tune during implementation.

### 5.4 Execution-phase records are separate

Architecture 4 covers test plan GENERATION. When the plan is later EXECUTED (a separate pipeline stage), it creates its own records with its own namespace. Those records follow the existing execution-phase cleanup rules, which are out of scope for this spec.

---

## 6. Retry Protocol

### 6.1 When retries happen

Retries happen when a tool call returns an error that the LLM could plausibly fix. Specifically:

- `INVALID_FIELD` — LLM referenced a field that doesn't exist → retry with corrected field
- `OBJECT_NOT_IN_ACTORS` — LLM called a tool on an undeclared object → retry after updating actors or using a different object
- `STATE_REF_NOT_FOUND` — LLM referenced a state_ref that wasn't created → retry after creating the record or using a different ref
- `DUPLICATE_STATE_REF` — LLM chose a name that's already taken → retry with a new name
- `UNEXPECTED_SUCCESS` — an `expect_failure` test succeeded unexpectedly → retry may adjust the test or remove `expect_failure`

Retries do NOT happen for:
- `SALESFORCE_CREATE_FAILED` (real-world Salesforce error) — the test plan proceeds as generated; execution phase will surface this as a real test failure
- `TIMEOUT` from wait_until — likely a real async-timing issue, not a plan-construction issue
- Infrastructure errors (DB unavailable, etc.) — these propagate up and fail the generation job

### 6.2 Retry budget

Per-tool-call retry budget: **3 attempts**.

Across the whole plan, cumulative retry budget: **15 retries**. If this is exceeded, the current test case is aborted (marked failed in the plan's metadata, no persistence for it) and the LLM is told "test case aborted due to retry budget" and may proceed to the next test case.

### 6.3 Retry context

On retry, the LLM receives ONLY:

- The failed tool call (verbatim)
- The error code and error_message
- Any relevant state: current test case's actors, currently-declared state_refs, recently-declared field_values

The LLM does NOT receive the full prompt history on retry. It receives a targeted correction request:

```
Your previous tool call failed:

  Tool: when_update_record
  Args: { "target_ref": "case_1", "field_values": { "FakeField": "x" } }
  
Error: INVALID_FIELD — "FakeField" does not exist on Case. 
Available fields on Case that accept updates include: Status, Priority, Subject, IsEscalated, AccountId, ContactId, OwnerId, Description...

Correct the tool call. Emit exactly one corrected tool call. Do not repeat earlier steps or change the test case structure.
```

The LLM responds with one corrected tool call. If that also errors, the retry counter increments and the process repeats.

### 6.4 Abort behavior

When retry budget is exhausted, the system:

1. Marks the current test case as ABORTED in the plan
2. Runs cleanup for any state in that test case
3. Prompts the LLM: "Test case TC-N aborted. Proceed with the next test case or call end_test_plan."
4. LLM either calls `start_test_case` to begin a new one, or `end_test_plan` to finish

Aborted test cases are NOT persisted as test case versions. They show up in the generation job's metadata as "attempted but failed to construct" with the final error that caused the abort.

---

## 7. Error Model

### 7.1 Error codes (complete list)

**Scope / declaration:**
- `NO_OPEN_TEST_CASE` — tool called before start_test_case
- `TEST_CASE_ALREADY_OPEN` — start_test_case called without end_test_case
- `OBJECT_NOT_IN_ACTORS` — operation on undeclared object
- `RELATIONSHIP_NOT_DECLARED` — related-record tool can't infer relationship

**State refs:**
- `DUPLICATE_STATE_REF` — reused a name already in scope
- `STATE_REF_NOT_FOUND` — referenced a name not in scope
- `INVALID_REFERENCED_FIELD` — `$ref.FakeField` where FakeField doesn't exist

**Metadata:**
- `INVALID_OBJECT` — object doesn't exist in org metadata
- `INVALID_FIELD` — field doesn't exist on object
- `FIELD_NOT_CREATABLE` — field is read-only, can't be in create payload
- `FIELD_NOT_UPDATEABLE` — field is read-only, can't be in update payload

**Assertion:**
- `ASSERTION_FAILED` — expected value didn't match actual
- `UNEXPECTED_SUCCESS` — expect_failure was declared but action succeeded
- `UNEXPECTED_FAILURE` — expect_failure was NOT declared but action failed (generation-time error, not execution-time)

**Execution:**
- `SALESFORCE_CREATE_FAILED` — real error from Salesforce during given_record / when_create_record
- `SALESFORCE_UPDATE_FAILED` — real error from Salesforce during when_update_record
- `SALESFORCE_DELETE_FAILED` — real error from Salesforce during when_delete_record
- `SALESFORCE_QUERY_FAILED` — real error from Salesforce during verify / query tools

**Control:**
- `TIMEOUT` — wait_until exhausted timeout
- `END_WITHOUT_WHEN` — end_test_case called without any when_* tools
- `RETRY_BUDGET_EXHAUSTED` — per-tool or per-plan retries depleted

### 7.2 Error shape

Every tool error returns:

```json
{
  "status": "error",
  "error_code": "INVALID_FIELD",
  "error_message": "Human-readable message for LLM to parse",
  "details": {
    "tool_name": "when_update_record",
    "failed_field": "FakeField",
    "available_fields": [...],  // where applicable
    "state_ref": "case_1"  // where applicable
  }
}
```

Error messages are authored to be maximally useful to the LLM retry layer. They name the specific problem and often suggest a resolution.

---

## 8. Prompt Architecture

The prompt sent to the LLM has four sections:

1. **System preamble** — unchanged from current architecture in spirit, but rewritten for tool-use paradigm. Explains the LLM's role: generate Salesforce test cases by calling tools. Emphasizes: scenario-first thinking, state discipline, Given/When/Then structure.
2. **Tool descriptions** — full JSON schemas for the 11 tools, with 1-2 example usages per tool.
3. **Domain Packs** — existing pack injection, unchanged. Packs are text that describes domain patterns and pitfalls. Positioned after tool descriptions, before examples.
4. **Requirement context** — the Jira requirement, metadata context, coverage expectations. Unchanged in content from current architecture.

**Examples in the preamble:** at least 2 full worked examples of test-case authoring via tools, covering:
- A simple positive test (given → when_update → then_verify)
- A complex test with async (given → when_update → wait_until → then_verify_related)

These examples are the LLM's clearest guide to desired output shape. They should be high-quality, hand-authored, and cover the patterns the LLM will most commonly hit.

---

## 9. Persistence — What Gets Stored

The output of `end_test_plan` is the same shape as today's `generation_batches` + `test_case_versions` records, but derived from tool calls rather than one-shot JSON.

**Mapping:**

Current `test_case_version.steps` becomes a JSONB array of tool invocations:

```json
[
  {"step": 1, "tool": "given_record", "args": {...}, "result": {"state_ref": "acc_1", "salesforce_id": "..."}},
  {"step": 2, "tool": "when_update_record", "args": {...}, "result": {"status": "ok"}},
  {"step": 3, "tool": "then_verify", "args": {...}, "result": {"status": "ok"}},
  ...
]
```

This preserves full execution fidelity. The legacy "steps" array format (action/target/field_values/expected_result) is also derived from this tool history for backward compatibility with the execution engine — the execution engine can continue consuming the legacy format while new generations carry both formats on the version record.

**New columns on `test_case_versions`:**
- `tool_invocations` JSONB — the full tool history
- `generation_architecture` VARCHAR — `"v3_oneshot"` for legacy, `"v4_tooluse"` for A4

Existing `test_case_versions` rows get `generation_architecture = "v3_oneshot"` via migration default. New rows carry `"v4_tooluse"`. This makes it trivial to query "which architecture produced this test case?"

**Scenario metadata** from `start_test_case` is also persisted:
- `test_case.intent` — already have similar
- `test_case.actors` JSONB
- `test_case.relationships` JSONB  
- `test_case.conditions` JSONB
- `test_case.expected_outcome` — similar to story_view's `expected_outcome`

These reuse/extend the story_view columns shipped previously.

---

## 10. Integration Points

### 10.1 Router chain

Architecture 4 has its own task name: `test_plan_generation_v4`. Separate from the existing `test_plan_generation`. They route independently:

```python
"test_plan_generation_v4": {
    COMPLEXITY_LOW:    [SONNET],
    COMPLEXITY_MEDIUM: [SONNET],  # no escalation — tool-use architecture should reduce need
    COMPLEXITY_HIGH:   [SONNET],  # tentative — we expect Sonnet+packs to cover HIGH with tools
},
```

**Decision to flag:** the HIGH tier on `test_plan_generation_v4` being Sonnet-only is aggressive. The TA's principle "LLM owns intent, System owns structure" predicts that intent-level tasks don't need Opus. But we should confirm via eval before locking. Initial implementation: leave HIGH on `[SONNET, OPUS]` with escalation allowed; tune down based on real data.

### 10.2 Gateway / usage log

`test_plan_generation_v4` flows through the same `llm_call` / `usage.record` path. Attribution keys in `context_for_log`:
- `domain_packs_applied` — unchanged
- `architecture` — `"v4"` 
- `tool_invocation_count` — total tool calls in the session (useful for cost analysis)
- `retry_count` — total retries consumed
- `aborted_test_cases` — count of test cases that hit retry budget

### 10.3 Feature flag

`tenant_agent_settings.llm_generation_architecture` — enum, default `"v3"`, can be set to `"v4"` per-tenant. When `"v4"`, `test_plan_generation` routes to the A4 pipeline; when `"v3"`, existing behavior.

This lets us roll out Architecture 4 per-tenant, compare side-by-side, and roll back without code deploys if needed.

### 10.4 Domain Packs

Unchanged. Packs load from `salesforce_domain_packs/`, selector picks matches, provider formats as prompt text. In A4, the packs text is appended after the tool descriptions section of the prompt (Section 8 item 3).

### 10.5 Linter

The current GenerationLinter has 7 checks. Under A4, several become unnecessary (unresolved `$vars`, formula fields in payload, Id in create — these can't happen by construction). Others remain useful as belt-and-braces (date format, picklist values).

**Plan:** keep the linter, mark the obsolete-under-A4 checks as "skipped if generation_architecture=v4". The checks that remain useful still run. Later, the obsolete checks can be removed entirely once v4 is the default everywhere.

---

## 11. Rollout & Validation

### 11.1 Phase 1: Implementation

Build the Architecture 4 pipeline end-to-end. Tool executor, state registry, retry loop, cleanup queue, prompt module. No tenant flag flipped yet. Tests.

**Exit criteria:**
- All 11 tools have schema + executor + tests
- Full generation pipeline works end-to-end for a known-good Jira ticket
- Retry loop verified with synthetic errors
- Cleanup queue verified with real Salesforce records

### 11.2 Phase 2: Shadow mode

For pilot tenants, every generation runs v3 AND v4 in parallel. v3's output is persisted and used. v4's output is persisted as a "shadow" record, not shown to users.

**Exit criteria:**
- Run 50+ real generations in shadow mode
- Compare v3 vs v4 on: test case count, coverage breadth, validator issues, confidence scores, LLM cost, wall-clock time
- Ensure v4 doesn't have systematic quality regressions

### 11.3 Phase 3: Per-tenant flip

Flip pilot tenants to `llm_generation_architecture = "v4"`. Monitor for issues. Keep v3 ready as rollback.

### 11.4 Phase 4: Default v4

After 2-4 weeks of pilot-tenant v4 usage with no systematic issues, flip the default. Existing non-pilot tenants stay on v3 until explicitly migrated.

### 11.5 Phase 5: v3 sunset

When no active tenant is on v3 and no recent generations use v3, remove v3 code. This is a months-later operation, not part of initial shipping.

---

## 12. Known Limitations & v2 Topics

Flagged explicitly so they don't get forgotten:

1. **Knowledge → validation bridge.** Domain Packs are prompt-layer only in v1. Enforcement via knowledge (making pack rules machine-checked) is a v2 topic.
2. **`when_call_method` / Apex direct invocation.** Cut from v1. Add only if real customer demand surfaces.
3. **Cross-test-case dependencies.** State is scoped to a single test case. Tests that depend on other tests' state are not supported. If this surfaces as a real need, a follow-up design is required.
4. **Scratch org isolation.** v1 runs against the connected Salesforce env with namespace-based cleanup. Scratch-org-per-run is a v2 topic.
5. **Multi-user tests.** Tests that require multiple Salesforce user contexts (approval flows, etc.) are not first-class in v1. Workaround: `given_record` can set `OwnerId` but running actions AS different users isn't supported.
6. **Bulk assertions.** `then_query_and_assert` handles "verify N records exist." More complex bulk patterns (aggregate sums, cross-object joins) may need a richer tool in v2.

---

## 13. Spec Decisions Requiring Your Override

Items I decided and flag explicitly in case you want to change them:

1. **Namespace stamping strategy** (Section 5.1) — I made "External_Id__c if available, else Name prefix, else Description suffix" the default. You may want a different strategy.

2. **Namespace purge window** (Section 5.3) — 48 hours is arbitrary. Could be 24h or 7d.

3. **Retry budgets** (Section 6.2) — 3 per tool, 15 per plan. These are guesses. Real data may show 2/10 is sufficient or 5/25 is needed.

4. **HIGH tier on v4 router** (Section 10.1) — I proposed Sonnet-only for HIGH under v4 but flagged as tentative. Safe default: keep `[SONNET, OPUS]` with escalation.

5. **Shadow mode duration** (Section 11.2) — 50+ generations is arbitrary. Could be more or fewer based on your risk appetite.

6. **Persistence format** (Section 9) — I proposed storing both `tool_invocations` (new) AND the legacy `steps` format (derived) on new versions. Alternative: store only tool_invocations and have execution engine consume the new format directly. My choice preserves execution-engine compatibility during rollout.

---

## 14. Non-Decisions (Deferred to Implementation)

Items Claude Code will decide during implementation:

- Exact Python module structure for tool executor
- State registry data structure (dict, class, Redis-backed, etc.)
- JSON schema validation library (pydantic vs jsonschema vs custom)
- How tool schemas are described to Anthropic API (native tool_use vs prompt-based)
- Error-code-to-retry-trigger mapping (which errors trigger retry vs fail fast — implementation detail within the framework above)
- Cleanup worker scheduling (existing worker.py vs new job type)
- Specific SQL migrations required for `test_case_versions.tool_invocations` etc.

These are deliberately left open. They're decisions that depend on codebase context Claude Code has and I don't.

---

## End of Spec

Review criteria before handoff to Claude Code:
- Architecture principles (Section 1) still feel right
- Tool vocabulary (Section 2) is complete and minimal
- No "we forgot to cover..." gaps
- Decisions flagged in Section 13 are acceptable or overridden
