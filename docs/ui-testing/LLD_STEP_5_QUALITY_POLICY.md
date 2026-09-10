# LLD — Step 5: the Release Quality Policy (TA condition 4; R6: functional + conformance in one decision)

**Status: DESIGN — GO (2026-09-10; six forks ratified on the leans, see "Rulings").** Branch `step-5-quality-policy` from
`main` @ 36228de (Step 4 live + its plan-view fix, D-486 / D-487). Mock
approved by AK: the decision names the policy + version + plan id; six
evidence lines (functional, conformance, regressions, waivers, human reviews,
environments), each showing what was observed and which rule it triggered with
what effect; "Nothing ungraded" when true; the human final decision recorded
beside the recommendation, never replacing it. The standing correction of
D-487 applies: the merge proof renders the actual decision card on production
data — this design gives the card a READ-ONLY live preview so that render
needs no write.

What this step does, in one line: the release recommendation is computed by a
versioned tenant POLICY — a closed declarative vocabulary of rules (axis ×
condition × effect) — over six evidence axes assembled from the substrate,
the browser plane, the plans and the waivers; every decision records the
policy, its version and the plan it graded; the human's final decision sits
beside it, never over it.

## 0. Pre-flight facts, cited (all read this session)

| fact | where |
|---|---|
| `releases.decision_criteria` (JSON, default `{}`; `DEFAULT_DECISION_CRITERIA = {min_pass_rate: 95, critical_tests_must_pass: True, max_flaky_test_percent: 10}` at create) is the v1 per-release criteria object. The v1 `DecisionEngine` retired with the v1 engine (D-221 R4; D-220 verified every v1 verdict vacuous); the substrate decision IS the recommendation; the composer keeps the `{mode, recommendation_source, v1, substrate}` envelope. Today the only keys the engine reads are `substrate_min_pass_rate` (95), `substrate_block_on_broken_grounding` (True), `substrate_max_run_age_hours` (168), `substrate_mode` (advisory). | `release/service.py:3-7`, `release/models.py:27`, `release/decision_composer.py:41-120`, `intelligence/substrate_decision.py:514-540` |
| `release_decisions(id, release_id, recommendation CHECK go/conditional_go/no_go/cannot_determine (071), confidence, reasoning JSONB, criteria_met JSONB, recommended_by ai/human, final_decision CHECK NULL/go/conditional_go/no_go, decided_by → users, decided_at, override_reason, agent_verdict_counts, created_at)`. The human's THREE-valued final decision already has its columns (D-482's record); the write path is the API `POST /api/releases/<id>/decisions/<id>/finalize` (Admin) → `ReleaseRepository.finalize_decision(decision_id, release_id, final_decision, decided_by, override_reason)`; the web template only DISPLAYS "Final decision:" — there is no web form. Production: one decision row in total (release 185, `go`, 2026-06-18); release 16 has none. | `migrations/009_releases.sql:92-108`, `071`, `release/models.py:100-115`, `release/routes.py:220-255`, `release/repository.py:223`, `templates/releases/detail.html:66-67, 305` |
| The Step B resolver + `cannot_determine` (D-482): `resolve_current_sequence(session, connected_org_id=)`; the four-valued ladder in `compute_substrate_decision` — `org_sequence` fail-closed, `readiness` (Step 2: NEVER_RUN / CANNOT_DETERMINE / ungrounded = UNGRADED inputs → `cannot_determine` unless a graded blocker; STALE → warn), `has_runs`, `pass_rate` (blocker below the threshold), `grounding_integrity` (broken → blocker; drifted / stale → warn), `flaky_quarantine`, `coverage`, `version_currency`, `freshness` (warn); ladder: `cannot_determine` if ungraded and no blocker, `go` if no blocker and no warning, `conditional_go` if warnings only, else `no_go`; `metrics.readiness{...}`; the per-environment loop with the D9 worst-of rollup. **This ladder is gate logic outside any policy — the object this step demotes to an evidence assembler.** | `substrate_decision.py:514-780, 895-925` |
| Readiness and ungraded-blocks-GO (D-484): `sync/readiness.resolve_run_readiness(_bulk)`; the scope check `release_scope_readiness` (Evaluate refuses a non-current scope); the active-environment interim (D-485); plans and declared targets (D-486): `run_plans` (scope, resolution with `manifests.functional.pairs`, `exclusions.items` with reasons incl. `read_only_target_admits_inspection_only`), `release_targets`, `plan_id` on jobs and runs. | D-484, D-485, D-486; `execution_engine/planner.py` |
| **Step 4's two pending legs proved on production overnight (read-only, 2026-09-10):** schedule 1, claimed by AK at 12:16Z on 09-09, fired at 06:00:55Z under his authority — `run_plans` 87f4b1d7 (scope schedule 1, `planned_by` NULL, `authorised_by` 1, tenant-wide template, 193 claims / 193 jobs, executed 06:00:58Z; 258 runs carrying the plan id: 248 passed, 10 failed, 0 errored, all stamped @ seq 255); AK ran plan **9ea0c522** on release 16 at 10:17:14Z → 10:17:35Z (19 jobs, 19 runs, all passed @ seq 255). The audit trail carries `s4.plan.create` / `s4.plan.execute` for both (the tick's rows with user NULL). Release 16 still has 0 decisions and 0 declared targets. | production read-only |
| Conformance transitions and drift subtraction (Phase 7): `interpretation/ui_comparison.py` — the transition vocabulary `NEW_FAIL / FIXED / STILL_FAILING / STILL_PASSING / NEW_CLAIM / RETIRED_CLAIM / NOT_COMPARABLE / NOT_RUN`; `diff_tool_pins` (tool drift), `diff_environment` (env delta), `fingerprint_delta`; `compare_processing_runs(session, baseline_job_id, candidate_job_id)`; a moved dimension (env / tool / bundle) makes a changed pair NOT_COMPARABLE with the cause recorded. Tables `s6_ui_comparison_runs(id, baseline_job_id, candidate_job_id, …, outcome, refusal_reason, tool_drift JSONB, env_delta JSONB, transition_counts JSONB)` and `s6_ui_verdict_transitions(comparison_id, test_id, transition, from_verdict, to_verdict, drift BOOLEAN, fingerprint_delta, causal, surface_key, plimsol_rule_id)`. Production: the last within-v2 compare (09-04) read 191 STILL_PASSING / 12 STILL_FAILING / 241 NC, zero tool drift. | `ui_comparison.py:40-70, 71-160, 265-370`; `alembic/…/20260826_0010:40-74` |
| NEEDS_HUMAN (3A-4): `interpretation/ui_conformance.py` emits `NEEDS_HUMAN` with `candidates` (the incomplete-census items) or `candidates_unavailable`; the s6 verdict vocabulary is PASS / FAIL / NEEDS_HUMAN / NOT_DETERMINED; `claim_set_members.applicability = HUMAN_REVIEW` marks members that never grade by engine. **No human-review resolution object exists** — nothing records "a person looked and decided"; the report console only ranks NEEDS_HUMAN. Step A / A.1's repair gate has its own verdicts (DERIVED / SPECULATIVE / SEMANTIC; reverify states) — a repair matter, not a release-evidence one. Production: 0 NEEDS_HUMAN on the active set's latest run. | `ui_conformance.py:51, 128-160`; `standard_view.py:35`; `repair_gate.py:22-51` |
| **No waiver object exists today** — the only "waiver" hit in the tree is vendor criteria HTML. Verified, not assumed. | `grep -rniE "waiver\|waive" primeqa/ migrations/ alembic/` → `knowledge/vendor/criteria/section508_2017.html` only |
| Criticality sources: accessibility — the ACTIVE standard map set (`s5_standard_map_sets.state = 'ACTIVE'`, `s5_standard_maps.level`) gives every rule its criterion level; production WCAG22 active set: A 69 / AA 6 / AAA 5 rules; EN301549 and SECTION508 also active. Functional — **no recorded severity exists**: `test_recipes.priority` (int, default 0) is unused by the engine; requirements and `release_requirements` carry no priority; v1's `critical_tests_must_pass` has no data source. | `standard_view.py:42-47, 100-150`; production read-only; `coordinator.py:223, 688`; `migrations/009:33-37` |
| Env 78 "Prod1": inactive, `is_production`, `execution_policy = read_only`; the dispatch chokepoint admits inspection (metadata) recipes only on `read_only`; Step 4's planner excludes a data recipe against such a target WITH A REASON (`read_only_target_admits_inspection_only`). | `execution_engine/run.py:116-153`; D-486 |
| Gate-ish logic in templates today: `releases/detail.html` switches on `recommendation` for colour and icon (presentation) and reads `decision_criteria.substrate_mode` (line 167) to label the card — no threshold, no comparison, no effect derived in a template. The grep-assert of §b keeps it so. | `templates/releases/detail.html:52-67, 165-301` |

