# PrimeQA Architecture 4 — Tool-Use Test Plan Generation

**Status:** v2 — revised after Claude Code sanity check, ready for implementation
**Supersedes:** the one-shot JSON generation path in `primeqa/intelligence/llm/prompts/test_plan_generation.py`
**Scope:** Test plan generation for Salesforce requirements. Execution, review, and dashboard layers are unchanged.
**Goal:** Make whole classes of bugs (unresolved `$vars`, invalid fields, out-of-order steps) impossible by construction.

**Changes from v1 (post-sanity-check):**
- Section 5: cleanup uses new `generation_created_entities` table, not `run_created_entities`
- Section 9: dropped dual-format persistence — v4 persists `tool_invocations` only, executor branches by architecture column
- Section 10.3: feature flag is enum (`'v3'` / `'v4'`), not bool — read path clarified
- Section 11.2: shadow mode runs bypass tier caps
- Section 15 (new): implementation gotchas — per-turn cost attribution, feedback auto-load, cache invalidation, sync-route prohibition

---

## 1. Design Principles — Locked

1. **LLM owns intent. System owns structure.** The LLM decides *what* to test and *what to verify*. The system decides *how* to execute, *how* to track state, and *how* to validate.

2. **State is handed out, never invented.** State refs are returned by tools, not guessed by the LLM. Referencing an undeclared state_ref is a tool error.

3. **Scenario binds execution.** When a test case declares `actors = ["Case", "Account"]`, the system enforces that declaration.

4. **Strict > convenient.** Duplicate state_refs error. Invalid field names error. Retries happen with narrowed context, not resubmitted plans.

5. **11 tools. No more.** Expansion requires real-customer evidence.

6. **Domain Packs influence, they don't enforce.** Packs remain prompt-layer text.

---

## 2. Tool Vocabulary — 11 Tools

### 2.1 Scenario control (3)

**`start_test_case`** — begins a test case. Must be called before any given/when/then tools. The `actors` list binds execution.

```json
{
  "title": "string, max 200",
  "intent": "string, one sentence",
  "actors": ["string"],
  "relationships": ["string, optional"],
  "conditions": ["string"],
  "expected_outcome": "string, one sentence",
  "coverage_type": "positive_flow | negative_validation | boundary | edge_case | regression"
}
```

Returns: `{"status": "ok", "test_case_id": "tc_1"}`.

**`end_test_case`** — finalizes the current test case. State refs cleared. Errors if zero `when_*` calls.

**`end_test_plan`** — signals no more test cases. System emits the accumulated plan for persistence.

### 2.2 Given — preconditions (1)

**`given_record`** — declares a precondition record. System creates it in Salesforce (namespace-stamped). Returns state_ref.

```json
{
  "object": "string (SF API name)",
  "state_ref": "string (unique within test case, snake_case)",
  "field_values": "object",
  "notes": "string, optional"
}
```

Success returns `{"status": "ok", "state_ref": "acc_1", "salesforce_id": "001xx..."}`.

Field values may reference prior state_refs via `$<state_ref>.<field>`. Resolved at tool-execution time.

### 2.3 When — actions under test (3)

**`when_create_record`** — same schema as `given_record` plus optional `expect_failure: {error_code, message_pattern}`. Semantically distinct from `given_record`: this IS the action being tested.

**`when_update_record`**:
```json
{
  "target_ref": "string",
  "field_values": "object",
  "expect_failure": "object, optional",
  "notes": "string, optional"
}
```

**`when_delete_record`**:
```json
{
  "target_ref": "string",
  "expect_failure": "object, optional",
  "notes": "string, optional"
}
```

### 2.4 Then — assertions (3)

**`then_verify`** — verifies fields on a specific record.
```json
{
  "state_ref": "string",
  "assertions": "object (field → expected value)",
  "notes": "string, optional"
}
```

Special assertion values: `"$NOT_NULL"`, `"$NULL"`, `"$GREATER_THAN:<v>"`, `"$LESS_THAN:<v>"`, plain values for exact match.

**`then_verify_related`** — verifies a related record exists with specific fields.
```json
{
  "parent_ref": "string",
  "related_object": "string",
  "filters": "object",
  "assertions": "object",
  "notes": "string, optional"
}
```

If `start_test_case.relationships` declared, strict enforcement. Else fall back to standard `<Parent>Id` naming inference.

**`then_verify_absence`** — verifies NO related record exists. Same args as `then_verify_related` minus assertions.

