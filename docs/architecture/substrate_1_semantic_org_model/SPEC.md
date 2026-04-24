# Substrate 1 — Semantic Org Model — SPEC

**Status:** SKELETON — design work has not yet begun. Sections marked `[TBD]` are placeholders.

**Last substantive update:** 2026-04-24 (skeleton created)

**Supersedes:** no prior design. The current flat "metadata context" in generation is an ad-hoc precursor.

---

## Purpose

This spec defines the Semantic Org Model: the data structure, contract, and operational characteristics of PrimeQA's representation of a Salesforce org.

Design sessions fill this document in. Each session ends with a commit and an update to EVOLUTION.md.

---

## 1. Scope

### 1.1 What this spec defines
[TBD] The concrete data model, query interface, refresh model, and storage backend for the semantic org model.

### 1.2 What this spec does NOT define
[TBD] Consumer usage patterns (those live in consuming substrates' specs). Tenant isolation policies (those are cross-cutting). Metadata API query mechanics (those are implementation details).

---

## 2. Requirements

### 2.1 Functional requirements

[TBD] To be filled in during design. Starting list of questions the model must answer:

- Which objects exist in this org, and what are their fields?
- What are the relationships between objects (lookups, master-details, junction relationships, external IDs)?
- What automations (flows, workflows, process builder, apex triggers, approval processes) exist and what do they trigger on?
- What validation rules exist on each object, and what do they assert?
- What record types exist, and what page layouts / picklist values apply to each?
- What profiles and permission sets exist, and what do they grant?
- What sharing rules affect record visibility?
- What is the org's recent change history (fields added, renamed, removed; flows activated/deactivated; etc.)?
- Given a proposed change, what else in the org might be affected?
- Given a test failure, what recent org changes could explain it?

### 2.2 Non-functional requirements

[TBD] Initial thoughts:

- Must scale to enterprise orgs (thousands of objects, tens of thousands of fields)
- Queries used by generation must return in <500ms
- Incremental refresh preferred; full rebuild acceptable for initial sync
- Per-tenant isolation
- Auditable: we can show what the model contained at a given time
- Debuggable: a human should be able to inspect the model and understand it

---

## 3. Data Model

### 3.1 Entities
[TBD] What are the top-level entity types in the model? Starting candidates:

- Object (Salesforce sObject)
- Field (belongs to an Object)
- Relationship (between two Objects via a Field)
- RecordType (belongs to an Object)
- Layout (belongs to an Object, assigned to Profile + RecordType combinations)
- ValidationRule (belongs to an Object)
- Flow (standalone; triggers defined in metadata)
- ApexTrigger (belongs to an Object)
- ApprovalProcess (belongs to an Object)
- Profile
- PermissionSet
- PermissionSetAssignment (links User to PermissionSet)
- User
- SharingRule (complex; belongs to an Object)
- ChangeEvent (historical: describes a change to any of the above)

This list is a starting point, not a decision.

### 3.2 Relationships between entities
[TBD]

### 3.3 Representation of behavior
[TBD] How much of flow logic, validation rule formulas, apex trigger behavior do we actually model? Or do we store references and defer interpretation?

### 3.4 Change history representation
[TBD]

---

## 4. Query Interface

### 4.1 Query patterns
[TBD] What queries do consuming substrates need? Likely categories:
- "What are the fields of object X?"
- "What automations trigger on object X?"
- "What references field Y?" (impact analysis)
- "What's different between today's model and last week's?"
- "Can profile P update field F on object O?"

### 4.2 Query language
[TBD] Structured API? Graph query language (Cypher, Gremlin)? SQL? Custom DSL?

### 4.3 Traversal semantics
[TBD] How deep do we traverse relationships by default? How does the caller control depth?

---

## 5. Storage Backend

### 5.1 Storage technology
[TBD] See Q-002 in top-level OPEN_QUESTIONS.md. Candidates include:
- PostgreSQL with JSONB + relations (leverages existing infra)
- Graph database (Neo4j, FalkorDB, etc.)
- In-process graph (NetworkX or similar) with persistent snapshots
- Hybrid (relational for entities, graph for traversals)

This is one of the most consequential decisions in this substrate. Defer until requirements are clearer.

### 5.2 Tenant isolation
[TBD]

### 5.3 Schema evolution
[TBD] How do we version the model's own schema as it evolves?

---

## 6. Refresh Model

### 6.1 Sync mechanism
[TBD] See Q-003. Decision framework needed: event-driven vs polled vs on-demand, and which parts of the model fall into each category.

### 6.2 Staleness tolerance
[TBD] For which queries is yesterday's data acceptable? For which do we need minute-fresh?

### 6.3 Conflict resolution
[TBD] If concurrent refreshes happen (unlikely but possible), how do we resolve?

---

## 7. Integration Contract

### 7.1 Consumer interface
[TBD] How do other substrates access the model? Python API? HTTP API? Direct DB access?

### 7.2 Event emission
[TBD] When the model detects a change, what events do other substrates receive? This is what feeds Substrate 8 (Evolution Engine).

### 7.3 Observability
[TBD] How do we know the model is healthy, current, and answering queries correctly?

---

## 8. Current State

### 8.1 What exists today
PrimeQA's current metadata context, located in [TBD: point Claude Code at the actual module path during design]. Flat structure, per-generation scope, not persistent.

### 8.2 Migration path
[TBD] How do we transition from the current flat structure to the semantic model without breaking existing generation?

---

## 9. Non-Decisions

Items explicitly deferred to implementation or to later design sessions:

[TBD to be populated as design progresses]

---

## 10. Glossary

See GLOSSARY.md in this substrate's directory.

---

## End of SPEC (skeleton)

To be filled in progressively through design sessions. Each session's updates are committed and logged in EVOLUTION.md.
