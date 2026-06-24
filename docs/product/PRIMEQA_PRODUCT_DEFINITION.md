# Plimsol — Product Definition

**Status:** Foundation document. All subsequent phase plans, architectural decisions, and feature scoping derive from this document. The architecture (§1–§5, the 8-substrate model) remains authoritative and current; §6's phasing/current-state has been refreshed (all 8 substrates shipped, see §6.1).
**Owner:** Amjad
**Date:** 2026-04-30 (currency refresh 2026-06-15)
**Version:** 1.1 (product renamed Plimsol → Plimsol 2026-06-15, display text only — `primeqa/` package path unchanged)

> **This document describes Plimsol as a product.** The underlying
> architectural decomposition into 8 substrates is in
> `docs/architecture/PLATFORM_VISION.md`, which is the architectural
> authority per D-001.
>
> Substrate numbering in this document follows PLATFORM_VISION's
> 8-substrate model. If you came from an earlier version of this doc
> that used 4-substrate numbering, see D-050 in
> `docs/architecture/DECISIONS_LOG.md` for the reconciliation.

---

## How to read this document

This is a comprehensive product definition. It is structured to answer six questions in order: why the product exists, who uses it and when, what it is and isn't, how it works internally, what tradeoffs and boundaries shape it, and what we are building now versus later.

The document is intended for two audiences. Primarily it is for the founder, as a working artifact that captures decisions and serves as the reference point for when architectural drift threatens. Secondarily it is for future contractors and engineers who join the project, who need to understand not just what the architecture does but why it is shaped the way it is.

Every section is intended to be substantive. Skip to sections of interest, but the document should hang together when read end to end.

---

## 1. Why Plimsol exists

### 1.1 The problem we are solving

Salesforce QA is structurally hard in a way that other QA work is not.

Salesforce orgs are metadata-driven systems. The behaviour a user observes — a failed save, an unexpected validation error, a missing field on a layout — is the emergent product of dozens of layered configurations: validation rules, flows, permission sets, record types, page layouts, picklist constraints, workflow rules, Apex triggers, and more. Most of this configuration is invisible at the UI layer. When something goes wrong, the cause is rarely obvious from the symptom.

This invisibility is fine when everything works. It becomes catastrophic when tests fail under release pressure.

A typical Salesforce QA engineer working on a sprint team validates JIRA-driven changes in a sandbox, runs regression tests before a release, and is responsible for telling the team whether to ship. The job sounds straightforward. The reality is that when a test fails, the engineer has to investigate why, and the investigation is genuinely difficult work. Salesforce does not tell them which validation rule fired. It does not tell them which flow ran and what decisions it made. It does not link the failure to any recent change. The engineer has to assemble that picture by hand: open Setup, search through validation rules, open relevant flows, check the data they used, ask the developer who made the recent change.

This investigation typically takes 30 to 120 minutes per failure. On a Friday afternoon before a release, with multiple failures, the engineer cannot do this work in the time available. They either escalate to the developer team and delay the release, or they make a release decision under uncertainty.

This is the core problem Plimsol exists to solve.

### 1.2 What this is not

Plimsol is not a faster Provar. It is not a smarter Copado. It is not a way to generate more test cases. The problem is not that QA teams cannot run tests or write them; the problem is that they cannot understand failures fast enough to act on them confidently.

The existing tools in this space optimize for the wrong thing. Provar excels at building and maintaining stable regression suites that survive Salesforce UI changes. Copado integrates testing into DevOps pipelines. Both ship robust execution engines and metadata-aware test authoring. Neither closes the gap between "this test failed" and "here is exactly why, here is what to do about it, here is how confident you should be in this answer."

That gap is the unowned layer. Owning it is what Plimsol is.

### 1.3 Why now, why us

Two structural shifts make this product possible now in a way it was not five years ago.

First, large language models can reliably translate structured technical context into natural-language explanations grounded in specific entities, when the inputs are constrained and the outputs are verified. This was not viable before 2023. It is now.

Second, the underlying Salesforce metadata model is not changing — its richness has long been the source of the pain. What has changed is that we can now build systems that ingest that metadata model into a queryable graph, embed it for semantic retrieval, and use LLMs as the final translation layer rather than as the source of truth. The substrate that makes this possible is build-able by a small team in a focused way.

Existing competitors carry ten years of product history. They cannot easily retrofit a metadata-graph-plus-AI architecture into their existing stacks. They will reach this layer eventually, but the architectural commitment required is large enough that a clean-sheet builder gets there first if they build the right substrate.

This is the leverage thesis. We do not compete with ten years of edge-case handling in regression test execution. We sidestep that competition by owning the failure-comprehension layer that no one else has built.

---

## 2. The user and the moment

### 2.1 The primary user

Plimsol is built for the **mid-level Salesforce QA Engineer**, two to six years into their career, working in a sprint-based team.

This user is not the QA Lead, who oversees strategy but does not execute day-to-day testing. They are not a junior QA, who lacks the Salesforce-specific intuition to use the product effectively. They are the engineer doing the actual testing work daily.

Their environment includes a Salesforce project (often an ongoing implementation), multiple orgs (a dev sandbox, a QA or UAT sandbox, occasionally feature-specific scratch orgs), JIRA for requirements, and either TestRail, Zephyr, or Excel for test cases. Some teams have partial automation through Provar or Selenium; most do not.

Their mental model is concrete and outcome-oriented. They think in steps ("create a record, update its stage, verify the trigger fires"), in business logic ("if the customer is inactive, save should fail"), and in outcomes ("did the test pass or fail"). They do not think in metadata graphs, Apex internals, or system architecture. They are not engineers who will accept abstract tooling; they want clarity, not power.

### 2.2 What their day looks like before Plimsol

The morning begins with picking up JIRA tickets assigned for testing. The engineer reads the acceptance criteria, tries to understand what the developer built, and prepares to validate it.

Through the middle of the day, they write or update test cases (often by copying old ones and adjusting), set up test data manually in Salesforce, and execute tests either manually or through whatever partial automation exists.

