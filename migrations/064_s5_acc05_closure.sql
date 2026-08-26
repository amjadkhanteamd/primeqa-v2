-- 064: ACC-05 closure — heading/landmark rules (LLD 3A-1 §3a; feat 3a-1b).
-- LIFECYCLE REPLAY, not a bootstrap: these four rules were created, bound,
-- mapped, review-approved and ACTIVATED through knowledge/rule_lifecycle.py
-- (real actor, activity_log audited, full DRAFT->...->ACTIVE chains) on the
-- verification DB; this migration replays that outcome so production reaches
-- the same state at MIGRATE-FIRST time. seed_provenance.lifecycle_replay
-- records the exercise; the service bootstrap guard is untouched (it polices
-- the service path; replays land chain-created state). Idempotent.
BEGIN;
INSERT INTO s5_rules (rule_id, owner) VALUES ('PLM-A11Y-069', 'plimsol') ON CONFLICT (rule_id) DO NOTHING;
INSERT INTO s5_rule_versions (rule_id, version, name, description, automation_capability, human_review_required, state, seed_provenance)
VALUES ('PLM-A11Y-069', 1, 'Heading levels should only increase by one', 'Ensures the order of headings is semantically correct

Mapping judgment: Plimsol mapping under registry authority (TA review pt 13: the engine is not the accessibility authority); axe tags these best-practice and declines the normative claim; Plimsol makes it.', 'AUTO', FALSE, 'ACTIVE', '{"lifecycle_replay": true, "engine_rule_id": "heading-order", "exercised": "DRAFT->REVIEW->APPROVED->VERSIONED->ACTIVE via rule_lifecycle.py, superadmin actor, transcript in commit 3a-1b", "judgment": "Plimsol mapping under registry authority (TA review pt 13)"}'::jsonb)
ON CONFLICT (rule_id, version) DO NOTHING;
INSERT INTO s5_engine_bindings (rule_id, rule_version, engine, engine_version, engine_rule_id)
VALUES ('PLM-A11Y-069', 1, 'axe-core', '4.13.0', 'heading-order')
ON CONFLICT (rule_id, rule_version, engine, engine_version, engine_rule_id) DO NOTHING;
INSERT INTO s5_standard_maps (rule_id, rule_version, standard, criterion, level)
VALUES ('PLM-A11Y-069', 1, 'WCAG22', '1.3.1', 'A')
ON CONFLICT (rule_id, rule_version, standard, criterion) DO NOTHING;
INSERT INTO s5_rules (rule_id, owner) VALUES ('PLM-A11Y-070', 'plimsol') ON CONFLICT (rule_id) DO NOTHING;
INSERT INTO s5_rule_versions (rule_id, version, name, description, automation_capability, human_review_required, state, seed_provenance)
VALUES ('PLM-A11Y-070', 1, 'Headings should not be empty', 'Ensures headings have discernible text

Mapping judgment: Plimsol mapping under registry authority (TA review pt 13: the engine is not the accessibility authority); axe tags these best-practice and declines the normative claim; Plimsol makes it.', 'AUTO', FALSE, 'ACTIVE', '{"lifecycle_replay": true, "engine_rule_id": "empty-heading", "exercised": "DRAFT->REVIEW->APPROVED->VERSIONED->ACTIVE via rule_lifecycle.py, superadmin actor, transcript in commit 3a-1b", "judgment": "Plimsol mapping under registry authority (TA review pt 13)"}'::jsonb)
ON CONFLICT (rule_id, version) DO NOTHING;
INSERT INTO s5_engine_bindings (rule_id, rule_version, engine, engine_version, engine_rule_id)
VALUES ('PLM-A11Y-070', 1, 'axe-core', '4.13.0', 'empty-heading')
ON CONFLICT (rule_id, rule_version, engine, engine_version, engine_rule_id) DO NOTHING;
INSERT INTO s5_standard_maps (rule_id, rule_version, standard, criterion, level)
VALUES ('PLM-A11Y-070', 1, 'WCAG22', '1.3.1', 'A')
ON CONFLICT (rule_id, rule_version, standard, criterion) DO NOTHING;
INSERT INTO s5_rules (rule_id, owner) VALUES ('PLM-A11Y-071', 'plimsol') ON CONFLICT (rule_id) DO NOTHING;
INSERT INTO s5_rule_versions (rule_id, version, name, description, automation_capability, human_review_required, state, seed_provenance)
VALUES ('PLM-A11Y-071', 1, 'All page content should be contained by landmarks', 'Ensures all page content is contained by landmarks

Mapping judgment: Plimsol mapping under registry authority (TA review pt 13: the engine is not the accessibility authority); axe tags these best-practice and declines the normative claim; Plimsol makes it.', 'AUTO', FALSE, 'ACTIVE', '{"lifecycle_replay": true, "engine_rule_id": "region", "exercised": "DRAFT->REVIEW->APPROVED->VERSIONED->ACTIVE via rule_lifecycle.py, superadmin actor, transcript in commit 3a-1b", "judgment": "Plimsol mapping under registry authority (TA review pt 13)"}'::jsonb)
ON CONFLICT (rule_id, version) DO NOTHING;
INSERT INTO s5_engine_bindings (rule_id, rule_version, engine, engine_version, engine_rule_id)
VALUES ('PLM-A11Y-071', 1, 'axe-core', '4.13.0', 'region')
ON CONFLICT (rule_id, rule_version, engine, engine_version, engine_rule_id) DO NOTHING;
INSERT INTO s5_standard_maps (rule_id, rule_version, standard, criterion, level)
VALUES ('PLM-A11Y-071', 1, 'WCAG22', '1.3.1', 'A')
ON CONFLICT (rule_id, rule_version, standard, criterion) DO NOTHING;
INSERT INTO s5_standard_maps (rule_id, rule_version, standard, criterion, level)
VALUES ('PLM-A11Y-071', 1, 'WCAG22', '2.4.1', 'A')
ON CONFLICT (rule_id, rule_version, standard, criterion) DO NOTHING;
INSERT INTO s5_rules (rule_id, owner) VALUES ('PLM-A11Y-072', 'plimsol') ON CONFLICT (rule_id) DO NOTHING;
INSERT INTO s5_rule_versions (rule_id, version, name, description, automation_capability, human_review_required, state, seed_provenance)
VALUES ('PLM-A11Y-072', 1, 'Document should have one main landmark', 'Ensures the document has a main landmark

Mapping judgment: Plimsol mapping under registry authority (TA review pt 13: the engine is not the accessibility authority); axe tags these best-practice and declines the normative claim; Plimsol makes it.', 'AUTO', FALSE, 'ACTIVE', '{"lifecycle_replay": true, "engine_rule_id": "landmark-one-main", "exercised": "DRAFT->REVIEW->APPROVED->VERSIONED->ACTIVE via rule_lifecycle.py, superadmin actor, transcript in commit 3a-1b", "judgment": "Plimsol mapping under registry authority (TA review pt 13)"}'::jsonb)
ON CONFLICT (rule_id, version) DO NOTHING;
INSERT INTO s5_engine_bindings (rule_id, rule_version, engine, engine_version, engine_rule_id)
VALUES ('PLM-A11Y-072', 1, 'axe-core', '4.13.0', 'landmark-one-main')
ON CONFLICT (rule_id, rule_version, engine, engine_version, engine_rule_id) DO NOTHING;
INSERT INTO s5_standard_maps (rule_id, rule_version, standard, criterion, level)
VALUES ('PLM-A11Y-072', 1, 'WCAG22', '1.3.1', 'A')
ON CONFLICT (rule_id, rule_version, standard, criterion) DO NOTHING;
INSERT INTO s5_standard_maps (rule_id, rule_version, standard, criterion, level)
VALUES ('PLM-A11Y-072', 1, 'WCAG22', '2.4.1', 'A')
ON CONFLICT (rule_id, rule_version, standard, criterion) DO NOTHING;
INSERT INTO s5_catalogue_releases (id, notes, content_hash)
VALUES (2, 'ACC-05 closure: +4 heading/landmark rules (Plimsol mapping authority)', '9b21e66728368f51fc0dfceafe9f5a643ce83b2f92a135558eece8729185e507')
ON CONFLICT (id) DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-001', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-002', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-003', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-004', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-005', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-006', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-007', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-008', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-009', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-010', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-011', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-012', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-013', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-014', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-015', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-016', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-017', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-018', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-019', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-020', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-021', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-022', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-023', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-024', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-025', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-026', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-027', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-028', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-029', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-030', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-031', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-032', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-033', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-034', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-035', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-036', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-037', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-038', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-039', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-040', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-041', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-042', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-043', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-044', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-045', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-046', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-047', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-048', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-049', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-050', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-051', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-052', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-053', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-054', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-055', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-056', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-057', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-058', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-059', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-060', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-061', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-062', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-063', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-064', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-065', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-066', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-067', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-068', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-069', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-070', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-071', 1) ON CONFLICT DO NOTHING;
INSERT INTO s5_catalogue_release_members (release_id, rule_id, rule_version) VALUES (2, 'PLM-A11Y-072', 1) ON CONFLICT DO NOTHING;
SELECT setval(pg_get_serial_sequence('s5_catalogue_releases','id'), GREATEST((SELECT MAX(id) FROM s5_catalogue_releases), 2));
COMMIT;
