# PrimeQA Build Execution Plan

## How to use this file

1. Open Claude Code in your primeqa project root
2. Run: `cat PRIMEQA_ARCHITECTURE_SPEC_v2.2.md` so Claude Code has the full spec in context
3. Then paste each step below one at a time
4. Verify the checklist before moving to the next step
5. If a step fails, share the error with Claude Code and ask it to fix before moving on

---

## STEP 1: Database migration — Core platform tables

```
Read the PRIMEQA_ARCHITECTURE_SPEC_v2.2.md file for reference.

Create a SQL migration file at migrations/001_core_platform.sql that creates these tables with all columns, types, constraints, and indexes exactly as defined in sections 1.1 through 1.6 of the spec:

- tenants
- users (with UNIQUE on tenant_id + email)
- refresh_tokens
- environments (including capture_mode, current_meta_version_id, cleanup_mandatory)
- environment_credentials (one-to-one with environments)
- activity_log

Include:
- All indexes defined in the spec
- A seed INSERT for a default tenant with slug 'default'
- A seed INSERT for an admin user (email: admin@primeqa.io, password: changeme123, role: admin, tenant_id: 1)
- Use bcrypt for the password hash — generate it in the migration or document the hash

Do NOT run the migration. Just create the file.
```

**Verify before moving on:**
- [ ] File exists at migrations/001_core_platform.sql
- [ ] All 6 tables present with correct columns
- [ ] environments has capture_mode, current_meta_version_id, cleanup_mandatory columns
- [ ] Default tenant and admin user seeded

---

## STEP 2: Database migration — Relational metadata tables

```
Read the PRIMEQA_ARCHITECTURE_SPEC_v2.2.md sections 2.1 through 2.7.

Create migrations/002_relational_metadata.sql that creates:

- meta_versions (including lifecycle column with default 'active')
- meta_objects (with UNIQUE on meta_version_id + api_name)
- meta_fields (with UNIQUE on meta_version_id + meta_object_id + api_name)
- meta_validation_rules
- meta_flows
- meta_triggers
- meta_record_types

Include all indexes. Add the FK from environments.current_meta_version_id to meta_versions.id as an ALTER TABLE (since meta_versions didn't exist in step 1).

Do NOT run the migration. Just create the file.
```

**Verify before moving on:**
- [ ] File exists at migrations/002_relational_metadata.sql
- [ ] All 7 tables present
- [ ] UNIQUE constraints on meta_objects and meta_fields
- [ ] ALTER TABLE for environments.current_meta_version_id FK
- [ ] meta_versions has lifecycle column

---

## STEP 3: Database migration — Test management tables

```
Read the PRIMEQA_ARCHITECTURE_SPEC_v2.2.md sections 3.1 through 3.8.

Create migrations/003_test_management.sql that creates:

- sections (with self-referencing parent_id FK)
- requirements (with UNIQUE on tenant_id + jira_key WHERE jira_key IS NOT NULL)
- test_cases (with CHECK constraint: requirement_id IS NOT NULL OR section_id IS NOT NULL)
- test_case_versions (with referenced_entities JSONB column, metadata_version_id FK)
- test_suites
- suite_test_cases (with UNIQUE on suite_id + test_case_id)
- ba_reviews
- metadata_impacts

Include the optimistic concurrency trigger on test_cases:
- Create the bump_version() function
- Create the trigger on test_cases

Do NOT run the migration. Just create the file.
```

**Verify before moving on:**
- [ ] File exists at migrations/003_test_management.sql
- [ ] All 8 tables present
- [ ] sections has self-referencing parent_id
- [ ] test_cases has CHECK constraint
- [ ] test_case_versions has referenced_entities and metadata_version_id
- [ ] bump_version() trigger created

---

## STEP 4: Database migration — Execution engine tables