In the afternoon, tests start failing. The engineer investigates: checks data, opens Salesforce Setup to look at validation rules, opens flows to trace logic, tries to reproduce, asks a developer when stuck. Each investigation takes 30 to 90 minutes. Most days a few failures eat the bulk of the engineer's time.

The evenings before a release are the hardest. A regression suite runs. Multiple tests fail. The QA Lead is asking for a status update. Developers are asking whether the failures are real bugs or test data issues. The release manager is waiting for a go/no-go decision. The engineer has to triage failures against the clock, and the failures themselves are opaque.

### 2.3 The moment Plimsol wins

We call this the Priya scene. It is the design center of the product.

It is Friday, 6:45 PM. Release cut at 7:30 PM. Priya is the QA Engineer responsible for the sign-off.

The regression suite has just finished. 120 tests executed, 4 failed. Slack is already lighting up. The QA Lead asks for status. A developer asks whether the failures are real bugs or data problems. The release manager wants to know whether they can go live.

Without Plimsol, Priya opens the test logs, re-runs the failed tests, checks the data setup, opens Salesforce to look at validation rules and flows, asks a developer for help on the one she cannot diagnose. Forty-five to ninety minutes pass. The release is delayed or shipped under uncertainty.

With Plimsol, Priya opens the failed test in Plimsol's UI. She sees:

```
Test: Close Opportunity — High Value
Step 3: Update Stage → Closed Won
Result: FAILED

Reason:
  Validation Rule fired:
    "Amount must be populated when Stage = Closed Won"

Context:
  - Test data set Amount = null
  - Rule introduced today via JIRA SQ-211

Impact:
  - Affects all Opportunity closure flows
  - 3 other tests failing for the same reason

Confidence: HIGH (rule directly mapped from Salesforce error response)
```

In thirty seconds, Priya knows: this is not a random failure, the four failures have a single root cause, this is an expected behaviour change from a deployment that landed today, and the appropriate fix is to update the test data to include Amount when closing Opportunities.

She tells the team: "These failures are the new validation rule from SQ-211. Tests need to include Amount when closing Opportunities. No system bug. We are good to ship once tests are updated."

The release happens on time. The developer team does not get pulled in for a non-bug. Priya goes home.

This is the moment we are building toward.

### 2.4 What this user requires from a tool

The Priya scene reveals what the user actually requires, which is different from what existing tools optimize for.

She requires **clarity**, not power. A simple structured explanation, not a dashboard with options.

She requires **evidence**, not narration. The system should show the rule it mapped to, the error response it parsed, the change record it found. She should be able to verify the chain by clicking through to source.

She requires **honesty under uncertainty**. If Plimsol is not sure why a test failed, it must say so. Confident wrongness is worse than no answer.

She requires **trust**. If Plimsol is wrong even a few times in a row, she stops believing it. The trust must be earned with every interaction. Once lost, regaining it is much harder than building it.

These four requirements — clarity, evidence, honesty, trust — are the architectural rails. Every design decision is evaluated against them.

---

## 3. What Plimsol is, and what it is not

### 3.1 What Plimsol is

Plimsol is an **AI-native QA platform for Salesforce**, centered on failure comprehension, with test generation and execution as supporting capabilities.

The product owns a complete workflow:

- **Generate.** The engineer selects a JIRA ticket, a batch of tickets, or an entire sprint, and asks Plimsol to generate test cases. Generation is grounded in Plimsol's semantic model of the connected Salesforce org. Tests are produced in a structured format the engineer can review and edit.

- **Review.** The engineer reviews generated tests, edits them, regenerates as needed. Approval is explicit. Nothing runs without review in the initial version of the product.

- **Execute.** Approved tests are run by Plimsol's own execution engine, which drives Salesforce through a hybrid of UI automation and API calls. Execution captures rich structured traces of what happened.

- **Understand.** When tests fail, Plimsol's attribution layer maps the failure back to the semantic model entities involved (the validation rule that fired, the flow that ran, the field that was missing) and produces a clean, grounded, QA-readable explanation. Where attribution cannot confidently identify the cause, the system says so honestly and falls back to raw error visibility.

The whole workflow lives inside Plimsol. Test cases are stored in Plimsol's own database. The web UI is the engineer's primary working surface for test lifecycle and failure investigation. External systems like TestRail, Zephyr, JIRA, and Excel are integrated through export and synchronization but are not the system of record.

### 3.2 What Plimsol is not

Plimsol is not a replacement for Provar's stable regression suites. Customers running large, mature regression suites in Provar should continue to do so. Plimsol is for sprint-level testing — the daily and weekly work of validating JIRA-driven changes — and for failure comprehension across all testing.

Plimsol is not a general-purpose test management system. TestRail and Zephyr are general; they support any testing workflow across any technology. Plimsol is Salesforce-specific by design and gets its power from that specificity.

Plimsol is not a generic AI assistant. ChatGPT can explain a Salesforce error message in the abstract. Plimsol explains a specific test failure with grounded references to specific entities in a specific org with specific recent changes. The grounding is not ornamental; it is what makes the explanation trustworthy.

Plimsol is not a code-generation tool. The tests it produces are structured records, not code. The execution engine consumes those records. The engineer interacts with tests through the web UI, not through commits to a Git repository.

Plimsol is not a solve-everything tool. It does not handle every Salesforce edge case at launch, does not compete with ten years of accumulated UI quirk handling, does not optimize for breadth of test coverage. It optimizes for the moments where it can deliver overwhelming value, and degrades gracefully where it cannot.

### 3.3 The honest competitive picture

The most likely customer objection is: "We already have Provar. Why do we need Plimsol?"

The honest answer is that Provar and Plimsol solve different problems.

Provar excels at building and maintaining stable regression test suites that survive Salesforce updates. Its metadata-aware test authoring and execution are mature and effective for what they do. But creating and maintaining Provar tests is expensive in time and skill. Most teams use Provar selectively for critical flows; the rest of their testing is manual or spreadsheet-based.