## a. THE POLICY OBJECT

`decision_criteria` grows into a versioned TENANT policy. The per-release
column stays as it is — **not altered, not migrated, not destructive**: it
remains the release's legacy criteria object (read for `substrate_mode`
only); its thresholds are superseded by the policy's rule parameters, stated
on the card ("policy Plimsol default v1 — per-release criteria no longer
grade").

### Tables (tenant; alembic `20260912_0010`, ADDITIVE)

```
quality_policies
  id             UUID PK
  name           TEXT NOT NULL                  -- "Plimsol default"
  version        INTEGER NOT NULL               -- 1, 2, …
  status         TEXT NOT NULL CHECK (status IN ('draft', 'active', 'retired'))
  note           TEXT NOT NULL DEFAULT ''
  created_by     INTEGER                        -- NULL = the seed (recorded as such)
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
  activated_by   INTEGER, activated_at TIMESTAMPTZ
  retired_by     INTEGER, retired_at TIMESTAMPTZ
  first_used_at  TIMESTAMPTZ                    -- set by the FIRST decision that graded under it
  UNIQUE (name, version); UNIQUE INDEX one_active ON (status) WHERE status = 'active'   -- one ACTIVE policy per tenant
quality_policy_rules
  policy_id      UUID NOT NULL REFERENCES quality_policies (id)
  position       INTEGER NOT NULL
  axis           TEXT NOT NULL CHECK (axis IN ('functional', 'conformance', 'regressions', 'waivers', 'human_reviews', 'environments', 'grading'))
  condition      TEXT NOT NULL CHECK (condition IN (<the closed set below>))
  parameter      NUMERIC                        -- only for a condition that declares one (pass_rate_below)
  effect         TEXT NOT NULL CHECK (effect IN ('ALLOW', 'CONDITIONAL', 'REVIEW', 'BLOCK'))
  scope          TEXT NOT NULL CHECK (scope IN ('release', 'item'))
  note           TEXT NOT NULL DEFAULT ''
  PRIMARY KEY (policy_id, position)
  CHECK (axis / condition pairs are the declared ones)        -- one CHECK listing the admitted (axis, condition) pairs
TRIGGERS  quality_policies_no_delete, quality_policy_rules_no_delete (BEFORE DELETE → RAISE)
          quality_policies_immutable_once_used: BEFORE UPDATE on quality_policies → RAISE when first_used_at IS NOT NULL and any of
              (name, version, note) changes; status may still move active → retired (a retirement is a state change with actor)
          quality_policy_rules_immutable_once_used: BEFORE UPDATE / INSERT on rules → RAISE when the parent's first_used_at IS NOT NULL
```

### The closed vocabulary (the F8 discipline applied to policy)

A rule is **axis × condition × effect (× scope)**. No expressions, no
connectives, no free text in the grading path. The DB CHECKs refuse an
unknown word; the pydantic model refuses one before the DB.

| axis | condition | reads | parameter |
|---|---|---|---|
| functional | `failure_present` — any counted latest run failed or errored on an approved claim in scope | the substrate assembler (D-198 rows) | — |
| functional | `pass_rate_below` | counted latest runs | p (percent) |
| conformance | `level_a_failed` / `level_aa_failed` / `level_aaa_failed` — a FAIL verdict on the latest processing run whose rule maps to that level in the ACTIVE map set of the tenant's standard | `s6_ui_verdicts` + `s5_standard_maps` | — |
| conformance | `not_determined_present` | the same run | — |
| regressions | `new_fail_present` — a NEW_FAIL transition in the latest comparison of the graded set, drift SUBTRACTED (a drift-flagged pair never counts) | `s6_ui_verdict_transitions` | — |
| regressions | `tool_drift_only` — the latest comparison moved on tool pins and nothing else | `s6_ui_comparison_runs.tool_drift`, `env_delta` | — |
| regressions | `not_comparable_present` | transitions | — |
| waivers | `active_waiver_covers_item` — an unexpired, unrevoked waiver names the item (scope `item`) | `quality_waivers` | — |
| human_reviews | `needs_human_pending` — a NEEDS_HUMAN verdict or a HUMAN_REVIEW member on the latest run with no active waiver | s6 + members + waivers | — |
| environments | `environment_drift` — a counted run reads STALE (a covered read moved since it ran) on a target | Step 2 readiness | — |
| environments | `scope_not_current` — any item NEVER_RUN / CANNOT_DETERMINE on a target (the Evaluate refusal, restated as evidence) | `release_scope_readiness` | — |
| environments | `production_target_ungraded_on_metadata` — a declared `is_production` + `read_only` target with no current metadata evidence for a claim that has an admitted inspection recipe | targets + plan + runs | — |
| grading | `ungraded_present` — any input the engine cannot grade: NEVER_RUN, CANNOT_DETERMINE, ungrounded-current, a conformance claim with no verdict on the latest run | the readiness census | — |

Effects and their severity order: **BLOCK > REVIEW > CONDITIONAL > ALLOW**.
Scope `item` limits an effect to the item the condition matched (a waiver
ALLOWs its item; the item then no longer counts on its own axis); scope
`release` applies to the decision.

### The recommendation ladder (D9 kept exactly)

The policy yields effects; the recommendation is the four-valued D-482 word:

- any **BLOCK triggered by a GRADED observation** (a failure, a level-A FAIL,
  a production target ungraded on metadata) → `no_go`;
- else any **BLOCK triggered by `ungraded_present`** or any **REVIEW** →
  `cannot_determine` (unknown blocks GO and CONDITIONAL GO — D9; unknown alone
  never makes NO GO — D-482);
- else any **CONDITIONAL** → `conditional_go`;
- else → `go`.

So the TA's "ungraded → BLOCK" holds as an EFFECT (it blocks GO and
CONDITIONAL GO) while D9's recommendation semantics hold as the WORD
(`cannot_determine`, never `no_go` on its own). **Fork 2** states this
reconciliation for the GO.

