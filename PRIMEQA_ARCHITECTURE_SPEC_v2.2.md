# PrimeQA Architecture Specification v2.2

## Overview

This document defines the complete database schema, system architecture, and execution model for PrimeQA's multi-user, multi-environment Salesforce test management platform.

**Design principles:**
- Execution trace is central; test case is input — not the other way around
- Metadata is relational and versioned — never stored as JSON blobs
- Step-level traceability is first-class with adaptive capture — full trace only where it adds value
- Impact analysis is entity-level exact — not heuristic, not object-level
- Idempotency is state reconciliation, not just key matching — retries never corrupt
- Cleanup has full lineage — every entity tracked from creation through deletion
- LLM reasoning is deterministic — structured but extensible input contract
- Domain boundaries enforced in code — schema domains mirror code modules
- Versioning includes lifecycle — not just storage, but archival and active pointers

---

## 1. CORE PLATFORM

### 1.1 tenants

One row per customer organization. Single-tenant for MVP, multi-tenant ready.

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| name | varchar(255) | Display name |
| slug | varchar(100) UNIQUE | URL-safe identifier |
| status | varchar(20) | active, suspended |
| settings | jsonb | Tenant-level config |
| created_at | timestamptz | |
| updated_at | timestamptz | |

### 1.2 users

Up to 20 active users per tenant.

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| tenant_id | FK → tenants | |
| email | varchar(255) | UNIQUE per tenant |
| password_hash | varchar(255) | bcrypt |
| full_name | varchar(255) | |
| role | varchar(20) | admin, tester, ba, viewer |
| is_active | boolean | Default true |
| last_login_at | timestamptz | |
| created_at | timestamptz | |
| updated_at | timestamptz | |

**Role permissions:**

| Action | admin | tester | ba | viewer |
|--------|-------|--------|----|--------|
| Manage users | ✓ | | | |
| Manage environments | ✓ | | | |
| Configure execution slots | ✓ | | | |
| Trigger runs | ✓ | ✓ | | |
| Cancel runs | ✓ | ✓ (own) | | |
| Generate test cases | ✓ | ✓ | | |
| Edit test cases | ✓ | ✓ | | |
| Review/approve test cases | ✓ | | ✓ | |
| Create sections | ✓ | | | |
| Create suites | ✓ | ✓ | | |
| View dashboard | ✓ | ✓ | ✓ | ✓ |
| View run results | ✓ | ✓ | ✓ | ✓ |

### 1.3 refresh_tokens

JWT refresh token storage. Max 5 active per user.

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| user_id | FK → users (CASCADE) | |
| token_hash | varchar(255) UNIQUE | SHA-256 of raw token |
| expires_at | timestamptz | 7-day default |
| revoked | boolean | Default false |
| created_at | timestamptz | |

### 1.4 environments

Each environment represents one connected Salesforce org.

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| tenant_id | FK → tenants | |
| name | varchar(255) | e.g. "Dev Sandbox", "Full UAT" |
| env_type | varchar(30) | sandbox, uat, staging, production |
| sf_instance_url | varchar(500) | e.g. https://acme--dev.sandbox.my.salesforce.com |
| sf_api_version | varchar(10) | e.g. "59.0" |
| execution_policy | varchar(20) | full, read_only, disabled |
| capture_mode | varchar(20) | minimal, smart, full. Default: smart |
| max_execution_slots | integer | Default 2. Admin configurable |
| cleanup_mandatory | boolean | Default false. True for production |
| current_meta_version_id | FK → meta_versions (nullable) | Active metadata version pointer |
| is_active | boolean | Default true |
| created_at | timestamptz | |
| updated_at | timestamptz | |

**Execution policies:**
- `full` — create, update, delete, query, verify all allowed
- `read_only` — query and verify only, no DML
- `disabled` — no execution, metadata only

**Capture modes (adaptive step-level tracing):**
- `minimal` — only API response + error message. Cheapest. Good for stable regression suites where you trust the tests.
- `smart` — captures before/after state based on runtime signals, not just metadata prediction. This is the default.
- `full` — before/after state on every step. For debugging, new test development, or when investigating intermittent failures.

**Smart mode logic (executed per step):**

The key insight from the architect: capture should be driven by observed behaviour, not just metadata. Metadata tells you what *could* happen; runtime signals tell you what *does* happen.

```
# Priority 1: Always capture on failure
if step.status == 'failed':
    capture before/after

# Priority 2: Runtime signals (observed behaviour)
elif step_had_side_effects(step):           # check run_created_entities for unexpected records
    capture before/after
elif is_historically_risky_step(step):      # check failure_patterns for this object+action combo
    capture before/after

# Priority 3: Metadata signals (predicted risk)
elif step.target_field in CRITICAL_FIELDS:
    capture before/after
elif entity_has_validation_rules(step.target_object):
    capture before/after

# Priority 4: Default
else:
    capture api_response only
```

**How runtime signals work:**
- `step_had_side_effects()` — after step execution, checks if `run_created_entities` recorded any trigger/flow-created records for this step. If yes, the next run of the same test will capture this step fully.
- `is_historically_risky_step()` — queries `failure_patterns` for active patterns matching this object + action combination. If this step type has failed in recent runs, capture it fully.

### 1.5 environment_credentials

Encrypted OAuth credentials per environment. Separate table for rotation safety.

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| environment_id | FK → environments (UNIQUE) | One-to-one |
| client_id | varchar(500) | Encrypted at rest |
| client_secret | varchar(500) | Encrypted at rest |
| access_token | varchar(2000) | Encrypted at rest |
| refresh_token | varchar(2000) | Encrypted at rest |
| token_expires_at | timestamptz | |
| last_refreshed_at | timestamptz | |
| status | varchar(20) | valid, expired, failed |

### 1.6 activity_log

Audit trail of all user actions.

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| tenant_id | FK → tenants | |
| user_id | FK → users (nullable) | Null for system actions |
| action | varchar(50) | run.triggered, test.approved, user.created, etc. |
| entity_type | varchar(50) | pipeline_run, test_case, environment, etc. |
| entity_id | integer | |
| details | jsonb | Action-specific context |
| created_at | timestamptz | Indexed DESC |

