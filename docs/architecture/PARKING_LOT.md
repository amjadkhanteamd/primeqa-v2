# PrimeQA Architecture — Parking Lot

Ideas that are out of scope now but worth preserving. Each item MUST have an explicit "revisit when X" trigger. Items without triggers get removed on the next review — this list is not a wishlist.

When a trigger fires, move the item into OPEN_QUESTIONS.md or a substrate's SPEC.md as appropriate, and remove from here.

---

## P-001 — Scratch-org-per-test-run isolation

**Idea:** Instead of running tests against a shared sandbox with namespace-stamped cleanup, provision a scratch org per test run. Total isolation, no cleanup concerns, no cross-run contamination.

**Revisit when:** customer demand for deeper isolation emerges, OR Salesforce makes scratch-org provisioning fast enough for per-test-run use (<30 seconds), OR we start testing destructive operations that are hard to clean up.

---

## P-002 — Direct Apex method invocation as a test primitive

**Idea:** A `call_method` primitive that directly invokes an `@AuraEnabled` or `@InvocableMethod` Apex method, rather than triggering it via record changes. Discussed and cut during A4 design as overspecified for v1.

**Revisit when:** a paying customer explicitly requests testing a method that has no record-change trigger path, AND the workaround (creating records that trigger it indirectly) is proving unreliable.

---

## P-003 — Coverage tags on test scenarios

**Idea:** Attach structured coverage tags to test scenarios (`coverage_tags: ["field:Amount", "state:ClosedWon", "rule:Prevent_Discount_Over_50"]`) to enable gap analysis and smart suggestions ("here are fields with no test coverage").

**Revisit when:** Substrate 5 (Knowledge System) is being designed, OR customer-facing coverage dashboards become a priority.

---

## P-004 — Agentforce and Einstein Copilot testing

**Idea:** Dedicated test archetypes for AI features — verify Agentforce agents respond correctly to prompts, verify Copilot action chains produce correct outcomes, verify prompt templates generate valid output.

**Revisit when:** a pilot customer deploys Agentforce to production, OR Salesforce releases official APIs for deterministic testing of AI features, OR competitive pressure (Provar adds Agentforce support) forces the issue.

---

## P-005 — Cross-test-case dependencies

**Idea:** Allow test case B to depend on test case A's state (e.g., B runs only if A created a certain record). Discussed during A4 design, explicitly rejected as complexity-before-value.

**Revisit when:** customer evidence shows the workaround (duplicate setup in each test) is causing real friction, OR we encounter a test scenario that's genuinely impossible to express as independent cases.

---

## P-006 — Multi-user execution context

**Idea:** Tests that require multiple Salesforce user contexts in sequence (approver approves, requester requests, observer observes). Currently requires owner swapping via OwnerId which is not the same as "acting as this user."

**Revisit when:** we need to test approval processes end-to-end, OR a customer's permission tests require real multi-user execution (not just metadata assertions).

---

## P-007 — Learned test authoring patterns across tenants

**Idea:** Substrate 5 (Knowledge System) could learn test patterns across tenants — if many tenants test escalation flows a certain way, suggest that pattern to new tenants. Privacy-preserving aggregation only.

**Revisit when:** Substrate 5 is being designed, AND we have enough tenant data to make cross-tenant patterns meaningful, AND we've designed tenant-isolation guarantees (see Q-001).

---

## P-008 — Natural-language test editing

**Idea:** Let users edit generated tests by describing changes in natural language ("change the expected outcome to reflect the new approval step"). Substrate 7 (Conversation) feature.

**Revisit when:** Substrate 2 (Test Representation) is designed such that NL edits can be reliably applied, AND we have real usage showing where current editing UX falls short.

---

## P-009 — Scheduled periodic regression runs

**Idea:** Auto-run the full test suite nightly or weekly, flag regressions automatically, compare against baseline. Not a substrate question but a product feature that depends on S4 (execution) and S6 (interpretation).

**Revisit when:** execution engine reliability is high enough that scheduled runs produce actionable signal rather than noise.

---

## P-010 — Partial-sync resume e2e scenario (deferred from §25)