```
Read the PRIMEQA_ARCHITECTURE_SPEC_v2.2.md sections 4.1 through 4.9.

Create migrations/004_execution_engine.sql that creates:

- pipeline_runs (with priority, max_execution_time_sec, cancellation_token columns)
- pipeline_stages
- run_test_results
- run_step_results (with execution_state column, before_state, after_state, field_diff, api_request, api_response)
- run_artifacts
- run_created_entities (with logical_identifier, primeqa_idempotency_key, creation_fingerprint, parent_entity_id self-ref)
- run_cleanup_attempts
- execution_slots
- worker_heartbeats

Include all indexes from the spec, especially:
- pipeline_runs: (tenant_id, status), (environment_id, status) WHERE status IN ('queued','running'), (status, priority, queued_at) WHERE status = 'queued'
- run_step_results: index on (run_test_result_id)
- execution_slots: index on (environment_id) WHERE released_at IS NULL

Also create these views:
- v_run_queue: queued runs with position, triggered_by_name
- v_active_runs: running jobs with current stage info

Do NOT run the migration. Just create the file.
```

**Verify before moving on:**
- [ ] File exists at migrations/004_execution_engine.sql
- [ ] All 9 tables present
- [ ] run_step_results has execution_state column
- [ ] run_created_entities has logical_identifier, creation_fingerprint, parent_entity_id
- [ ] pipeline_runs has priority and cancellation_token
- [ ] All partial indexes created
- [ ] Both views created

---

## STEP 5: Database migration — Intelligence layer and vector store

```
Read the PRIMEQA_ARCHITECTURE_SPEC_v2.2.md sections 5.1 through 5.5 and section 6.1.

Create migrations/005_intelligence_and_vector.sql that creates:

- entity_dependencies (with discovery_source, confidence columns)
- explanation_requests
- failure_patterns (with confidence, status, last_validated_at columns for decay mechanism)
- behaviour_facts
- step_causal_links (with from_step_result_id, to_step_result_id, link_type, confidence, discovery_source)
- embeddings (with pgvector extension: CREATE EXTENSION IF NOT EXISTS vector)

Include the pgvector index on embeddings:
- CREATE INDEX ON embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)
- Index on (tenant_id, environment_id) for filtering

Do NOT run the migration. Just create the file.
```

**Verify before moving on:**
- [ ] File exists at migrations/005_intelligence_and_vector.sql
- [ ] All 6 tables present (5 intelligence + 1 vector)
- [ ] entity_dependencies has discovery_source and confidence
- [ ] failure_patterns has confidence, status, last_validated_at
- [ ] step_causal_links has all columns from spec
- [ ] pgvector extension enabled
- [ ] Vector index created

---

## STEP 6: Run all migrations

```
Connect to the Railway PostgreSQL database and run all 5 migration files in order:

psql $DATABASE_URL -f migrations/001_core_platform.sql
psql $DATABASE_URL -f migrations/002_relational_metadata.sql
psql $DATABASE_URL -f migrations/003_test_management.sql
psql $DATABASE_URL -f migrations/004_execution_engine.sql
psql $DATABASE_URL -f migrations/005_intelligence_and_vector.sql

After running, verify by listing all tables:
psql $DATABASE_URL -c "\dt"

Expected: 36 tables plus 2 views.

If any migration fails, fix the SQL error and re-run only the failed file.
```

**Verify before moving on:**
- [ ] All 5 migrations ran without errors
- [ ] `\dt` shows 36 tables
- [ ] Default tenant exists: `SELECT * FROM tenants;`
- [ ] Admin user exists: `SELECT id, email, role FROM users;`
- [ ] Views work: `SELECT * FROM v_run_queue;` (should return empty)

---

## STEP 7: Domain module scaffold

