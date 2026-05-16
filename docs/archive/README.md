# Documentation Archive

Historical PrimeQA documentation that is no longer authoritative but
preserved for reference.

## Currently archived

### `build_plans/PRIMEQA_BUILD_PLAN.md`

The v2 architecture build plan. Directs implementation of numbered
steps against `PRIMEQA_ARCHITECTURE_SPEC_v2.2.md`. v2 is built; plan
is complete; archived 2026-05-14.

### `project_state/PROJECT_STATE.md`

Project-wide snapshot from 2026-04-24. Current project state lives
in `CLAUDE.md` and per-substrate `EVOLUTION.md` files. Archived
2026-05-15.

### `qa_reports/DEMO_PREP_REPORT.md`

2026-04-23 overnight demo prep status snapshot. Archived 2026-05-15.

### `qa_reports/QA_REPORT.md`

2026-04-22 QA sweep — 72 checks, 0 P0/P1 failures, 68 PASS.
Superseded by subsequent code changes. Archived 2026-05-15.

### `qa_reports/QA_REPORT_2026-04-20.md`

Earlier QA sweep (2026-04-20). Self-declared archived in
`QA_REPORT.md`'s header. Archived 2026-05-15.

### `codebase_maps/CODEBASE_MAP.md`

Historical project map from 2026-04-20. Current structure is in
`CLAUDE.md` and per-substrate docs. Archived 2026-05-15 as a
snapshot (regeneratable if a future snapshot is ever needed).

### `qa_sweeps/qa/`

Playwright-based QA automation from the demo-prep era (2026-04-23).
Contains `browser.py`, `report.py`, `run_sweep.py`, `test_01_auth`
through `test_11_api`, `screenshots/` and `demo_screenshots/`.
Active test location is the top-level `tests/` directory. Archived
2026-05-15.

## Not currently archived (transitional)

The following remain at their current locations with legacy headers
because they are still operationally relevant:

- **`PRIMEQA_ARCHITECTURE_SPEC_v2.2.md`** (root) — describes the v2
  Flask/HTMX runtime which is still operational. Will be archived
  after Phase 4 cutover (substrate-1 replaces v2 `meta_*` tables).
- **`docs/architecture/substrate_1_semantic_org_model/PHASE_2_PLAN.md`**
  — locked planning artifact. Captures the plan as approved at
  Phase 2 start. Actual implementation choices tracked in
  `PHASE_2_PLAN_corrections.md` and `DECISIONS_LOG.md`.

## Not archived (still in play)

The following might LOOK historical but were evaluated in the
2026-05-14 cleanup pass and confirmed as active working documents:

- **`docs/architecture/PARKING_LOT.md`** — every item carries a
  forward-looking "Revisit when X" trigger (S5/S7/S8 design
  kickoffs, customer pilot signals, etc.). It is a watch list, not
  a history.
- **`docs/architecture/OPEN_QUESTIONS.md`** — 5 of 8 questions
  remain OPEN, each tied to future substrate design or Phase 3 ops
  decisions.
- **`docs/architecture/substrate_1_semantic_org_model/OPEN_QUESTIONS.md`**
  — substrate-specific open questions; lives with the substrate.

## Archive policy

Archive a document when:

- It refers to a phase/process that is complete
- Its content is fully superseded by newer authoritative docs (with
  the supersession documented)
- Inbound references are minimal and can be updated

Keep a document at its current location with a legacy header when:

- Still actively referenced by current work
- Operational system depends on it
- Inbound references are too numerous to chase