**Idea:** End-to-end scenario verifying the engine's resume-from-`last_completed_phase` code path: kill a sync mid-phase, restart, observe clean resumption without duplicates or orphans. The engine's resume path exists (see `_mark_sync_run_failed` + the run_sync orchestration that picks up `last_completed_phase`) and is unit-tested in `test_engine.py`. An e2e formalization would be redundant at this stage.

**Revisit when:** an engine refactor risks the resume path, OR a customer-reported bug surfaces in this area, OR the worker is parallelized across phases (changing resume semantics).

---

## P-011 — Bitemporal historical query e2e scenario (deferred from §25)

**Idea:** End-to-end scenario verifying "after sync N+1 supersedes entities from sync N, query at `logical_version` N returns the version-N state, not N+1." Currently covered at the Phase 1 derivation-supersession unit level (`tests/integration/test_bitemporal_supersession.py`); an e2e sync-driven version would validate the full cross-version story.

**Revisit when:** Substrate 2 (Test Representation) needs to query at historical versions for "what did the org look like when this test was generated?", which is the real downstream consumer.

---

## P-012 — Error-recovery sync e2e scenario (deferred from §25)

**Idea:** Simulate a transient Salesforce API failure mid-phase; verify the sync recovers without losing already-materialized work. Recovery works in practice — the live test logs show ~30 HTTP 400s during the Layout phase (industry-cloud objects not enabled in this sandbox) which auto-recover. Not explicitly asserted.

**Revisit when:** a real failure mode surfaces that recovery doesn't handle, OR when the platform expands to non-sandbox orgs where API instability matters more.

---

## P-013 — Worker restart mid-enrichment-drain e2e scenario (deferred from §25)

**Idea:** Subprocess + SIGKILL test for the §23 enrichment worker's `_reap_stalled` behavior. Currently covered at the unit-test level (`tests/unit/test_worker_enrichment.py`) plus the queue claim's at-least-once semantics are well-understood (`FOR UPDATE SKIP LOCKED`).

**Revisit when:** production worker restart causes data corruption (would indicate the at-least-once semantics are broken), OR a customer-reported issue traces back to mid-drain crashes.

---

## P-014 — Cross-tenant isolation e2e scenario (deferred from §25)

**Idea:** Provision a `tenant_2` schema, sync both `tenant_1` and `tenant_2`, verify cross-tenant invariants (no cross-tenant entity references, no readiness state leakage, no queue rows touching the wrong schema). The schema-per-tenant + `SET app.tenant_id` CHECK enforces isolation at the DB layer already; an e2e test would verify what's structurally guaranteed.

**Revisit when:** a real second tenant gets onboarded (customer milestone), OR when a substrate change creates new cross-tenant invariants worth defending.

---

## P-016 — `limits._starter_defaults` rate-limit tightness (surfaced by §25 attempt 1)

**Idea:** `primeqa/intelligence/llm/limits.py` `_starter_defaults()` returns starter-tier limits (30 calls/min, 500/hour, $5/day) when `tenant_agent_settings` is absent. The worker's enrichment drain can burst above 30/min when embedding (1 batch / tick) + summary (5 LLM calls / tick) run concurrently, producing intermittent `rate_limited` responses. Summaries retry; usually all succeed (`summaries_failed=0`), but occasionally hit `ENRICHMENT_MAX_ATTEMPTS=5` retries and become `failed_permanent` → `sync_run.status='partial_success'` rather than `'success'`.

**Fix shape:** distinguish "table missing" (UndefinedTable → fail open with `TenantLimits()` and all-NULL caps; no rate limiting in the test environment) from "table exists, no row for tenant" (return starter-tier defaults per the spec). Two-line change in `load_tenant_config`'s except handler.

**Revisit when:** production rate-limit-driven failures appear in operational logs, OR new substrate work increases the worker's burst rate, OR before customer-facing release.

(P-015 — D-030 multi-org shared-model reconciliation — was on
this list briefly between §25 and §26 but is now fully resolved
by the §26 commit. Slot intentionally left unfilled to preserve
the numbering reference in corrections-log entries.)
