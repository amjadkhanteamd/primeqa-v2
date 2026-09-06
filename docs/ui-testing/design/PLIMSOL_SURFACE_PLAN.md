# Plimsol surface plan

Status: DRAFT. No phase is approved.
Author: architecture turn, 2026-08-18.
Language: ASD-STE100 Simplified Technical English.

---

## 1. Purpose

This plan repairs the review surface of Plimsol. It is not a visual refresh.

The surface is the only instrument that a buyer sees. If the surface renders a
past result as a present fact, the product tells the same lie that PrimeQA
exists to prevent.

---

## 2. Root cause

The navigation has seven items: Requirements, Run Tests, Results, My Reviews,
Dashboard, Test Library, Releases.

Each item is a table of a database table. Each item is not an answer to a
question. This is the root cause. The navigation mirrors the schema, not the
job.

Every surface renders a subset of the same four facts:

1. what the system asserts (the claim)
2. whether an operator approved it (the recipe and the approval)
3. whether it ran (the run)
4. whether the run is still current (the drift)

Each surface renders these four facts differently. The measured symptoms
follow from this one cause:

- **Four identity formats** for one object: `#282`, `SQ-205`,
  `REQ-ARC-APPROVED`, `req-302`.
- **Three places start a run**: Run Tests, the requirement page, the schedule.
  A fourth is named in the Results empty-state text.
- **Four places show an outcome**: Run Tests, Results, Test Library, Dashboard.
- **One fact renders twice on one row**: the Test Library shows depth in the
  `DEPTH` badge and again in the subtitle.
- **Absence renders three ways**: `never run`, `0 of 0 tests green`, and
  `—%`.

Do not repair these symptoms one at a time. Repair the cause.

---

## 2A. SETTLED — the Org model duplication is a read-path defect

CC answer: **H2 is true. H1 is false. S1 is not corrupt.**

Within each org the entities are unique. env-59 holds 156 distinct objects at
version 207. The page returns 303 rows because it reads without the org
clause, and it collects 147 more objects from env-78.

### The mechanism

The route org-scopes only when `multi_env` is true. `multi_env` counts
**active** environments. Tenant 1 has 1 active environment out of 29. env-78
is inactive. The route therefore takes the "single environment, vacuously
safe" branch and reads org-blind.

The vacuous-safety assumption is false. An inactive environment keeps its S1
data in the schema. Environment liveness does not control data presence.

### Correction

I called this a blocker on screenshot evidence alone. That was wrong. There is
no substrate corruption, and the Flow lane is not blocked by one.

### The new question — the blast radius is unmeasured

CC checked one page. The org clause exists in the reader, and other callers
may pass a real org id. If any generation, validation, or version-diff path
also gates its org clause on an active-environment count, then that path reads
two orgs blended.

### CC answer — the blast radius is contained

All three named paths are org-scoped, and each one is scoped deliberately:

- **Generation** pins the org and fails loud. A D-286 comment names the intent.
- **Grounding validation** builds one model per org inside an explicit per-org
  loop, and pins that org's own sequence.
- **Drift detection** is scoped by signature. Every detector requires the org
  id and puts it in the SQL.

**The Flow lane is clear.** Nothing in this plan gates it.

Four org-blind reads remain. Three are benign: the org-model page (display
only), a documented advisory whole-tenant metadata picker, and an offline
evaluation harness over fixture data.

### The fourth is not benign — the decision engine

The release decision engine holds its own staleness pin. On one branch it
reads a **tenant-wide maximum sequence**, with no org clause.

The branch keys on a count of environments that hold run evidence for the
release's claims. It is a different counter from the org-model page, and it is
the same pattern: a count decides whether the org clause exists.

**Today this is latent, not wrong.** The tenant-wide maximum is 207, and 207
belongs to env-59's org. env-78 tops out at 206.

**It is one sync away from wrong.** If env-78 ever syncs past env-59, then a
release decision about env-59 claims grades itself against env-78's sequence.
A wrong GO is the exact failure this product exists to prevent.

This also changes Phase 2. See the one-resolver note there.

---

## 3. Target information architecture