---

## 2. RELATIONAL METADATA (VERSIONED)

Every metadata refresh creates a new version. All metadata is stored relationally, not as JSON blobs. Diffing is a SQL join between versions.

### 2.1 meta_versions

One row per metadata refresh per environment. Includes lifecycle management to prevent version explosion.

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| environment_id | FK → environments | |
| version_label | varchar(20) | Auto-incremented: v1, v2, v3 |
| snapshot_hash | varchar(64) | SHA-256 of combined metadata for fast diff detection |
| status | varchar(20) | in_progress, complete, partial, failed |
| lifecycle | varchar(20) | active, archived, deleted. Default: active |
| object_count | integer | |
| field_count | integer | |
| vr_count | integer | Validation rules |
| flow_count | integer | |
| trigger_count | integer | |
| started_at | timestamptz | |
| completed_at | timestamptz | |

**Lifecycle management:**
- `active` — current or recent. Used for diffing and referenced by test case versions.
- `archived` — older than N versions (configurable, default 20). Still queryable but excluded from diff queries by default. Child rows (meta_objects, meta_fields, etc.) remain intact.
- `deleted` — marked for cleanup. Child rows can be purged by a maintenance job.

The `environments.current_meta_version_id` pointer always references the latest `active` + `complete` version. Queries default to this version unless explicitly requesting historical data.

**Archival rule:** When a new version completes, any version older than the 20th most recent for that environment is automatically moved to `archived`. A nightly job can purge `deleted` versions older than 90 days.

### 2.2 meta_objects

SObject definitions per version.

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| meta_version_id | FK → meta_versions | |
| api_name | varchar(255) | e.g. Opportunity, Case, Custom_Object__c |
| label | varchar(255) | |
| key_prefix | varchar(5) | e.g. 006 for Opportunity |
| is_custom | boolean | |
| is_queryable | boolean | |
| is_createable | boolean | |
| is_updateable | boolean | |
| is_deletable | boolean | |

**Index:** (meta_version_id, api_name) UNIQUE

### 2.3 meta_fields

Field definitions per object per version.

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| meta_version_id | FK → meta_versions | |
| meta_object_id | FK → meta_objects | |
| api_name | varchar(255) | e.g. Amount, Custom_Field__c |
| label | varchar(255) | |
| field_type | varchar(50) | string, currency, picklist, reference, boolean, etc. |
| is_required | boolean | |
| is_custom | boolean | |
| is_createable | boolean | |
| is_updateable | boolean | |
| reference_to | varchar(255) | Target object for lookups/master-detail |
| length | integer | For string fields |
| precision | integer | For number fields |
| scale | integer | Decimal places |
| picklist_values | jsonb | For picklist/multipicklist fields |
| default_value | varchar(500) | |

**Index:** (meta_version_id, meta_object_id, api_name) UNIQUE

### 2.4 meta_validation_rules

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| meta_version_id | FK → meta_versions | |
| meta_object_id | FK → meta_objects | |
| rule_name | varchar(255) | |
| error_condition_formula | text | The formula expression |
| error_message | text | User-facing error |
| is_active | boolean | |

### 2.5 meta_flows

Process Builders, Record-Triggered Flows, Screen Flows, Autolaunched Flows.

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| meta_version_id | FK → meta_versions | |
| api_name | varchar(255) | |
| label | varchar(255) | |
| flow_type | varchar(50) | autolaunched, record_triggered, screen, process_builder |
| trigger_object | varchar(255) | Which object triggers this flow |
| trigger_event | varchar(50) | create, update, delete, create_or_update |
| is_active | boolean | |
| entry_conditions | jsonb | When the flow fires |

### 2.6 meta_triggers

Apex triggers per object.

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| meta_version_id | FK → meta_versions | |
| meta_object_id | FK → meta_objects | |
| trigger_name | varchar(255) | |
| events | varchar(255) | Comma-separated: before_insert, after_update, etc. |
| is_active | boolean | |

### 2.7 meta_record_types

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| meta_version_id | FK → meta_versions | |
| meta_object_id | FK → meta_objects | |
| api_name | varchar(255) | |
| label | varchar(255) | |
| is_active | boolean | |
| is_default | boolean | |

### How metadata diffing works

When a new `meta_version` is created for an environment:

1. System compares `snapshot_hash` with previous version. If identical, no diff needed.
2. If different, SQL joins identify:
   - Fields in v(N-1) not in v(N) → `field_removed`
   - Fields in v(N) not in v(N-1) → `field_added`
   - Fields where `is_required`, `field_type`, or `picklist_values` changed → `field_changed`
   - Validation rules where `error_condition_formula` or `is_active` changed → `vr_changed`
   - Flows where `is_active`, `entry_conditions`, or `trigger_event` changed → `flow_changed`
3. Each change generates a `metadata_impacts` row matched against `test_case_versions.referenced_entities`

---

## 3. TEST MANAGEMENT

### 3.1 sections

Admin-created folder tree. Flexible containers for requirements and standalone test cases.

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| tenant_id | FK → tenants | |
| parent_id | FK → sections (nullable) | Self-ref for nesting |
| name | varchar(255) | |
| description | text | |
| position | integer | Sort order within parent |
| created_by | FK → users | |
| created_at | timestamptz | |

### 3.2 requirements

Anchor entity for test cases. Imported from Jira or created manually.

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| tenant_id | FK → tenants | |
| section_id | FK → sections | |
| source | varchar(20) | jira, manual |
| jira_key | varchar(50) | Nullable. e.g. SQ-207 |
| jira_summary | varchar(500) | |
| jira_description | text | |
| acceptance_criteria | text | |
| jira_version | integer | Increments on each detected Jira change |
| is_stale | boolean | True when Jira updated since last sync |
| jira_last_synced | timestamptz | |
| created_by | FK → users | |
| created_at | timestamptz | |
| updated_at | timestamptz | |

**Index:** (tenant_id, jira_key) UNIQUE WHERE jira_key IS NOT NULL

### 3.3 test_cases

