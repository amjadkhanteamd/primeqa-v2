-- =============================================================================
-- D-242 — one-time S1 maintenance: close edges orphaned by entity supersession.
-- Tenant: tenant_1 (schema-qualified throughout). Run by AK on the Railway prod DB.
-- =============================================================================
--
-- WHAT THIS FIXES
--   The live sync edge-writer (primeqa/sync/materialize.py) closes edges only by
--   set-difference over the CURRENT (incoming) source entity id. When a source
--   entity is superseded it gets a NEW row id (SCD-2 close-old/insert-new,
--   materialize.py:188-196); the edge-writer then reads existing edges only for
--   the new ids, so the OLD id's edges are never in scope and keep
--   valid_to_seq IS NULL — a dangling "active" edge hanging off a now-historical
--   entity version. The lifecycle primitive that WOULD close them
--   (primeqa/semantic/derivation.py::supersede_and_derive, step 1) has no
--   production caller. See D-242 in docs/architecture/DECISIONS_LOG.md.
--
--   This closes each dangle at the seq its SOURCE entity was superseded
--   (valid_to_seq := src.valid_to_seq) — the bitemporally-correct moment the
--   edge should have ended.
--
-- CENSUS (read-only, 2026-06-14, HEAD logical_version_seq = 64):
--   3154 dangles — Profile 2331, Field 714, Layout 76, User 29, ValidationRule 4.
--   Closing each to src.valid_to_seq yields a valid interval for ALL 3154
--   (gap 1..11; zero rows where close <= open). 3150/3154 are legit env-59
--   daily supersession; 4 are from two sandbox test orgs.
--
-- SAFETY (adversarially verified, all grounded against prod read-only + the code):
--   * Row-selection closes EXACTLY the 3154 dangles; 1:1 join on entities PK,
--     no fan-out; the 20389 legit current edges (current source) are excluded.
--   * Changes NO current read result — dangles never surface in current reads
--     (outbound binds a current near-id; inbound filters via far-source as-of).
--   * Idempotent: a second run finds 0 dangles and commits a no-op.
--   * Self-guarding: sets the tenant GUC the edges_tenant_assertion CHECK needs,
--     and RAISES (auto-rollback) if the population drifted from expectation.
--
-- HOW TO RUN
--   Run OUTSIDE the nightly sync window (~04:05-04:15 UTC).
--     psql "$DATABASE_URL" -f scripts/backfill_close_dangling_edges_d242.sql
--   The leading SELECT previews the population (expect 3154); the DO block does
--   the work and prints a NOTICE with the closed count, or RAISES on any drift
--   (in which case nothing is changed — investigate and re-census before retry).
-- =============================================================================

-- ---- Preview (read-only): expect 3154 -------------------------------------
SELECT count(*) AS dangles_to_close
FROM tenant_1.edges e
JOIN tenant_1.entities src ON src.id = e.source_entity_id
WHERE e.valid_to_seq IS NULL
  AND src.valid_to_seq IS NOT NULL
  AND src.valid_to_seq > e.valid_from_seq;

-- ---- Backfill (self-guarding; auto-rollback on drift) ----------------------
DO $$
DECLARE
  v_before  bigint;
  v_updated bigint;
  v_after   bigint;
  v_patho   bigint;  -- dangles that CANNOT be safely closed (close <= open); expect 0
BEGIN
  -- edges_tenant_assertion CHECK = (tenant_id = current_setting('app.tenant_id')::int)
  -- is re-evaluated on every updated row; without this the UPDATE aborts.
  PERFORM set_config('app.tenant_id', '1', true);

  SELECT count(*) INTO v_patho
  FROM tenant_1.edges e
  JOIN tenant_1.entities src ON src.id = e.source_entity_id
  WHERE e.valid_to_seq IS NULL
    AND src.valid_to_seq IS NOT NULL
    AND src.valid_to_seq <= e.valid_from_seq;

  SELECT count(*) INTO v_before
  FROM tenant_1.edges e
  JOIN tenant_1.entities src ON src.id = e.source_entity_id
  WHERE e.valid_to_seq IS NULL
    AND src.valid_to_seq IS NOT NULL
    AND src.valid_to_seq > e.valid_from_seq;

  UPDATE tenant_1.edges e
  SET valid_to_seq = src.valid_to_seq
  FROM tenant_1.entities src
  WHERE e.source_entity_id = src.id
    AND e.valid_to_seq IS NULL
    AND src.valid_to_seq IS NOT NULL
    AND src.valid_to_seq > e.valid_from_seq;   -- strict-interval guard (mirrors supersede_and_derive)
  GET DIAGNOSTICS v_updated = ROW_COUNT;

  SELECT count(*) INTO v_after
  FROM tenant_1.edges e
  JOIN tenant_1.entities src ON src.id = e.source_entity_id
  WHERE e.valid_to_seq IS NULL
    AND src.valid_to_seq IS NOT NULL
    AND src.valid_to_seq > e.valid_from_seq;

  IF v_after <> 0 OR v_updated <> v_before THEN
    RAISE EXCEPTION 'dangle backfill aborted (rolled back): before=% updated=% after=% pathological=%. Expected after=0 and updated=before; population drifted, re-census before retry.', v_before, v_updated, v_after, v_patho;
  END IF;

  RAISE NOTICE 'D-242 dangle backfill OK: closed % edges; pathological(untouched)=%; remaining dangles=%.',
    v_updated, v_patho, v_after;
END $$;

-- ---- Post-verify (read-only): expect 0 -------------------------------------
SELECT count(*) AS dangles_remaining
FROM tenant_1.edges e
JOIN tenant_1.entities src ON src.id = e.source_entity_id
WHERE e.valid_to_seq IS NULL
  AND src.valid_to_seq IS NOT NULL
  AND src.valid_to_seq > e.valid_from_seq;
