# PrimeQA Architecture — Open Questions

Questions that affect multiple substrates or are not yet assigned. Substrate-specific open questions live in the relevant substrate's directory.

When a question is answered, move it into DECISIONS_LOG.md as a formal decision and remove from this list.

---

## Resolved

- ~~Q-001 — Tenant isolation model for learned knowledge~~ → resolved by D-006 (per-tenant authoritative) + D-011 (cross-tenant boundary policy).
- ~~Q-003 — Sync model between live Salesforce orgs and the semantic model~~ → resolved by D-009 (background + on-demand, no event-driven for v1).

## Open

### Q-002 — What is the storage backend for the semantic org model?

Substrate 1's storage technology is not yet decided. Options:
- PostgreSQL with JSONB + relational tables (leverages existing infrastructure; pgvector already deployed)
- Graph database (Neo4j, FalkorDB, etc.)
- In-process graph (NetworkX or similar) with persistent snapshots
- Hybrid (relational for entities, graph layer for traversals)

This is one of the most consequential decisions for S1. Defer to Phase 2 design when concrete query patterns and scale requirements are clearer.

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

### Q-007 — Logical version naming policy

D-007 commits to logical version names but doesn't define a naming policy. Examples used in SPEC: `v_genesis`, `v_2026_04_24_pre_deploy`, `v_deploy_42`.

Should names follow a fixed schema (timestamp + tag), be free-form when manually checkpointed, or both? Decide before Phase 3 (operational details).

### Q-008 — How does S5 (Knowledge System) actually derive shareable patterns from per-tenant models?

D-011 commits to the cross-tenant boundary policy. The mechanism by which S5 derives Tier 2 patterns and Tier 3 statistics from per-tenant models is left for S5 design.

This is a real design problem (statistical derivation that respects the boundary), not just a policy question. Revisit when S5 design begins.

---

## Questions specific to substrates

See:
- `substrate_1_semantic_org_model/OPEN_QUESTIONS.md`
- (other substrates' OPEN_QUESTIONS.md as those substrates come online)