### 2.5 Query (1)

**`then_query_and_assert`**:
```json
{
  "object": "string",
  "filters": "object",
  "expected_count": "integer, optional",
  "assertions": "object, optional",
  "notes": "string, optional"
}
```

At least one of `expected_count` or `assertions` required.

### 2.6 Async (1)

**`wait_until`** — polls a condition tool until success or timeout.
```json
{
  "condition_tool": "then_verify | then_verify_related | then_query_and_assert",
  "condition_args": "object",
  "timeout_seconds": "integer, default 10, max 60",
  "poll_interval_seconds": "integer, default 1, min 1",
  "notes": "string, optional"
}
```

Returns `TIMEOUT` on exhaustion with last condition-tool error in details.

---

## 3. State Registry

### 3.1 Scope
State refs live at **test case scope**. Fresh registry on `start_test_case`, cleared on `end_test_case`. Refs do NOT persist across test cases.

### 3.2 Registry contents
Per state_ref: `object`, `salesforce_id`, `created_by_tool`, `created_at_step`, `field_values_set`, `namespace`.

### 3.3 Lifecycle ownership
`GenerationState` is created inside `generation_jobs.process_job` when a job is claimed and torn down in the `finally` block alongside the heartbeat loop. **NOT tied to Flask request context** — A4 runs on the worker service only.

### 3.4 Reference resolution
- `{"AccountId": "$acc_1.Id"}` → resolved to real ID
- `{"Description": "See $acc_1"}` → literal text (no `.field` = no resolution)
- Undeclared ref → `STATE_REF_NOT_FOUND`
- Invalid field on ref → `INVALID_REFERENCED_FIELD`

### 3.5 Standard env references
`$CURRENT_USER.Id`, `$TODAY`, `$NOW` — resolved without declaration.

---

## 4. Scenario Enforcement

When `start_test_case` declares `actors`:
- `given_record(object=X)` — X must be in actors
- `when_create_record(object=X)` — X must be in actors
- `when_update_record(target_ref=R)` — R's object must be in actors
- `when_delete_record(target_ref=R)` — same
- `then_verify_related(related_object=X)` — X must be in actors
- `then_verify_absence(related_object=X)` — X must be in actors
- `then_query_and_assert(object=X)` — X must be in actors

`relationships` is optional. If declared, strict enforcement; if empty, fall back to standard-naming inference.

Mid-test-case scope update: `start_test_case` called again BEFORE `end_test_case` updates current scope. After `end_test_case`, it begins a new test case.

---

## 5. Cleanup — Dual Layer

### 5.1 Namespace stamping
Every record created via `given_record` / `when_create_record` gets a namespace stamp. LLM doesn't see it.

Default strategy (per-object via metadata):
1. `External_Id__c` field available → `pqa_<run_id>_<tc_index>_<random>`
2. Name writable without format validation → prefix `[PQA-<run_id>-<tc_index>] `
3. Description/Notes field available → namespace suffix
4. None of the above → log warning, rely on cleanup queue only

### 5.2 Cleanup queue — new table

**New table `generation_created_entities`** (not extension of `run_created_entities`):

```sql
CREATE TABLE generation_created_entities (
    id SERIAL PRIMARY KEY,
    tenant_id INTEGER NOT NULL,
    generation_batch_id INTEGER REFERENCES generation_batches(id) ON DELETE CASCADE,
    generation_job_id INTEGER REFERENCES generation_jobs(id) ON DELETE SET NULL,
    environment_id INTEGER NOT NULL,
    salesforce_id VARCHAR(20) NOT NULL,
    object_name VARCHAR(80) NOT NULL,
    state_ref VARCHAR(100),
    namespace VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW(),
    cleanup_status VARCHAR(20) NOT NULL DEFAULT 'pending',
    cleanup_attempted_at TIMESTAMP,
    cleanup_error TEXT
);

CREATE INDEX idx_gce_pending ON generation_created_entities(cleanup_status, created_at)
  WHERE cleanup_status = 'pending';
CREATE INDEX idx_gce_batch ON generation_created_entities(generation_batch_id);
```

**Decision rationale:** Reusing `run_created_entities` would require making `run_id` nullable and adding `generation_batch_id`. New table is cleaner: clearer semantics, independent lifecycle, no cascade ambiguity. `CleanupEngine._delete_entity` body is reusable.

### 5.3 Cleanup worker job
New job type in existing worker (`worker.py:566-575`). Third polling loop for `generation_cleanup`.

