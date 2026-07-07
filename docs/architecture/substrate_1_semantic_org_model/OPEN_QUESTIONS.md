# Substrate 1 — Semantic Org Model — Open Questions

Questions specific to this substrate's design. Cross-cutting questions live in the top-level OPEN_QUESTIONS.md.

---

## Resolved in Phase 1 (2026-04-24)

- Cross-tenant policy → resolved by D-011

## Resolved in Phase 2 (2026-04-25)

- ~~S1-Q-005 — RecordType + Profile + Layout three-way assignment~~ → resolved by D-019 (`ASSIGNED_TO_PROFILE_RECORDTYPE` edge with `record_type_entity_id` in properties)
- ~~S1-Q-007 — Initial Tier 1 entity coverage~~ → resolved by D-018 (10 entity types defined)
- Storage backend (top-level Q-002) → resolved by D-014 (Postgres with graph-friendly design)

---

## Resolved post-design (implementation caught up; status audit 2026-07-07)

- ~~S1-Q-002 — Validation rule formula parser scope~~ → resolved by D-107
  (`primeqa/semantic/formula/`: tokenizer + recursive-descent parser, typed AST,
  three-valued Kleene `eval`, `derive` → violating payload, emission gate).
  Supported: field refs, comparisons, AND/OR/NOT, ISBLANK/ISNULL/ISPICKVAL,
  PRIORVALUE/ISCHANGED/ISNEW. The "SaaS-specific functions" unknown resolved as
  predicted: unrecognized constructs (REGEX, CASE, cross-object refs, date math)
  return `NotParsed` and the claim is caveated instead of verified (D-107 Amendment).
- ~~S1-Q-008 — Default background sync schedule~~ → resolved by D-153: 24-hour
  default via the scheduler's `s1_sync_enqueuer_tick` (`primeqa/scheduler.py`) +
  `run_s1_sync_enqueuer_tick(resync_interval_hours=24)` (`primeqa/sync/consumer.py`);
  incomplete runs are resumed promptly. Remaining refinement (not blocking):
  D-020's per-entity-type cadence is designed but unimplemented — all phases run
  at the single 24 h cadence; tenant-configurable schedules deferred as ops work.

---

## Open — to be addressed during Tier 2 / Tier 3 design

### S1-Q-001 — Flow logic interpretation depth

> **Status 2026-07-07:** Tier 1 shipped — `phase_flow` extracts trigger type /
> activation / version into `flow_details`, and `TRIGGERS_ON` edges are derived
> from the flow start element. `parsed_logic` (JSONB) and
> `interpreted_at_capability_level` columns are reserved and NULL. Tier 2 depth
> remains the open decision; pull it when a concrete S3 grounding gap demands it.

Tier 2 commits to interpreting flow XML. The depth question remains:
- Minimum (Tier 2 starter): Extract entry conditions and record updates only
- Medium (Tier 2 mature): Interpret decision branches, loops, assignments
- Full (Tier 3): Simulate flow execution

Decide when Phase 2 design begins for flow modeling. Likely answer: minimum-Tier-2 starter.

### S1-Q-003 — Apex modeling approach (Tier 3)

> **Status 2026-07-07:** still open, correctly — Apex is Tier 3 per D-010; no
> entity type, no sync phase, no schema exists.

Options for Tier 3:
- Reference only: model knows apex classes/triggers exist, what objects they touch
- LLM-assisted interpretation: feed apex code to LLM at sync time, extract structured summaries
- Static analysis: build an actual analyzer

Likely answer: reference-only at Tier 3 entry, LLM-assisted summaries when value is shown to be high.

### S1-Q-004 — Granularity of change history events

Decided in Phase 2 (D-021): granular change_type values plus changed_field_names array. Field-level granularity for entity attribute changes; entity-level for create/delete; edge-level for edge changes.

(Effectively resolved by D-016 and D-021. Removing from open questions.)

### S1-Q-006 — Managed package handling

> **Status 2026-07-07:** partially settled in code as the hybrid option —
> `namespace_prefix` is tracked on `field_details`, and managed-package
> internals are filtered out of the Tier 1 sync scope (`primeqa/sync/phases.py`).
> No dedicated entity-type modeling. The full decision still waits on the first
> managed-package-heavy tenant, as written below.

Managed packages introduce namespaced entities with opaque internals. Options:
- First-class entities with namespace labels
- Opaque blobs
- Hybrid: public API exposed, internals opaque

Affects testability of orgs relying on managed packages. Decide during Tier 1 implementation when first managed-package-heavy tenant onboards.

---

## Open — Phase 3 deferred (operational details)

(S1-Q-008 — default sync schedule — resolved by D-153; moved to the resolved
section above.)

### S1-Q-009 — change_log retention policy

> **Status 2026-07-07:** still open — `change_log` is append-only with no purge
> logic anywhere in code. The only unbounded-growth item on this list; cheap to
> close when storage pressure appears (lean: after-N-years default,
> tenant-overridable).

`change_log` grows linearly with org activity. At what point do we purge?
- Never (keeps full audit history)
- After N years (compliance-friendly default)
- Tenant-configurable
- Based on storage pressure

Affects diff engine's "purged version" failure mode. Decide in Phase 3.

### S1-Q-010 — Materialized view refresh strategy

> **Status 2026-07-07:** decided-not-built — D-020 designed the matview; the
> D-024 lock-window decision explicitly deferred it (unneeded for the
> single-edge permission claim). The underlying permission edges
> (GRANTS_OBJECT_ACCESS / GRANTS_FIELD_ACCESS / HAS_PROFILE /
> HAS_PERMISSION_SET) ARE synced; only the aggregation view — and therefore
> this refresh question — remains.

`effective_field_permissions` materialized view (D-020). Refresh approaches:
- After every sync run (simple, may over-refresh)
- After permission-related sync (specific, requires sync-event awareness)
- Triggered by edge changes (most precise, complex)

Decide in Phase 3 based on observed sync patterns.

### S1-Q-011 — Tenant onboarding sequence

> **Status 2026-07-07:** largely built — org provisioning (D-150,
> `sync/credentials.py`), job enqueue/claim (D-153, `sync/jobs.py`), the sync
> engine with `sync_runs` phase tracking + resume-from-`last_completed_phase`
> (D-152) cover orchestration and failure recovery. Still open: the
> user-facing progress surface (the sync console reads status, but the
> onboarding UX doesn't exist) and explicit query-availability semantics
> during initial sync (materialize UPSERTs, so partial reads are possible).
> Belongs with the multi-tenant provisioning/onboarding plan.

When a tenant connects an org, what's the orchestration?
- How long does initial sync take?
- Is the model available for queries during initial sync?
- What's the failure-recovery path?
- How do we communicate progress to the user?

Decide in Phase 3 when implementation begins.

### S1-Q-012 — Schema migration parallelism

> **Status 2026-07-07:** still open — alembic runs sequentially per tenant
> schema (`version_table_schema` per D-015); no parallel orchestration exists.
> Irrelevant at the current tenant count; revisit when tenant provisioning
> scales.

D-015 mentions sequential vs parallel migration. At what scale do we need parallel? What's the failure handling for partial migrations?

Decide in Phase 3 when migration tooling is built.