### The seed — "Plimsol default v1" (the TA's example rules)

| # | axis | condition | effect | scope | note |
|---|---|---|---|---|---|
| 1 | functional | failure_present | BLOCK | release | critical functional failure — **v1: every functional failure is critical** (no recorded severity exists; Fork 1) |
| 2 | conformance | level_a_failed | BLOCK | release | critical accessibility failure |
| 3 | conformance | level_aa_failed | CONDITIONAL | release | AA failure |
| 4 | human_reviews | needs_human_pending | REVIEW | release | NEEDS_HUMAN pending |
| 5 | waivers | active_waiver_covers_item | ALLOW | item | an active waiver allows THAT item |
| 6 | regressions | tool_drift_only | ALLOW | release | tool drift only |
| 7 | environments | environment_drift | REVIEW | release | environment drift |
| 8 | grading | ungraded_present | BLOCK | release | ungraded (D9) |
| 9 | environments | production_target_ungraded_on_metadata | BLOCK | release | §d |
| 10 | functional | pass_rate_below (95) | BLOCK | release | the legacy threshold, carried as a rule parameter |

Rules 1–8 are the TA's; 9 is §d made concrete; 10 carries the legacy
threshold so nothing the current engine blocks on stops blocking. The seed is
recorded with `created_by` NULL and `note = "seed: Plimsol default v1 (TA
example rules)"`, activated by the migration for tenant 1 (stated: a data
act inside an additive migration — Fork 3 asks whether the seed's ACTIVATION
is the migration's or AK's).