Belongs to either a requirement (via requirement_id) or directly to a section (standalone). Never both.

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| tenant_id | FK → tenants | |
| requirement_id | FK → requirements (nullable) | If linked to requirement |
| section_id | FK → sections (nullable) | If standalone (no requirement) |
| title | varchar(500) | |
| owner_id | FK → users | Who created/generated it |
| visibility | varchar(20) | private, shared |
| status | varchar(20) | draft, approved, active |
| current_version_id | FK → test_case_versions | Points to latest active version |
| created_by | FK → users | |
| updated_at | timestamptz | |
| version | integer | Optimistic concurrency counter |

**Constraint:** CHECK (requirement_id IS NOT NULL OR section_id IS NOT NULL)
**Trigger:** Auto-increment `version` on UPDATE for optimistic concurrency.

**Visibility flow:**
1. `draft` + `private` — only owner sees it
2. Owner clicks "Share" → `draft` + `shared` — team can see it
3. BA approves → `approved` + `shared`
4. Admin activates → `active` + `shared` — ready for execution and suites

### 3.4 test_case_versions

Immutable snapshots. Each version records what metadata it was generated against and what entities it depends on.

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| test_case_id | FK → test_cases | |
| version_number | integer | Sequential: 1, 2, 3 |
| metadata_version_id | FK → meta_versions | Which metadata was used |
| steps | jsonb | Ordered test steps (see schema below) |
| expected_results | jsonb | Per-step expected outcomes |
| preconditions | jsonb | Setup requirements |
| generation_method | varchar(20) | ai, manual, regenerated |
| confidence_score | float | AI confidence (0.0-1.0) |
| referenced_entities | jsonb | Entity-level dependency list (see below) |
| created_by | FK → users | |
| created_at | timestamptz | |

**`referenced_entities` schema:**
```json
[
  "Opportunity.Amount",
  "Opportunity.StageName",
  "Opportunity.CloseDate",
  "ValidationRule.Opportunity.RequireAmount",
  "Flow.Opportunity.StageUpdateProcess",
  "Trigger.Opportunity.OpportunityTrigger",
  "RecordType.Opportunity.Enterprise"
]
```

This is what makes impact analysis exact. When metadata diffs find that `ValidationRule.Opportunity.RequireAmount` changed, the system joins against this array to find every test case version that references it.

**`steps` schema:**
```json
[
  {
    "step_order": 1,
    "action": "create",
    "target_object": "Opportunity",
    "field_values": {
      "Name": "Test Opp {{timestamp}}",
      "StageName": "Prospecting",
      "CloseDate": "{{today+30}}",
      "Amount": 50000
    },
    "expected_result": "Opportunity created successfully",
    "state_ref": "$opp_id"
  },
  {
    "step_order": 2,
    "action": "update",
    "target_object": "Opportunity",
    "record_ref": "$opp_id",
    "field_values": {
      "StageName": "Closed Won"
    },
    "expected_result": "Stage updated to Closed Won"
  }
]
```

### 3.5 test_suites

Curated collections for execution. A test case can belong to multiple suites.

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| tenant_id | FK → tenants | |
| name | varchar(255) | |
| description | text | |
| suite_type | varchar(30) | regression, smoke, sprint, custom |
| created_by | FK → users | |
| created_at | timestamptz | |
| updated_at | timestamptz | |

### 3.6 suite_test_cases

Join table for suites and test cases.

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| suite_id | FK → test_suites | |
| test_case_id | FK → test_cases | |
| position | integer | Order within suite |

**Constraint:** (suite_id, test_case_id) UNIQUE

### 3.7 ba_reviews

Reviews are on versions, not test cases. When regenerated, new version needs fresh review.

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| tenant_id | FK → tenants | |
| test_case_version_id | FK → test_case_versions | |
| assigned_to | FK → users | BA assigned to review |
| reviewed_by | FK → users (nullable) | Null until reviewed |
| status | varchar(20) | pending, approved, rejected, needs_edit |
| feedback | text | BA's comments/corrections |
| reviewed_at | timestamptz | |
| created_at | timestamptz | |

### 3.8 metadata_impacts

Entity-level impact tracking when metadata changes.

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| new_meta_version_id | FK → meta_versions | The new snapshot |
| prev_meta_version_id | FK → meta_versions | The previous snapshot |
| test_case_id | FK → test_cases | Affected test case |
| impact_type | varchar(30) | field_removed, field_added, field_changed, vr_changed, flow_changed, trigger_changed |
| entity_ref | varchar(255) | e.g. "Opportunity.Amount" or "ValidationRule.RequireAmount" |
| change_details | jsonb | Before/after values |
| resolution | varchar(20) | pending, regenerated, edited, dismissed |
| resolved_by | FK → users (nullable) | |
| resolved_at | timestamptz | |
| created_at | timestamptz | |

---

## 4. EXECUTION ENGINE

### 4.1 pipeline_runs

One row per execution request. Targets a specific environment.

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| tenant_id | FK → tenants | |
| environment_id | FK → environments | Which org to run against |
| triggered_by | FK → users | |
| run_type | varchar(30) | full, generate_only, execute_only |
| source_type | varchar(30) | jira_tickets, suite, requirements, rerun |
| source_ids | jsonb | Ticket keys, suite ID, requirement IDs, or previous run ID |
| status | varchar(20) | queued, running, completed, failed, cancelled |
| priority | varchar(20) | normal, high, critical. Default: normal |
| max_execution_time_sec | integer | Timeout ceiling. Default: 3600 (1 hour) |
| cancellation_token | varchar(100) | UUID for abort. Worker checks between steps |
| config | jsonb | Model overrides, flags |
| total_tests | integer | Default 0 |
| passed | integer | Default 0 |
| failed | integer | Default 0 |
| skipped | integer | Default 0 |
| error_message | text | Run-level error if applicable |
| queued_at | timestamptz | |
| started_at | timestamptz | |
| completed_at | timestamptz | |

**Indexes:**
- (tenant_id, status) — for queue queries
- (environment_id, status) WHERE status IN ('queued', 'running') — for slot management
- (status, priority, queued_at) WHERE status = 'queued' — for queue ordering

