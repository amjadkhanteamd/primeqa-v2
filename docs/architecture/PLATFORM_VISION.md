# PrimeQA Platform Vision

**Status:** near-immutable. Changes require explicit DECISIONS_LOG entry.
**Last substantive revision:** 2026-04-24
**Version:** 1.0

## What PrimeQA Is

PrimeQA is to QA what Claude Code is to developers.

Not a test automation tool where humans write tests faster. A platform where QA and development teams describe what they want tested in natural language, and the system handles generation, execution, maintenance, coverage evolution, and interpretation autonomously.

The promise is not "faster test scripts." The promise is "no scripts" — a QA agent that maintains and evolves a comprehensive test suite for a Salesforce org, explaining itself in human terms.

## Product Scope

PrimeQA tests Salesforce orgs across five archetypes:

1. **Data behavior** — records created/updated/deleted per permissions and rules
2. **Configuration** — metadata assertions about layouts, record types, validation rules, Flows, permissions
3. **Permissions** — what different users can see and do
4. **UI** — Lightning page rendering, component visibility, navigation
5. **Integration** — outbound messages, platform events, external callouts

The architecture must support all five archetypes as first-class concerns. The initial implementation may cover only a subset, but the foundation cannot foreclose the others.

## Competitive Position

We compete with Provar and Copado Robotic Testing on breadth and depth. We differentiate on being AI-native — built to interpret intent, generate tests autonomously, maintain them as orgs evolve, and explain results in human terms.

We do not differentiate on being faster at script authoring. That framing accepts the incumbents' premise that humans write tests. We reject that premise.

## The Eight Substrates

The architecture is composed of eight substrates. Each is a general capability that the product is built on. Features accumulate on top of substrates; substrates themselves evolve slowly.

Getting substrates right is more important than getting features right. Substrates that are designed well permit years of feature work without rewrites. Substrates that are designed poorly force rewrites when their assumptions break.

### Substrate 1 — Semantic Org Model

A rich, queryable representation of a Salesforce org's structure, behavior, and change history.

Not a metadata dump. A model the system can reason about: "this change to Account affects 47 flows across 12 objects." "The Service Rep profile inherits these 3 permission sets which together grant X." "The Case escalation flow triggers when IsEscalated flips true, which can happen via update or via rule Y."

This is the substrate everything else depends on. Without it:
- Generation can't reason about impact
- Execution can't understand what it's testing in context
- Interpretation can't explain failures through the org's actual structure
- Evolution can't know what to update when the org changes

This is designed first.

### Substrate 2 — Test Representation

The data structure that represents a test case. Must be executable, human-readable, evolvable.

Richer than "JSON with steps." Captures test intent, coverage, relationships to org entities, execution history, assumptions about org state, provenance (which requirement generated it, which Domain Pack shaped it).

Must represent tests across all five archetypes using a common substrate — not five different representations.

### Substrate 3 — Generation Engine

Reads a requirement (Jira ticket, change spec, natural language prompt) and produces test cases represented in Substrate 2.

The architecture we've been iterating on (Architecture 4, v1 through v4) is an attempt at this substrate. Its design needs to be revisited once Substrates 1 and 2 are defined — with a proper semantic org model and rich test representation, Substrate 3 becomes considerably simpler than A4 proposed.

### Substrate 4 — Execution Engine

Runs tests and captures evidence. Archetype-aware: data-behavior tests run via API, configuration tests via Tooling API queries, permission tests via "run as" context, UI tests via browser automation, integration tests via event capture.

Current PrimeQA execution engine handles only Archetype 1 (CRUD + verify). This substrate must expand to all five archetypes over time. The design must permit that expansion without rewrite.

### Substrate 5 — Knowledge System

Persists and improves knowledge that shapes generation and execution.

Includes:
- Domain Packs (prescriptive patterns, current)
- System rules (proscriptive rules, current)
- Learned facts specific to a tenant's org
- Cross-tenant patterns that stay tenant-isolated
- User feedback signals that tune future generations