Three navigation items. Each one answers a question that a QA lead asks.

| Navigation | Question it answers | What it absorbs |
|---|---|---|
| Requirements | What do we assert, and is it approved and covered? | Test Library, My Reviews |
| Releases | What is in scope, is it current, and can we ship? | Run Tests |
| Results | What broke, and what broke it? | Dashboard |

Reference material moves to Settings. Operational truth does not.

### Why each move

**Test Library folds into Requirements.** A claim has no meaning apart from
its requirement. The Test Library is a cross-requirement view of the same
rows. Keep the view. Remove the navigation item. Reach the view from
Requirements as "all claims".

**My Reviews folds into Requirements.** A review queue is a task list, not a
place. Keep the count badge in the header. Make it a filter on the claim list.

**Run Tests is removed.** A launcher is not a destination. Its one unique
capability is multi-requirement selection. A release is the correct scope for
that capability.

**Dashboard folds into Results.** VERIFY FIRST. I have not seen the Dashboard.
This move assumes the Dashboard duplicates the Results summary. Phase 0 must
confirm this before the move.

### Org model stays in Settings. Org state does not.

An earlier version of this plan proposed an `Org` navigation item. That
proposal is withdrawn. The object catalogue is reference material: 303 rows,
no action, and a rare visit. Settings is the correct home.

Split the object in two:

| Thing | Nature | Home |
|---|---|---|
| Org model — objects, fields, validation rules | reference | Settings, as today |
| Org state — sync time, org sequence, drift count | operational | a status band where decisions happen |

Org state must never travel into Settings. A drift backlog of about 121
unacknowledged events gates every run and every GO/NO-GO decision. Buried in
`Settings → Tools → Org model`, nobody sees it.

Org state is not a page. It is a band that renders on the environment control,
on the release page, and on Results.

### Settings observations

1. `LLM usage & plan` appears under SETTINGS. `LLM usage` appears again under
   SUPER ADMIN. One fact, two entries.
2. The `TOOLS` group holds Ask, Substrate Insights, Org model, and Knowledge.
   These are read surfaces, not settings. Rename the group to `Reference`. Move
   `Ask` back to the main application.
3. Platform status shows `29` environments against `4` connections. Phase 0
   must count how many are fixtures.
4. The Appearance text states that dark is the default. The application
   renders light, and `Light` is selected. Correct the text or correct the
   default.

---

## 4. Phase A — repair proposal gate

**Priority: immediate. This phase is independent of the rest of the plan.**

The repair panel is live. It can turn a real red into a green today.

### The defect

The panel converts an LLM guess into a mutation of a recipe, behind one
button. Three examples from the live panel:

- `The automation likely requires the order amount to stay within a valid
  threshold (e.g., under 250000)` — a guess about org behaviour.
- `adding a plausible triggering field is the best minimal guess` — the text
  declares itself a guess.
- `"Home Loan" is not a valid value ... replacing it with a valid value like
  "Mortgage"` — the diagnosis is correct and derived. The remedy changes the
  meaning of the claim.

The panel header says `real defects never appear here`. That is a statement
about a classifier. No measurement supports it.

### The change

Apply a three-verdict gate to every proposal.

| Verdict | Condition | Action shown |
|---|---|---|
| `DERIVED` | S1 or the platform error string gives the answer | Approve and apply |
| `SPECULATIVE` | LLM inference only | Open recipe — the operator edits |
| `SEMANTIC` | the value appears in the asserted claim | Refuse — route to the requirement |

Rules:

1. Remove the confidence percentage. It is not calibrated against any measured
   outcome.
2. Split the diagnosis from the remedy. A derived diagnosis with a chosen
   remedy value is still an operator decision.
3. Remove the header sentence until a measured false-negative rate supports it.
4. Switch the panel off until the gate lands. Dormant-first applies.

### Precondition — check for an existing switch

Settings holds an `Agent autonomy` item under SUPER ADMIN. This may already
control the repair agent. Read it before any build. Do not build a second
switch beside a switch that works.

### CC answer — the switch does not do what the name says