Flow:
1. Query pending entities where parent batch is complete
2. Delete in reverse-creation order (LIFO)
3. On success: `cleanup_status = 'cleaned'`
4. On failure: `cleanup_status = 'failed'` with error, don't retry immediately

### 5.4 Periodic purge
Scheduler job (daily) queries Salesforce for PQA-namespaced records older than 48 hours and deletes them. Backstop for partial cleanup failures.

### 5.5 Execution-phase records are separate
A4 covers GENERATION. Execution creates its own records under existing `run_created_entities`. That pipeline is untouched.

---

## 6. Retry Protocol

### 6.1 Retryable errors
`INVALID_FIELD`, `OBJECT_NOT_IN_ACTORS`, `STATE_REF_NOT_FOUND`, `DUPLICATE_STATE_REF`, `UNEXPECTED_SUCCESS`, `RELATIONSHIP_NOT_DECLARED`

### 6.2 Non-retryable errors
`SALESFORCE_*_FAILED` (real SF errors), `TIMEOUT`, infrastructure errors → fail the generation job.

### 6.3 Budget
- Per-tool-call: 3 attempts
- Per-plan cumulative: 15 retries
- On exhaustion: abort current test case, LLM proceeds with next or `end_test_plan`

### 6.4 Retry context
LLM receives the failed call, error code, error message, and relevant state (actors, declared refs, recent fields). NOT full prompt history.

```
Your previous tool call failed:
  Tool: when_update_record
  Args: { "target_ref": "case_1", "field_values": { "FakeField": "x" } }

Error: INVALID_FIELD — "FakeField" does not exist on Case.
Available fields on Case that accept updates: Status, Priority, Subject,
IsEscalated, AccountId, ContactId, OwnerId, Description, Origin, Type...

Correct the tool call. Emit exactly one corrected tool call.
Do not repeat earlier steps or change the test case structure.
```

### 6.5 Abort behavior
- Mark test case ABORTED in plan metadata
- Run cleanup for its state
- Prompt LLM: proceed with next or `end_test_plan`
- Aborted test cases NOT persisted as versions

---

## 7. Error Model

### 7.1 Error codes

**Scope / declaration:** `NO_OPEN_TEST_CASE`, `TEST_CASE_ALREADY_OPEN`, `OBJECT_NOT_IN_ACTORS`, `RELATIONSHIP_NOT_DECLARED`
**State refs:** `DUPLICATE_STATE_REF`, `STATE_REF_NOT_FOUND`, `INVALID_REFERENCED_FIELD`
**Metadata:** `INVALID_OBJECT`, `INVALID_FIELD`, `FIELD_NOT_CREATABLE`, `FIELD_NOT_UPDATEABLE`
**Assertion:** `ASSERTION_FAILED`, `UNEXPECTED_SUCCESS`, `UNEXPECTED_FAILURE`
**Execution:** `SALESFORCE_CREATE_FAILED`, `SALESFORCE_UPDATE_FAILED`, `SALESFORCE_DELETE_FAILED`, `SALESFORCE_QUERY_FAILED`
**Control:** `TIMEOUT`, `END_WITHOUT_WHEN`, `RETRY_BUDGET_EXHAUSTED`

### 7.2 Error shape
```json
{
  "status": "error",
  "error_code": "INVALID_FIELD",
  "error_message": "Human-readable for LLM to parse",
  "details": { "tool_name": "...", "failed_field": "...", "available_fields": [...], "state_ref": "..." }
}
```

---

## 8. Prompt Architecture

Four sections in order:

1. **System preamble** — tool-use paradigm, scenario-first thinking, state discipline, Given/When/Then structure. Cached.
2. **Tool descriptions** — full schemas for 11 tools with 1-2 examples each. Cached.
3. **Domain Packs** — existing pack injection, unchanged. Appended after tool descriptions. Uncached (pack content varies per requirement).
4. **Requirement context** — Jira requirement + metadata + coverage expectations. Uncached.

At least 2 worked examples in preamble:
- Simple positive test (given → when_update → then_verify)
- Complex with async (given → when_update → wait_until → then_verify_related)

---

## 9. Persistence — v4 Native

**Revision from v1:** Dropped dual-format persistence. v4 stores `tool_invocations` only. Executor branches by `generation_architecture` column.

### 9.1 New columns on `test_case_versions`
- `tool_invocations` JSONB — full tool history (v4 only)
- `generation_architecture` VARCHAR(10) NOT NULL DEFAULT 'v3'