ChatGPT (or any general-purpose LLM tool) can explain errors in the abstract. But it has no access to the specific org, the specific metadata, the specific changes. The QA engineer still has to gather and interpret context before asking the question, and even then the answer is ungrounded.

Plimsol fills the gap. It sits inside the workflow, captures execution context automatically, maps failures to specific entities in the model, and explains them with grounded evidence. There is no copy-paste, no manual context-gathering, no dependency on the engineer's tribal knowledge.

In practice, customers will often run both: Provar for large stable regression suites that they have already invested in, Plimsol for sprint-level testing and the failure-comprehension moment. Plimsol does not require Provar to go away. It requires only that customers see value in solving the failure-comprehension problem properly, which they universally do.

---

## 4. How Plimsol works

### 4.1 The eight-substrate architecture

Plimsol is built on eight substrates, each with a distinct responsibility, loosely coupled to the others through clean interfaces. This framing is authoritative per `docs/architecture/PLATFORM_VISION.md` and D-001 / D-050. The v1 product moment is built on S1 + S2 + S3 + S4 + S6; the remaining three substrates (S5, S7, S8) are part of the architectural foundation but ship later.

```
              Substrate 7 — Conversation and Control
                              │
                              ▼
            Substrate 6 — Observation and Interpretation
                ▲                              ▲
                │                              │
       Substrate 4 — Execution     ◀─── Substrate 8 — Evolution
                ▲                              │
                │                              ▼
       Substrate 3 — Generation    ◀─── Substrate 5 — Knowledge
                ▲                              │
                │                              ▼
                └────────► Substrate 1 — Semantic Org Model
                                              ▲
                                              │
                  Substrate 2 — Test Representation ─┐
                              ▲                      │
                              └──────────────────────┘
```

Read the graph as: every higher substrate depends on substrates below. Substrate 1 (semantic org model) is foundational — referenced directly by Generation, Execution, Evolution, Interpretation, and Knowledge. Substrate 2 (test representation) is also foundational and referenced by Generation, Execution, Interpretation, Evolution. Substrate 5 (knowledge) is cross-cutting — it shapes generation, gets signals from execution and user feedback, and feeds interpretation. Substrate 7 (conversation) sits on top and touches every other substrate as a user-facing surface.

Each substrate is described below, including its responsibilities, its interfaces, and what it deliberately does not do. The deeply-built v1 substrates (S1, S2, S3, S4, S6) get full treatments; the more-briefly-described substrates (S5, S7, S8 — all now built, see §6.1, but lighter v1 surfaces) get shorter sections so readers know they exist and how they fit.

### 4.2 Substrate 1 — Semantic Org Model

**Responsibility:** Be the authoritative source of truth about what is in the connected Salesforce org, in a queryable structured form.

The semantic org model is a bitemporal graph of Salesforce metadata entities. Objects, fields, record types, layouts, validation rules, flows, profiles, permission sets, users, and picklist values are first-class entities. Relationships between them — a field belongs to an object, a validation rule references fields, a record type constrains picklist values — are first-class edges, typed and categorized into structural, configuration, permission, and behavior groups.

The model is bitemporal: every entity and edge has `valid_from_seq` and `valid_to_seq` columns that track the version range over which the row was the active truth. Changes do not destroy history. When a field's metadata changes, the previous version is superseded (its `valid_to_seq` is set) and a new version is inserted. The complete change history of the org's metadata is recoverable from the model.

The model is enriched at sync time with two AI-derived layers:

**Embeddings.** Each entity has a vector embedding generated from a deterministic semantic text representation of its structured data. The embedding enables similarity search: given a natural-language requirement or failure description, find the metadata entities most semantically related. Embeddings are cheap to generate (sub-cent per entity). The embedding model is Voyage AI `voyage-3` (1024 dim), accessed via a direct HTTP API from the enrichment worker. This choice — recorded in D-049 — preserves environment parity across every target (Intel and Apple-Silicon dev machines, Linux production, CI) while delivering retrieval quality competitive with state-of-the-art models; Voyage AI is Anthropic's recommended embedding partner.

**Lightweight LLM summaries.** For two entity types whose semantics are encoded in non-English content — validation rules (formula text) and flows (decision logic) — a plain-English summary is generated by an LLM at sync time. The summary is bounded, short, and grounded in the source content. It exists for one purpose: to make these entities discoverable and explainable from natural-language queries. The summary is not the source of truth; the underlying formula or flow definition remains the truth. The summary is a discovery and explanation aid.

The substrate explicitly does not use AI for structural facts. The list of fields on an object, their types, their relationships — these come from Salesforce's describe and tooling APIs, parsed deterministically, written through Pydantic-validated boundaries. AI cannot invent a field, change a type, or alter a value. This is an architectural rule (see §4.7).

The substrate exposes a clean query interface (a planned materialized view in Phase 2) that downstream substrates consume. Substrate 3 (Generation) retrieves entities relevant to a JIRA ticket's content. Substrate 6 (Observation and Interpretation) retrieves the entity for a given Salesforce error. Both consumers see the same model.

### 4.3 Substrate 2 — Test Representation

**Responsibility:** Be the canonical data structure for a test case — executable, human-readable, evolvable.

A test case in Plimsol is more than "JSON with steps." It captures test intent, coverage (which entities in the model the test touches), relationships to org entities (the validation rule it exercises, the flow it triggers), execution history (which runs it was part of, what their results were), assumptions about org state, and provenance (which JIRA ticket generated it, which prompt version, which Domain Pack shaped it).

Tests are stored as structured records in Plimsol's database. Generation history, edit history, and execution history are all first-class — they are what enable Substrate 6 (Observation and Interpretation) to explain failures by mapping them back to the originating intent. The representation is what Substrate 3 (Generation) produces and what Substrate 4 (Execution) consumes; the two substrates can evolve independently because they meet at this stable contract.