**`agent_enabled` does not gate proposal creation.** The triage function
writes proposals on every scheduler tick, whatever the setting says. It uses
only an attempt cap. The autonomy settings gate the **automatic apply** pass
only.

This reverses the shape of the risk:

- The autonomous path is better guarded than I assumed. It needs two flags,
  plus a high trust threshold, and it always skips production environments.
- The **human** path is guarded by nothing. Proposals appear with an
  `Approve & apply` button, and no setting stops them.

Three more defects follow from the same answer:

1. **The kill switch has a split brain.** `agent_enabled` is edited on the
   agent settings page. `repair_auto_apply` is edited on the LLM usage page.
   One safety control lives on two surfaces.
2. **`trust_threshold_medium` has no consumer.** It is stored and validated.
   The repair path reads only the high threshold. A dead control on a safety
   surface is worse than no control.
3. **The displayed confidence may share a scale with the machine threshold.**
   VERIFY. If the number beside `Approve & apply` is the same field that the
   automatic pass compares against a threshold, then an operator reads a
   number calibrated only for a machine.

### CC answer — it is worse than that

The number is the same field. The automatic pass reads
`repair_proposals.confidence` and compares it against the high trust
threshold. The user interface renders the same column.

And the column holds **the LLM's own self-reported confidence**, parsed
straight from its output.

So the safety gate on autonomous recipe mutation is set by the same process
the gate exists to guard. The model grades its own work, and the grade opens
the door.

A null value coerces to zero, so an unconfident proposal can never apply
automatically. That guards the null case. It does not guard the confident
guess. A model that emits `0.9` on a plausible invention passes the gate.

**Therefore:** remove the confidence from the operator surface **and** remove
it from the automatic gate. The gate must read a derived signal — the
three-verdict classification below — not a self-report.

### Revised change list

1. Gate proposal **creation**, not only the apply pass. No such gate exists.
2. Move both flags onto one page. A safety control has one home.
3. Remove `trust_threshold_medium`, or wire it.
4. Apply the three-verdict gate below.
5. Remove the confidence percentage from the operator surface.
6. Remove the header sentence until a measured false-negative rate supports it.

### The three-verdict gate is buildable today

CC confirms the recipe payload carries no per-value marking. But the
distinction is derivable: the assert step names its fields, and the staged
keys are known. The intersection is exactly what `coverage_flag.py` already
computes for D-454. Reuse it. This is a recomputation at gate time, not a new
substrate feature.

**Bare keys must fail closed.** 105 of 1014 create-step keys are unqualified.
An unqualified key cannot be classified. Treat it as `SEMANTIC` and refuse.

### Done means

- Every proposal carries a verdict.
- No `SPECULATIVE` or `SEMANTIC` proposal exposes an apply action.
- A test proves that a proposal which touches a claim-referenced value gets
  `SEMANTIC` and refuses.

---

## 5. Phase 0 — grounding

**Read only. No mutation. No schema change.**

Every figure in later phases comes from this phase. I hold no verified counts
today.

### Measure

0. **The S1 duplicate. Highest priority.** Separate the three hypotheses in
   section 2A. Report the distinct object count, the row count, the tenant and
   org filter on the read path, and the version scope of the query.
1. Requirement count by origin. How many rows are fixtures, probes, Jira
   requirements, and manual requirements?
2. Claim count by status. How many are draft, approved, and rejected?
3. Run records. **Does a run record carry the org sequence at run time?**
   This is the key unknown. Phase 2 depends on the answer.
4. Release count by state. How many are fixtures? Has any release advanced
   past `Planning`?
5. Dashboard content. What does it render, and does Results render the same?
6. Identity formats. Where does each of the four formats come from?
7. Environment count. Platform status shows 29 environments against 4
   connections. How many are fixtures?

### Done means

A written measurement, held against the repo, with the query for each figure.
No build starts before this exists.

---

## 6. Phase 1 — provenance and absence

**Goal: remove the noise. This is the loudest complaint.**

### Changes

1. **The origin column already exists.** `requirements.source` is a
   `varchar(20)`, and a database CHECK limits it to `jira` and `manual`. The
   change is a constraint widening, not a new column. Add `fixture`, `probe`,
   and `CANNOT_CLASSIFY`.