```
Read the PRIMEQA_ARCHITECTURE_SPEC_v2.2.md section 7.1 for the code domain module structure.

Create the following directory structure and empty files. Each file should have a docstring explaining its purpose and the tables it owns. Do not implement any logic yet — just the scaffold.

primeqa/
├── __init__.py
├── core/
│   ├── __init__.py
│   ├── models.py          # SQLAlchemy models for: tenants, users, refresh_tokens, environments, environment_credentials, activity_log
│   ├── service.py         # Business logic: user management, auth, tenant operations
│   ├── repository.py      # DB queries scoped to core tables only
│   └── routes.py          # API endpoints: /api/auth/*, /api/users/*, /api/environments/*
├── metadata/
│   ├── __init__.py
│   ├── models.py          # SQLAlchemy models for: meta_versions, meta_objects, meta_fields, meta_validation_rules, meta_flows, meta_triggers, meta_record_types
│   ├── service.py         # Business logic: refresh, diff, impact analysis, lifecycle archival
│   ├── repository.py      # DB queries scoped to metadata tables only
│   └── routes.py          # API endpoints: /api/metadata/*
├── test_management/
│   ├── __init__.py
│   ├── models.py          # SQLAlchemy models for: sections, requirements, test_cases, test_case_versions, test_suites, suite_test_cases, ba_reviews, metadata_impacts
│   ├── service.py         # Business logic: CRUD, versioning, Jira sync, stale detection
│   ├── repository.py      # DB queries scoped to test management tables only
│   └── routes.py          # API endpoints: /api/sections/*, /api/requirements/*, /api/test-cases/*, /api/suites/*, /api/reviews/*
├── execution/
│   ├── __init__.py
│   ├── models.py          # SQLAlchemy models for: pipeline_runs, pipeline_stages, run_test_results, run_step_results, run_artifacts, run_created_entities, run_cleanup_attempts, execution_slots, worker_heartbeats
│   ├── service.py         # Business logic: pipeline orchestration, queue management, slot acquisition
│   ├── executor.py        # Step execution engine: adaptive capture, before/after state, PQA_ naming
│   ├── cleanup.py         # Cleanup engine: reverse-order deletion, lineage tracking, reconciliation
│   ├── idempotency.py     # Idempotency: key management, state reconciliation, trigger detection
│   ├── repository.py      # DB queries scoped to execution tables only
│   └── routes.py          # API endpoints: /api/runs/*, /api/results/*
├── intelligence/
│   ├── __init__.py
│   ├── models.py          # SQLAlchemy models for: entity_dependencies, explanation_requests, failure_patterns, behaviour_facts, step_causal_links
│   ├── service.py         # Business logic: explanation assembly (pattern-first/LLM-fallback), pattern detection, decay
│   ├── repository.py      # DB queries scoped to intelligence tables only
│   └── routes.py          # API endpoints: /api/explanations/*, /api/patterns/*
├── vector/
│   ├── __init__.py
│   ├── models.py          # SQLAlchemy models for: embeddings
│   ├── service.py         # Business logic: RAG search with tenant+environment scoping
│   └── repository.py      # DB queries scoped to embeddings table only
├── worker.py              # Background worker entrypoint — polls pipeline_runs, executes stages
├── scheduler.py           # Reaper/scheduler entrypoint — dead jobs, slot reaper, pattern decay, metadata archival
└── app.py                 # Flask web entrypoint — registers all route blueprints

Rules to follow:
- Each models.py should define SQLAlchemy model classes matching the migration schemas
- Each repository.py should have a class with a get_db() dependency and methods stubbed as pass
- Each service.py should have a class with methods stubbed as pass
- Each routes.py should define a Flask Blueprint with route stubs returning 501 "Not Implemented"
- app.py should register all blueprints and set up the Flask app

Create all files with proper docstrings and stubs.
```

**Verify before moving on:**
- [ ] All directories and files created
- [ ] Each models.py has SQLAlchemy model classes with correct table names
- [ ] Each routes.py has a Blueprint registered
- [ ] app.py imports and registers all blueprints
- [ ] `python -c "from primeqa.app import app; print('OK')"` runs without import errors

---

## STEP 8: Auth module — full implementation