The substrate is designed to represent tests across the full range of QA archetypes (data-behavior, configuration, permission, UI, integration) using a single common representation, not five different ones. The initial product covers one archetype; the representation must not foreclose the others.

### 4.4 Substrate 3 — Generation Engine

**Responsibility:** Produce test cases in Substrate 2's representation from natural-language requirements (JIRA tickets), grounded in Substrate 1.

Generation is **user-driven**, not background-driven. The engineer selects one or more JIRA tickets, either individually or as a sprint batch, and explicitly asks Plimsol to generate tests. The system does not generate tests automatically when JIRA tickets land. Background generation creates noise, erodes trust, and produces output the engineer never asked for.

Generation is **batch-capable**. Engineers work in batches, not one ticket at a time. The system handles a sprint of tickets at once, generating tests for all of them, and presenting them for review.

Generation is **review-gated**. Every generated test is reviewed by the engineer before execution. There is no auto-approval path in the initial version of the product. Trust is brittle in the early stages; skipping review breaks it. The flow is: generate → review → approve → run.

Generation is **iteratively refinable**. When generation gets a test wrong, the engineer corrects it inside Plimsol — either editing the test directly or regenerating it with adjusted prompts. The correction loop does not bounce between tools. The requirement (the JIRA ticket) has not changed; only the interpretation has, and the interpretation is Plimsol's responsibility.

The internal flow for generation is approximately:

1. Engineer selects JIRA tickets in Plimsol's UI.
2. Plimsol fetches ticket descriptions (via JIRA integration).
3. For each ticket, Plimsol embeds the ticket content and retrieves the most semantically relevant entities from Substrate 1 (objects, fields, validation rules, flows, etc. that the ticket likely concerns).
4. The retrieved entities, ticket content, and a structured prompt are sent to an LLM (Anthropic Claude Sonnet for this work, given quality matters more than cost here). Knowledge from Substrate 5 (Domain Packs, system rules, learned tenant facts) shapes the prompt.
5. The LLM generates a test case in Substrate 2's representation, referencing real entity IDs from the retrieved set. Schema enforcement at the boundary prevents the LLM from inventing entity references.
6. The generated test is presented in Plimsol's UI for engineer review.

The schema-enforcement step is critical. The LLM's output is constrained to reference only entities that exist in the model. If the LLM produces a test step that references a field that does not exist, that step fails validation and is not shown to the engineer. This is the architectural defense against hallucinated tests.

### 4.5 Substrate 4 — Execution Engine

**Responsibility:** Run approved tests (in Substrate 2's representation) against Salesforce orgs and capture rich, structured traces of what happened.

Execution uses a **hybrid UI plus API model**. The UI path is essential because many Salesforce behaviors — validation rules firing, flows triggering, layout constraints, lightning component behavior — only fully manifest through the UI. It is what the end user actually experiences, and it is what tests must validate. The API path is used where it makes execution faster and cleaner: data setup, deterministic state checks, backend validation. The mix is per-test-step, chosen for fidelity to what is being tested.

Execution is **observation-only**. It does not interpret what happened. It does not generate explanations. It does not decide whether a test passed or failed in any nuanced way. It runs the steps, captures the responses, and records what occurred.

The execution layer captures, for every test run:

- Each step's action (set this field, click this button, save this record)
- The response from Salesforce for each action — including raw error text from validation rules, flow exceptions, or other failures
- Field-level state changes
- Timing information
- Screenshots at failure points
- Metadata about the test, the org, the model version at the time of execution

This trace is structured and machine-readable. It is not for human consumption directly. The engineer never sees raw traces. Substrate 6 (Observation and Interpretation) consumes them.

The principle is: **capture everything, explain selectively.** The richness of the trace is what enables intelligence later. Simplification happens in the explanation layer (Substrate 6), not at execution. Execution is where truth is preserved.

The execution environment is **hybrid cloud-and-agent**. By default, tests run from Plimsol's cloud infrastructure for ease of onboarding and scalability. For enterprise customers with strict security or data residency requirements, a customer-hosted agent runs tests within their own environment, with traces shipped back to Plimsol's cloud for storage and analysis. The hybrid model is deferred build (cloud first, agent when an enterprise customer requires it).

### 4.6 Substrate 5 — Knowledge System

**Responsibility:** Persist and improve the knowledge that shapes generation and execution — Domain Packs (prescriptive patterns), system rules (proscriptive rules), per-tenant learned facts, cross-tenant patterns (within strict isolation boundaries), and user feedback signals that tune future generations.

The substrate is what makes Plimsol get smarter the more it is used. Domain Packs ship with Plimsol today as the early manifestation of this substrate; learned facts and cross-tenant patterns are deferred to a later phase. The architectural commitment is that knowledge is data, not configuration: it accumulates, evolves, and is queryable.

S5 is cross-cutting. Generation (S3) reads from it; execution (S4) and user feedback write signals back into it (what worked, what didn't); interpretation (S6) can consult it for context. It does not have a primary user-facing surface — it shows up through the quality of generation, the relevance of explanations, and the accuracy of suggested fixes.

### 4.7 Substrate 6 — Observation and Interpretation

**Responsibility:** Convert execution traces into clean, grounded, QA-readable explanations of what happened. This is where the v1 product moment lives.

The flow is approximately:

1. Test fails. Substrate 4 produces a structured trace including the raw Salesforce response.
2. Interpretation extracts the salient signal from the trace: the error message, the failing step, the fields involved, the operation context.
3. Interpretation maps the signal to entities in Substrate 1's model. A validation rule error message is matched (by exact text and by semantic similarity using embeddings) to the validation rule entity that produced it. A flow exception is matched to the flow. A "field does not exist" error is mapped to the missing field entity (or the absence thereof in the target org).
4. Interpretation correlates with change history. The matched entity's recent supersession history (from Substrate 1's bitemporal model) is examined. If the entity was introduced or modified recently, the correlation is noted.
5. JIRA correlation, where available, links the recent metadata change to a specific JIRA ticket. (See §5.5 on the JIRA correlation strategy and its limitations.)
6. The structured record (matched entity, change context, JIRA link, confidence score) is fed to an LLM (Anthropic Claude Sonnet) to produce a clean QA-readable explanation. The LLM's output is constrained: it can describe the matched entity, reference its content, summarize the change context, and explain the implication. It cannot invent entities or claims that are not in the structured input.
7. The explanation is presented in Plimsol's UI alongside the structured evidence chain.

When the substrate cannot confidently match a failure to a specific entity, the system says so honestly. The explanation reads: "Failure occurred during save operation. Unable to map to a specific rule. See raw error." This is better than hallucinating an explanation. The architectural rule (§4.11) is precision over completeness, and graceful fallback over confident wrongness.

The interpretation layer also performs **failure clustering**. When multiple tests in a run fail with the same root cause (the same validation rule, for example), the failures are grouped. Priya in the scene saw "3 other tests failing for the same reason." This is the clustering output. It dramatically reduces the engineer's investigation surface.

### 4.8 Substrate 7 — Conversation and Control

**Responsibility:** Be the natural-language layer through which users interact with Plimsol. Not a chatbot bolted onto a dashboard, but a surface integrated throughout — generation, debugging, and coverage exploration are all conversational where conversation is the natural shape of the interaction.

Example questions S7 must answer well: "Why is our regression coverage dropping?" "What's at risk if we deploy this package?" "Show me tests for the new approval process." "Did yesterday's failures have a common cause?"

S7 is deferred for v1. The initial product is structured-UI-first; conversational interactions are an enhancement on top. The architectural commitment is that the substrate exists in the design — the data model (S1), the test representation (S2), the failure attribution (S6) — so that when S7 ships, it has grounded answers to query against rather than free-floating LLM hallucinations.

### 4.9 Substrate 8 — Evolution Engine

**Responsibility:** Maintain tests as the org evolves so engineers don't have to. Field renamed → references in affected tests update. New required field added → affected tests adjust. Validation rule changed → tests re-verified against the new rule. Flow deactivated → dependent tests flagged for review.

This is the maintenance burden Provar dumps on customers; Plimsol automates it. The substrate sits between S1 (which detects org changes via its bitemporal model) and S2 (which it rewrites). Depending on the change type, S8 may act autonomously (rename propagation), propose changes for review (semantic adjustments), or flag for human attention (deactivation cascades). The autonomy gradient is a S8 design question, not yet resolved (see Q-006 in `docs/architecture/OPEN_QUESTIONS.md`) — note that the grounding-validity predicate that would *gate* any such action is already live; it is these autonomous-action mechanics that remain deferred.

S8's grounding-validity core is built and live — it runs every scheduler tick (`s8_grounding_tick` re-grounds a tenant's current claims when S1 advances) and is read by five consumers including the GO/NO-GO decision engine (per §6.1; D-142/D-143). What remains deferred is the *evolution mechanics* (autonomous re-grounding / supersession execution) and the admissibility leg (S3-blocked). The architectural commitment held: S1 makes change detection feasible (bitemporal model) and S2 makes tests rewritable (rich representation), so the grounding core had the substrates it needed.