2. Backfill the existing rows to `CANNOT_CLASSIFY`. An unknown origin renders
   as unknown. The operator acknowledges each one.
3. Default the list to `jira` and `manual`. Show the hidden count with a
   toggle. Do not hide rows in silence.
4. Apply the same origin rule to releases. The release list holds fixtures.
5. Render an absent title as an absence: "no title recorded", muted. Move the
   re-sync action to a row menu.
6. Unify the identity format. One format, everywhere.

### CC answer — this phase was built on the wrong table

There is no contradiction between the two counts. The Run Tests page does not
read `requirements` at all.

The picker reads distinct `external_key` values from
`test_requirement_links`, joined to current approved claims. Live: 408 link
rows, 42 distinct keys, and 34 keys survive the join. The `221` figure is the
sum of approved claims across those 34 keys, and it reproduces exactly.

The `requirements` table is consulted only to decorate a row with a summary.

**A widening of `requirements.source` therefore fixes nothing here.** Of the
34 keys, 21 are fixture and probe keys that never had a `requirements` row at
all. Round 1 already showed why: fixture and probe origins are not
representable in `requirements`, so those campaigns wrote links only.

### `#282` is a dangling key, not a missing title

The `req-282` link key has no matching requirement record. Six of the seven
`req-N` keys match a record. That one does not.

So `Untitled — re-sync from Jira` is not a blank title. It is the interface
covering a referential gap. My earlier reading was wrong, and a title fallback
would have hidden the real defect.

The same explanation covers the identity chaos on every surface. `#282`,
`SQ-205`, `REQ-ARC-APPROVED`, and `req-302` are not four formats of one thing.
They are keys from four populations inside one free-text string column.

### The fork this opens — TA question 2

Is `external_key` an identity, or is it a reference?

Today it is both, and that ambiguity produces the dangling key, the four
formats, and the un-representable origins.

Two exits:

- **A.** Every link key gets a real requirement record, and origin lives on
  that record. The link stays a reference.
- **B.** The link key becomes first-class identity and carries origin itself.
  The `requirements` table becomes optional decoration.

Do not build either until this is decided. It sets the identity format on
every surface in this plan.

### Migration

MIGRATE-FIRST applies. Predict-gate, then an explicit migration at the exact
revision, then a read-back, then a fast-forward merge.

### Done means

- No fixture appears in a default view on any surface.
- The hidden count is visible on every surface that filters.
- One identity format appears on all seven surfaces.

---

## 7. Phase 2 — contemporaneity

**Goal: make the greens honest. This is the load-bearing phase.**

A green from Jun 29, after the org moved, is true and useless at the same
time. The `Evaluate GO/NO-GO` button reads these greens today.

### Changes

1. Stamp the org sequence on each run at run time. **The executor already
   computes this value in memory and discards it.** The change is a column
   plus a persist.
2. Derive a readiness state: `NEVER RUN`, `STALE`, `CURRENT`,
   `CANNOT_DETERMINE`.
3. Split the status badge into two columns: `Readiness` and `Last outcome`.
   Never combine them.
4. When readiness is `NEVER RUN`, the outcome cell is empty, not zero.
5. Stamp the generation environment and the execution environment on each run.
   Render the difference when the two differ.
6. Default the Results window to "since the last run", not to a clock window.
7. Replace `0 of 0 tests green` with "no tests ran in this window".

### Correction — the precedent I cited does not ship

An earlier version of this plan told you to reuse the
`assessed_against_s1_seq` pattern from the coverage panel. CC found that no
code on `main` writes that key. It exists only in data written by the unmerged
D-456 branch. I cited a precedent from memory and did not verify it. There is
no pattern to copy. There is a column to add.

### Staleness is targeted, not global

My earlier objection was that a global sequence stamp marks every run stale
after any org change, and that the badge then becomes noise.

That objection is now answered. `test_claim_coverage` holds the claim-to-entity
edge, and it is fully populated: 922 rows, 393 of 393 current claims, all 215
approved claims. A drift event names an entity. Join the entity to the claims
that read it. Only those runs go stale.