```
Read the PRIMEQA_ARCHITECTURE_SPEC_v2.2.md sections 1.2 (users table) and 1.3 (refresh_tokens).

Implement the complete auth module in primeqa/core/:

1. repository.py — implement these methods:
   - get_user_by_email(tenant_id, email)
   - get_user_by_id(user_id)
   - create_user(tenant_id, email, password_hash, full_name, role)
   - update_user(user_id, updates)
   - list_users(tenant_id)
   - count_active_users(tenant_id)
   - create_refresh_token(user_id, token_hash, expires_at)
   - get_refresh_token(token_hash)
   - revoke_refresh_token(token_id)
   - revoke_all_user_tokens(user_id)

2. service.py — implement:
   - login(email, password) → {access_token, refresh_token, user}
   - refresh(raw_refresh_token) → {access_token, refresh_token}
   - logout(user_id) — revokes all refresh tokens
   - create_user(tenant_id, email, password, full_name, role) — enforces 20-user limit
   - update_user(user_id, role, is_active) — admin only
   - list_users(tenant_id)

3. routes.py — implement all endpoints:
   - POST /api/auth/login
   - POST /api/auth/refresh
   - POST /api/auth/logout (requires auth)
   - GET /api/auth/me (requires auth)
   - GET /api/auth/users (admin only)
   - POST /api/auth/users (admin only)
   - PATCH /api/auth/users/<user_id> (admin only)

4. Create a middleware decorator require_auth that:
   - Extracts Bearer token from Authorization header
   - Decodes JWT, validates expiry
   - Sets request.user = {id, tenant_id, email, role, full_name}
   - Returns 401 on invalid/expired token with code TOKEN_EXPIRED for expired

5. Create a require_role(*roles) decorator that chains with require_auth

Use these dependencies:
- pip install PyJWT bcrypt
- JWT_SECRET from environment variable (with a dev fallback)
- ACCESS_TOKEN_EXPIRY = 30 minutes
- REFRESH_TOKEN_EXPIRY = 7 days

Write a test script at tests/test_auth.py that:
- Creates a user
- Logs in
- Verifies the token works on /api/auth/me
- Refreshes the token
- Tests role-based access (admin vs tester)
```