### Lifecycle

Versioned; a policy version becomes IMMUTABLE the moment a decision grades
under it (`first_used_at`, set by the engine in the same transaction as the
decision row; the triggers refuse every edit after); editing = a new version
(draft → active retires the previous active, both audited with the real
actor); authoring is Settings-side and **CLI in v1** (`python -m
primeqa.intelligence.quality_policy seed | show | new-version | activate`),
with a read-only view of the active policy at `/settings/quality-policy` (§f).

## b. THE ENGINE

Two modules, both cross-cutting (intelligence):

- `intelligence/quality_evidence.py` — **assembles** the six axes for a
  release scope, read-only, best-effort, one tenant session:
  1. **functional**: the D-198 assembler's rows per active target
     environment (`_assemble_claim_evidence` — unchanged), reduced to
     observations: counted runs, failed / errored (with claim keys), pass
     rate, grounding broken / drifted, readiness census;
  2. **conformance**: the scope's conformance claims (Step 3 `verifies` links)
     → their verdicts on the LATEST processing run of the ACTIVE claim set
     (`s6_ui_verdicts` by `test_id`), each verdict's rule level from the
     ACTIVE map set of the tenant's standard (WCAG22 default; the
     `standard_profile` of the set), NOT_DETERMINED and no-verdict counts;
  3. **regressions**: the latest `s6_ui_comparison_runs` whose candidate is
     that processing run: transition counts with `drift = TRUE` pairs
     SUBTRACTED, `tool_drift` / `env_delta` presence; none → "no comparison
     recorded" (an observation, not ungraded);
  4. **waivers**: active waivers (unexpired, unrevoked) for the release (and
     tenant-wide ones) by item;
  5. **human_reviews**: NEEDS_HUMAN verdicts on the latest run + HUMAN_REVIEW
     members of the active set on the scope's surfaces, minus those an active
     waiver covers → pending;
  6. **environments**: the targets (declared / fallback, from Step 4's
     `release_targets` and the same fallback), per target: readiness census
     (STALE count = drift), non-current count, production/read-only flags;
     the plan: the LATEST EXECUTED plan for the release scope (`run_plans`),
     its excluded data recipes on read-only targets (§d), or "none".
  The evidence carries a `graded` flag per item and the `ungraded` census.