Global staleness is not needed and must not be built.

### 726 existing runs can never be stamped

The org sequence at the time of a past run is unrecoverable. No backfill is
possible.

Those runs render `CANNOT_DETERMINE`. They are not stale, and they are not
current. This follows the absence rule.

**Name the commercial consequence:** on the day this lands, the surface turns
mostly grey until the tests run again. That is honest, and it is also the
first thing a buyer sees. Decide deliberately.

### One resolver, not two

I wrote this phase as though staleness did not exist anywhere. It does.

The release decision engine already grades staleness, against a sequence it
pins itself. On one branch that pin is a tenant-wide maximum with no org
clause.

A run-level readiness state would therefore be a **second** staleness answer
beside an existing one, and the two can disagree. The binding invariant
forbids that.

Before any stamp lands, decide which component owns the staleness primitive,
and make the other component read it. Do not add readiness beside the decision
engine's pin. Replace the pin.

### A second-order hole

`test_claim_coverage` is current-only. It carries no claim `version_seq`, and
the coordinator rederives it on every claim write. So it describes what a claim
reads **now**, not what it read at run time.

The run row does carry `claim_version_seq`. Therefore claim drift and org drift
are two different questions, and both need an answer before a green is honest:

- Did the org change under this run? Compare the stamped org sequence.
- Did the claim itself change since this run? Compare `claim_version_seq`.

### Done means

- Every run carries an org sequence and both environment identities.
- A drift event marks the affected runs stale within one sync.
- No surface shows an outcome without a readiness state beside it.

---

## 8. Phase 3 — surface collapse

**Goal: seven navigation items become four.**

Do this phase after Phase 1 and Phase 2. A collapse over dishonest data moves
the dishonesty to fewer pages.

### Changes

1. Remove the Run Tests page.
2. Fold the Test Library into Requirements as a cross-requirement claim view.
3. Fold My Reviews into the claim view as a status filter. Keep the header
   count.
4. Fold the Dashboard into Results, subject to the Phase 0 finding.
5. Create the Org page. Move the run schedule, the environment list, the sync
   state, and the drift backlog into it.
6. Make `Root cause` the default grouping on Results. Requirement grouping is
   what every competitor does. Root cause is the differentiator.
7. Promote the claim kind. `prohibition claim`, `automation effect claim`,
   `state transition claim`, and `existence claim` are the strongest signal on
   the Test Library row, and they render as grey micro-text today. No
   script-based competitor can print that column.

### Done means

- Four navigation items.
- One run entry point per context: the requirement, the release, the schedule.
- One outcome surface.

---

## 9. Phase 4 — the release becomes runnable

**Goal: a release decision reads current truth, not old truth.**

### The defect

The release page has no run action. `Evaluate GO/NO-GO` reads whatever results
exist. A GO on stale greens is the exact failure that PrimeQA exists to
prevent. This is the highest-stakes vacuous green in the product.

The release list also shows a state machine that nothing has exercised. Every
release sits in `Planning`. `In progress`, `Ready`, `Decided`, and `Shipped`
have no observed transition.

### Changes

1. Add "Run the scope" to the release page.
2. Show scope readiness on the release: claims in scope, approved, current,
   stale, never run.
3. `Evaluate GO/NO-GO` refuses when any claim in scope is stale or never run.
   It reports which ones. It does not return a verdict on incomplete evidence.
4. Name a release by a human or derive the name from the scope. A hash is not
   a name.
5. Demonstrate one release through every state. An unexercised state machine
   is the same class of gap as an unarmed evaluator family.

### Done means

- A release can run its own scope.
- `GO` is impossible while any claim in scope is stale.
- One release has passed through every state, observed.

---

## 10. Sequence against the Flow lane

The Flow lane opens next. This plan competes with it. My lean:

| Order | Work | Reason |
|---|---|---|
| 1 | Repair agent gate | Proposal creation is ungated, and the gate reads a self-report. |
| 2 | Decision engine sequence pin | Latent wrong GO, one sync away. |
| 3 | Org filter fix, Results window default | Small, and both are correctness defects. |
| 4 | Flow lane | Nothing blocks it. |
| 5 | Fork 1 and Fork 2, then run stamp and origin | These need TA answers first. |
| 6 | Phase 3 and Phase 4 | Last. |