### 4.2 pipeline_stages

Each stage of the 6-stage pipeline, independently trackable and retryable.

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| run_id | FK → pipeline_runs (CASCADE) | |
| stage_name | varchar(50) | metadata_refresh, jira_read, generate, store, execute, record |
| stage_order | integer | 1 through 6 |
| status | varchar(20) | pending, running, passed, failed, skipped |
| input_payload | jsonb | What this stage received |
| output_payload | jsonb | What this stage produced |
| attempt | integer | Current attempt number. Default 1 |
| max_attempts | integer | Per-stage retry limit |
| last_error | text | Error from most recent attempt |
| duration_ms | integer | |
| started_at | timestamptz | |
| completed_at | timestamptz | |

**Retry policy per stage:**

| Stage | max_attempts | Retry strategy |
|-------|-------------|----------------|
| metadata_refresh | 3 | Backoff. SF rate limit recovery |
| jira_read | 2 | Refresh token, retry once |
| generate | 3 | Backoff. LLM 529 overload recovery |
| store | 2 | Idempotent write, retry once |
| execute | 1 | No auto-retry. Failures are meaningful |
| record | 3 | Backoff. Write-back to Jira/DB |

### 4.3 run_test_results

One row per test case per run. Test-level summary — detail lives in step results.

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| run_id | FK → pipeline_runs | |
| test_case_id | FK → test_cases | |
| test_case_version_id | FK → test_case_versions | Pinned to exact version |
| environment_id | FK → environments | |
| status | varchar(20) | passed, failed, error, skipped |
| failure_type | varchar(30) | validation_rule, metadata_mismatch, system_error, assertion_mismatch, dependency_failure. Null if passed |
| failure_summary | text | One-line human-readable failure |
| total_steps | integer | |
| passed_steps | integer | |
| failed_steps | integer | |
| duration_ms | integer | |
| executed_at | timestamptz | |

### 4.4 run_step_results ⭐ (CORE IP TABLE)

One row per step per test case. This is PrimeQA's differentiator — full execution trace at step level.

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| run_test_result_id | FK → run_test_results | |
| step_order | integer | Matches test_case_versions.steps[n].step_order |
| step_action | varchar(20) | create, update, query, verify, convert, wait, delete |
| target_object | varchar(255) | e.g. Opportunity, Lead |
| target_record_id | varchar(20) | SF record ID acted on |
| status | varchar(20) | passed, failed, error, skipped |
| execution_state | varchar(20) | not_started, in_progress, partially_completed, completed |
| before_state | jsonb | Field values queried before the step |
| after_state | jsonb | Field values queried after the step |
| field_diff | jsonb | Computed: {field: {old: X, new: Y}} |
| api_request | jsonb | Full REST request: method, url, headers, body |
| api_response | jsonb | Full REST response: status, headers, body |
| error_message | text | |
| duration_ms | integer | |
| executed_at | timestamptz | |

**`before_state` / `after_state` schema:**
```json
{
  "StageName": "Prospecting",
  "Amount": 50000,
  "CloseDate": "2026-05-15",
  "OwnerId": "005xx000001abcDEF"
}
```

**`field_diff` schema (auto-computed):**
```json
{
  "StageName": {"old": "Prospecting", "new": "Closed Won"},
  "LastModifiedDate": {"old": "2026-04-12T10:00:00Z", "new": "2026-04-13T14:30:00Z"}
}
```

**How before/after capture works (governed by environment.capture_mode):**

In `full` mode:
1. Before executing the step, query the target record: `GET /sobjects/{object}/{id}?fields=...`
2. Store response as `before_state`
3. Execute the step action (create/update/etc.)
4. Query the record again
5. Store response as `after_state`
6. Compute `field_diff` as the delta

In `smart` mode (default):
1. Execute the step action
2. If step failed → capture before_state retroactively (query current state as the "after" of the failure) and store api_response
3. If step touched a critical field or entity with VRs/triggers → do the full capture above
4. Otherwise → store only `api_request` and `api_response`, leave before/after/diff as null

In `minimal` mode:
1. Execute the step action
2. Store only `api_request` and `api_response`
3. All state columns remain null

**API budget comparison (10 tests × 8 steps = 80 steps):**

| Mode | Extra API calls | Total per run | Use case |
|------|----------------|---------------|----------|
| full | 160 | ~320 | Debugging, new test development |
| smart | ~40 (est. 25% of steps need capture) | ~200 | Default for all environments |
| minimal | 0 | ~160 | Stable regression, API-limited orgs |

**Step execution state (partial step recovery):**

The `execution_state` column tracks how far a step got before stopping, enabling intelligent retry:

- `not_started` — step hasn't been attempted yet
- `in_progress` — step is currently executing (set at start of execution)
- `partially_completed` — step's primary action succeeded but a secondary action failed (e.g. Opportunity created but related Quote update failed)
- `completed` — step fully executed (pass or fail — `status` tells you the outcome)

**Retry logic based on execution_state:**
```
if execution_state == 'not_started':
    execute normally
elif execution_state == 'in_progress':
    # worker crashed mid-step — state is unknown
    check if primary record was created (via idempotency key)
    if created → mark partially_completed, resume secondary actions
    if not created → reset to not_started, re-execute
elif execution_state == 'partially_completed':
    # primary action succeeded, secondary failed
    skip primary action (record exists)
    retry only the secondary actions
elif execution_state == 'completed':
    skip entirely (already done)
```

This prevents the dangerous scenario where a retry re-runs a partially successful step and creates duplicate records or inconsistent state.

### 4.5 run_artifacts

Screenshots, logs, debug logs — linked to step or test level.

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| run_test_result_id | FK → run_test_results | |
| run_step_result_id | FK → run_step_results (nullable) | Null for test-level artifacts |
| artifact_type | varchar(30) | screenshot, log, debug_log, api_trace |
| storage_url | varchar(1000) | Cloudinary or S3 URL |
| filename | varchar(255) | |
| file_size_bytes | integer | |
| captured_at | timestamptz | |

### 4.6 run_created_entities

