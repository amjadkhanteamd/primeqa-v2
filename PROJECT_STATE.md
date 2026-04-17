# PrimeQA v2 — Project State

## Deployment
- **Live URL**: https://primeqa-v2-production.up.railway.app
- **Login**: admin@primeqa.io / changeme123
- **Railway project**: giving-enthusiasm
- **Region**: europe-west4
- **Services**: primeqa-v2 (web), worker, scheduler, Postgres

## Build Progress

### Completed (Steps 7–16 + Admin Setup)

| Step | Feature | Tests | Status |
|------|---------|-------|--------|
| 7 | Domain scaffold — 37 files, 6 modules, 36 models | import OK | Done |
| 8 | Auth — JWT, bcrypt, roles, 20-user limit | 15/15 | Done |
| 9 | Environments — CRUD, Fernet encryption, connection test | 14/14 | Done |
| 10 | Metadata refresh — SF fetch, diffing, archival | 10/10 | Done |
| 11 | Test management — sections, requirements, suites, BA reviews | 23/23 | Done |
| 12 | Pipeline — worker, slots, queue priority, reaper, cancellation | 12/12 | Done |
| 13 | Execution engine — adaptive capture, idempotency, PQA_ naming | 15/15 | Done |
| 14 | Cleanup — reverse deletion, dependency retry, production safety | 9/9 | Done |
| 15 | Intelligence — pattern-first explanations, causal links, decay | 11/11 | Done |
| 16 | Web UI — dashboard, runs, test library, BA reviews, admin | renders OK | Done |
| — | Railway deployment — Dockerfile, 3 services, health check | deployed | Done |
| — | Admin setup — connections, groups, env visibility, setup wizard | 8 phases | Done |

**Total: 109 passing tests across 8 test suites**

### Database Tables (40)
Core (8): tenants, users, refresh_tokens, environments, environment_credentials, activity_log, groups, group_members, group_environments, connections
Metadata (7): meta_versions, meta_objects, meta_fields, meta_validation_rules, meta_flows, meta_triggers, meta_record_types
Test Management (8): sections, requirements, test_cases, test_case_versions, test_suites, suite_test_cases, ba_reviews, metadata_impacts
Execution (9): pipeline_runs, pipeline_stages, run_test_results, run_step_results, run_artifacts, run_created_entities, run_cleanup_attempts, execution_slots, worker_heartbeats
Intelligence (5): entity_dependencies, explanation_requests, failure_patterns, behaviour_facts, step_causal_links
Vector (1): embeddings

### API Endpoints (~60)
- Auth: login, refresh, logout, me, users CRUD
- Environments: CRUD, credentials, test-connection
- Connections: CRUD, test, Salesforce/Jira/LLM
- Groups: CRUD, members, environments
- Metadata: refresh, current, diff, impacts
- Test Management: sections, requirements, test cases, versions, suites, reviews
- Execution: runs CRUD, cancel, queue, slots, results, cleanup
- Intelligence: dependencies, explanations, patterns, causal links, facts

### Web UI Pages (~25)
- /login, /logout
- / (dashboard with setup banner)
- /setup (wizard)
- /runs, /runs/new, /runs/{id}
- /test-cases, /test-cases/{id}
- /reviews, /reviews/{id}
- /connections, /connections/new, /connections/{id}, /connections/{id}/edit
- /environments, /environments/new, /environments/{id}
- /groups, /groups/new, /groups/{id}
- /users, /users/new
- /impacts

## What's Next (Potential)
- [ ] Connect to a real Salesforce org end-to-end
- [ ] Test case creation forms in the UI
- [ ] Jira import UI flow
- [ ] Run detail: before/after state diffs, explanation display
- [ ] Suite management UI
- [ ] Pattern management page
- [ ] Dependency graph visualization
- [ ] Version diff viewer for test cases
- [ ] SSE streaming for live run monitoring
- [ ] Playwright screenshot integration

## Known Issues
- Health check disabled on Railway (was blocking deploys)
- Tests create data in the shared Railway DB (need cleanup between runs)
- No automated test runner (tests run manually)
- bcrypt rounds=12 makes test setup slow over remote DB