### 9.2 Tool invocations format
```json
[
  {"step": 1, "tool": "given_record", "args": {...}, "result": {"state_ref": "acc_1", "salesforce_id": "..."}, "retry_count": 0},
  {"step": 2, "tool": "when_update_record", "args": {...}, "result": {"status": "ok"}, "retry_count": 0},
  {"step": 3, "tool": "then_verify", "args": {...}, "result": {"status": "ok"}, "retry_count": 1}
]
```

### 9.3 Execution engine branching
`StepExecutor` checks `version.generation_architecture`:
- `'v3'` → reads `version.steps` (existing behavior)
- `'v4'` → reads `version.tool_invocations` (new reader)

`wait_until`, `then_verify_absence`, `then_query_and_assert` have no legacy equivalent — they execute natively under v4. No attempt to back-translate to v3 format.

**Rationale for dropping dual-format:** Claude Code audit identified lossy derivation for three tools. Cleaner to branch at execution time than fake backward compat.

### 9.4 Scenario metadata
Scenario metadata from `start_test_case` persists on `test_cases`:
- `test_case.intent` — existing/extended
- `test_case.actors` JSONB — new
- `test_case.relationships` JSONB — new, nullable
- `test_case.conditions` JSONB — new
- `test_case.expected_outcome` — reuses existing `story_view.expected_outcome` or new column

---

## 10. Integration Points

### 10.1 Router chain

New task: `test_plan_generation_v4`.

```python
"test_plan_generation_v4": {
    COMPLEXITY_LOW:    [SONNET],
    COMPLEXITY_MEDIUM: [SONNET, OPUS],   # escalation preserved as safety valve
    COMPLEXITY_HIGH:   [SONNET, OPUS],   # conservative — tune after shadow data
},
```

**Revision from v1:** Initial implementation keeps `[SONNET, OPUS]` chains. "Sonnet-only even for HIGH" hypothesis needs shadow data before committing.

### 10.2 Gateway / usage log
`test_plan_generation_v4` flows through `llm_call_loop()` (new — see Section 15.1). Attribution keys in `context_for_log`:
- `domain_packs_applied` — unchanged
- `architecture` — `"v4"`
- `tool_invocation_count`
- `retry_count`
- `aborted_test_cases`
- `turn_index` — per-turn row distinguisher

### 10.3 Feature flag

`tenant_agent_settings.llm_generation_architecture` — VARCHAR, default `'v3'`, values `'v3'` or `'v4'`.

**Read path (enum, NOT bool):**
```python
def _v4_enabled(tenant_id: int, db) -> bool:
    try:
        row = db.query(TenantAgentSettings).filter_by(tenant_id=tenant_id).first()
        return getattr(row, 'llm_generation_architecture', 'v3') == 'v4'
    except Exception:
        return False
```

**Revision from v1:** Existing precedents (`llm_enable_story_enrichment`, `llm_enable_domain_packs`) are BOOL. This is enum. Read-path must check equality, not truthiness.

### 10.4 Domain Packs
Unchanged. Packs load from `salesforce_domain_packs/`, selector picks matches, provider formats as prompt text. In A4, packs appended after tool descriptions section.

### 10.5 Linter
Current 7 checks invoked together from `service.py:312`. Under A4, checks 1-3 (unresolved vars, Id-in-create, readonly fields) are impossible by construction.

**Implementation:** add `skip_checks_for_architecture: str | None = None` param to `GenerationLinter.lint()`. When `"v4"`, checks 1-3 early-return as passes. Checks 4-7 (date, picklist, formula, untraced) still run.

Existing linter tests use v3 fixtures explicitly; new tests verify v4-mode skip behavior.

### 10.6 Task name reference cleanup
`"test_plan_generation"` literal appears in 9 places per audit: `generation.py:199`, `prompts/registry.py:22`, `dashboard.py:149,155`, `eval/runner.py:109`, `eval/scorer.py:165`, `gateway.py:171`, `views.py:1837`.

Extend each to `task in ("test_plan_generation", "test_plan_generation_v4")` where logic applies to both. `gateway.py:171` auto-load-feedback — see Section 15.2.

---

## 11. Rollout & Validation

### 11.1 Phase 1 — Implementation (3-4 weeks)
- `llm_call_loop` multi-turn gateway
- Tool executor + state registry
- 11 tools implemented
- Retry loop
- Cleanup queue + worker job
- Prompt module with tool descriptions
- Persistence (new columns, branching executor)
- Feature flag + router chain
- Test suite

