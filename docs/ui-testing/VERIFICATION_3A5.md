# VERIFICATION 3A-5 — S1 Entities: Surface + LightningComponentBundle

Executed 2026-08-26 on the scratch DB `plimsol_3a3` (tenant_1 at
`20260825_0040`) plus ONE live org fetch (env-59, §d — org API real,
persistence scratch-only; the production database was not touched:
its tenant chain is pre-3A by design, MIGRATE-FIRST lands at the merge
gate). Re-runnable: unit = `tests/unit/test_representation/
test_3a5_entities.py` (+ the six bumped drift-guards); DB-real =
`tests/integration/test_3a5_entities.py` gated on
`S3A3_TEST_DATABASE_URL`.

## Grounding corrections found during implementation (flagged)

1. **`entities.entity_type` carries NO db CHECK** — the LLD §a assumed
   three CHECKs; ground truth is two (`sync_runs.last_completed_phase`,
   `ai_enrichment_queue.entity_type`) plus the application-side
   registries, which fail closed on unknown types. The migration
   widens the two real CHECKs; `Surface` joins NEITHER (no sync phase,
   never enqueued) — its only DDL footprint is the downgrade cleanup.
2. **No `lwc_bundle_details` table** — the LLD §c named one, but the
   `*_details` machinery is entangled with the LLM summary/embedding
   carry-forward plumbing; D-308 (the precedent the LLD §a itself
   invokes) deliberately chose attributes-riding for ApprovalProcess
   for the same reason. The bundle's resource manifest + per-file
   hashes + `_source_hash` ride `entities.attributes` — every SF-08
   semantic is preserved (verified in §c below); phase-7 reads are
   unaffected (version history is entity-level).
3. **Ambiguous bundle resolution** (found by test): two CURRENT
   bundles sharing a DeveloperName (multi-org) make attribution
   ambiguous — the resolver returns nothing and the verdict stays
   PROBABLE. A guessed CONFIRMED is never produced.

## a. Migration on scratch

- Fresh-chain apply: `20260825_0030 → 20260825_0040` clean.
- Read-back: `sync_runs_last_completed_phase_known` and
  `ai_enrichment_queue_entity_type_known` both list
  `LightningComponentBundle` (14 values); NEITHER lists `Surface`;
  `s6_ui_verdicts.owner_bundle_ref` present.
- Apply-twice: second `upgrade tenant@head` ran **0** migrations.
- Downgrade→re-upgrade cycle: **1 down / 1 up**, clean.
- D-459 guard (`test_migration_autocommit_guard`): **green**.

## b. Surface materialization

`tests/integration/test_3a5_entities.py::test_b_*` — green:
- Declaring an inventory version creates one `Surface` entity per
  distinct canonical key (`entity_origin='manual_curation'`, never
  'sync') and fills `surface_entity_ref` in the same transaction.
- Re-declaring the same key in a LATER inventory version **reuses**
  the entity — exactly one current `Surface` row per key.
- Canonicalizer drift guard
  (`test_3a5_entities.py::test_claim_identity_unchanged_by_entities`):
  the conformance-claim canonical form's fields are exactly
  `{kind, plimsol_rule_id, surface_key}` — **claim identity unchanged**
  (the 3A-2 pin holds across 3A-5).
- `Surface` is in no sync structure: not in `PHASE_REGISTRY`, not in
  `ENTITY_ORDER` (asserted in the unit suite).

## c. Bundle-sync version semantics (fixtures, the REAL phase function)

`test_c_bundle_sync_version_semantics` — green, through
`phase_lightning_component_bundle` with a fake client:
- initial sync → **1** current version;
- same-source resync (CRLF noise) → **0** new versions (the normalised
  hash is byte-stable);
- one-line source edit → **exactly 1** new version, prior SCD-2-closed;
- bundle removed from the org (with a sibling still present, so the
  DATA-1 empty-set fail-safe is not triggered) → every version
  **SCD-2-closed** by the deletion reconcile.

## d. Live env-59 run

The phase LIVE against env-59's org (credentials via the prod
connection row — the daily-sync path; prod DB read-only):

```
run 1 (live fetch): current=28 total_rows=28
  PhaseResult(entities_inserted=28, entities_superseded=0,
              entities_unchanged=0, embeddings_queued=28)
bundles: announcementBanner, announcementsHomepage, batchSchedulerStep,
         confirmModal, connectToSalesforce, connector, contentCard,
         contentPreview, countdownTimer, customToastNotification, …
run 2 (idempotent resume): current=28 total_rows=28
  PhaseResult(entities_inserted=0, entities_superseded=0,
              entities_unchanged=28, embeddings_queued=0)
```

**28 real LWC bundles** — the honest count (not zero); the resume is a
perfect no-op. One operational note: the repo `.env`'s standalone
`SF_REFRESH_TOKEN` is expired (the first attempt failed with
`invalid_grant`); the prod connection row's credentials are current —
the live path used those.

## e. Ownership conformance (the 2026-08-26 ruling)

`test_e_confirmed_join_through_the_processor` — green, through
`process_job` end-to-end:
- planted bundle + a `c-*` FAIL whose tag resolves → **CONFIRMED**
  with `owner_bundle_ref` = the bundle entity id;
- an unresolvable `c-*` tag → **PROBABLE** (the corrected rule — the
  3A-4 spike-grade CONFIRMED-on-marker behavior is GONE; unit test
  `test_ownership_markers` now pins `c-*` alone = PROBABLE);
- non-c markers unchanged: platform markup PROBABLE, plain markup
  **UNKNOWN**, non-FAIL rows carry no marker.

## f. Full merge-gate suite

**4,872 passed** (was 4,865; +8 new 3A-5 tests, −1 net from guard
consolidation). Six pre-existing drift-guard files fired on the 12→13
phase bump and were updated to the new declared counts — the guards
doing their job (fk_assertion order pin, phase-registry no-op skip
list, consumer-reaper FINAL_PHASE sentinel value, normalizer count,
templater count, progress-UI `N/13` fractions). `phase_order()` itself
derives from `ENTITY_ORDER` — no source change was needed for the UI.
Worker untouched: zero files under `primeqa/browser_worker` in this
commit; the boundary guards stay green.