Every record PrimeQA creates in Salesforce during a run. Full lineage tracking.

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| run_id | FK → pipeline_runs | |
| run_step_result_id | FK → run_step_results | Which step created it |
| entity_type | varchar(255) | e.g. Opportunity, Contact, Task |
| sf_record_id | varchar(20) | Salesforce record ID |
| creation_source | varchar(30) | direct, trigger, workflow, process_builder, flow |
| logical_identifier | varchar(100) | e.g. "primary_opportunity", "child_quote_1", "converted_contact" |
| primeqa_idempotency_key | varchar(200) | Composite: {run_id}_{step_order}_{entity_type}_{logical_identifier} |
| creation_fingerprint | varchar(64) | Hash of field values + parent record ID |
| parent_entity_id | FK → run_created_entities (nullable) | For trigger-created entities, points to the parent |
| cleanup_required | boolean | Default true |
| created_at | timestamptz | |

**Idempotency model (state reconciliation, not just key matching):**

The key insight: idempotency is about recognising "this entity already exists from a previous attempt" — not just preventing double-inserts.

Before creating any record, the executor:
1. Checks `run_created_entities` for matching `primeqa_idempotency_key`
2. If found, verifies the `sf_record_id` still exists in Salesforce (it might have been cleaned up by a partial retry)
3. If record exists in SF → reuse it, skip creation
4. If record was cleaned up → create fresh, update the tracking row with new `sf_record_id`
5. If no tracking row found → create the record, insert the tracking row

**Logical identifiers** solve the collision problem: if step 3 creates 2 Opportunities, they get distinct logical identifiers like "primary_opportunity" and "upsell_opportunity". These are defined in the test step definition.

**Trigger-created entities:**
Trigger/flow-created entities are inherently non-idempotent — you can't prevent a trigger from firing. The approach is detection + lineage, not prevention:
1. After each step, query for recently created records:
   ```
   WHERE CreatedById = :integration_user_id   -- filter to our connected app user
   AND CreatedDate > :step_start_time
   AND Id NOT IN (:directly_created_ids)      -- exclude records we created directly
   ```
2. For each detected record, verify it's related to our step by checking parent relationships (e.g. does this Task's WhatId point to the Opportunity we just created?) or by matching field fingerprints
3. Map confirmed trigger-created records to the parent step via `parent_entity_id`
4. Track with `creation_source` = trigger/workflow/flow
5. On retry, the reconciliation logic checks: "does an entity matching this `creation_fingerprint` already exist from the previous attempt?"

**Why CreatedById matters:** In a multi-user Salesforce org, other users and integrations are creating records concurrently. Time-based detection alone picks up noise. Filtering by the integration user ID eliminates records created by other users, though it won't catch records created by triggers running as a different user (e.g. a flow with "Run as System"). For those edge cases, parent relationship matching is the fallback.

**Data isolation naming convention (proactive identification, not just reactive cleanup):**

Cleanup is reactive — it tries to remove data after the fact and sometimes fails. Identification is proactive — it makes PrimeQA-created data instantly recognisable in the org. This is critical for pilot trust.

Every record PrimeQA creates directly must follow this naming pattern:

```
Name field:     "PQA_{run_id}_{logical_identifier} {timestamp}"
                e.g. "PQA_47_primary_opportunity 2026-04-13"

External ID:    "PQA_{run_id}_{step_order}_{logical_identifier}"
                e.g. "PQA_47_3_primary_opportunity"
```

**Implementation:**
- The executor prepends `PQA_` to the Name field of every created record (where the object has a Name field)
- If the target object has a text External ID field (custom or standard), the executor sets it to the full idempotency key
- If no External ID field exists, the Name convention alone provides identification

**Benefits:**
- Manual cleanup becomes trivial: `SELECT Id FROM Opportunity WHERE Name LIKE 'PQA_%'`
- Org admins can create list views filtering PrimeQA test data
- Stale test data is immediately visible, not hidden among real records
- Idempotency checks can use External ID for faster lookups than Name matching

### 4.7 run_cleanup_attempts

Each cleanup attempt is tracked separately. Multiple attempts possible per entity.

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| run_created_entity_id | FK → run_created_entities | |
| attempt_number | integer | 1, 2, 3... |
| status | varchar(20) | success, failed, skipped |
| failure_reason | text | |
| failure_type | varchar(30) | validation_rule, dependency, permission, system_error |
| api_response | jsonb | Full API response from delete attempt |
| attempted_at | timestamptz | |

**Cleanup order:** Reverse of creation order (last created = first deleted). Handles dependency chains correctly.

**Production safety:** For environments with `cleanup_mandatory = true`:
- Run cannot complete with status "completed" if any created entity has no successful cleanup attempt
- Status becomes "completed_with_warnings" and a notification is sent
- UI shows: "Cleanup incomplete — N records remain in {environment}" with record IDs

### 4.8 execution_slots

Physical slot tracking per environment.

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| environment_id | FK → environments | |
| run_id | FK → pipeline_runs | Which run holds this slot |
| acquired_at | timestamptz | |
| released_at | timestamptz (nullable) | Null while held |

**Slot acquisition logic:**
```
1. SELECT COUNT(*) FROM execution_slots 
   WHERE environment_id = X AND released_at IS NULL
2. If count < environment.max_execution_slots:
   INSERT slot, start run
3. Else:
   Run stays queued. UI shows position.
```

**Reaper process** (runs every 60 seconds):
- Finds slots held > `max_execution_time_sec` of the associated run
- Releases the slot, marks the run as failed with "execution timeout"
- Finds runs with status = 'running' but no worker heartbeat for 5+ minutes
- Marks as failed with "worker timeout", releases slot

**Queue ordering:**
Queued runs are picked in order: `priority DESC, queued_at ASC`.
Critical > high > normal. Within same priority, first-come-first-served.

### 4.9 worker_heartbeats

Dead worker detection.

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| worker_id | varchar(100) UNIQUE | hostname + pid |
| status | varchar(20) | alive, dead |
| current_run_id | FK → pipeline_runs (nullable) | |
| current_stage | varchar(50) | |
| last_heartbeat | timestamptz | Updated every 30 seconds |
| started_at | timestamptz | |

