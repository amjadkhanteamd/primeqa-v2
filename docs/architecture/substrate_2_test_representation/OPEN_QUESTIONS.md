# Substrate 2 — Test Representation — Open Questions

Questions specific to this substrate's design. Cross-cutting
questions live in the top-level `OPEN_QUESTIONS.md`.

All substrate-2 design questions are now resolved. The substrate's
internal coherence (claims, recipes, identity, validation, mutation
paths) and external boundaries (S4 execution-history, JIRA
requirements, outward API surface, v2.2 disposition) are fully
specified across D-051 through D-065.

---

## Resolved

- ~~S2-Q-001 — Deepest invariant of a test case~~ → resolved by
  D-051. See `SPEC.md` §2.
- ~~S2-Q-002 — Common substrate across five archetypes (structural
  and semantic)~~ → resolved by D-052. See `SPEC.md` §3.
- ~~S2-Q-003 — Test case data model~~ → fully resolved across
  five sub-cycles:
  - Sub-cycle 1 (D-053): claim-kind taxonomy locked (16 kinds)
  - Sub-cycle 2 (D-054): recipe-kind taxonomy locked (5 kinds)
  - Sub-cycle 3 (D-056): storage realization (four-table layout,
    Pattern D)
  - Sub-cycle 4 (D-059): identity-hash mechanics and governance
    contract
  - Sub-cycle 5 (D-060): validation layering and the Semantic
    Transaction Coordinator

  See `SPEC.md` §3 (taxonomies), §4 (data model and validation
  layering), §6.3 (canonicalization mechanics).
- ~~S2-Q-004 — References to S1 entities: reproducibility vs
  evolvability~~ → resolved by D-058. Hybrid by layer: pinned
  required in identity-bearing layers, logical default with
  pinned opt-in in operational layers. Cross-layer validation
  is ontology enforcement. Identity_hash canonicalizes
  `entity_id` only from pinned references. Coverage derived
  from identity-bearing layer pinned refs only. Multi-mode
  `external_id` drift detection is S8's responsibility.
  Semantic-replay forward-resolution conditioned on S8-blessed
  transitions. See `SPEC.md` §5.
- ~~S2-Q-005 — Lifecycle and versioning model~~ → resolved by D-057.
  Effective-time supersession with `version_seq` canonical authority,
  `identity_hash` as semantic equivalence fingerprint, logical-default
  recipe FK, current-only coverage, no archival (lineage-continuity
  rationale). See `SPEC.md` §6.
- ~~S2-Q-006 — Mutation paths and authority over meaning~~ →
  resolved by D-061. Three mutation paths (human / S3 / S8)
  routed by the Semantic Transaction Coordinator with per-path
  authority rules. S8 invariant: **no autonomous semantic
  divergence** (mechanical via `identity_hash`); S8 can mutate
  any layer including identity-bearing layers provided hash
  preserves. Identity continuity (test_id) and semantic
  continuity (identity_hash) as orthogonal dimensions. Claim
  approval governed by hash change (mechanical); recipe
  re-approval as **conservative default** with future
  auto-preservation reserved. Linear supersession preserved;
  current-approved as governance resolution in Coordinator,
  not simple status lookup. Test-level approval as derived
  composition. Rollback via supersession, not status mutation.
  Four forward-compat reservations: recipe auto-preservation,
  merge/rebase, provenance streams, deprecation taxonomy. See
  `SPEC.md` §7.
- ~~S2-Q-007 — Execution-history boundary against S4~~ →
  resolved by D-062. Last-run snapshot pattern via new
  `test_recipe_runtime_state` table (per recipe, not per recipe
  version). Pure snapshot: no aggregate statistics, no history
  (S4 holds full history). S4 reports run outcomes via
  Coordinator callback (push-based; S2 never queries S4).
  Test-level runtime status as **resolution operation**
  composing recipe-level state with conservative initial policy.
  Multi-recipe outcome resolution has acknowledged pressure;
  raw recipe state exposed for consumers needing different
  composition policies. Two forward-compat reservations:
  richer runtime-state resolution, run history beyond last-run.
  See `SPEC.md` §8.
- ~~S2-Q-008 — Requirement linkage~~ → resolved by D-063.
  External typed reference model. New `test_requirement_links`
  table with multi-kind linkage (`generated_from` / `verifies`
  / `related_to`). No ticket content replicated in PrimeQA;
  external system (JIRA) remains source of truth. `external_system`
  as typed identifier today (enum); registry-based evolution
  reserved. Three forward-compat reservations: registry-based
  external systems, sprint/release associations, bidirectional
  sync. See `SPEC.md` §9.
- ~~S2-Q-009 — Outward surfaces (for S3, S4, S6, S8)~~ →
  resolved by D-064. Semantic Transaction Coordinator framed
  as **semantic OS infrastructure** rather than substrate-internal
  component. Five interface groups: write (actor-aware,
  authority-enforced), read (current-approved vs latest
  distinction), equivalence and discovery, runtime state,
  provenance. **Behavioral contracts** specified per interface
  (idempotency, authority, atomicity, error, concurrency,
  asymptotics) as substrate-level commitments. Three
  **resolution-class operations** named as first-class
  substrate concept (current-approved, runtime status, recipe
  selection). Wire format unspecified at substrate level;
  behavioral contracts are not. Three forward-compat
  reservations: cross-substrate Coordinator concerns, API
  versioning, behavioral contract evolution. See `SPEC.md` §10.
- ~~S2-Q-010 — Disposition of v2.2 test-management tables~~ →
  resolved by D-065. Per-table disposition: `test_cases`
  ABSORB; `test_case_versions` DROP; `requirements` DROP;
  `metadata_impacts` DROP; `sections`, `test_suites`,
  `suite_test_cases`, `ba_reviews` MIGRATE to future substrates
  (test catalog, review workflow). Substrate-2 ships first;
  orthogonal substrates ship later. Migration execution is
  post-Phase-3 implementation work. **Intentional architectural
  trade-off** framing: short-term v2.2 feature parity sacrificed
  for long-term substrate coherence. Gap is real, acceptable,
  intentional. See `SPEC.md` §11.

  > **Superseded in execution (status audit 2026-07-07):** the v1
  > retirement (D-191…D-221) overtook D-065's MIGRATE dispositions.
  > Migration `053_drop_v1_product_tables.sql` DROPPED `test_suites`,
  > `suite_test_cases`, `ba_reviews`, `test_cases`, and
  > `test_case_versions` — no migrate-to-future-substrate happened
  > for the suite/review tables. Only `sections` survived and is live
  > (`test_management/service.py` `create_section`). The D-065
  > *decision* stands as recorded history; this note records that
  > the later retirement resolved the deferred dispositions by drop.
- ~~S2-Q-011 — Trigger-kind classification~~ → resolved by D-055.
  5 kinds locked, four-discriminator framing extended, six-layer
  model amended (rename: "execution realization" → "observation
  realization"). See `SPEC.md` §3.

---

## Open

None. Substrate-2 design is complete. (The §1 synthesis section this
entry once pended on was written 2026-05-18 — see `SPEC.md` "Last
substantive update".)

> **Implementation status notes (audit 2026-07-07):**
> `test_recipe_runtime_state` shipped (alembic
> `20260518_1014_create_substrate_2_tables.py`, per D-062). The
> reserved provenance surfaces (`get_provenance` /
> `get_recipe_provenance`, SPEC §10.2) have NOT shipped — the S3
> generation ledger still owns emission history and has not retired
> into S2 provenance.
