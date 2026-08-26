-- 062: S5 Rule Registry + Catalogue Store (LLD 3A-1, HLD DE-01; D-460/D-461).
-- PLATFORM-GLOBAL catalogue in public — one truth, versioned once (the
-- llm_models/061 precedent). Tenant custom rules (R3, future) will live in
-- tenant schemas under the disjoint PLM-CUST namespace; nothing tenant-scoped
-- here. Idempotent per the 016+ convention. MIGRATE-FIRST (D-285): apply
-- before any reader deploys. Seeded by 063 (generated, reviewable fixture).
-- Lifecycle: DRAFT->REVIEW->APPROVED->VERSIONED->ACTIVE->RETIRED; transitions
-- are SERVICE-layer only (knowledge/rule_lifecycle.py, superadmin + audit);
-- the DB enforces the corruption-class invariants: version uniqueness, the
-- single-ACTIVE partial unique index, and the state vocabulary.

-- 1. identity anchor
CREATE TABLE IF NOT EXISTS s5_rules (
    rule_id     VARCHAR(32) PRIMARY KEY,
    owner       VARCHAR(16) NOT NULL DEFAULT 'plimsol',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT s5_rules_id_shape CHECK (rule_id ~ '^PLM-[A-Z0-9]+-[0-9]{3}$'),
    CONSTRAINT s5_rules_owner_known CHECK (owner IN ('plimsol'))
);

-- 2. immutable version rows + lifecycle state
CREATE TABLE IF NOT EXISTS s5_rule_versions (
    rule_id                VARCHAR(32) NOT NULL REFERENCES s5_rules(rule_id),
    version                INT NOT NULL,
    name                   VARCHAR(200) NOT NULL,
    description            TEXT NOT NULL,
    automation_capability  VARCHAR(24) NOT NULL,
    human_review_required  BOOLEAN NOT NULL,
    state                  VARCHAR(12) NOT NULL,
    seed_provenance        JSONB,
    created_by             INT,
    state_changed_by       INT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    state_changed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (rule_id, version),
    CONSTRAINT s5_rule_versions_capability_known CHECK (
        automation_capability IN
        ('AUTO','AUTO_WITH_ACTION','HUMAN_WITH_CANDIDATE','HUMAN_ONLY')),
    CONSTRAINT s5_rule_versions_state_known CHECK (
        state IN ('DRAFT','REVIEW','APPROVED','VERSIONED','ACTIVE','RETIRED'))
);
-- the single-ACTIVE invariant: at most one ACTIVE version per rule, DB-enforced
CREATE UNIQUE INDEX IF NOT EXISTS s5_rule_versions_single_active
    ON s5_rule_versions (rule_id) WHERE state = 'ACTIVE';
CREATE INDEX IF NOT EXISTS s5_rule_versions_state_idx
    ON s5_rule_versions (state);

-- 3. plimsol rule version -> engine rule(s); many-to-many by construction
CREATE TABLE IF NOT EXISTS s5_engine_bindings (
    id              BIGSERIAL PRIMARY KEY,
    rule_id         VARCHAR(32) NOT NULL,
    rule_version    INT NOT NULL,
    engine          VARCHAR(32) NOT NULL,
    engine_version  VARCHAR(32) NOT NULL,
    engine_rule_id  VARCHAR(128) NOT NULL,
    FOREIGN KEY (rule_id, rule_version)
        REFERENCES s5_rule_versions (rule_id, version),
    CONSTRAINT s5_engine_bindings_unique
        UNIQUE (rule_id, rule_version, engine, engine_version, engine_rule_id)
);
CREATE INDEX IF NOT EXISTS s5_engine_bindings_engine_idx
    ON s5_engine_bindings (engine, engine_version, engine_rule_id);

-- 4. rule -> criterion per standard (MAPPING, never identity — D2)
CREATE TABLE IF NOT EXISTS s5_standard_maps (
    id            BIGSERIAL PRIMARY KEY,
    rule_id       VARCHAR(32) NOT NULL,
    rule_version  INT NOT NULL,
    standard      VARCHAR(24) NOT NULL,
    criterion     VARCHAR(32) NOT NULL,
    level         VARCHAR(3),
    FOREIGN KEY (rule_id, rule_version)
        REFERENCES s5_rule_versions (rule_id, version),
    CONSTRAINT s5_standard_maps_standard_known CHECK (
        standard IN ('WCAG22','EN301549','SECTION508')),
    CONSTRAINT s5_standard_maps_unique
        UNIQUE (rule_id, rule_version, standard, criterion)
);
CREATE INDEX IF NOT EXISTS s5_standard_maps_standard_idx
    ON s5_standard_maps (standard, criterion);

-- 5. pinned artifacts: hash-referenced repo files, NEVER bytea (SAD A4/A10 —
-- bytes ship in the worker image; the store is the authority for the pin)
CREATE TABLE IF NOT EXISTS s5_artifacts (
    id           BIGSERIAL PRIMARY KEY,
    kind         VARCHAR(24) NOT NULL,
    name         VARCHAR(64) NOT NULL,
    version      VARCHAR(32) NOT NULL,
    sha256       CHAR(64) NOT NULL,
    repo_path    VARCHAR(255) NOT NULL,
    source_url   VARCHAR(500),
    byte_size    BIGINT,
    retrieved_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT s5_artifacts_unique UNIQUE (kind, name, version)
);

-- 6. catalogue releases: the D-461 manifest pin target; membership RECORDED
-- at creation (D-281 drift-immunity law), never recomputed
CREATE TABLE IF NOT EXISTS s5_catalogue_releases (
    id            BIGSERIAL PRIMARY KEY,
    notes         TEXT,
    content_hash  CHAR(64) NOT NULL,
    created_by    INT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS s5_catalogue_release_members (
    release_id    BIGINT NOT NULL REFERENCES s5_catalogue_releases(id),
    rule_id       VARCHAR(32) NOT NULL,
    rule_version  INT NOT NULL,
    PRIMARY KEY (release_id, rule_id, rule_version),
    FOREIGN KEY (rule_id, rule_version)
        REFERENCES s5_rule_versions (rule_id, version)
);