---

## 5. INTELLIGENCE LAYER

### 5.1 entity_dependencies

Deterministic relationships extracted from relational metadata. Precursor to Neo4j graph.

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| meta_version_id | FK → meta_versions | |
| source_entity | varchar(255) | e.g. "Flow.LeadConvertProcess" |
| source_type | varchar(30) | flow, trigger, validation_rule, process_builder, workflow_rule |
| target_entity | varchar(255) | e.g. "Contact" or "Opportunity.Amount" |
| dependency_type | varchar(20) | creates, updates, reads, deletes, validates |
| discovery_source | varchar(20) | metadata_parse, execution_trace, inferred, manual |
| confidence | float | 1.0 for metadata_parse, lower for inferred. Default 1.0 |

**Discovery sources:**
- `metadata_parse` — extracted directly from Salesforce metadata (flow definitions, trigger events, VR formulas). Confidence 1.0. These are facts.
- `execution_trace` — observed during test execution (e.g. a trigger created a record we didn't expect). Confidence 0.8-0.9. These are observed.
- `inferred` — LLM inferred from patterns (e.g. "this trigger probably updates Contact based on naming convention"). Confidence 0.5-0.7. These need validation.
- `manual` — entered by a user or BA. Confidence 1.0.

This is the bridge to your self-improving graph and eventual Neo4j migration. High-confidence edges from metadata parsing form the backbone. Execution traces add observed edges. Over time the graph learns the org's actual behaviour, not just its declared configuration.

**Example rows for Lead conversion:**

| source_entity | source_type | target_entity | dependency_type |
|---------------|-------------|---------------|-----------------|
| Flow.LeadConvertProcess | flow | Contact | creates |
| Flow.LeadConvertProcess | flow | Opportunity | creates |
| Flow.LeadConvertProcess | flow | Account | creates |
| Trigger.LeadTrigger | trigger | Lead.Status | updates |
| ValidationRule.RequireCompany | validation_rule | Lead.Company | validates |

When the LLM generates a Lead conversion test, it traverses these edges to know deterministically what will be created, not guess via similarity search.

### 5.2 explanation_requests

Structured contract between execution data and LLM. No freeform prompts.

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| run_test_result_id | FK → run_test_results | |
| run_step_result_id | FK → run_step_results (nullable) | Specific failed step |
| explanation_type | varchar(30) | failure_analysis, root_cause, impact_assessment, anomaly_detection |
| structured_input | jsonb | Assembled context (see schema below) |
| llm_response | jsonb | Raw LLM output |
| parsed_explanation | jsonb | Structured result (see schema below) |
| model_used | varchar(50) | claude-sonnet-4-20250514 etc. |
| prompt_tokens | integer | |
| completion_tokens | integer | |
| requested_at | timestamptz | |
| completed_at | timestamptz | |

**`structured_input` schema (the explanation contract):**
```json
{
  "failure_context": {
    "step_order": 5,
    "step_action": "update",
    "target_object": "Opportunity",
    "error_message": "FIELD_CUSTOM_VALIDATION_EXCEPTION: Amount is required when Stage is Closed Won",
    "api_request": { "method": "PATCH", "url": "/sobjects/Opportunity/006xxx", "body": {"StageName": "Closed Won"} },
    "api_response": { "status": 400, "body": [...] },
    "before_state": { "StageName": "Prospecting", "Amount": null },
    "after_state": null
  },
  "related_metadata": {
    "validation_rules": [
      { "rule_name": "RequireAmount", "formula": "AND(ISPICKVAL(StageName, 'Closed Won'), ISBLANK(Amount))", "error_message": "Amount is required for Closed Won opportunities" }
    ],
    "active_triggers": [],
    "active_flows": []
  },
  "entity_dependencies": [
    { "source": "ValidationRule.RequireAmount", "target": "Opportunity.Amount", "type": "validates" }
  ],
  "prior_failures_same_run": [
    { "step": 3, "error": "...", "was_related": false }
  ],
  "historical_pattern": {
    "pattern_signature": "abc123",
    "occurrence_count": 7,
    "description": "RequireAmount validation blocks stage progression when Amount is null"
  },
  "extensions": {}
}
```

**`parsed_explanation` schema:**
```json
{
  "root_cause": "ValidationRule.RequireAmount blocks Stage update to Closed Won when Amount is null",
  "root_cause_entity": "ValidationRule.Opportunity.RequireAmount",
  "fix_suggestion": "Set Amount field before updating Stage to Closed Won",
  "affected_steps": [5],
  "related_test_cases": [12, 45, 78],
  "confidence": 0.95,
  "reasoning_chain": [
    "Step 5 attempted to update Opportunity.StageName to 'Closed Won'",
    "Opportunity.Amount was null (confirmed by before_state)",
    "ValidationRule.RequireAmount fires when StageName = 'Closed Won' AND Amount IS BLANK",
    "This is a deterministic match — the validation rule formula exactly matches the failure condition"
  ],
  "extensions": {}
}
```

**Explanation cost optimization (LLM is fallback, not default):**

Every failure triggering an LLM call creates cost and latency. The system should exhaust cheaper options first:

```
when step fails:
  1. Check failure_patterns for matching pattern_signature
     if match found AND confidence > 0.5:
       → return cached explanation from pattern
       → no LLM call, no cost
       → log as "pattern_matched" explanation

  2. Check if failure is deterministic from metadata alone
     (e.g. validation rule formula exactly matches the error)
     if deterministic:
       → generate explanation from metadata + template
       → no LLM call
       → log as "deterministic" explanation

  3. Fallback: assemble structured_input, call LLM
     → log as "llm_generated" explanation
     → check if result matches existing pattern → update pattern
     → check if result creates new pattern → insert pattern
```

This means most recurring failures never hit the LLM after the first occurrence. Only novel failures or ambiguous cases require an LLM call. At scale with a mature failure_patterns table, the system could handle 80%+ of failures without any LLM cost.

### 5.3 failure_patterns

Recurring failure signatures detected across runs. Includes decay mechanism so stale patterns don't dominate.

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| tenant_id | FK → tenants | |
| environment_id | FK → environments (nullable) | Null if cross-environment |
| pattern_signature | varchar(64) | Hash of failure characteristics |
| failure_type | varchar(30) | validation_rule, metadata_mismatch, etc. |
| root_entity | varchar(255) | e.g. "ValidationRule.RequireAmount" |
| description | text | Human-readable pattern description |
| occurrence_count | integer | |
| confidence | float | Starts at 1.0, decays over time. Default 1.0 |
| affected_test_case_ids | jsonb | Array of test case IDs |
| status | varchar(20) | active, decayed, resolved. Default: active |
| first_seen | timestamptz | |
| last_seen | timestamptz | |
| last_validated_at | timestamptz | Last time this pattern was confirmed by a new failure |

**Decay mechanism:**
- Confidence decays by 0.1 every 7 days without a new occurrence
- When confidence drops below 0.3, status moves to `decayed`
- `decayed` patterns are excluded from explanation lookups and LLM context
- When a new failure matches a decayed pattern, confidence resets to 1.0 and status returns to `active`
- Admin can manually mark a pattern as `resolved` (e.g. after a fix is deployed)

**Pattern signature computation:**
Hash of: `failure_type` + `root_entity` + `target_object` + `error_message_normalized`

When a new failure matches an existing pattern, `occurrence_count` increments and `affected_test_case_ids` expands. When the system recognises a known pattern, it skips the LLM call and returns the cached explanation — saving cost and time.

### 5.4 behaviour_facts

Learned and seeded facts about Salesforce entity behaviour, scoped to environments.

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| tenant_id | FK → tenants | |
| environment_id | FK → environments | |
| entity_ref | varchar(255) | e.g. "Opportunity.Stage", "Lead.Convert" |
| fact_type | varchar(30) | constraint, default, side_effect, sequence, dependency |
| fact_description | text | |
| source | varchar(20) | seeded, learned, ba_feedback, execution_trace |
| confidence | float | 0.0 to 1.0 |
| is_active | boolean | |
| learned_at | timestamptz | |

### 5.5 step_causal_links

Explicit causal chains between steps within a test execution. Makes "Step 3 caused Step 5 to fail" a first-class data structure rather than something the LLM infers.

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| run_test_result_id | FK → run_test_results | |
| from_step_result_id | FK → run_step_results | The causing step |
| to_step_result_id | FK → run_step_results | The affected step |
| link_type | varchar(30) | data_dependency, trigger_cascade, validation_block, state_mutation, cleanup_dependency |
| reason | text | e.g. "Step 3 set Amount to null; Step 5 failed because VR requires Amount for Closed Won" |
| confidence | float | 1.0 for deterministic links, lower for inferred |
| discovery_source | varchar(20) | execution_trace, metadata_analysis, llm_inferred |
| created_at | timestamptz | |

**Link types:**
- `data_dependency` — Step B reads a field that Step A wrote. If Step A wrote a bad value, Step B fails.
- `trigger_cascade` — Step A created a record that triggered a flow/trigger which affected Step B's target.
- `validation_block` — Step A's action left the record in a state where Step B's action is blocked by a validation rule.
- `state_mutation` — Step A changed a field (e.g. Stage) that altered the available transitions for Step B.
- `cleanup_dependency` — cleanup of Step B's record depends on Step A's record being deleted first.

**How links are created:**
1. After each failed step, the system checks if any previous step in the same test modified the same record or a related field
2. If the failed step's `before_state` shows a field value that was set by a previous step (traceable via `field_diff`), a causal link is created with `discovery_source = execution_trace` and confidence 1.0
3. For less obvious links (e.g. trigger cascades), the explanation LLM can suggest causal links which are stored with `discovery_source = llm_inferred` and lower confidence

Over time, these links feed back into the explanation layer: when Step 5 fails and has a high-confidence causal link to Step 3, the `structured_input` for the explanation includes this link, making the LLM's reasoning faster and more accurate.

---

## 6. VECTOR STORE (SCOPED)

pgvector stays. Only used for genuinely unstructured content. All org metadata is now relational.

### 6.1 embeddings

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| tenant_id | FK → tenants | |
| environment_id | FK → environments (nullable) | Null for tenant-wide docs |
| content_type | varchar(30) | jira_description, jira_comment, confluence_doc, bug_report, ba_feedback |
| source_id | varchar(255) | Jira key, doc URL, etc. |
| content_text | text | Original text |
| embedding | vector(1024) | Voyage AI voyage-3 |
| created_at | timestamptz | |

**Every similarity search MUST include:**
```sql
WHERE tenant_id = :tenant_id
AND (environment_id = :env_id OR environment_id IS NULL)
ORDER BY embedding <=> :query_vector
LIMIT 10
```

---

## 7. INFRASTRUCTURE

### 7.1 Code domain modules (non-negotiable boundary enforcement)

The schema has 6 domains. The codebase must mirror this structure. Without boundaries, 35 tables become an unmaintainable monolith.

```
primeqa/
├── core/                      # tenants, users, auth, activity
│   ├── models.py              # SQLAlchemy models
│   ├── service.py             # business logic
│   ├── repository.py          # DB queries
│   └── routes.py              # API endpoints
├── metadata/                  # meta_versions, objects, fields, VRs, flows, triggers
│   ├── models.py
│   ├── service.py             # refresh, diff, impact analysis
│   ├── repository.py
│   └── routes.py
├── test_management/           # sections, requirements, test_cases, versions, suites, reviews
│   ├── models.py
│   ├── service.py
│   ├── repository.py
│   └── routes.py
├── execution/                 # runs, stages, step_results, artifacts, cleanup, slots
│   ├── models.py
│   ├── service.py             # pipeline orchestration
│   ├── executor.py            # step execution engine
│   ├── cleanup.py             # cleanup + reconciliation
│   ├── repository.py
│   └── routes.py
├── intelligence/              # entity_deps, explanations, patterns, behaviour_facts
│   ├── models.py
│   ├── service.py             # explanation assembly, pattern detection
│   ├── repository.py
│   └── routes.py
├── vector/                    # embeddings, RAG search
│   ├── models.py
│   ├── service.py
│   └── repository.py
├── worker.py                  # background worker entrypoint
├── scheduler.py               # reaper/scheduler entrypoint
└── app.py                     # Flask web entrypoint
```

**Rules:**
- Each domain imports only from its own `repository.py` for DB access
- Cross-domain calls go through service layers, never direct SQL across domains
- e.g. execution needs metadata → calls `metadata.service.get_current_version(env_id)`, not a raw query on meta_versions
- This is what makes 36 tables manageable — each module owns ~6 tables and nothing else

### 7.2 Railway services (3 processes, same codebase)

**Web (Flask)**
- Serves API and UI
- Stateless — all state in PostgreSQL
- Handles JWT auth, role checks
- Writes jobs to pipeline_runs, reads status
- SSE streaming for live run monitor
- Can scale horizontally (2+ instances behind load balancer)

**Worker**
- Polls pipeline_runs for queued jobs (respects priority + slot availability)
- Executes pipeline stages independently
- Writes heartbeat every 30 seconds
- Checks cancellation_token between steps
- Loads per-environment credentials for each run
- Start with 1 worker, scale to 2-3 for concurrent runs

**Scheduler/reaper**
- Runs on timer (APScheduler), not HTTP
- Dead job reaper: every 60 seconds
- Slot reaper: release stuck slots
- Jira sync: check for requirement staleness
- Metadata refresh scheduling (if configured)
- Failure pattern decay: nightly confidence recalculation
- Metadata version archival: nightly lifecycle check

### 7.3 Database sizing (PostgreSQL on Railway)

For a single tenant with 20 users, expected volumes in first 6 months:

| Table | Estimated rows | Growth pattern |
|-------|---------------|----------------|
| meta_fields | ~500 per version × ~50 versions = 25K | Per metadata refresh |
| run_step_results | ~10 steps × ~20 tests × ~5 runs/day = 1K/day | Per execution |
| run_created_entities | ~3 per test × ~100 tests/day = 300/day | Per execution |
| embeddings | ~2K initial + ~100/week | Per Jira import |
| test_case_versions | ~500 initial + ~50/week | Per generation cycle |

All comfortably within Railway's PostgreSQL tier. No sharding needed for 1-2 years.

---

## 8. CROSS-TABLE RELATIONSHIP SUMMARY

Key foreign key paths that span domains:

```
pipeline_runs.environment_id → environments.id
environments.current_meta_version_id → meta_versions.id
run_test_results.test_case_version_id → test_case_versions.id
test_case_versions.metadata_version_id → meta_versions.id
meta_versions.environment_id → environments.id
metadata_impacts.test_case_id → test_cases.id
metadata_impacts.new_meta_version_id → meta_versions.id
entity_dependencies.meta_version_id → meta_versions.id
explanation_requests.run_step_result_id → run_step_results.id
run_created_entities.run_step_result_id → run_step_results.id
run_created_entities.parent_entity_id → run_created_entities.id
run_cleanup_attempts.run_created_entity_id → run_created_entities.id
step_causal_links.from_step_result_id → run_step_results.id
step_causal_links.to_step_result_id → run_step_results.id
```

---

## 9. TOTAL TABLE COUNT

| Domain | Tables | Count |
|--------|--------|-------|
| Core platform | tenants, users, refresh_tokens, environments, environment_credentials, activity_log | 6 |
| Relational metadata | meta_versions, meta_objects, meta_fields, meta_validation_rules, meta_flows, meta_triggers, meta_record_types | 7 |
| Test management | sections, requirements, test_cases, test_case_versions, test_suites, suite_test_cases, ba_reviews, metadata_impacts | 8 |
| Execution engine | pipeline_runs, pipeline_stages, run_test_results, run_step_results, run_artifacts, run_created_entities, run_cleanup_attempts, execution_slots, worker_heartbeats | 9 |
| Intelligence | entity_dependencies, explanation_requests, failure_patterns, behaviour_facts, step_causal_links | 5 |
| Vector store | embeddings | 1 |
| **Total** | | **36** |

---

## 10. BUILD SEQUENCE

Once this spec is approved, the implementation order:

1. **Database migration** — create all 36 tables with indexes and constraints
2. **Domain module scaffold** — set up core/, metadata/, test_management/, execution/, intelligence/, vector/ with models, services, repositories
3. **Auth module** — users, JWT, roles, middleware
4. **Environment management** — CRUD, credential vault, connection testing, capture mode config
5. **Metadata refresh** — relational storage, versioning, diffing, lifecycle archival
6. **Test management** — sections, requirements, test cases, versions, suites, BA reviews
7. **Pipeline refactor** — stage isolation, background worker, slot management, queue priority, cancellation
8. **Execution engine** — adaptive step-level capture, step execution state, partial step recovery, idempotency with state reconciliation, cleanup tracking with lineage, PQA_ naming convention enforcement
9. **Intelligence layer** — entity dependencies, explanation contract with pattern-first/LLM-fallback, failure patterns with decay, step causal links
10. **UI updates** — multi-user views, run queue, BA review queue, impact dashboard

---

## 11. FUTURE ROADMAP (planned, not built now)

### Test data strategy

Data isolation naming convention (PQA_ prefix) is now built into the execution engine — every record PrimeQA creates is identifiable and manually cleanable. The remaining test data improvements are planned for post-pilot:

- **Test data templates** — reusable data sets per object type (e.g. "standard Opportunity" with all required fields pre-filled)
- **Synthetic data layer** — generate realistic but safe test data (no PII, no real account names)
- **Reusable fixtures** — named data sets that persist across runs (e.g. "test account for regression") instead of create/destroy every time

### Neo4j migration

When entity_dependencies exceed ~10K edges per environment and graph traversal queries in PostgreSQL become the bottleneck, migrate the entity_dependencies and behaviour_facts tables to Neo4j. The relational tables serve as the staging ground — same data model, different engine.

### Multi-tenant

Current schema is multi-tenant ready (tenant_id on every table). Enabling multi-tenancy requires: tenant provisioning flow, billing integration, cross-tenant isolation testing, and a shared vs dedicated database decision.