- `intelligence/quality_policy.py` — the pure **engine**: `evaluate(policy,
  evidence) -> Decision{recommendation, effects[], evidence_lines[6],
  triggered_rules[], ungraded[], policy{id, name, version}, plan_id}`. Each
  evidence line: axis, `observed` (a sentence with the numbers), `rule`
  (position + words) or "no rule triggered", `effect`. "Nothing ungraded" is
  the grading line when the census is empty. Deterministic; no I/O; the
  ladder above.

The composer's `evaluate_and_record` calls the assembler then the engine and
records ONE row: `recommendation` = the engine's word, `reasoning` = the
engine's decision (policy, evidence lines, triggered rules, ungraded, plan)
plus the legacy envelope keys, `policy_id` / `policy_version` / `plan_id` as
COLUMNS (public migration 073, ADDITIVE, nullable — old rows stay NULL);
`first_used_at` on the policy set in the same transaction. The Evaluate act
still REFUSES a non-current scope before grading (Step 2); the same
observation is ALSO the `scope_not_current` evidence on the environments line
of the preview. `compute_substrate_decision` is demoted: it keeps producing
the substrate block CI reads (D-198 slice 4) and the per-environment cards,
but the RELEASE recommendation is the policy engine's — **gate logic lives
only in the policy**; `tests/unit/test_templates_no_gate_logic.py`
grep-asserts that no template compares a metric, reads a threshold, or
derives an effect (styling on the engine's `recommendation` / `effect` words
stays presentation).

