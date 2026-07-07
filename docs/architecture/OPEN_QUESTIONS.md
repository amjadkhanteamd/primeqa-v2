# PrimeQA Architecture — Open Questions

Questions that affect multiple substrates or are not yet assigned. Substrate-specific open questions live in the relevant substrate's directory.

---

## Resolved

- ~~Q-001 — Tenant isolation model for learned knowledge~~ → resolved by D-006 + D-011
- ~~Q-002 — Storage backend for the semantic org model~~ → resolved by D-014 (Postgres with graph-friendly design)
- ~~Q-003 — Sync model between live Salesforce orgs and the semantic model~~ → resolved by D-009 + D-020 (background + on-demand, entity-scoped schedules)
- ~~Q-004 — How does Architecture 4's useful work carry forward?~~ → resolved by
  D-085…D-088 (S3 Theme 5): tool-use selected as the generation topology, three
  thin semantic primitives (`propose_semantic_intent`, `select_canonical`,
  `emit_outcome` in `primeqa/generation/tools.py`). See
  `substrate_3_generation/OPEN_QUESTIONS.md` S3-Q-005. (Moved here at the
  2026-07-07 status audit — the entry already recorded its own resolution but
  sat under Open.)
- ~~Q-005 — Is there a substrate between "Execution Engine" and "Observation"?~~
  → answered **no** by the D-111 S4/S6 boundary: S4 captures truth
  (`evidence.outcome`), S6 consumes it directly and interprets — carried
  verbatim, never re-judged. No intermediate "result processing" substrate was
  needed. (Status audit 2026-07-07.)

## Open

### Q-006 — Does the Evolution Engine (S8) act autonomously or with human approval?

> **Status 2026-07-07:** partially decided. For fix proposals the answer is
> human-gated (D-236: the agent proposes with confidence + rationale + diff;
> auto-apply is flag-gated and sandbox-only, `evolution/repair.py`). The
> re-grounding autonomy boundary — when S8 may re-ground without a human gate —
> remains explicitly fenced in S8's deferred mechanics phase (see S8-Q-006).

When the org changes (field renamed, flow deactivated), S8 may update affected tests:
- Autonomous: system updates and notifies
- Review-required: system proposes, user approves

Could vary by change type. Revisit during S8 design.

### Q-007 — Logical version naming policy

> **Status 2026-07-07:** still open — the dual-identifier columns exist in the
> substrate schema, but no naming-policy code populates `version_name` and no
> manual-checkpoint feature exists. Correctly waiting on the first
> manual-checkpoint feature work.

Phase 2 (D-016) committed to dual identifiers: `version_seq` (BIGINT, monotonic) for queries, `version_name` (VARCHAR) for human use. Naming convention `<type>-<timestamp>-<sequence>` (e.g., `deploy-20260425-001`).

Refinement still open: How are user-named manual checkpoints structured? `manual-<timestamp>-<user>-<freeform>`? Allow arbitrary user naming?

Decide during Phase 3 (operational details) or first manual-checkpoint feature work.

### Q-008 — How does S5 (Knowledge System) actually derive shareable patterns from per-tenant models?

> **Status 2026-07-07:** reshaped by what S5 actually shipped
> (`primeqa/knowledge/`): curated domain packs + per-tenant learned rules +
> the system-rules channel. Cross-tenant statistical derivation was never
> built — it is explicitly a deferred vision piece (S5 `DEFERRED_ITEMS`).
> The design question stands for whenever that piece is pulled.

D-011 commits to the cross-tenant boundary policy. The mechanism by which S5 derives Tier 2 patterns and Tier 3 statistics from per-tenant models is left for S5 design.

Real design problem (statistical derivation that respects the boundary). Revisit when S5 design begins.

---

## Questions specific to substrates

See:
- `substrate_1_semantic_org_model/OPEN_QUESTIONS.md`
- `substrate_2_test_representation/OPEN_QUESTIONS.md`
- `substrate_3_generation/OPEN_QUESTIONS.md`
- `substrate_4_execution/OPEN_QUESTIONS.md`
- `substrate_6_intelligence/OPEN_QUESTIONS.md`
- `substrate_7_conversation/OPEN_QUESTIONS.md`
- `substrate_8_evolution/OPEN_QUESTIONS.md`

(S5 knowledge has no OPEN_QUESTIONS.md; its open items live in its
`DEFERRED_ITEMS`/SPEC.)