**The Flow lane is not blocked.** Generation, grounding validation, and drift
detection are all org-scoped. My earlier claim that a substrate defect gated
Flows was wrong, and CC disproved it twice.

Items 1 to 3 are small and independent of the Flow lane. They can run beside
it or before it. Item 5 cannot start until the TA answers, because both forks
set substrate shape.

**Reason for the split:** the Flow lane creates a new population of claims and
runs. If the surface cannot separate a fixture from a real requirement, and
cannot separate a stale green from a current green, then the Flow lane only
adds more unreadable rows. Flows makes the data problem worse. Flows does not
make the layout problem worse. Therefore the data-layer phases go first, and
the layout phase goes last.

---

## 11. TA package — five judgment forks

CC answered every factual question in rounds 1 and 2. What remains needs
judgment, not a query.

### Fork 1 — `external_key`: identity or reference?

The largest fork. See Phase 1. It sets the identity format on every surface,
and it decides whether origin is representable at all.

My lean: **B**, the link key becomes first-class identity. Reason: 21 of 34
keys have no requirement record today, and option A demands that we invent 21
records to describe fixtures that are not requirements.

### Fork 2 — who owns the staleness primitive?

The decision engine pins one sequence. A run stamp would pin another. One
resolver, not two.

My lean: the run stamp owns it, and the decision engine reads run-level
readiness. Reason: a release decision is a roll-up of run facts, so the fact
should live at the run.

### Fork 3 — 726 grey rows on day one

Existing runs carry no sequence, and no backfill can recover one. They render
`CANNOT_DETERMINE`. The surface turns mostly grey until the tests run again.

My lean: ship the grey. Reason: the absence rule is not negotiable for a
commercial impression. But this is a commercial judgment, not an architectural
one, and it is the TA's call more than mine.

### Fork 4 — should the repair agent create proposals at all today?

Proposal creation is ungated. The confidence is an LLM self-report, and the
same self-report opens the automatic gate.

My lean: **no**. Stop creation until the three-verdict gate lands. A proposal
that an operator can apply is a live mutation path, whatever the automatic
pass does.

### Fork 5 — the navigation collapse

Still my weakest claim. I asserted that a launcher is not a destination, with
no evidence. Plan, run, and report is the shape a QA lead arrives expecting,
and it is what the named competitors ship.

No lean. This one needs the TA more than the others, because I have already
argued both sides and neither rests on evidence.

---

## 12. Verified figures

CC verified the following against the live database at HEAD
`414c9020`, in a read-only session. These are safe to build on.

- `test_claim_coverage`: 922 rows, covering 393 of 393 current claims and all
  215 approved claims. Every entity reference joins to a real entity.
- `s4_execution_runs`: 16 columns, no S1 sequence column. 726 run rows, and
  none carries a sequence in its evidence.
- Create-step keys: 909 of 1014 qualified, 105 bare.
- env-59 at version 207: 156 distinct objects. The page returns 303 rows.
  147 names come from env-78.
- Tenant 1: 1 active environment out of 29.
- `requirements`: 6 `manual`, 5 `jira`. The CHECK constraint allows no other
  value.
- The Results window default is `24h`, set in one place.

Read from the screenshots only, and still unverified:

- Seven navigation items.
- The schedule is paused, last fired `2026-06-17T06:00`.
- Eighteen releases visible, all `Planning`.

Round 2 added:

- `test_requirement_links`: 408 rows, 42 distinct keys, 34 keys survive the
  approved-claim join. The `221` total reproduces exactly.
- Of the 34 keys: 7 point at `req-N` records (one of them, `req-282`, has no
  matching record), 6 are Jira keys, and 21 are fixture or probe keys with no
  record at all.
- The repair confidence column holds the LLM's own parsed self-report, and the
  automatic gate compares that same column against the trust threshold.
- Generation, grounding validation, and drift detection are all org-scoped.

The requirement count contradiction is resolved. There was none. The two
numbers count different things.