**The card** (per the approved mock; `templates/releases/_quality_decision.html`,
on the decision tab): header "Policy Plimsol default v1 · plan 9ea0c522 ·
evaluated <when>"; six lines, each `observed → rule → effect`; the grading
line "Nothing ungraded" or the census; the recommendation; the human final
decision beside it (§e). The tab renders a **live PREVIEW** (GET, no row
written) above the recorded decisions — the D-487 render on production data
without a write — and "Evaluate" records.

## c. WAIVERS — a minimal object, never auto-created

```
quality_waivers (tenant; the same migration)
  id             UUID PK
  release_id     INTEGER                         -- NULL = tenant-wide
  item_kind      TEXT NOT NULL CHECK (item_kind IN ('claim', 'rule', 'surface'))
  item_ref       TEXT NOT NULL                   -- test_id / plimsol_rule_id / surface_key
  axis           TEXT NOT NULL CHECK (axis IN ('functional', 'conformance', 'human_reviews'))
  reviewer_user_id INTEGER NOT NULL              -- who reviewed
  reason         TEXT NOT NULL
  expires_at     TIMESTAMPTZ NOT NULL
  created_by     INTEGER NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
  revoked_by INTEGER, revoked_at TIMESTAMPTZ, revocation_reason TEXT
  CHECK (expires_at > created_at); no-delete trigger; revocation is the state change
```

Active = `revoked_at IS NULL AND expires_at > now()`. Expired waivers stop
counting (the evidence line says "1 expired, not counted"). A waiver ALLOWs
its item on its axis (rule 5): a waived failing claim stops triggering
`failure_present`, a waived NEEDS_HUMAN stops being pending. Recorded by a
human only: a minimal "Record waiver" form on the decision tab (item, axis,
reason, expiry; MEMBER+; audited) — **Fork 4**: form vs CLI-only in v1.

## d. PRODUCTION TARGETS — the rule, stated so Step 4 and Step 5 agree

