# PrimeQA Architecture — Open Questions

Questions that affect multiple substrates or are not yet assigned. Substrate-specific open questions live in the relevant substrate's directory.

---

## Resolved

- ~~Q-001 — Tenant isolation model for learned knowledge~~ → resolved by D-006 + D-011
- ~~Q-002 — Storage backend for the semantic org model~~ → resolved by D-014 (Postgres with graph-friendly design)
- ~~Q-003 — Sync model between live Salesforce orgs and the semantic model~~ → resolved by D-009 + D-020 (background + on-demand, entity-scoped schedules)

## Open

### Q-004 — How does Architecture 4's useful work carry forward?

A4 spec captured real design thinking: scenario declarations binding execution, state refs returned by tools not invented, strict validation errors, retry with narrowed context. Some of this transfers to the eventual Substrate 3 (Generation Engine) design.

Worth carrying forward (per archive/ARCHITECTURE_4_NOTE.md):
- Scenario binds execution (declared actors constrain operations)
- State is handed out, not invented (state refs returned by creation tools)
- Strict validation over silent recovery
- Retry with narrowed context
- Feature-flag-gated rollout with shadow mode

Open question: Is tool-use the right generation model, or is single-shot structured JSON generation with validate-then-retry sufficient? Address during S3 design with Tier 1 model in hand.

### Q-005 — Is there a substrate between "Execution Engine" and "Observation" that we haven't named?

S4 runs tests and captures evidence; S6 interprets results. Is there a "result processing" substrate between them — normalizing raw execution data, enriching with org context, making it queryable?

Revisit when S4 or S6 design begins.

### Q-006 — Does the Evolution Engine (S8) act autonomously or with human approval?

When the org changes (field renamed, flow deactivated), S8 may update affected tests:
- Autonomous: system updates and notifies
- Review-required: system proposes, user approves

Could vary by change type. Revisit during S8 design.

### Q-007 — Logical version naming policy

Phase 2 (D-016) committed to dual identifiers: `version_seq` (BIGINT, monotonic) for queries, `version_name` (VARCHAR) for human use. Naming convention `<type>-<timestamp>-<sequence>` (e.g., `deploy-20260425-001`).

Refinement still open: How are user-named manual checkpoints structured? `manual-<timestamp>-<user>-<freeform>`? Allow arbitrary user naming?

Decide during Phase 3 (operational details) or first manual-checkpoint feature work.

### Q-008 — How does S5 (Knowledge System) actually derive shareable patterns from per-tenant models?

D-011 commits to the cross-tenant boundary policy. The mechanism by which S5 derives Tier 2 patterns and Tier 3 statistics from per-tenant models is left for S5 design.

Real design problem (statistical derivation that respects the boundary). Revisit when S5 design begins.

---

## Questions specific to substrates

See:
- `substrate_1_semantic_org_model/OPEN_QUESTIONS.md`
- (other substrates' OPEN_QUESTIONS.md as those substrates come online)