### 4.10 The Priya scene retraced through architecture

To make the architecture concrete, here is the Priya scene mapped to the substrates that produce each part of her experience.

Priya runs a regression suite of 120 tests. Each test was previously generated by Substrate 3 from a JIRA ticket, in Substrate 2's representation, reviewed and approved by Priya, and stored in Plimsol's database. Substrate 4 executes all 120 tests against the QA sandbox, capturing rich traces for each.

Four tests fail. For each failure, Substrate 6's interpretation layer:

- Extracts the raw error from the Substrate 4 trace ("FIELD_CUSTOM_VALIDATION_EXCEPTION: Amount must be populated when Stage = Closed Won").
- Performs exact-text and semantic-similarity matching against Substrate 1's validation rule entities.
- Identifies the specific validation rule (let's call it `Opportunity.Amount_Required_On_Close`).
- Examines the rule's bitemporal history in Substrate 1 and finds it was inserted with `valid_from_seq` matching today's morning sync.
- Correlates the recent insertion with JIRA's change history (per the integration strategy described in §5.5) and finds JIRA SQ-211 as the source.
- Notes that three other failed tests share the same matched validation rule.
- Sends the structured attribution to the LLM with a constrained prompt; receives the clean explanation.
- Renders the explanation in Plimsol's UI with the evidence chain (rule entity link, supersession history link, JIRA SQ-211 link).

Priya sees the explanation in seconds. She trusts it because she can click through to verify each piece. She makes the release decision in thirty seconds.

This is what the architecture is for.

### 4.11 Locked architectural rules

These rules are non-negotiable foundations. Every design decision is evaluated against them. They appear elsewhere in the document; they are consolidated here for reference.

**Rule 1 — Precision over completeness in attribution.** When the system cannot confidently attribute a failure, it says so honestly. Confident wrongness is the most damaging failure mode and the architecture defends against it above all else.

**Rule 2 — Plimsol owns its own execution layer.** Attribution depends on rich, structured traces. We cannot rely on external execution tools for this data. Customers using Provar for regression suites continue to do so; Plimsol does sprint-level testing end-to-end with its own execution.

**Rule 3 — Tests stored in Plimsol, exported to others.** Plimsol's database is the system of record for tests. The web UI is the engineer's primary working surface. TestRail, Zephyr, JIRA, and Excel are integration targets, not systems of record.

**Rule 4 — Capture everything, explain selectively.** The execution layer captures rich structured traces. The attribution layer interprets and selects. The UI explains in clean human terms. The richness is preserved internally; the simplification happens at the surface.

**Rule 5 — Graceful fallback over hallucination.** When the system encounters something it cannot confidently handle, it falls back to honest "unable to map" responses with raw error visibility. It does not invent explanations. The architectural posture is that failing to deliver an explanation is acceptable; delivering a wrong one is not.

**Rule 6 — Hybrid execution: cloud default, agent for enterprise.** The default execution environment is Plimsol's cloud, for ease of onboarding. A customer-hosted agent is the enterprise option for security-sensitive customers. Both are first-class but the agent is built when an enterprise customer requires it, not before.

**Rule 7 — AI for translation, not invention.** AI's role is to translate structured technical context into natural-language explanations and to retrieve semantically relevant entities. AI does not invent structural facts about the org. The semantic model's structural truth is deterministic.

**Rule 8 — Generation is user-driven, batch-capable, review-gated.** Tests are generated when the engineer asks. Generation handles batches naturally. Every test is reviewed before execution. There is no background generation, no auto-approval path.

**Rule 9 — Correction loops stay inside Plimsol.** When generation gets it wrong, the engineer corrects it without leaving the product. Edit-or-regenerate is the friction-free path. Bouncing between tools loses context.

---

## 5. Boundaries and tradeoffs

This section is about the things we deliberately do not do, the failure modes we defend against, and the dependencies we accept.

### 5.1 The most expensive failure mode

Every product has a failure mode that, if it occurs frequently, kills the product. Plimsol's is **confident wrongness in attribution**.

If Plimsol explains a failure incorrectly and the engineer trusts the explanation, the engineer makes the wrong decision. They may report "no system bug" to the release manager when there is one, or they may dismiss a failure as expected when it is a regression. The downstream consequence is a customer-impacting bug shipping to production. This is the only failure mode where Plimsol actively causes harm rather than just failing to deliver value.

The architecture defends against this above all else. Specifically:

- Attribution is precision-biased. The system reports HIGH confidence only when the mapping is direct (e.g., the Salesforce error response contained the exact text of a validation rule's error message). Below HIGH, the system says so explicitly.
- Below a confidence threshold, the system falls back to raw error visibility rather than attempting an explanation.
- Every explanation includes the evidence chain. The engineer can click through to verify the matched entity, the change history, the JIRA link. Hidden evidence chains (where the engineer cannot verify the system's claim) are an architectural smell.
- LLM outputs are constrained to the structured attribution input. The LLM cannot invent entities, claims, or facts that are not in the structured input.
- We invest disproportionately in this layer's correctness. Attribution gets more test coverage, more eval-set effort, more careful prompt engineering than any other layer.

The secondary failure modes are real but recoverable. False positives (tests fail when they should pass) waste engineer time but do not cause harm. False negatives in test generation (tests pass when they should fail) limit coverage but are recoverable when the gap is noticed. Stale sync produces explanations that map to entities that no longer exist; the architecture flags this rather than failing silently. Cost overruns are a business concern but not an existential threat.

The architecture is shaped by the asymmetry of these failure modes. The expensive failure is the one we never let happen.

### 5.2 What we do not compete on

Provar and Copado have a ten-year lead in execution-layer maturity. They have handled hundreds of Salesforce-specific edge cases that a new entrant cannot replicate quickly:

- Lightning component re-renders, dynamic IDs, shadow DOM handling
- Asynchronous lazy-loading and timing edge cases
- Test data management at scale (Provar's data generation, Copado's data masking)
- Long-running CI/CD integrations (Jenkins, Azure DevOps, GitLab, Salesforce DevOps Center)
- Enterprise sales and procurement relationships
- Documentation, certification programs, partner ecosystems

We do not try to match these in v1. We accept that:

- Our execution engine handles common patterns reliably and falls back gracefully on edge cases. Coverage expands incrementally as data accumulates.
- We do not ship CI/CD integrations in v1. They are post-v1 work.
- We do not try to be the test data generation tool. Customers manage test data themselves; Plimsol helps them understand when test data setup is the cause of a failure (a Substrate 6 capability), but does not generate test data automatically.
- Our enterprise sales motion takes years to build. v1 lands with mid-market and progressive enterprise customers; broad enterprise adoption is a multi-year journey.

The architectural sidestep is that we do not need to win on execution depth. We win on the failure-comprehension layer that Provar and Copado do not own. Customers can keep Provar for their stable regression suites; Plimsol earns its keep on sprint-level testing and the moment-of-failure understanding.

### 5.3 Integration burden on customers

Plimsol requires customers to do some things they may not currently do. We should be honest about this.

**Connect their Salesforce org(s) via OAuth.** Standard. Most testing tools require this. Low burden.

**Run the sync as a Salesforce System Administrator.** The integration user the org is connected as (the OAuth Run-As user) must be a System Administrator. Salesforce's REST describe API is field-level-security-aware, so any field hidden from the Run-As user is indistinguishable from a field that does not exist — the semantic org model would silently miss it. A System-Administrator Run-As user has full field visibility, which is why field-level-security / hidden-field detection (CF-1) is NOT built as a separate capability: it is replaced by this onboarding rule (D-266). This is enforced at setup/training time, not in code — an under-privileged Run-As user produces a quietly incomplete model with no error.

**Use Plimsol as the primary test working surface.** This is real. Customers with mature TestRail or Zephyr installations have to choose: keep using TestRail for general test management and use Plimsol only for sprint-level sandbox tests, or migrate progressively to Plimsol. We support export/sync to TestRail/Zephyr, so the choice is not binary, but customers do feel this.

**Tag JIRA tickets in their deployment process** to enable change correlation in attribution explanations. Some customer teams do this; many do not. Where the link is absent, attribution explanations omit the JIRA reference and fall back to "rule introduced today" without a ticket link. We do not require the link for the product to work, but it makes the product better.

**Run Plimsol's execution engine** rather than depending on Provar for test execution. Customers running existing Provar test suites continue to do so for those suites. New tests authored in Plimsol execute through Plimsol. Some duplication of execution capability across the customer's stack is unavoidable in the transition.

These are real burdens. They are not blockers. The Priya scene's value is large enough that engineers and QA leads are willing to take on the burden. We monitor onboarding friction and remove friction proactively.

### 5.4 Open dependencies

Some product capabilities depend on resolving questions that are still open.

**Test data generation.** Test data setup is the hidden pain in QA work — engineers spend significant time creating Account-Opportunity-related-record chains in the right state. Plimsol's v1 does not generate test data automatically. It captures and explains failures that happen because of test data issues (e.g., "test failed because Amount was null; the test data did not set Amount"). Whether to add test data generation as a Substrate 3 extension or a separate substrate is a v2-or-later question.

**JIRA correlation strategy.** The Priya scene's "introduced via JIRA SQ-211" requires linking a metadata change in Substrate 1's bitemporal history to a JIRA ticket. Possible strategies: (a) the customer's deployment process tags Salesforce metadata with JIRA ticket IDs, (b) Plimsol correlates by timing (the rule deployed at 2pm, ticket SQ-211 was merged at 1:55pm), (c) Plimsol reads JIRA descriptions and matches to metadata semantically via LLM. (a) is most reliable but requires customer process; (b) is opportunistic; (c) is expensive and unreliable. We will likely build (a)+(b) hybrid for v1: prefer customer-tagged links where available, fall back to timing-based correlation otherwise, and degrade gracefully (omit the JIRA link in the explanation) when neither is available.

**Multi-release support.** Enterprise customers have multiple releases in flight simultaneously: production at release N, UAT at N+1, dev sandboxes at N+2 with various features, integration sandboxes for merge testing. v1 Plimsol represents one canonical metadata model — whatever was most recently synced. This works for single-release customers and works for multi-release customers with the limitation that the model represents one release context at a time. True multi-release support (parallel branches of the metadata model, branch-aware queries, merge testing) is deferred to a future phase when a customer drives the requirements.

**Cost predictability at scale.** LLM costs are bounded but not yet validated against real customer-scale orgs. A 50,000-entity Salesforce org generates approximately $30-100 in LLM cost for initial sync (one-time) and approximately $5 per delta sync (when only changed entities are re-summarized). Test generation costs $0.05-0.20 per test; attribution costs cents per failure explanation. These numbers are bookable per-customer, but we will validate them against actual customer-scale orgs as part of Phase 2 readiness.

---

## 6. What we build now, what we build later

### 6.1 Current state

**As of 2026-06-15, all eight substrates are built, deployed, and the full
release-intelligence loop runs daily against the live env-59 org.** S1
(semantic org model + live sync), S2 (claims/recipes), S3 (grounded
generation), S4 (execution engine with grounded evidence + provisioning/
cleanup), S5 (knowledge), S6 (interpretation + cause attribution), S7
(grounded-or-refuse conversation), and S8 (grounding-validity + repair) all
ship as packages under `primeqa/`. The decision loop is wired (GO/NO-GO with
explainable NO-GO, D-237). The original v1 product layer was retired
(D-191…D-221, its product tables dropped in migration 053). All six product
themes from the 2026-06 vision review closed 2026-06-14.

*(Historical note: this section formerly described the as-of-Phase-1 state —
"Substrate 1's foundation built, 111 passing tests, nothing writing to it from
Salesforce yet." That snapshot is years of progress behind; the phase ladder
in §6.3 below is now a completed record.)*

### 6.2 The substrate-first approach

Given the strategic context — multiple paying customers, self-funded with no time pressure, solo founder plus AI plus contractors — the right path is **substrate-first, deep**. Build each substrate properly before moving to the next. Avoid throwaway thin slices that we have to retrofit later. Carry the technical-debt budget at zero, because a small team cannot afford technical debt.

The risk of this approach is that we build the wrong substrate because we have not validated with users. This risk is mitigated because we already have paying customers whose feedback informs the design. The Priya scene is not hypothetical. The architecture is shaped by real workflow knowledge, not speculation.

The reward is that when v1 ships, the foundation supports the product's evolution rather than constraining it. Five years out, the substrate's depth is the leverage that lets us out-architect competitors who cannot retrofit equivalent foundations into their existing stacks.

### 6.3 Phasing toward v1

> **Historical — the whole ladder below has shipped (2026-06-14).** This is the
> phasing as planned; every phase is now complete and the v1 product moment
> (Phase 6) is past. Kept as the build record.

The product reaches v1 — the Priya scene end-to-end, working reliably for a customer — at the end of a phased build. The phases are:

**Phase 1 (complete).** Substrate 1 foundation. Schema, edges, derivation, tests. Done.

**Phase 2 (complete).** Substrate 1 sync. Pulls Salesforce metadata into the model. Adds embeddings (Voyage `voyage-3`, 1024 dim, per D-049) and lightweight LLM summaries (Claude Haiku 4.5, for Flow and ValidationRule per D-044) for retrieval. Builds the materialized-view query interface. Details in `docs/architecture/substrate_1_semantic_org_model/PHASE_2_PLAN.md` (locked planning artifact) and `PHASE_2_PLAN_corrections.md` (implementation divergences §1–§23).

**Phase 3 (complete).** Substrate 2 (Test Representation) design. The rich data structure for a test case — intent, coverage, relationships to org entities, execution history, provenance — designed to span all QA archetypes with a single representation.

**Phase 4.** Substrate 3 (Generation Engine). JIRA → test cases in Substrate 2's representation, grounded in Substrate 1. Web UI for review and approval. Schema-enforced LLM output to prevent hallucinated entity references. Iterative refinement loop. Knowledge from Substrate 5 (Domain Packs, system rules) shapes the prompt.

**Phase 5.** Substrate 4 (Execution Engine). Hybrid UI-plus-API execution. Rich structured trace capture. Cloud-hosted execution environment.

**Phase 6.** Substrate 6 (Observation and Interpretation). Attribution layer. Confidence-scored entity mapping. Failure clustering. LLM-mediated explanation generation. Web UI for failure investigation. **This is the v1 product moment.**

**Phase 7 and beyond.** Hardening (OAuth encryption, security review). The architectural foundation of Substrate 5 (Knowledge System) formalized — learned tenant facts, cross-tenant patterns, user feedback signals — beyond today's Domain Packs. Substrate 7 (Conversation and Control) for natural-language interactions. Substrate 8 (Evolution Engine) for autonomous test maintenance as the org evolves. Multi-tenant orchestration. Customer-hosted execution agent. CI/CD integrations. Broader intelligence (test prioritization, coverage analysis, flakiness detection). Multi-release support when a customer drives it. Test data generation if it earns its place.

Each phase is detailed in its own phase plan document (`PHASE_N_PLAN.md`) at the time the phase begins. Phase plans are derived from this product document, not the other way around.

### 6.4 What v1 includes

v1 Plimsol delivers the Priya scene end-to-end:

- A QA engineer can connect a Salesforce sandbox.
- They can synchronize the org's metadata into Plimsol, populating Substrate 1.
- They can select JIRA tickets and generate test cases. Tests are reviewable, editable, regenerable.
- They can approve tests and run them.
- When tests fail, they see clean grounded explanations with confidence scores and evidence chains.
- They can cluster related failures, see change history, click through to source.
- The product runs reliably. Confident wrongness does not happen. Graceful fallback does.

v1 supports:

- Single Salesforce org per tenant (initial focus; multi-org sync from any registered org is supported in Phase 2 but multi-release is deferred)
- Cloud-hosted execution
- Sandbox connections only (no production org connections until Phase 6 hardening)
- Common Salesforce metadata patterns (the entity types covered in Phase 1's Tier 1 registry)
- Validation rule and flow attribution as primary failure-explanation paths

### 6.5 What v1 does not include

v1 deliberately does not include:

- Customer-hosted execution agent (added when an enterprise customer requires it)
- Production org connection (post-v1, requires OAuth encryption and security review)
- CI/CD integrations (post-v1)
- Multi-release / multi-branch metadata support (post-v1, customer-driven)
- Test data generation (post-v1, may or may not earn inclusion)
- Test prioritization, coverage analysis, flakiness detection (broader intelligence, post-v1)
- Mobile UI, custom dashboards, advanced reporting (post-v1, prioritized by customer demand)
- Non-Salesforce platforms (Plimsol is Salesforce-specific by design; expansion to other platforms is years away if ever)

### 6.6 The horizon beyond v1

v1 establishes the failure-comprehension layer. v2 and beyond expand the surface in directions that the substrate makes natural:

**Pre-deployment impact analysis.** Given a JIRA ticket about to be merged, predict which tests will be affected and surface them for the developer to review. This is post-failure attribution run in reverse, against not-yet-deployed metadata changes.

**Test maintenance intelligence.** When the org evolves (fields renamed, validation rules adjusted), automatically flag tests whose references are now broken and suggest updates. This is Substrate 8 (Evolution Engine) — applying the change-detection capabilities of S1's bitemporal model to S2's test representation, rather than running interpretation against live failures. (The grounding-validity half — flagging tests whose references are now broken — is already built and live, see §4.9; it is the *suggest-updates* auto-repair half that remains on the horizon.)

**Coverage analysis.** Given the org's metadata graph and the test inventory, identify gaps — entities or behaviors that no test exercises. Produce recommendations.

**Test prioritization.** Given a change set and a test inventory, rank which tests to run first based on impact likelihood. Useful for CI/CD integration.

**Cross-org diffing.** Compare metadata between two orgs (production vs UAT, dev sandbox vs QA sandbox) and surface the differences. Useful for release readiness.

**Multi-release support.** Represent multiple metadata branches simultaneously, support testing of merges between them, surface release-specific failures.

These are real product opportunities. They are not v1. They are surfaced here so the architecture leaves room for them — and the substrate-first approach exists precisely to ensure that the foundation supports them when they become priorities.

---

## Appendix A — Glossary of key terms

**Substrate.** A loosely-coupled architectural layer with a distinct responsibility and clean interfaces. Plimsol is built on eight substrates (per `docs/architecture/PLATFORM_VISION.md` and D-001 / D-050). The v1 product moment is built on S1 + S2 + S3 + S4 + S6; S5 (Knowledge), S7 (Conversation), and S8 (Evolution) are part of the architectural foundation but ship later.

**Bitemporal.** A data model where every row tracks both the time range it represents and (implicitly via supersession) the time range it was current truth. Allows history reconstruction.

**Attribution.** The process of mapping a test failure to the specific entity in the semantic model that caused it.

**Semantic model / normative model.** Substrate 1's representation of the org's metadata as a structured, queryable graph. Sometimes called "normative" to emphasize that it is authored truth, not a passive mirror of any single org.

**Confidence-scored.** Outputs from the attribution layer that carry an explicit indication of how reliable the mapping is. HIGH means direct exact-text match; lower confidence triggers fallback behavior.

**Graceful fallback.** The architectural posture of producing honest "unable to determine" output rather than confident-but-possibly-wrong output when the system encounters uncertain situations.

**Schema-enforced LLM output.** The pattern of constraining LLM outputs to validated schemas at the boundary between the LLM and the rest of the system. Prevents hallucinated references from entering downstream substrates.

**Trace.** The structured record produced by Substrate 4 (Execution Engine) during execution. Captures every action, every response, every state change, every error. Consumed by Substrate 6 (Observation and Interpretation).

**v1 product moment.** The point in the build at which the Priya scene works end-to-end. Reached at the end of Phase 6.

---

## Appendix B — Document maintenance

This document is the foundation product definition. Subsequent architectural decisions, phase plans, and feature scoping derive from it. When this document and another document conflict, this document wins until the conflict is resolved by explicit revision here.

This document is versioned. Material changes increment the version number and are dated. Minor edits (typo fixes, wording polish) do not require version increments.

The document is committed to the repository at `docs/product/PRIMEQA_PRODUCT_DEFINITION.md`. Subsequent phase plans reference it.

When a future contractor or hire joins the project, this document is part of their onboarding. They should read it end to end before reading any phase plan or architectural decision document.

---

*End of document.*
