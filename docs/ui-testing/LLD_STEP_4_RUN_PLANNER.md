# LLD — Step 4: the Run Planner (TA condition 3; Fork 5: PLAN succeeds Run Tests)

**Status: BUILT (2026-09-09; design b11a67f, GO with rulings 1–4; AK confirms he re-enabled schedule 1 by hand via the D-214 toggle, so the 02:09Z fire is authorised in fact and unrecorded only because the toggle wrote no audit row). Eight corrections found at build are recorded in VERIFICATION_STEP_4.md §0 (a failing recipe is named even when its claim survives; the plan id rides conditionally so injected fakes keep their signatures; best-effort writes on savepoints; the legacy enqueue seam kept for tests only; the Jinja dict-keys gotcha; the first-render CSRF field; create-time classification of a planted fixture key; four pre-existing reaper reds on the harness).** Branch `step-4-run-planner` from
`main` @ fb59095 (Step 3 live, D-485). Mock approved by AK: the PLAN view
answers what / why / scope only — claims by kind with reasons, declared
target environments with exclusions named, manifests + pins, an explicit
excluded line, "Run this plan" as a second act, impact / risk reserved with
"not yet — every scoped item". Fixture screenshots from all three entry
points ride the build push; the merge is gated on AK seeing them.

What this step does, in one line: every execution starts from a RECORDED
plan — a deterministic resolution of a scope (a requirement, a release, a
schedule's template) into the claims, recipes, target environments, personas
and manifests that will run, with every exclusion named and its reason
stated — and "Run this plan" executes THAT plan by id, so what ran is
provably what was shown. "Run all approved" — a query, not a plan — retires,
and the daily cadence fires a plan.

## 0. Pre-flight facts, cited (all read this session)

| fact | where |
|---|---|
| The Run Tests page `/run` (`run_page`): an environment select over ACTIVE non-production environments + a requirement picker from `list_runnable_requirements` (every key with ≥1 approved claim, with the count; fixtures/probes hidden behind `show_hidden` per Step 1); its POST (`run_page_submit`) enqueues per selected requirement via `enqueue_claims_for_requirements`, or — `run_all=1` — **every approved claim on the environment** via `enqueue_all_approved_claims`; production refused ("Substrate runs are sandbox-only"). | `views.py:693-846`; `intelligence/s4_execution_console.py:803-835, 842-860`; `execution_engine/intake.py:33-100` |
| "Run all approved" (Step 1 ledger: count made honest, scope deferred to the Run Planner) = `SELECT test_id FROM test_claims WHERE status='approved' AND valid_to IS NULL` → one S4 job per claim, the executability gate skipping unrunnable shapes as `skipped_unexecutable`. | `s4_execution_console.py:803-835`; `execution_engine/executability.py:68-82` (`gate_enqueue`: shape only — raises when ≥1 eligible recipe exists and ALL fail) |
| **Job 734's system-actor enqueue path, named:** the D-214 schedule. `s4_run_schedules` row 1 (env 59, cron `0 6 * * *`, `created_by` NULL) reads `enabled = TRUE`, `last_fired_at = 2026-09-09 02:09:53Z` — it was `enabled = FALSE` at the Step 2 pre-flight (last fired 2026-06-17) and no `activity_log` row records the re-enabling (`s4_schedule_update` toggles `set_enabled` without an audit row). The scheduler's `s4_schedule_tick` → `fire_due_schedules` (lateness-tolerant: a re-enabled schedule whose next occurrence has passed is due at once) → `enqueue_all_approved_claims(tenant, 59, created_by=None)`: **218 jobs (705–922) in twenty seconds, every one `created_by` NULL** — every approved claim whose recipe shape passes the gate; 281 stamped runs followed (org 902850e3 @ seq 253): 265 passed, 12 failed (req-302 ×6, req-315 ×3, req-320, REQ-L7D-NEGCTL-1, REQ-L7G-JOURNEY-1), 4 errored. The cadence runs the retired query with no actor and no recorded scope — the exact shape this step replaces. **AK confirms (GO): he re-enabled schedule 1 by hand through the D-214 toggle; the 02:09Z fire is authorised in fact, unrecorded only because the toggle writes no audit row — an execution control changed state with no audit row (FIX PLAN, fixed here by ruling 4). The 281 runs stand as valid stamped evidence enqueued by the tenant's admin.** | `scheduler.py:157-181`; `execution_engine/schedules.py:1-17, 125-165`; `views.py:3975-3986`; production read-only 2026-09-09 |
| Every enqueue converges on `enqueue_s4_execution(tenant_id, test_id, environment_id, created_by=None)` → `ExecutionJobStore.create_or_get_job` (get-or-create while active, D-130.A). The eight callers: `/run` (both branches, user), the requirement page's `run-substrate` (user), the release's `/releases/<id>/run` (user; SEC-4 production gate), the claim page, the API enqueue, the CI webhook's re-verify (`enqueue_claims_for_keys`, no user), the repair gate's revert / reexamine (CLI), the repair agent's auto-apply (`decided_by`). | `intake.py:23-32`; `jobs.py:95-112`; call sites listed in §0 of the transcript |
| Release scope environments are filtered to ACTIVE at the one enumeration seam (Step 3 interim); the declared-target model was deferred to this step. Env 78 "Prod1": inactive, `is_production`, `execution_policy = read_only`. | `intelligence/substrate_decision.py:266-292`; D-485 |
| The dispatch chokepoint: `execution_policy = disabled` → reject all; `read_only` → reject any recipe whose `mode_for(recipe_kind) != READ_ONLY` (data recipes); a non-Admin against a production env may run inspection recipes only. | `execution_engine/run.py:116-153`; `execution_engine/modes.py:15` |
| Recipe kinds live on `test_recipes.recipe_kind` ∈ {`data-recipe`, `metadata-recipe`, `ui-inspection`}; selection-eligible statuses via `coord.list_active_recipes(session, claim_test_id) -> list[RecipeRead]`. Production census (approved, current, the top eight rows of the kind × environment × status split): 444 `ui-inspection` (the conformance claims), 58 `metadata-recipe`, 41 `data-recipe`, plus `generated_unapproved` rows of both kinds; the full split is read at build. | `run.py:82-83`; `coordinator.py:1185-1215`; production read-only |
| Claims by kind (approved, current, production): `ui / conformance-claim` 444; `data_behavior`: automation-effect 88, prohibition 54, acceptance 33, value 12, state-transition 11; `configuration`: existence 22, property 3. Requirement keys with approved claims by origin: fixture 21, jira 5, manual 5, CANNOT_CLASSIFY 2, probe 1. | production read-only; `identity.py:43-48` (`ORIGINS`), `:203` (`origins_for_keys`) |
| The D-214 schedules panel lives on `/runs/substrate` (the S4 runs list): list from `RunScheduleStore.list()`, create `POST /runs/substrate/schedules` (env + cron), toggle / delete `POST /runs/substrate/schedules/<id>`. Table: `id, environment_id, cron_expr, enabled, last_fired_at, created_by, created_at`, UNIQUE (environment_id, cron_expr). | `views.py:3880-3895, 3948-3986`; `templates/runs/s4_list.html:230-260`; `alembic/…/20260611_0010` |
| The conformance path: `enqueue_ui_run(session, *, subject, claim_set_id, …, trigger)` authorises MEMBER, builds an immutable manifest from the APPROVED claim set (`build_manifest_for_claim_set`: membership by reference, `excluded_revoked` visible, the `surfaces` list derived from the members, pins: catalogue release, engine run set + hash, census, catalogue content hash, org snapshot), commits it, then enqueues (D-461: no browser execution without a committed manifest). **No surface filter exists today** — the builder takes the whole set. UI schedules (D-477) reference a `claim_set_id` and fire with `trigger = {scheduled_by_schedule, authorised_by_user}` — borrowed authority, audited each tick. | `execution_engine/ui_manifest.py:219-318, 319-360`; `execution_engine/ui_schedules.py:172-290`; `alembic/…/20260904_0010` |
| The Step 3 relationship the planner CONSUMES, never guesses (TA): `requirement_surface_links` (DECLARED, active) → the requirement's surfaces + their `persona_scope` from the inventory member; the materialised S2 `verifies` links put the surface's conformance claims on the requirement's COVERAGE_LINK_KINDS read. Production: 0 declarations yet. | D-485; `test_representation/surface_links.py` (`declared_surfaces`, `surface_claims`) |
| Release scope keys: `external_keys_for_requirements(release.requirements)`; the engine's claim set: `_claim_test_ids(session, keys)` over COVERAGE_LINK_KINDS. `s4_execution_runs` carries no job id and no plan reference (`batch_id` / `source` exist; `source` is NULL on every row). | `release/decision_composer.py:27`; `substrate_decision.py:238-252`; `result_store.py` |

## a. THE PLANNER SERVICE — `execution_engine/planner.py` (S4)

S4 owns execution; `intake.py` already composes S2 reads for enqueue. The
planner is the same composition made explicit, recorded and shown before it
runs. It never writes claims, recipes, links, sets or approvals.

### The scope

```
PlanScope = {kind: "requirement", key}            # one requirement's identity key
          | {kind: "release", release_id}         # a release's requirements
          | {kind: "schedule", schedule_id}       # the schedule's plan template (§d)
          | {kind: "tenant", environment_id}      # every approved claim on one env — the honest successor of "Run all approved"
+ inputs: environment_id (requirement / tenant scopes name their environment;
          a release resolves its DECLARED targets), include_hidden=false
          (fixture / probe origins), planned_by (user id or NULL for a system
          actor with authorised_by), authorised_by (the schedule's creator)
```

### The resolution — deterministic, in this order, every step recorded

1. **Claims.** The scope's requirement keys → the COVERAGE_LINK_KINDS read
   (`generated_from` + `verifies` — exactly what the requirement page, the
   sequence and the release scope read; the Step 3 `verifies` links are how a
   declared surface's conformance claims arrive — consumed, never inferred).
   Per claim: latest version; `deprecated` → **excluded `claim_deprecated`**;
   no current APPROVED version → **excluded `claim_not_approved`** (a draft);
   the requirement's origin (`origins_for_keys`) ∈ {fixture, probe} →
   **excluded `origin_fixture` / `origin_probe`** unless `include_hidden`
   (CANNOT_CLASSIFY is a gap, not a fixture: included, flagged
   `origin_unclassified`). Grouped **by kind** = `(archetype, claim_kind)`.
2. **Recipes.** `list_active_recipes` per admitted claim; zero eligible
   recipes → **excluded `no_eligible_recipe`**; ≥1 eligible but every one
   fails `check_recipe_executability` → **excluded `unexecutable_shape`**
   (the D-223 gate, applied at plan time so the plan is honest before the
   worker is); recipe kind decides the lane: `ui-inspection` → the
   **conformance lane**; `data-recipe` / `metadata-recipe` → the
   **functional lane**.
3. **Required environments.**
   - release scope: the release's **DECLARED targets** (`release_targets`,
     active) — `targets_source = "declared"`; **none declared** → the Step 3
     interim as the stated fallback: the ACTIVE environments holding
     evidence for the scope's claims (`_environments_with_evidence(…,
     tenant_id=)`), `targets_source = "fallback:evidence-active"`, and the
     plan SAYS so ("no target declared — planned against the environments
     that hold evidence; declare a target to make this a decision");
   - requirement / tenant scope: the named `environment_id` (the user's
     pick, as today);
   - schedule scope: the template's environments.
   Every environment holding evidence for the scope's claims that is NOT a
   target → **excluded `env_not_target`**, the reason naming what it is
   ("env 78 Prod1 — not a declared target; inactive; production; read_only").
4. **Admission per (recipe, environment)** — the chokepoint's rules applied
   at plan time, as exclusions with reasons, never as policy (Step 5 owns
   policy): `execution_policy = disabled` → `env_disabled`; `read_only` and
   `mode_for(recipe_kind) != READ_ONLY` → **`read_only_target_admits_inspection_only`**
   (the Step-5 note made concrete: a data recipe against a production
   read-only target is excluded and never enqueued; the claim's metadata
   recipe, if any, stays); `is_production` and the planner's tier < ADMIN →
   `production_requires_admin`; `is_active = false` → `env_inactive`. The
   chokepoint still re-checks at dispatch (§e: execution is its own act).
5. **Personas.** The persona scopes of the scope's DECLARED surfaces (Step
   3; the inventory member's `persona_scope`) — the set the conformance lane
   runs as; an empty set = no conformance lane. Persona SELECTION is a seam
   (§c): v1 plans every declared persona.
6. **Manifests.**
   - functional: the list of `(claim_test_id, environment_id)` pairs the
     plan will enqueue through `enqueue_s4_execution` — the existing S4 path,
     one job per pair (get-or-create: an already-active job is ATTACHED,
     recorded as such, never duplicated);
   - conformance: per persona, the ACTIVE claim set on the ACTIVE inventory
     (Step 3's derivation) **filtered to the scope's declared surfaces** —
     `enqueue_ui_run(…, surface_keys=[…])`, a new optional filter on the
     manifest builder (§f) that narrows the manifest's `surfaces` list and
     records `scope.surface_keys` + `scope.excluded_surfaces` in the payload;
     membership stays by reference to the APPROVED set — **never a new
     approval**, never a new set. No declared surface → no conformance
     manifest, stated.
7. **Pins.** Per environment: the org (D-286 seam) and its current sequence
   at plan time (`resolve_current_sequence`); per claim: the approved claim
   version and the eligible recipe versions; conformance: claim set id,
   inventory version, catalogue release id (the manifest adds the engine
   run-set hash at execution). Execution stamps its own values (Step 2);
   the plan's pins are what was SHOWN, the run's stamps what RAN — a
   difference is visible, never silent.
8. **The excluded line** — every exclusion `{kind: claim | recipe |
   environment | surface, ref, reason, detail}` with counts by reason.
9. **Seams** (§c) recorded on the resolution as `selection = {impact: "not
   yet — every scoped item", risk: "not yet — every scoped item", persona:
   "not yet — every declared persona", regression: "not yet — every eligible
   recipe"}`.

### The recorded object — tenant table `run_plans` (additive, `20260911_0010`)

```
run_plans
  id             UUID PK
  scope_kind     TEXT NOT NULL CHECK (scope_kind IN ('requirement','release','schedule','tenant'))
  scope_ref      TEXT NOT NULL                 -- the key / release id / schedule id / env id
  inputs         JSONB NOT NULL                -- environment_id, include_hidden, targets_source, …
  resolution     JSONB NOT NULL                -- claims by kind, recipes, environments, personas, manifests, pins, selection seams
  exclusions     JSONB NOT NULL                -- the excluded line
  planned_by     INTEGER                       -- NULL = a system actor (schedule) …
  authorised_by  INTEGER                       -- … carrying the schedule creator's borrowed authority (D-477)
  planned_at     TIMESTAMPTZ NOT NULL DEFAULT now()
  executed_by    INTEGER
  executed_at    TIMESTAMPTZ
  execution      JSONB                         -- {s4_jobs: [{test_id, environment_id, job_id, attached}], ui_jobs: [{persona, manifest_id, job_id}], refusals: [...]}
  TRIGGER run_plans_immutable  BEFORE UPDATE → RAISE when scope_*, inputs, resolution, exclusions, planned_* or authorised_by change
                                 (only executed_by / executed_at / execution may be set, once: RAISE when executed_at IS NOT NULL already)
  TRIGGER run_plans_no_delete  BEFORE DELETE → RAISE
release_targets
  id             UUID PK
  release_id     INTEGER NOT NULL              -- public.releases.id (tenant-scoped by the schema; no cross-schema FK, like cust_release_members)
  environment_id INTEGER NOT NULL
  declared_by    INTEGER NOT NULL
  declared_at    TIMESTAMPTZ NOT NULL DEFAULT now()
  active         BOOLEAN NOT NULL DEFAULT TRUE
  deactivated_by INTEGER, deactivated_at TIMESTAMPTZ, deactivation_reason TEXT
  CHECK deactivation-complete (as Step 3); UNIQUE (release_id, environment_id) WHERE active; no-delete + immutable triggers (the Step 3 pattern)
s4_execution_jobs.plan_id   UUID NULL     -- set by "Run this plan"; the consumer carries it to the run
s4_execution_runs.plan_id   UUID NULL     -- "the run carries the plan id"
s4_run_schedules.plan_template JSONB NULL -- {scope_kind, scope_ref, environment_id, include_hidden}; NULL = the legacy shape, read as {tenant, environment_id} (§d)
s4_run_schedules.authorised_by INTEGER NULL, authorised_at TIMESTAMPTZ NULL   -- "claim this schedule" (ruling 2); authority = authorised_by else created_by; both NULL → the tick refuses
s4_run_schedules.last_plan_id UUID NULL, last_refusal TEXT NULL, last_refused_at TIMESTAMPTZ NULL   -- what the last tick did, shown on the panel
```

`plan(scope) -> Plan` records the row and returns it; `execute_plan(plan_id,
*, executed_by, subject) -> ExecutionReceipt` re-reads the ROW (never the
caller's object), refuses an already-executed plan (`plan_already_executed`
— a re-run is a new plan; **ruling 3**), enqueues exactly the recorded functional
pairs with `created_by = executed_by` and `plan_id`, enqueues the recorded
conformance manifests with `trigger = {plan_id, executed_by, authorised_by}`,
and writes `execution` + `executed_*` once. The test asserts the enqueued
set == the plan's recorded set (§g item 2).

## b. EXCLUSIONS — first-class output

`claim_deprecated`, `claim_not_approved`, `origin_fixture`, `origin_probe`,
`no_eligible_recipe`, `unexecutable_shape`, `env_not_target`, `env_inactive`,
`env_disabled`, `read_only_target_admits_inspection_only`,
`production_requires_admin`, `surface_not_declared` (a conformance surface
of the active set outside the scope's declarations), `job_already_active`
(informational at execution: attached, not duplicated). Each carries a
human sentence and the ref it names. The PLAN view's "Excluded" line reads
the counts by reason and expands to the list; a plan with zero admitted
items is still recorded and shown ("nothing to run — and why").

## c. SEAMS — named, not built

Extension points on the resolution, each a documented function slot the v1
planner fills with "all": `select_by_impact(scope, claims) -> claims`,
`prioritise_by_risk(claims) -> ordered claims`, `select_personas(declared)
-> personas`, `select_regression(recipes) -> recipes`. The view renders one
"not yet — every scoped item" line per seam (the mock's reserved area). No
engine, no scoring, no comparison in this step.

## d. ENTRY POINTS

| entry | today | after Step 4 |
|---|---|---|
| release decision tab | "Run the scope" (env select) enqueues the scope's approved claims | **"Plan"** → `POST /releases/<id>/plan` records the plan → the PLAN view `/plans/<uuid>` → **"Run this plan"** (`POST /plans/<uuid>/run`). Beside it, **"Target environments"**: the release's declared targets with a declare (env select + Declare) and an explicit remove — the affordance the declared-target model needs (**ruling 1**: the block, minimal, screenshot-gated at the merge) |
| requirement page | "Run" (`run-substrate`, env select) enqueues the requirement's approved claims | the same button becomes **"Plan"** → `POST /requirements/<id>/plan` (env select) → the PLAN view → "Run this plan"; the live-chips poller keeps working after execution |
| `/runs/substrate` schedules panel (D-214) | a schedule = env + cron; the tick runs `enqueue_all_approved_claims` | a schedule references a **plan template**; the tick **plans** (a recorded `run_plans` row, `planned_by` NULL, `authorised_by` = the schedule's creator — borrowed authority, D-477) then **executes that plan**; the panel shows the template ("tenant-wide on env 59"), the last plan id or the last refusal, and **"Claim this schedule"** when the row has no authority (ruling 2); a legacy row (no template) is READ as `{tenant, environment_id}` — the honest successor of "Run all approved": the same set, now recorded and visible |
| `/run` (Run Tests) | the picker + "Run all approved" | GET → redirect to `/releases` with a retirement flash ("Run Tests has moved: plan from a release or a requirement"); POST → the same redirect (nothing enqueued). "Run all approved" retires; its successor is the tenant-wide plan a person must LOOK AT first (`POST /plans` with scope tenant + env → the view → Run). Nav slot: Step 6 |

The PLAN view (`templates/plans/detail.html`) renders the recorded row —
never recomputes: header (scope, planned by / at, executed by / at or "not
yet run"), **claims by kind with reasons** (why each is in: its requirement
key + link kind + origin), **target environments** (declared / fallback, each
with its pin: org + sequence), **personas**, **manifests + pins**
(functional: N jobs on env X; conformance: set id, inventory v, surfaces
K of M), **Excluded** (counts by reason, expandable), the four **"not yet"**
seam lines, and **"Run this plan"** (MEMBER+; disabled with the reason once
executed). MEMBER tier for plan / run / targets, the same as decorate and
run-substrate (D-245 declaration: min tier MEMBER; environment policy is
applied by the planner as exclusions and re-checked by the chokepoint).

## e. ACTOR SEMANTICS (rulings 2 and 4)

The plan records **who planned** (`planned_by`; NULL for a system actor,
which then MUST carry `authorised_by`). Execution records **who ran**
(`executed_by`; the S4 jobs' `created_by` and the UI trigger's `executed_by`
— may differ from the planner: a BA plans, a tester runs). Schedules keep
D-477's borrowed authority and **extend it (ruling 2): no schedule ever fires
on absent authority.** A schedule's authority is `authorised_by` (set by the
human act "claim this schedule" — audited, on the D-214 panel) else its
`created_by`; when both are NULL (the legacy row 1) the tick's planner
**REFUSES loudly** — a `run_plans` row is NOT written; the schedule row
records `last_refusal = "no authorising user — claim this schedule"` +
`last_refused_at`, an `activity_log` row names it, the panel shows the
refusal with the exit **"Claim this schedule"** (MEMBER+; sets
`authorised_by` = the actor, `authorised_at`, writes the audit row). AK claims
schedule 1 at the merge; until then its fires refuse. The authority is
re-checked at every tick (a dead / demoted authoriser → the schedule
deactivates loudly, as the UI schedules do).

**Ruling 4 — the toggle audits.** `s4_schedule_update` (enable / disable /
delete) writes an `activity_log` row (`s4.schedule.<action>`: actor, schedule
id, `old → new` enabled state) — the audit the pre-flight found missing.

## f. NON-GOALS

No policy object (Step 5 — the planner applies the chokepoint's rules as
exclusions, it does not own them); no layout collapse or nav (Step 6); no
impact engine, no risk scoring, no persona comparison, no regression
selection (seams only); no conformance semantics change (the surface
filter narrows a manifest; rules, applicability, verdicts, standard views
untouched); no change to S2 (claims, recipes, links, sets, approvals); no
backfill of plans for past runs (the 281 runs of 02:09Z stay plan-less,
honestly — `plan_id` NULL).

## g. Verification plan (the brief's list, restated as tests)

1. **release 16**: `plan(release=16)` names the 19 claims and their recipes
   on env 59 only; env 78 excluded `env_not_target` with the reason naming
   inactive / production / read_only; fixture-origin keys excluded with
   counts (on the harness: a planted fixture-origin requirement).
2. **"Run this plan" executes exactly the recorded plan**: the enqueued
   `(test_id, environment_id)` set == the plan's functional set; every job
   carries `plan_id`; the consumer stamps `plan_id` on the run; a second
   execution refuses `plan_already_executed`.
3. **a declared target change re-plans**: declare env B on the release → a
   new plan names A and B; deactivate A → the next plan names B only and the
   evidence-holding A appears under `env_not_target`.
4. **a data recipe against a read-only target** is excluded
   `read_only_target_admits_inspection_only` and never enqueued; the
   claim's metadata recipe (if any) is admitted.
5. **the conformance batch is filtered to declared surfaces**: two of six
   surfaces declared on the scope's requirement → the manifest's `surfaces`
   list has 2 entries and `scope.excluded_surfaces` the other 4; membership
   still by reference to the approved set (no new set, no new approval).
6. **`/run` redirects** (GET and POST) with the retirement flash; nothing
   is enqueued by the POST.
7. **the schedule fires a plan**: `fire_due_schedules` records a `run_plans`
   row (`planned_by` NULL, `authorised_by` = the creator) and the runs carry
   its id; a legacy row reads as the tenant-wide template.
8. **screens** over the real app on scratch from the three entry points
   (release decision tab → Plan → view → Run; requirement → Plan → view;
   schedules panel with the template + last plan) plus the retirement
   redirect — `docs/ui-testing/step-4-fixtures/`.

Plus the D-468 set. Suites at the implementation commit as before.

## h. Migration classification (for the runbook)

`20260911_0010` — ADDITIVE: two new tables (+ triggers), three nullable
columns on existing tables (`s4_execution_jobs.plan_id`,
`s4_execution_runs.plan_id`, `s4_run_schedules.plan_template`); no data
write; no backfill. Dumpless under D-476. Reader window: old code ignores
the new columns (nullable, omitted from its INSERTs); new code before the
migration would fail on `plan_id` at enqueue / persist and on the plan
routes — migration first (D-285), as every step.

## Blast radius

| touched | how |
|---|---|
| tenant schema | `run_plans`, `release_targets`, three nullable columns |
| S4 | `execution_engine/planner.py` (new); `intake.enqueue_s4_execution(plan_id=)`, `jobs.create_or_get_job(plan_id=)`, the consumer → `persist_run_evidence(plan_id=)` / `RunEvidence.plan_id`; `schedules.fire_due_schedules` plans then executes; `ui_manifest.build_manifest_for_claim_set(surface_keys=)` + `enqueue_ui_run(surface_keys=)` |
| views + templates | `/releases/<id>/plan`, `/releases/<id>/targets[…]`, `/requirements/<id>/plan`, `/plans/<uuid>`, `/plans/<uuid>/run`, `/run` redirects; the decision tab's Plan + targets block; the requirement page's button; the schedules panel's template column; `plans/detail.html` |
| not touched | S2, the readers, the decision engine and its ladder, conformance semantics, the chokepoint's rules (applied, not changed), Step 3's tables |

## Rulings at the GO (2026-09-09)

1. **Target environments**: the minimal "Target environments" block on the
   decision tab (declared list + env select + Declare + Remove),
   screenshot-gated at the merge.
2. **Absent authority refuses**: a schedule whose `authorised_by` and
   `created_by` are both NULL never fires; the tick refuses loudly with the
   exit "claim this schedule" (a human act, audited, sets `authorised_by`).
   Extends D-477. Schedule 1 stays enabled; its next fire refuses until AK
   claims it at the merge.
3. **One execution per plan**; run again = re-plan.
4. **The schedule toggle audits** (actor, old → new) — the gap the
   pre-flight found, fixed here.

The 02:09Z fire: AK re-enabled schedule 1 by hand via the D-214 toggle; its
281 stamped runs stand as valid evidence enqueued by the tenant's admin;
the only defect was the missing audit row (ruling 4).