A declared `is_production` + `read_only` target admits METADATA evidence
only (the chokepoint's law). The planner (Step 4) excludes every data recipe
against it with the reason `read_only_target_admits_inspection_only`. The
policy grades that target on its metadata evidence: rule 9 —
`production_target_ungraded_on_metadata → BLOCK` — fires when a claim with an
ADMITTED inspection recipe has no current run on that target. **The absence
of data-recipe evidence on such a target is NOT ungraded**: the planner
excluded it with a recorded reason, and the grading census reads the plan's
exclusions before it reads absence — an item the plan excluded by reason is
"not graded here by design", never "ungraded". The evidence line says so:
"production target Prod1: 12 inspection checks current · 7 data checks not
run here by policy (read-only) · not ungraded".

## e. FINAL DECISION — the human's record beside the recommendation

The three-valued record (D-482) already lives on the row (`final_decision`,
`decided_by`, `decided_at`, `override_reason`). This step adds the web act:
a form on the card — "Record final decision" — go / conditional_go / no_go +
reason (MEMBER+; Admin when the record is a GO over a non-GO recommendation,
as the API gates today). **"Record GO with reason" is the ONLY path to a GO
when the recommendation is not GO**: the form disables the plain GO and
requires `override_reason`. The card renders "Recommendation: NO GO · Final
decision: GO (AK, reason: …)" — beside, never replacing. Audited.

## f. NON-GOALS

No layout collapse (Step 6); no policy authoring UI beyond a read-only view of
the active policy (authoring is Settings-side; **CLI in v1** — said plainly
on the view); no persona comparison; no change to the substrate assembler's
rows, the readiness vocabulary, the planner, the conformance semantics or the
comparison engine; no backfill of old decision rows (their policy columns stay
NULL — "decided before policies").

## Blast radius

| touched | how |
|---|---|
| tenant schema | `quality_policies`, `quality_policy_rules`, `quality_waivers` + triggers; the seed rows (Fork 3 on activation) |
| public schema | `release_decisions` + `policy_id UUID, policy_version INTEGER, plan_id UUID` (073, ADDITIVE; `decision_criteria` UNTOUCHED — non-destructive) |
| intelligence | `quality_evidence.py`, `quality_policy.py` (new); `release/decision_composer.py` records the engine's word + refs; `substrate_decision.compute_substrate_decision` unchanged in code but demoted in role (its recommendation no longer the release's) |
| routes / templates | the card partial; `POST /releases/<id>/decisions/<id>/final` (web); `POST /releases/<id>/waivers`; `GET /settings/quality-policy`; the CLI |
| not touched | the assembler's rows, readiness, plans, Step 3 links, conformance / comparison semantics, the API finalize route (kept) |

## g. Verification plan (the brief's list, restated as tests)

1. **the default policy over release 16's current scope** (scratch with a
   prod-shaped world; production read-only preview after merge): a decision
   with six evidence lines, each naming its observed values and the
   triggered rule or "no rule triggered"; the plan id = 9ea0c522's analogue;
   "Nothing ungraded" when true.
2. **a planted critical failure → BLOCK** (`no_go`, rule 1 named on the
   functional line; and rule 2 on a level-A conformance FAIL).
3. **a planted waiver flips one item → ALLOW** (the failing claim leaves the
   functional observation; rule 5 on the waivers line) **and its expiry
   un-flips it** (expired → counted again; the line says "1 expired").
4. **NEEDS_HUMAN pending → REVIEW** → `cannot_determine`; a waiver on it
   clears the pending.
5. **ungraded → BLOCK, never GO or CONDITIONAL**: a NEVER_RUN item → the
   grading line names it, recommendation `cannot_determine`; with a graded
   failure beside it → `no_go` (D9: NO GO stands).
6. **a decision records policy + version + plan** (columns + reasoning); the
   policy's `first_used_at` set; **a used policy version refuses edit**
   (rules UPDATE / INSERT and the policy's name refused by trigger); a new
   version activates and retires the previous (audited).
7. **§d**: a declared production read-only target with current metadata
   evidence and plan-excluded data recipes → not ungraded, the environments
   line says so; the same target with a missing metadata run → rule 9 BLOCK.
8. **zero gate logic in templates** (grep-asserted).
9. **screens**: the card on scratch (default policy; BLOCK; waiver; REVIEW;
   the final-decision form) **and the production-data render**: the live
   preview over release 16 on production after the merge, read-only.

Plus the D-468 set.

## h. Migrations, classified (for the runbook)

- tenant `20260912_0010`: three new tables + triggers, plus the seed INSERTs
  (one policy + its 10 rules). **ADDITIVE, dumpless** under ruling 3: the
  seed is written as a DRAFT — new rows in new tables, no state change on any
  existing object, nothing graded until a human activates it. Idempotent (the
  seed is skipped when `(name, version)` exists).
- public `073_release_decisions_policy_refs.sql`: three nullable columns —
  ADDITIVE, non-destructive, dumpless; no data write, no constraint that can
  reject a row; `releases.decision_criteria` untouched.

## Build corrections (2026-09-10)

Six corrections found while building; each is recorded with its evidence in
`VERIFICATION_STEP_5.md` §0 and folded into the design above:

1. **The lanes are split.** A conformance claim never runs on the S4 lane, so
   Step 2's readiness census (and the Evaluate refusal) reads the FUNCTIONAL
   lane; the policy's conformance axis grades the browser plane, and a
   conformance claim with no verdict on the latest processing run is ungraded
   THERE. Without this the Evaluate act refused a healthy release for ever.
2. **The ACTIVE map set is PUBLIC (migrations 065/066), not tenant.** The
   level read runs under a savepoint; the evidence carries
   `levels_available`; a FAIL whose rule maps to no level is recorded
   `unmapped`, never silently AA.
3. **The policy is frozen before the decision row is written** (two
   connections; the conservative order).
4. **`expires_at` is NOT NULL at the table** — "no expiry, no waiver" holds
   against a direct INSERT, not only the form.
5. **The decider's name is a two-column SELECT**, not the ORM row (a render
   must not depend on more than it shows — the D-487 class).
