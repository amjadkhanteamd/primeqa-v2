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
