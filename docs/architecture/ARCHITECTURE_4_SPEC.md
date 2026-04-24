# PrimeQA Architecture 4 — Tool-Use Test Plan Generation

**Status:** v4 — post-Claude-Code-v2-audit, ready for implementation
**Supersedes:** the one-shot JSON generation path in `primeqa/intelligence/llm/prompts/test_plan_generation.py`
**Scope:** Test plan generation for Salesforce requirements. Execution, review, and dashboard layers are unchanged.
**Goal:** Make whole classes of bugs (unresolved `$vars`, invalid fields, invalid relationships, out-of-order steps) impossible by construction.

**Changes from v3 (Claude Code's v2-audit gap-closure pass):**
- §5: naming convention locked — `sf_record_id` (not `salesforce_id`), consistent with existing `run_created_entities`
- §9: explicit list of non-executor consumers of `version.steps` + v4-era behavior per consumer
- §10.3: clarification that `_v4_enabled` follows `_story_enrichment_enabled` (caller-passes-db), not `_domain_packs_enabled` (detached session)
- §11.2: shadow rows REMAIN VISIBLE in cost dashboards — only `limits.py:check()` exempts them
- §15.1: explicit per-turn `usage_log_ids` back-attach pattern
- §15.2: feedback-rules signal handling under v4 (tag-don't-purge for grammar signals)
- §15.3: cache invalidation fires on every prompt revision, not just cutover
- §15.4: `/api/test-cases/generate` added to route audit — sync path flipped to async-enqueue under v4

**Unchanged from v3:**
- 11 tools (locked)
- Principles (§1)
- TA feedback revisions: field/relationship enforcement (§4), query mode (§2.5), wait_until description (§2.6), given_record forbids expect_failure (§2.2), state isolation on retry (§6.4), duration_ms (§9.2)
- v2 revisions: cleanup table, persistence approach, feature flag enum, shadow cap bypass

---

## 1. Design Principles — Locked

1. **LLM owns intent. System owns structure.**
2. **State is handed out, never invented.**
3. **Scenario binds execution.**
4. **Strict > convenient.**
5. **11 tools. No more.**
6. **Domain Packs influence, they don't enforce.**

---

## 2. Tool Vocabulary — 11 Tools

### 2.1 Scenario control (3)

**`start_test_case`**

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

**`end_test_case`** — finalizes current test case. Errors if zero `when_*` calls.

**`end_test_plan`** — signals no more test cases.

### 2.2 Given — preconditions (1)

**`given_record`**:
```json
{
  "object": "string (SF API name)",
  "state_ref": "string (unique within test case, snake_case)",
  "field_values": "object",
  "notes": "string, optional"
}
```

**CRITICAL: `given_record` MUST NOT accept `expect_failure`.** Setup is assumed to succeed; failure is a metadata/environmental problem, not a test outcome. Schema validator rejects `expect_failure` on this tool.

Field values may reference prior state_refs via `$<state_ref>.<field>`. Resolved at execution time.

### 2.3 When — actions under test (3)

**`when_create_record`** — same as `given_record` plus optional `expect_failure: {error_code, message_pattern}`.

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

**`then_verify`**:
```json
{
  "state_ref": "string",
  "assertions": "object (field → expected value)",
  "notes": "string, optional"
}
```

Special assertion values: `"$NOT_NULL"`, `"$NULL"`, `"$GREATER_THAN:<v>"`, `"$LESS_THAN:<v>"`, plain values for exact match.

**`then_verify_related`**:
```json
{
  "parent_ref": "string",
  "related_object": "string",
  "filters": "object",
  "assertions": "object",
  "notes": "string, optional"
}
```

Relationship validated via declared `relationships` or standard-naming inference (see §4).

**`then_verify_absence`** — same args as `then_verify_related` minus assertions.

### 2.5 Query (1)

**`then_query_and_assert`**:
```json
{
  "object": "string",
  "filters": "object",
  "mode": "all | any | none",
  "expected_count": "integer, optional",
  "assertions": "object, optional",
  "notes": "string, optional"
}
```

`mode` (required, default `"all"`):
- `"all"` — every matching record must satisfy `assertions`
- `"any"` — at least one matching record must satisfy `assertions`
- `"none"` — no matching record may satisfy `assertions`

`expected_count` is orthogonal to `mode`. At least one of `expected_count` or `assertions` required.

### 2.6 Async (1)

**`wait_until`**:
```json
{
  "condition_tool": "then_verify | then_verify_related | then_query_and_assert",
  "condition_args": "object",
  "description": "string (required) — human-readable description of what we're waiting for",
  "timeout_seconds": "integer, default 10, max 60",
  "poll_interval_seconds": "integer, default 1, min 1",
  "notes": "string, optional"
}
```

`description` appears in timeout errors and logs.

---

## 3. State Registry

### 3.1 Scope
Test case scope. Fresh on `start_test_case`, cleared on `end_test_case`.

### 3.2 Contents
Per state_ref: `object`, `sf_record_id`, `created_by_tool`, `created_at_step`, `field_values_set`, `namespace`.

### 3.3 Lifecycle
`GenerationState` created inside `generation_jobs.process_job` on job claim. Torn down in `finally` alongside heartbeat. NOT tied to Flask request context — A4 runs worker-only.

### 3.4 Reference resolution
- `{"AccountId": "$acc_1.Id"}` → resolved to real SF Id
- `{"Description": "See $acc_1"}` → literal (no `.field` = no resolution)
- Undeclared ref → `STATE_REF_NOT_FOUND`
- Invalid field on ref → `INVALID_REFERENCED_FIELD`

### 3.5 Env references
`$CURRENT_USER.Id`, `$TODAY`, `$NOW` — resolved without declaration.

---

## 4. Scenario Enforcement — field + relationship validity

### 4.1 Object-level
`actors` declared in `start_test_case` binds all subsequent object references (`given_record.object`, `when_*.target_ref`'s object, `then_*.related_object`, etc.). Non-declared → `OBJECT_NOT_IN_ACTORS`.

### 4.2 Field-level
All tools accepting field names validate against metadata:
- `given_record.field_values` — every field must exist AND `createable=true`
- `when_create_record.field_values` — every field must exist AND `createable=true`
- `when_update_record.field_values` — every field must exist on target's object AND `updateable=true`
- `then_verify.assertions` — every field must exist on target's object
- `then_verify_related.filters` / `.assertions` — every field must exist on `related_object`
- `then_verify_absence.filters` — every field must exist on `related_object`
- `then_query_and_assert.filters` / `.assertions` — every field must exist on `object`

Failures return `INVALID_FIELD` / `FIELD_NOT_CREATABLE` / `FIELD_NOT_UPDATEABLE` with `details.available_fields`.

Uses metadata already loaded as `metadata_context`. No new Salesforce calls.

### 4.3 Relationship validity
For `then_verify_related` / `then_verify_absence`:

**If `relationships` declared:** exact match required. Format: `"ChildObject.ParentReferenceField -> ParentObject.Id"`.

**If `relationships` NOT declared:** standard-naming inference from describe. Tries: `<ParentObject>Id`, `<ParentObject>__c`. If none valid, returns `RELATIONSHIP_NOT_DECLARED` with `details.valid_relationships`.

### 4.4 Mid-test-case scope update
`start_test_case` called BEFORE `end_test_case` updates current scope. After `end_test_case`, begins a new test case.

---

## 5. Cleanup — Dual Layer

### 5.1 Namespace stamping
Per-object strategy via metadata:
1. `External_Id__c` available → `pqa_<run_id>_<tc_index>_<random>`
2. Name writable without format validation → prefix `[PQA-<run_id>-<tc_index>] `
3. Description/Notes available → namespace suffix
4. None available → log warning, rely on cleanup queue only

### 5.2 Cleanup queue — new table

**Naming convention: `sf_record_id` (matches existing `run_created_entities` column naming).**

```sql
CREATE TABLE generation_created_entities (
    id SERIAL PRIMARY KEY,
    tenant_id INTEGER NOT NULL,
    generation_batch_id INTEGER REFERENCES generation_batches(id) ON DELETE CASCADE,
    generation_job_id INTEGER REFERENCES generation_jobs(id) ON DELETE SET NULL,
    environment_id INTEGER NOT NULL,
    sf_record_id VARCHAR(20) NOT NULL,
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

CASCADE on batch is safe: `generation_batches` is write-only in app code. Cascade only fires in future ops/cleanup migrations, where dropping queue rows alongside batch is the correct intent.

### 5.3 Cleanup worker job
New job type in existing worker (`worker.py:540-578` already multiplexes runs → metadata → generation_jobs in sequence; cleanup becomes a fourth poll). Deletes in LIFO order. Failures marked, don't block other cleanups.

### 5.4 Periodic purge
Scheduler job (daily) queries Salesforce for PQA-namespaced records older than 48 hours.

### 5.5 Execution-phase records are separate
A4 covers GENERATION. Execution uses existing `run_created_entities`.

---

## 6. Retry Protocol

### 6.1 Retryable errors
`INVALID_FIELD`, `OBJECT_NOT_IN_ACTORS`, `STATE_REF_NOT_FOUND`, `DUPLICATE_STATE_REF`, `UNEXPECTED_SUCCESS`, `RELATIONSHIP_NOT_DECLARED`, `FIELD_NOT_CREATABLE`, `FIELD_NOT_UPDATEABLE`, `INVALID_REFERENCED_FIELD`, `FORBIDDEN_FIELD_ON_GIVEN_RECORD`

### 6.2 Non-retryable errors
`SALESFORCE_*_FAILED`, `TIMEOUT`, infrastructure errors → fail the generation job.

### 6.3 Budget
- Per-tool-call: 3 attempts
- Per-plan cumulative: 15 retries
- Exhaustion → abort current test case

### 6.4 State isolation on retry
**For create-family tools (`given_record`, `when_create_record`):**
- On retry, check state registry first
- If `state_ref` exists → return `DUPLICATE_STATE_REF`
- If not → proceed with retry
Prevents duplicate Salesforce records on retry.

**For non-create tools:** no special check; update/delete/verify/query/wait are idempotent wrt state.

### 6.5 Retry context
LLM receives failed call, error code, error message, relevant state (actors, declared refs, recent fields). NOT full prompt history.

### 6.6 Abort behavior
Mark test case ABORTED, run cleanup for its state, prompt LLM to proceed with next or `end_test_plan`. Aborted test cases NOT persisted.

---

## 7. Error Model

### 7.1 Error codes

**Scope / declaration:** `NO_OPEN_TEST_CASE`, `TEST_CASE_ALREADY_OPEN`, `OBJECT_NOT_IN_ACTORS`, `RELATIONSHIP_NOT_DECLARED`, `FORBIDDEN_FIELD_ON_GIVEN_RECORD`
**State refs:** `DUPLICATE_STATE_REF`, `STATE_REF_NOT_FOUND`, `INVALID_REFERENCED_FIELD`
**Metadata:** `INVALID_OBJECT`, `INVALID_FIELD`, `FIELD_NOT_CREATABLE`, `FIELD_NOT_UPDATEABLE`
**Assertion:** `ASSERTION_FAILED`, `UNEXPECTED_SUCCESS`, `UNEXPECTED_FAILURE`, `QUERY_MODE_VIOLATION`
**Execution:** `SALESFORCE_CREATE_FAILED`, `SALESFORCE_UPDATE_FAILED`, `SALESFORCE_DELETE_FAILED`, `SALESFORCE_QUERY_FAILED`
**Control:** `TIMEOUT`, `END_WITHOUT_WHEN`, `RETRY_BUDGET_EXHAUSTED`

### 7.2 Error shape
```json
{
  "status": "error",
  "error_code": "INVALID_FIELD",
  "error_message": "Human-readable for LLM",
  "details": {"tool_name": "...", "failed_field": "...", "available_fields": [...], "state_ref": "..."}
}
```

Retry-vs-fatal is code-level mapping (§6.1/6.2), not a response field.

---

## 8. Prompt Architecture

Four sections:
1. **System preamble** — tool-use paradigm. Cached.
2. **Tool descriptions** — 11 tools with schemas + 1-2 examples each. Cached.
3. **Domain Packs** — existing pack injection. Uncached.
4. **Requirement context** — Jira + metadata + coverage. Uncached.

At least 2 worked examples in preamble:
- Simple positive (given → when_update → then_verify)
- Complex with async (given → when_update → wait_until → then_verify_related)

---

## 9. Persistence — v4 Native

### 9.1 New columns on `test_case_versions`
- `tool_invocations` JSONB — full tool history (v4 only)
- `generation_architecture` VARCHAR(10) NOT NULL DEFAULT 'v3'

### 9.2 Tool invocations format
```json
[
  {"step": 1, "tool": "given_record", "args": {...}, "result": {"state_ref": "acc_1", "sf_record_id": "..."}, "retry_count": 0, "duration_ms": 342},
  {"step": 2, "tool": "when_update_record", "args": {...}, "result": {"status": "ok"}, "retry_count": 0, "duration_ms": 187},
  {"step": 3, "tool": "then_verify", "args": {...}, "result": {"status": "ok"}, "retry_count": 1, "duration_ms": 245}
]
```

`duration_ms` includes Salesforce round-trip, validation, state registry update. Excludes retry latency.

### 9.3 Execution engine branching
`StepExecutor` checks `version.generation_architecture`:
- `'v3'` → reads `version.steps` (existing)
- `'v4'` → reads `version.tool_invocations` (new reader)

v4-only tools (`wait_until`, `then_verify_absence`, `then_query_and_assert` with modes) execute natively under v4. No back-translation.

### 9.4 Non-executor consumers of `version.steps`

Multiple code paths consume `version.steps` beyond the executor. Each needs explicit v4-era behavior:

| Consumer | Location | v4-era behavior |
|---|---|---|
| **Validator** (`primeqa/intelligence/validator.py:141`, called at `service.py:388, 629`) | Checks TC step shape | **Skip for v4 versions.** Return early with reason `'v4-not-applicable'`. v4 field/relationship validation happens at tool-call time, not post-hoc. |
| **Linter** (`primeqa/intelligence/linter.py:213`) | Catches malformed steps | **Already handled** via `skip_checks_for_architecture='v4'` (§10.5). Checks 1-3 skip; 4-7 still run. |
| **Agent fix-and-rerun** (`primeqa/intelligence/agent.py:442, 487`) | Fixes failing step shapes | **Disabled for v4 versions in Phase 1.** Agent-fix under v4 is a v4.1 topic — it needs tool-aware logic. Phase 1 implementation returns `"agent_fix_not_supported_for_v4"` when invoked on v4 versions. |
| **Requirement detail view** (`primeqa/views.py:2165`) | Shows TC summary | Render scenario metadata (`intent`, `actors`, `expected_outcome`) + `tool_invocations` count. No step table. |
| **TC edit page** (`primeqa/views.py:2271`, `templates/test_cases/edit.html`) | User-editable step editor | **Read-only for v4 versions in Phase 1.** v4 TC editing is a v4.1 topic. Phase 1: show `tool_invocations` as a read-only JSON view; edit button disabled with tooltip "v4 test cases are edited via regeneration — edit the requirement and regenerate." |
| **Review detail** (`primeqa/views.py:2435`, `templates/reviews/detail.html`) | BA review interface | Render via `_tc_body.html` macro (see below). |
| **Run detail** (`primeqa/views.py:2517`, `templates/runs/detail.html`) | Execution results | Render per-step outcomes. Executor writes `RunTestResult` rows per tool invocation; rendering is uniform across v3/v4. |
| **`_tc_body.html` macro** (migration 048, used by `reviews/detail.html`, `tickets/_run_summary.html`) | Shared TC body renderer | **Branch on `version.generation_architecture`.** v3 renders `steps` array as today. v4 renders a tool-invocations view: scenario header (intent, actors, expected_outcome), then tool list grouped by Given/When/Then/Async. Macro gets one new block; v3 path untouched. |

Phase 1 ships with all consumers v4-aware (skip, disable, or render). Agent-fix and TC edit under v4 are deferred to v4.1.

### 9.5 Scenario metadata persistence
On `test_cases`:
- `intent` — existing, extended under v4
- `actors` JSONB — new column
- `relationships` JSONB — new column, nullable
- `conditions` JSONB — new column
- `expected_outcome` — reuses `story_view.expected_outcome` or new column

---

## 10. Integration Points

### 10.1 Router chain
New task: `test_plan_generation_v4`.

```python
"test_plan_generation_v4": {
    COMPLEXITY_LOW:    [SONNET],
    COMPLEXITY_MEDIUM: [SONNET, OPUS],
    COMPLEXITY_HIGH:   [SONNET, OPUS],
},
```

### 10.2 Gateway / usage log
Flows through new `llm_call_loop()` (§15.1). `context_for_log` keys:
- `domain_packs_applied` (unchanged)
- `architecture` (`"v4"`)
- `tool_invocation_count`, `retry_count`, `aborted_test_cases`, `turn_index`

### 10.3 Feature flag
`tenant_agent_settings.llm_generation_architecture` — VARCHAR, default `'v3'`.

Read path (enum equality, not bool):
```python
def _v4_enabled(self, tenant_id: int, db) -> bool:
    try:
        row = db.query(TenantAgentSettings).filter_by(tenant_id=tenant_id).first()
        return getattr(row, 'llm_generation_architecture', 'v3') == 'v4'
    except Exception:
        return False
```

**Pattern to follow:** mirrors `_story_enrichment_enabled(self, tenant_id, db)` in `primeqa/test_management/service.py:555-570` — instance method, caller passes `db`. Do NOT copy `_domain_packs_enabled`'s detached-session pattern (`primeqa/intelligence/generation.py:24-46`). The v4 call site is `generation_jobs.process_job` which owns a fresh session already.

### 10.4 Domain Packs
Unchanged. Appended after tool descriptions section. Uncached.

### 10.5 Linter
`GenerationLinter.lint(skip_checks_for_architecture='v4')` early-returns checks 1-3 as pass. Checks 4-7 still run.

### 10.6 Task name reference cleanup
`"test_plan_generation"` literal in 9 places. Extend each to `task in ("test_plan_generation", "test_plan_generation_v4")`.

---

## 11. Rollout & Validation

### 11.1 Phase 1 — Implementation (3-4 weeks)
Multi-turn gateway, tool executor, 11 tools, retry loop, cleanup queue, prompt module, persistence (new columns + branching executor + consumer branches), feature flag, router. Test suite.

Exit: all tools implemented+tested, end-to-end for known-good ticket, retry verified, cleanup queue verified.

### 11.2 Phase 2 — Shadow mode (3-4 days build + 2 weeks data)

Pilot tenants run v3 AND v4 in parallel. v3 used, v4 shadowed.

**Cost accounting:**
- Shadow rows are flagged via `context_for_log['shadow'] = True` → written to `llm_usage_log.context`
- **`limits.py:check()` exempts shadow rows** from daily-spend cap enforcement (`AND NOT (context->>'shadow' = 'true')` in the usage query)
- **Cost dashboards (`/settings/llm-usage`, `/settings/my-llm-usage`) continue to COUNT shadow rows in totals** — visibility is preserved; only cap enforcement is bypassed. This prevents silent invisible spend.
- Shadow restricted to Pro+ tenants for first 7 days

Exit: 50+ generations, v3 vs v4 quality comparison, no systematic regressions.

### 11.3 Phase 3 — Per-tenant flip
Flip pilots one at a time. v3 stays as rollback.

### 11.4 Phase 4 — Default v4 (2-4 weeks after pilot success)

### 11.5 Phase 5 — v3 sunset (months later)

---

## 12. Known Limitations & v2 Topics

1. Knowledge → validation bridge (Domain Packs enforce-by-validation)
2. `when_call_method` for Apex direct invocation
3. Cross-test-case state dependencies
4. Scratch-org-per-run isolation
5. Multi-user test context
6. Bulk assertions beyond count/mode
7. Coverage tags (`coverage_tags: ["field:Amount", "state:ClosedWon"]`) — additive, no lock-in
8. **v4 agent-fix-and-rerun** — disabled in Phase 1, re-enabled in v4.1 with tool-aware logic
9. **v4 TC editor** — read-only in Phase 1, full editor in v4.1

---

## 13. Spec Decisions

1. Namespace stamping: metadata-driven per-object
2. Namespace purge: 48 hours
3. Retry budgets: 3 per tool, 15 per plan
4. Router HIGH: `[SONNET, OPUS]` initially
5. Shadow mode: 50+ generations minimum
6. Persistence: v4-only `tool_invocations`
7. Cleanup: new `generation_created_entities` table, `sf_record_id` convention
8. Feature flag: enum (`'v3'` / `'v4'`)
9. Query mode: `all | any | none`, default `all`
10. wait_until description: required
11. given_record forbids `expect_failure`: schema-enforced
12. State isolation on create-family retries
13. v4 non-executor consumers: skip (validator), disable (agent-fix, edit), render (views, templates)
14. Shadow rows: exempt from caps, visible in dashboards

---

## 14. Non-Decisions (Implementation-level)

- Python module structure for tool executor
- State registry data structure
- JSON schema validation library
- Error-code-to-retry-trigger mapping details (framework in §6)
- Cleanup worker polling interval

---

## 15. Implementation Gotchas

### 15.1 Per-turn cost attribution in `llm_usage_log`

Today: one generation = one row with `generation_batch_id`. Under v4: N tool-call turns = N rows.

**Required implementation pattern** (mirrors existing v3 escalation-row handling):
- Loop driver collects every per-turn `usage_log_id` as tool calls complete
- After the plan persists and `db.commit()` fires, back-attach all N ids to the batch in a single post-commit pass
- Same code path as v3's 1-2 escalation rows, just N-element list

The `attach_batch` helper (ref. commit `06b582b`) already tolerates FK rollback (ref. commit `ca5bdc0`) — v4's higher row count is safe against mid-batch failures.

Dashboard: GROUP BY `generation_batch_id` queries continue to work. Per-call counts go up N× for v4. Consider a `turn_index` dimension.

### 15.2 Gateway auto-load-feedback

`gateway.py:171` checks `task == "test_plan_generation"`. Extend to `task in ("test_plan_generation", "test_plan_generation_v4")`.

**Feedback signal handling under v4:**
- Signals about grammar/step-shape (unresolved `$vars`, malformed steps, Id-in-create) become v4-irrelevant — they're impossible-by-construction under tool validation
- Signals about wrong field names, wrong objects, bad picklist values remain valid for v4
- **Tag obsolete signals at filter-time, don't purge them.** A `feedback_rules.py` filter tagging signal sub-categories as `v4_irrelevant=True` lets v3 traffic continue consuming all signals while v4 traffic filters them out

### 15.3 Anthropic cache invalidation

**Not a one-time event.** Cache invalidates on EVERY change to cached prompt blocks — not just cutover:
- v4 cutover: 1× invalidation per tenant
- Any subsequent prompt-module revision (tool description tweak, new worked example, schema fix): 1× invalidation per tenant per revision
- Adding a new tool in v4.1: full invalidation across all tenants

Budget ongoing prompt-iteration cost as 1× cache-write spike per tenant per revision. Plan prompt changes to batch where possible.

### 15.4 Sync-route compatibility — three routes audited

**A4 generation runs only on the worker service.** Multi-turn LLM + N SF calls = 30-90s, well past Gunicorn 30s default.

**Sync routes calling `generate_test_plan` — all three audited:**

| Route | File:Line | Current behavior | v4 behavior required |
|---|---|---|---|
| `POST /requirements/<id>/generate` | `views.py:5726-5762` | Enqueues via `create_or_get_job` | No change — already async |
| `POST /api/requirements/bulk-generate` | `routes.py:365` | Enqueues | No change — already async |
| **`POST /api/test-cases/generate`** | **`routes.py:1423-1449`** | **Runs INLINE via `svc.generate_test_case()` → `generate_test_plan` at `service.py:684-720`** | **MUST flip to async-enqueue under v4.** Option A: always enqueue (changes API contract — returns 202 instead of 200). Option B: enqueue only when v4 flag is on for tenant, inline otherwise (preserves v3 behavior). **Decision: Option B.** Branch on `_v4_enabled(tenant_id, db)` inside the route. v3 path unchanged; v4 returns 202 with job id. |

Without this fix, single-TC regeneration on a v4 tenant hangs Gunicorn workers.

### 15.5 Eval harness deferral

`intelligence/llm/eval/runner.py` is CLI-only, no scheduler hook. Deferring v4 eval coverage to Phase 2 creates no silent coverage gap.

---

## End of Spec v4

Ready for implementation prompt authoring.