6. **The gate-logic sweep is scoped to the decision surfaces**, with the four
   pre-existing colour bands elsewhere pinned by name.

## Forks for the GO

1. **"Critical functional failure" in v1 = every functional failure** (no
   recorded severity exists; recipe `priority` is unused, requirements carry
   none). Lean: yes, with the recorded successor "a requirement / claim
   severity, when recorded, splits rule 1 into critical → BLOCK and other →
   CONDITIONAL".
2. **BLOCK's recommendation word splits by cause**: graded → `no_go`;
   ungraded → `cannot_determine` (D9 kept; the TA's "ungraded → BLOCK" kept
   as the effect). Lean: yes.
3. **The seed's activation**: activated by the migration for tenant 1 (a
   data act in an additive migration → dump-first) vs seeded as `draft` and
   activated by AK through the CLI as an audited act. Lean: seed as draft;
   AK activates — no policy grades a release without a human having
   activated it.
4. **Waiver creation**: a minimal form on the decision tab vs CLI-only.
   Lean: the form (a waiver is a release act a reviewer makes where the
   decision is).
5. **Human review resolution = a waiver** in v1 (a NEEDS_HUMAN item a person
   has looked at is recorded as a waiver with the reason) vs a separate
   review record. Lean: waiver (one object, one vocabulary); a review record
   is the successor if reviews need to be distinguished from exceptions.
6. **The decision's plan** = the latest EXECUTED plan for the release scope
   (none → "evidence predates plans", not ungraded). Lean: yes.

## Rulings (AK, 2026-09-10 — GO on the design)

1. **Every functional failure is critical in v1.** Successor recorded: a
   requirement / claim severity, when recorded, splits rule 1 into
   critical → BLOCK and other → CONDITIONAL.
2. **BLOCK's word splits by cause**: a graded BLOCK → `no_go`; an ungraded
   BLOCK (or any REVIEW) → `cannot_determine`. D9 and D-482 both hold.
3. **The seed is a DRAFT.** Human activation — audited, CLI or Settings —
   precedes any grading; a tenant with no ACTIVE policy gets a refusal
   ("no active quality policy — activate one"), never a silent default. The
   migration therefore writes only a draft policy and its rules (no state
   change on any graded object) and **stays dumpless**.
4. **Waiver form on the decision tab.** Reviewer, reason and expiry are all
   REQUIRED: no expiry, no waiver (DB CHECK + form validation).
5. **Human review resolved via waiver in v1, with the guard**: the human
   reviews evidence line reads "N pending, N waived by <actor> until
   <date>" — never "resolved". A waiver is a named human ACCEPTING a known
   state, not an adjudication. **Adjudication is a later slice** (a review
   record with its own verdict, distinct from an exception) — recorded here
   as the successor of rule 4 / the human_reviews axis.
6. **The decision cites the latest EXECUTED plan for the scope**; none →
   "evidence predates plans" (an observation, not ungraded).
