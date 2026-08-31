-- 065: standard map SETS get their own lifecycle (LLD Phase 4 §b).
--
-- WHY: `add_standard_map` gates authoring on the RULE VERSION being DRAFT
-- (content freeze from REVIEW onward). Adding EN 301 549 / Section 508
-- projections to 72 ACTIVE rule versions would therefore force a v2 of
-- every rule — falsely recording that 72 rules CHANGED when only their
-- projection was added, and contradicting "rules are atoms, standards are
-- maps" (D-462).
--
-- THE FIX: a map set is a reviewable unit in its own right, mirroring
-- catalogue releases. The authoring gate moves from "the rule version is
-- DRAFT" to "the MAP SET is DRAFT". The rule content-freeze law is
-- untouched: what a rule CHECKS is still frozen at REVIEW; an additional
-- PROJECTION of it may be asserted and is reviewed as its own unit.
-- Maps stay bound to (rule_id, rule_version) — a projection is asserted of
-- a SPECIFIC rule version, so a future v2 must re-assert its maps.
--
-- Single-ACTIVE is enforced per STANDARD (not per standard+version): the
-- projection for "EN301549" must be unambiguous. Other versions coexist in
-- APPROVED/RETIRED and can be rendered by explicit id.
-- Idempotent.
BEGIN;

CREATE TABLE IF NOT EXISTS s5_standard_map_sets (
    id               BIGSERIAL PRIMARY KEY,
    standard         VARCHAR(24) NOT NULL,
    standard_version VARCHAR(64) NOT NULL,
    state            VARCHAR(12) NOT NULL DEFAULT 'DRAFT',
    provenance       JSONB NOT NULL DEFAULT '{}'::jsonb,
    notes            TEXT NOT NULL DEFAULT '',
    content_hash     CHAR(64),
    created_by       INT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_by      INT,
    reviewed_at      TIMESTAMPTZ,
    activated_at     TIMESTAMPTZ,
    CONSTRAINT s5_standard_map_sets_standard_known CHECK (
        standard IN ('WCAG22','EN301549','SECTION508')),
    CONSTRAINT s5_standard_map_sets_state_known CHECK (
        state IN ('DRAFT','REVIEW','APPROVED','ACTIVE','RETIRED')),
    CONSTRAINT s5_standard_map_sets_unique_version UNIQUE (standard, standard_version)
);

-- Exactly one ACTIVE set per standard — the unambiguous default projection.
CREATE UNIQUE INDEX IF NOT EXISTS s5_standard_map_sets_single_active
    ON s5_standard_map_sets (standard) WHERE state = 'ACTIVE';

ALTER TABLE s5_standard_maps
    ADD COLUMN IF NOT EXISTS map_set_id BIGINT REFERENCES s5_standard_map_sets (id);
-- Per-map provenance: derived / engine_corroborated / authored, plus any
-- engine disagreement surfaced for the reviewer and the rationale text.
ALTER TABLE s5_standard_maps
    ADD COLUMN IF NOT EXISTS provenance JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS s5_standard_maps_map_set_idx
    ON s5_standard_maps (map_set_id);

-- The WCAG22 maps seeded in 063/064 predate map sets. Give them their
-- implicit set so the model is uniform (one ACTIVE set per standard) and
-- the projection read has no special case for "legacy maps".
INSERT INTO s5_standard_map_sets
    (standard, standard_version, state, provenance, notes, created_by,
     activated_at)
SELECT 'WCAG22', 'WCAG 2.2 AA', 'ACTIVE',
       '{"origin": "seeded", "lineage": "migrations 063 (axe 4.13.0 seed) + 064 (ACC-05 heading/landmark closure, Plimsol registry authority)", "retrofitted_by": "migration 065"}'::jsonb,
       'Retrofitted set for the WCAG22 maps that predate the map-set lifecycle.',
       1, NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM s5_standard_map_sets WHERE standard = 'WCAG22');

UPDATE s5_standard_maps m
SET map_set_id = (SELECT id FROM s5_standard_map_sets
                  WHERE standard = 'WCAG22' AND state = 'ACTIVE')
WHERE m.standard = 'WCAG22' AND m.map_set_id IS NULL;

-- Uniqueness is PER MAP SET, not per standard. The original constraint
-- UNIQUE (rule_id, rule_version, standard, criterion) predates map sets
-- and would forbid a SECOND version of a standard's projection from
-- asserting the same clause — i.e. it would block exactly the
-- "EN V3.2.1 and a future EN version coexist" property the map-set
-- lifecycle exists to provide. Widened after the backfill above, so no
-- row is left with a NULL map_set_id.
ALTER TABLE s5_standard_maps
    DROP CONSTRAINT IF EXISTS s5_standard_maps_unique;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 's5_standard_maps_unique_in_set') THEN
        ALTER TABLE s5_standard_maps
            ADD CONSTRAINT s5_standard_maps_unique_in_set
            UNIQUE (map_set_id, rule_id, rule_version, standard, criterion);
    END IF;
END $$;

COMMIT;