Gets smarter the more the system is used. Not static configuration.

### Substrate 6 — Observation and Interpretation

Understands what happened when tests ran. Explains failures in human terms.

Not "test 37 failed with error X." Rather: "test 37 failed because the Flow 'Opportunity Close' was deactivated in the sandbox last Tuesday by user Y. Here's the change log. Test was last green 6 days ago."

Connects test results to org changes, code commits, deployments, configuration changes. Turns raw data into actionable information.

### Substrate 7 — Conversation and Control

The natural-language layer through which users interact with the system.

"Why is our regression coverage dropping?" "What's at risk if we deploy this package?" "Show me tests for the new approval process." "Did yesterday's failures have a common cause?"

Not a chatbot bolted onto a dashboard. Integrated throughout: generation is conversational, debugging is conversational, coverage exploration is conversational.

### Substrate 8 — Evolution Engine

Tests maintain themselves as the org evolves.

Field renamed? References in affected tests update. New required field added? Affected tests adjust. Validation rule changed? Tests re-verified against the new rule. Flow deactivated? Dependent tests flagged for review.

This is the maintenance burden Provar dumps on customers. PrimeQA automates it.

## How Substrates Relate

```
        Substrate 7 (Conversation)
                 |
                 v
       Substrate 6 (Interpretation)
          ^                ^
          |                |
  Substrate 4 <----- Substrate 8 (Evolution)
  (Execution)              |
          ^                |
          |                v
  Substrate 3 <----- Substrate 5 (Knowledge)
  (Generation)             |
          ^                |
          |                v
          +----- Substrate 1 (Semantic Org Model)
                         ^
                         |
                  Substrate 2 (Test Representation) --+
                         ^                            |
                         +----------------------------+
```

Read the graph as: every higher substrate depends on substrates below. Substrate 1 (semantic org model) is foundational — it is referenced directly by Generation, Execution, Evolution, Interpretation, and Knowledge.

Substrate 2 (test representation) is also foundational and referenced by Generation, Execution, Interpretation, Evolution.

Substrate 5 (knowledge) is cross-cutting — it shapes generation, gets signals from execution and user feedback, and feeds interpretation.

Substrate 7 (conversation) sits on top and touches every other substrate as a user-facing surface.

## Design Order

Not a rigid sequence, but a preferred ordering that respects dependencies:

1. **Substrate 1** (Semantic Org Model) — must be designed first. Everything else depends on it.
2. **Substrate 2** (Test Representation) — designed early, in parallel with S1 if possible. Generation and Execution can't be designed without it.
3. **Substrate 3** (Generation Engine) — designed after S1 and S2 have stable contracts.
4. **Substrate 4** (Execution Engine) — designed in parallel with S3; they share Substrate 2 as the contract between them.
5. **Substrate 5** (Knowledge System) — extends/formalizes the current Domain Packs and System Rules infrastructure. Can be designed after S1-S4 have initial designs.
6. **Substrate 6** (Observation and Interpretation) — depends on S4 producing rich execution data and S1 providing org context.
7. **Substrate 8** (Evolution Engine) — depends on S1 detecting changes and S2 being rewritable.
8. **Substrate 7** (Conversation and Control) — last. Depends on every other substrate having queryable APIs.

Implementation order may differ from design order — we may ship a simple execution expansion (S4) before we finish designing S6. But foundational design work proceeds in the order above.

## What This Vision Does Not Include

Deliberate exclusions, to keep scope clear:

- **Testing outside Salesforce.** PrimeQA is Salesforce-specific. The substrates are general in shape but tuned for Salesforce semantics.
- **Manual test script authoring tools.** We don't compete with TestRail or Zephyr on test case management for manually-authored tests. Our tests are system-generated.
- **Code-based test frameworks.** We don't produce Apex test classes or Selenium scripts. We produce executable test representations.
- **Replace developers' unit tests.** We test the org's behavior, not Apex code correctness. Apex test classes still run in the developer's CI.

These exclusions can be revisited, but they are the current scope boundaries.