**Exit criteria:** all 11 tools implemented + tested, end-to-end generation works for a known-good ticket, retry loop verified, cleanup queue verified against real Salesforce.

### 11.2 Phase 2 — Shadow mode (3-4 days build + 2 weeks data)

Pilot tenants with `llm_generation_architecture = 'v4'` run v3 AND v4 in parallel. v3 output persisted and used. v4 persisted as shadow.

**Revision from v1 — shadow runs bypass tier caps:**
- `limits.py` daily-spend checker exempts rows where `llm_usage_log.context->>'shadow' = 'true'`
- Shadow restricted to Pro+ tenants for first 7 days even with cap exemption

Rationale: Starter tier ($5/day) can't absorb 2× LLM spend.

**Exit criteria:** 50+ real generations, v3 vs v4 comparison (TC count, coverage, validator issues, confidence, cost, time), no systematic quality regressions.

### 11.3 Phase 3 — Per-tenant flip (ongoing)
Flip pilots one at a time. v3 stays as rollback.

### 11.4 Phase 4 — Default v4 (2-4 weeks after pilot success)
Default flipped. Non-pilot tenants stay on v3 until explicitly migrated.

### 11.5 Phase 5 — v3 sunset (months later)
Remove v3 code when no active v3 tenants.

---

## 12. Known Limitations & v2 Topics

1. Knowledge → validation bridge (Domain Packs enforce-by-validation)
2. `when_call_method` for Apex direct invocation
3. Cross-test-case state dependencies
4. Scratch-org-per-run isolation
5. Multi-user test context
6. Bulk assertions beyond count (aggregates, joins)

---

## 13. Spec Decisions

1. Namespace stamping: metadata-driven per-object (External_Id__c → Name prefix → Description suffix → warn)
2. Namespace purge window: 48 hours
3. Retry budgets: 3 per tool, 15 per plan
4. Router chain HIGH: `[SONNET, OPUS]` initially, tune after data
5. Shadow mode: 50+ generations minimum
6. Persistence: v4-only `tool_invocations`, executor branches by architecture column
7. Cleanup table: new `generation_created_entities`
8. Feature flag: enum (`'v3'` / `'v4'`), not bool

---

## 14. Non-Decisions (Implementation-level)

- Python module structure for tool executor
- State registry data structure (dict vs class vs Redis)
- JSON schema validation library
- Tool schema format (native Anthropic `tool_use`, already in use)
- Error-code-to-retry-trigger mapping details
- Cleanup worker polling interval

---

## 15. Implementation Gotchas

### 15.1 Per-turn cost attribution in `llm_usage_log`

Today: one generation = one row with `generation_batch_id`. Under v4: N tool-call turns = N rows.

**Required:** every per-turn row must carry the same `generation_batch_id`. The gateway's batch-linking path (related to commit `06b582b` fix) fires on every turn, not just the final one.

**Cost dashboard impact:** GROUP BY `generation_batch_id` queries continue to work. Per-call counts go up N× for v4 traffic. Dashboard may want a `turn_index` dimension.

### 15.2 Gateway auto-load-feedback decision

`gateway.py:171` checks `task == "test_plan_generation"` for auto-loading feedback rules.

**Decision: Option 1 (inherit) for v1.** Extend condition to `task in ("test_plan_generation", "test_plan_generation_v4")`. Feedback rules apply to both architectures. If shadow data shows redundancy under v4, revisit.

### 15.3 Anthropic cache invalidation on cutover

When v4 ships, cached prefix content changes. Every cache entry becomes stale. First v4 call per tenant pays 1.25× cache-write cost instead of 0.1× cache-read cost. One-time spike per tenant; document in rollout.

### 15.4 Sync-route compatibility (prohibited)

**A4 generation runs only on the worker service.**

Multi-turn LLM + N Salesforce calls = 30-90 seconds. Gunicorn web default timeout is 30s. The `service.generate_test_plan` v4 branch MUST enqueue a `generation_job` and return 202 Accepted. No inline execution from a web route.

### 15.5 Eval harness branching

`intelligence/llm/eval/runner.py` constructs prompts independently. When v4 ships, eval harness needs its own branch. Defer to Phase 2 (shadow mode). Eval regression tests run against v3 until then.

---

## End of Spec v2

Ready for handoff to implementation prompt authoring.