**Verify before moving on:**
- [ ] Can create admin user via seed or POST
- [ ] POST /api/auth/login returns access_token and refresh_token
- [ ] GET /api/auth/me with valid token returns user info
- [ ] GET /api/auth/me with expired/invalid token returns 401
- [ ] POST /api/auth/users enforces 20-user limit
- [ ] Role-based access works (tester can't hit admin endpoints)
- [ ] tests/test_auth.py passes

---

## STEP 9: Environment management — full implementation

```
Read the PRIMEQA_ARCHITECTURE_SPEC_v2.2.md sections 1.4 and 1.5.

Implement environment management in primeqa/core/ (it's part of core domain):

1. repository.py — add these methods:
   - create_environment(tenant_id, name, env_type, sf_instance_url, sf_api_version, execution_policy, capture_mode, max_execution_slots, cleanup_mandatory)
   - get_environment(environment_id, tenant_id) — tenant-scoped
   - list_environments(tenant_id)
   - update_environment(environment_id, updates)
   - store_credentials(environment_id, client_id, client_secret, access_token, refresh_token)
   - get_credentials(environment_id) — decrypted

2. service.py — add:
   - create_environment(...) — validates env_type, execution_policy, capture_mode
   - update_environment(...) — admin only
   - test_connection(environment_id) — uses stored credentials to call Salesforce /services/data/vXX.0/ and returns success/failure
   - refresh_sf_token(environment_id) — refreshes OAuth token if expired
   - list_environments(tenant_id)

3. routes.py — add endpoints:
   - GET /api/environments (list all for tenant)
   - POST /api/environments (admin only — create)
   - PATCH /api/environments/<id> (admin only — update)
   - POST /api/environments/<id>/test-connection (admin only)
   - POST /api/environments/<id>/credentials (admin only — store/update credentials)

4. Credential encryption:
   - Use Fernet symmetric encryption from the cryptography library
   - Encryption key from CREDENTIAL_ENCRYPTION_KEY environment variable
   - Encrypt before storing, decrypt when reading

For test_connection, make a simple GET request to:
{sf_instance_url}/services/data/v{sf_api_version}/

If it returns 200, connection is valid.
```

**Verify before moving on:**
- [ ] Can create an environment via API
- [ ] Credentials are stored encrypted in the database (verify by querying raw DB)
- [ ] test_connection endpoint returns success when credentials are valid
- [ ] List environments returns only for the authenticated user's tenant
- [ ] Only admin role can create/update environments

---

## STEP 10: Metadata refresh — full implementation

```
Read the PRIMEQA_ARCHITECTURE_SPEC_v2.2.md sections 2.1 through 2.7 and the "How metadata diffing works" section.

Implement the metadata module in primeqa/metadata/:

1. repository.py — implement:
   - create_meta_version(environment_id, version_label)
   - complete_meta_version(version_id, snapshot_hash, counts)
   - get_current_version(environment_id) — uses environments.current_meta_version_id
   - get_previous_version(environment_id) — second most recent complete version
   - store_objects(meta_version_id, objects_list)
   - store_fields(meta_version_id, object_id, fields_list)
   - store_validation_rules(meta_version_id, object_id, rules_list)
   - store_flows(meta_version_id, flows_list)
   - store_triggers(meta_version_id, object_id, triggers_list)
   - store_record_types(meta_version_id, object_id, record_types_list)
   - diff_fields(old_version_id, new_version_id) — returns added, removed, changed fields
   - diff_validation_rules(old_version_id, new_version_id)
   - diff_flows(old_version_id, new_version_id)
   - archive_old_versions(environment_id, keep_count=20)

2. service.py — implement:
   - refresh_metadata(environment_id):
     a. Create new meta_version with status 'in_progress'
     b. Authenticate to Salesforce using environment credentials
     c. Call Salesforce Tooling API / describe to fetch: objects, fields, validation rules, flows, triggers, record types
     d. Store all metadata relationally
     e. Compute snapshot_hash
     f. If hash differs from previous version → run diff
     g. Update environments.current_meta_version_id
     h. Archive old versions beyond keep_count
     i. Return diff summary
   - run_impact_analysis(environment_id, new_version_id, old_version_id):
     a. Get diffs (fields, VRs, flows)
     b. For each change, query test_case_versions.referenced_entities to find affected test cases
     c. Create metadata_impacts rows
     d. Return count of affected test cases

3. routes.py — implement:
   - POST /api/metadata/<environment_id>/refresh (admin/tester — triggers refresh)
   - GET /api/metadata/<environment_id>/current (get current version summary)
   - GET /api/metadata/<environment_id>/diff (get diff between current and previous)
   - GET /api/metadata/<environment_id>/impacts (list pending metadata impacts)

For the Salesforce API calls, use the existing Salesforce credentials from the environment. The key API calls are:
- GET /services/data/vXX.0/sobjects/ — list all objects
- GET /services/data/vXX.0/sobjects/{object}/describe/ — get fields, record types
- GET /services/data/vXX.0/tooling/query/?q=SELECT+...+FROM+ValidationRule — get VRs
- GET /services/data/vXX.0/tooling/query/?q=SELECT+...+FROM+Flow — get flows
- GET /services/data/vXX.0/tooling/query/?q=SELECT+...+FROM+ApexTrigger — get triggers
```

**Verify before moving on:**
- [ ] Can trigger metadata refresh for a connected Salesforce environment
- [ ] meta_versions shows a new row with status 'complete'
- [ ] meta_objects, meta_fields, meta_validation_rules populated with real org data
- [ ] environments.current_meta_version_id points to the new version
- [ ] Running refresh again creates v2, and diff endpoint shows changes (if any)
- [ ] Impact analysis finds affected test cases (will be empty until test cases exist)

---

## STEPS 11-16: Remaining build steps

Once steps 1-10 are solid, continue with these. I'll give you the detailed prompts for each when you reach them:

**STEP 11:** Test management — sections, requirements, test cases, versions, suites, BA reviews, Jira import
**STEP 12:** Pipeline refactor — stage isolation, background worker, slot management, queue priority
**STEP 13:** Execution engine — adaptive step-level capture, step execution state, partial recovery, PQA_ naming, idempotency
**STEP 14:** Cleanup engine — reverse-order deletion, lineage tracking, cleanup attempts, production safety
**STEP 15:** Intelligence layer — entity dependencies, explanation contract (pattern-first/LLM-fallback), failure patterns with decay, step causal links
**STEP 16:** UI updates — multi-user views, run queue, BA review workspace, impact dashboard

---

## Tips for running in Claude Code

- Start each step by saying: "Read PRIMEQA_ARCHITECTURE_SPEC_v2.2.md and execute Step N from the build plan"
- If Claude Code asks clarifying questions, point it to the specific spec section
- After each step, run the verification checklist manually
- If something fails, share the exact error and ask Claude Code to fix it before moving on
- Don't skip steps — each step depends on the previous ones
- Commit to git after each successful step: `git add -A && git commit -m "Step N: description"`
