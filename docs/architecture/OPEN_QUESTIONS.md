# PrimeQA Architecture — Open Questions

Questions that affect multiple substrates or are not yet assigned. Substrate-specific open questions live in the relevant substrate's directory.

When a question is answered, move it into DECISIONS_LOG.md as a formal decision and remove from this list.

---

## Cross-cutting questions

### Q-001 — What is the tenant isolation model for learned knowledge?

Substrate 5 (Knowledge System) will accumulate tenant-specific facts and cross-tenant patterns. The boundary between "tenant's private knowledge" and "aggregated learning across tenants" needs a clear model.

Implications for Substrate 1 (org model stores facts about this org), Substrate 5 (knowledge), Substrate 7 (conversation — what can users ask across their history vs their org only).

### Q-002 — Is the semantic org model a graph database, relational, or something else?

Substrate 1's storage technology is not yet decided. Graph databases (Neo4j, FalkorDB, in-process like NetworkX) are a natural fit for relationship-heavy data. Relational (PostgreSQL with JSONB, possibly with pgvector — which PrimeQA already uses) keeps infrastructure simple.

This decision affects query expressiveness, performance characteristics, ops burden, and how other substrates interact with S1. Decide during S1 design work.

### Q-003 — What is the sync model between live Salesforce orgs and the semantic model?

Substrate 1 represents an org's state, but the org changes constantly. When do we refresh? Continuous sync is expensive. Snapshot-on-demand may be stale. Event-driven (react to Change Data Capture, Platform Events, deploy notifications) is ideal but requires infrastructure.

Will need a decision framework: which parts of the model are hot (refreshed often) vs warm (on-demand) vs cold (snapshotted at known intervals).

### Q-004 — How does Architecture 4's useful work carry forward?

A4 spec (v1-v4) captured real design thinking: scenario declarations binding execution, state refs returned by tools not invented, strict validation errors, retry with narrowed context. Some of this transfers to the eventual Substrate 3 (Generation Engine) design. Some doesn't — the execute-during-generation pattern is explicitly dropped.

When we design S3, we revisit A4 for salvageable patterns. Don't archive-and-forget.

### Q-005 — Is there a substrate between "Execution Engine" and "Observation" that we haven't named?

Question raised by Claude during vision-setting. S4 runs tests and captures evidence; S6 interprets results and explains failures. Is there a "result processing" substrate between them — a place where raw execution data is normalized, enriched with org context, and made queryable? Or is that part of S4 or S6?

Revisit when S4 or S6 design begins.

### Q-006 — Does the Evolution Engine (S8) act autonomously or with human approval?

When the org changes (field renamed, flow deactivated), S8 may update affected tests. Two modes possible:

- Autonomous: system updates tests and notifies the user
- Review-required: system proposes updates, user approves

Could vary by change type (autonomous for cosmetic changes, review-required for semantic changes). Revisit during S8 design.

---

## Questions specific to substrates

See:
- `substrate_1_semantic_org_model/OPEN_QUESTIONS.md`
- (other substrates' OPEN_QUESTIONS.md as those substrates come online)
