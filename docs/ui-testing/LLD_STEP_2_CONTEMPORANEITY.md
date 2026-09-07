# LLD Step 2 — contemporaneity (Fork 2: the run stamp owns staleness; Fork 3: legacy runs render CANNOT_DETERMINE)

Status: RULED 2026-09-08 (AK GO) — build follows on this branch.
Rulings: **R-ground** (one targeted predicate governs BOTH stamps — a
grounding is stale only when a covered read changed after its verdict;
the mint fix in §d lands only with it); **R-ungrounded** (a current run
with no grounding verdict is an UNGRADED input on the grounding axis —
per D9 it blocks GO and CONDITIONAL GO, never NO GO alone, rendered as a
named warning with the count; production count measured below: zero);
**the sentence** is the TA's Fork-3 wording verbatim; legacy count
corrected to 727.

**The design's own invariant (recorded):** the world's pin and the run
stamp are ONE value by construction — captured once in the select
bracket and carried on the prep tuple; the stamp cannot drift from the
pin because there is no second read.
Branch: `step-2-contemporaneity` (from main @0f34e82, D-483).
Derives from: D-479 (the sequence; Step 2 = "org sequence stamped per
run; readiness NEVER_RUN / STALE / CURRENT / CANNOT_DETERMINE split from
outcome; targeted staleness via `test_claim_coverage`, never global; both
environments stamped; the legacy runs render CANNOT_DETERMINE"), the
surface plan §7 (the seven changes; "one resolver, not two"; "global
staleness is not needed and must not be built"; "726 existing runs can
never be stamped"), Fork 2 (ratified: the run stamp owns it) and Fork 3
(ratified: ship the grey), D-482 (Step B: the org-required resolver whose
shape reserves `source = run_stamp`; the org-less checkpoint finding),
D-483 (Step 1), the FIX PLAN HIGH item "absent grounding is graded as
fine" (assigned here), D-230.2 (the async select/execute brackets), D-286
(the ONE env→org seam), D-9 (roll-up order: NO GO stands over ungraded).

**Thesis.** A green run says nothing about NOW unless the run records
WHAT it ran against. Step 2 stamps every new run with the org and the
org's logical sequence at execution — the executor's own value, taken in
the same bracket that planned the world — and derives readiness for
(claim, environment) from that stamp against the org's current sequence,
scoped through what the claim actually reads. Outcome and readiness
become two facts, never one. A run with no stamp says so
(CANNOT_DETERMINE), and no legacy row is relabelled. The release
decision stops grading an unrun or unstampable claim as fine, and the
Evaluate act refuses a non-current scope by name, with the scope's run
one click away.

---

## 0. Pre-flight — the sites, cited (verified 2026-09-08, read-only)

| fact | where |
|---|---|
| runs carry NO org sequence | `s4_execution_runs` columns on production: `run_id, recipe_id, recipe_version_seq, claim_test_id, claim_version_seq, environment_id, outcome, started_at, finished_at, duration_ms, evidence, failure_category, sf_error_code, batch_id, source, executing_identity` — no org, no sequence |
| the executor already computes the org sequence at run time | `execution_engine/run.py:651-690` `_prepare_async_execute` (the SELECT bracket, D-230.2): `org_id = _resolve_run_org(session, environment_id)` (:682, the D-286 seam) → `plan_data_recipe_world(plan, SemanticOrgModel(conn, org_id), …)` (:683) → `data_executor.py:317` `at_seq = s1.current_version_seq()`. The value is used to pin the world and then DISCARDED — `RunEvidence` (`evidence.py:301-341`) carries `recipe_version_seq`, `claim_version_seq`, `environment_id`, no org, no sequence; `result_store.py:148-172` persists exactly those. The sync path (`run.py:348-352`) builds the same model; the metadata path (`executor.py`) and the 1-step create-rejected path read no S1 at all |
| the execute bracket holds NO connection | `run.py:612-640`: `prep = _prepare_async_execute(…)` under the session; `_execute_for_kind(recipe, None, …, world_plans=…)` after it. A "later read" is impossible there by design — the stamp MUST ride the prep tuple |
| the drift hook writes nothing | `semantic/metadata_drift.py` executes SELECTs only (:318, :381, :618-659); the review watermark `s1_drift_review_watermarks` advances only on the `--ack` act (D-449/D-450) |
| legacy runs | **727** rows on tenant 1 (633 in env 59, 94 in env 78; 2026-06-06 → 2026-09-07; 1 with a NULL `claim_version_seq`) — the brief's 726 corrected: one run landed since the surface plan counted. None can be stamped after the fact: the sequence at the time of a past run is unrecoverable |
| `test_claim_coverage` records what a claim reads | columns `claim_test_id, entity_type, entity_id, reference_kind ∈ {subject, condition}`; 922 rows (558 subject / 364 condition) over 393 claims; every `entity_id` resolves to an `entities` row (922/922); written by the coordinator at `write_claim` (`coordinator.py:553`, `extract_coverage` step 9; rewritten on a new claim version, :627-635). **All 727 runs are on covered claims.** 659 approved claims, 215 with coverage — the 444 uncovered approved claims have no runs |
| S1 is SCD Type 2 | `entities(id PK, …, valid_from_seq, valid_to_seq, connected_org_id)`, `edges(id, source_entity_id, target_entity_id, …, valid_from_seq, valid_to_seq, connected_org_id)`; a change CLOSES the row (`valid_to_seq = seq`, `materialize.py:38-44`) and inserts a successor. So "a covered read changed since S" = the covered row's `valid_to_seq > S`, or an edge on it closed/opened after S |
| `claim_version_seq` already on the run row | above; the claim-version axis is graded by `version_currency` (Step B kept it separate — `SequenceResolution.axis = org_sequence`) |
| the Step B resolver reserves `source = run_stamp` | `sync/readiness.py` `SequenceResolution(state, current_seq, as_of, source ∈ {org_current, run_stamp}, axis, connected_org_id, reason)`; `SEQ_SOURCE_RUN_STAMP` is a constant nothing produces |
| inventory cuts mint an ORG-LESS checkpoint | `semantic/surface_entities.py:25-64` `materialize_surface_entities(session, *, inventory_version)` — no org parameter; inserts `logical_versions (version_name, version_type, description)` with no `connected_org_id` (:59-64) and Surface entities with none; no in-repo caller (an operator script). Production: 6 Surface entities, 0 with an org, at seqs 228 and 241; the four org-less version rows 1, 2, 228, 241 (Step B) |
| absent grounding graded as fine | `substrate_decision.py:366-373`: `gv is None → grounding = None`; `:555-563` the `broken`/`drifted`/`stale` comprehensions count only rows WITH a grounding, and the `else` branch reports "All claim groundings intact and current"; `:425` `never_run` is a per-row flag that only feeds `has_runs` (ALL never run → blocker) and `coverage` (SOME never run → a warning) — a never-run claim beside a green one costs one warning |

## a. THE RUN STAMP — the executor's own value, carried, never re-read

**Columns** (alembic tenant `20260909_0010_run_org_stamp`, ADDITIVE):
`s4_execution_runs.connected_org_id UUID NULL`,
`s4_execution_runs.org_version_seq INTEGER NULL`, and one index
`(claim_test_id, environment_id, finished_at DESC)` for the latest-run
read. The 727 legacy rows stay NULL — no backfill, no inference (Fork 3).

**Where the value comes from.** ONE place, the SELECT bracket
(`_prepare_async_execute`), for EVERY recipe kind: `org_id =
_resolve_run_org(session, environment_id)` (the seam, unchanged) →
`resolve_current_sequence(session, connected_org_id=org_id)` (the Step B
resolver, `source = org_current`) → an `OrgStamp(connected_org_id,
org_version_seq)`. For a data recipe with a world, **that same number is
handed to `plan_data_recipe_world(…, at_seq=stamp.seq)`** so the world
and the stamp are one value by construction — the executor's own value,
not a later read. The sync path (`_execute_for_kind` with a session)
derives the stamp from the model it already builds (`s1.connected_org_id`,
`s1.current_version_seq()`), same transaction. The prep tuple grows by
the stamp; `_execute_for_kind`, `execute_data_recipe`,
`execute_metadata_recipe` and the errored-probe path (`run.py:749`) take
`org_stamp=` and set two new optional `RunEvidence` fields
(`connected_org_id`, `org_version_seq`, default `None`);
`result_store.persist` writes them. A resolver refusal (org unbound, org
never synced) yields NO stamp — the run is written with NULLs and reads
CANNOT_DETERMINE, honestly, rather than a number from nowhere. Both
environments are stamped by construction: the stamp is per run, whatever
its environment.

## b. THE RESOLVER, `source = run_stamp` — targeted through what the claim reads

`sync/readiness.py` gains `resolve_run_readiness(session, *,
claim_test_id, environment_id) -> ReadinessResolution` and a bulk form
`resolve_run_readiness_bulk(session, pairs)` for the lists (one query per
page, the D-331 discipline). The shape:

```python
@dataclass(frozen=True)
class ReadinessResolution:
    state: str            # NEVER_RUN | STALE | CURRENT | CANNOT_DETERMINE
    source: str = "run_stamp"
    axis: str = "org_sequence"       # never the claim-version axis
    run_id: UUID | None = None
    stamp_seq: int | None = None     # the run's org_version_seq
    current_seq: int | None = None   # the org's current (Step B resolver)
    connected_org_id: str | None = None
    changed_reads: tuple = ()        # (entity_type, entity_id, what) — STALE only
    reason: str | None = None        # unstamped | org_unbound | org_never_synced | no_coverage
```

**The rules, in order.**

1. The latest run for `(claim, environment)` by `finished_at` (any claim
   version — readiness is about the ORG; the claim-version axis is a
   separate fact, below). None → **NEVER_RUN**.
2. The run's `org_version_seq` is NULL → **CANNOT_DETERMINE**, reason
   `unstamped` (every legacy run; a run whose resolver refused).
3. The org's current sequence via `resolve_current_sequence` (Step B); a
   refusal → **CANNOT_DETERMINE** with its reason.
4. The claim has NO `test_claim_coverage` rows → **CANNOT_DETERMINE**,
   reason `no_coverage` — its reads are unrecorded, so "nothing it reads
   changed" is unknowable; never CURRENT by absence. (Zero runs hit this
   today: 727/727 are on covered claims.)
5. `covered_reads_changed(conn, claim, org, since=stamp_seq)`: over the
   claim's covered `entity_id`s in this org — any entity row with
   `valid_to_seq > since` (the read was superseded after the run), or any
   edge whose source/target is a covered id with `valid_to_seq > since`
   or `valid_from_seq > since` (a relationship on the read closed or
   appeared). Any → **STALE**, with the changed reads NAMED; none →
   **CURRENT**. An org change that touches none of the claim's reads
   leaves it CURRENT — never "org moved → all stale".

**Claim-version drift stays its own fact** (the TA's distinction): the
evidence row's `latest_run.version_unknown` / `superseded_newer_run` and
the `version_currency` check are untouched; the screens render it as its
own note ("run is of claim v2; approved is v3"), never as readiness.

**Ruling R-ground — RULED.** Step B grades `grounding.stale` as
`evaluated_at_version_seq < org current` — global on the org axis. An
org-BOUND inventory checkpoint (§d) at MAX+1 would therefore flip every
grounding of that org to stale on every inventory cut, the Step B
false-STALE reborn one level down. ONE targeted predicate governs both
stamps: `grounding.stale = covered_reads_changed(claim, org, since =
evaluated_at_version_seq)` — a grounding is stale only when a covered
read changed after its verdict; the `sequence` resolution stays on the
row for the record. The mint fix in §d lands only with this.

## c. THE ENGINE — an unrun or unstampable claim is an UNGRADED input

`_assemble_claim_evidence` attaches `readiness` (the bulk resolution) to
every row. `compute_substrate_decision` gains a `readiness` check,
evaluated with `org_sequence` before the graded checks:

- rows with readiness **NEVER_RUN or CANNOT_DETERMINE** are UNGRADED —
  named in the check line ("3 claim(s) ungraded: 2 never run in this
  environment, 1 run without a stamp"); per D9 they block GO and
  CONDITIONAL GO → `cannot_determine`, unless a GRADED blocker exists
  (a failed run, broken grounding), in which case **NO GO stands** and
  the roll-up line names the ungraded count beside it. The existing
  `has_runs` blocker (all never run → `no_go`) is superseded: all
  never-run is the fully ungraded case → `cannot_determine`.
- rows with readiness **STALE** are graded evidence that is old → a
  WARNING naming the claims and their changed reads (at best
  CONDITIONAL GO), consistent with grounding drift.
- **Absent grounding** (the HIGH item, assigned here) — **Ruling
  R-ungrounded, RULED**: a claim with a current run but NO grounding
  verdict is an UNGRADED input on the grounding axis. Per D9 it blocks GO
  and CONDITIONAL GO — the recommendation is `cannot_determine` unless a
  graded blocker makes it NO GO — and it never produces NO GO alone; the
  render is the named warning line with the count ("N claim(s) have no
  grounding verdict for this org"). That is the difference between
  "fine" (the HIGH item) and "unknown, and unknown blocks". The
  vacuous-green class closes on both edges: no run → ungraded; run but
  no grounding → ungraded.

  **The production count, measured read-only 2026-09-08: ZERO.** All 659
  approved current claims hold a grounding verdict at their approved
  version for BOTH orgs (659/659 each); among the 159 (env 59) / 32 (env
  78) approved claims with a run, 0 lack one; among the 88 approved
  claims in the ten releases' scope, 0 lack one for either org. What
  keeps it at zero: the scheduler's `s8_grounding_tick` (`scheduler.py:
  254-271` → `recompute_tenant_grounding`, cap 100 artifacts per org per
  tick) recomputes every CURRENT artifact against each live org — a
  newly approved claim lacks a verdict only until the next tick. Not
  material today, so "Run the scope" does NOT enqueue grounding
  evaluation in this step; the exit for the bounded window is the tick
  itself, and the refusal block names "awaiting grounding evaluation"
  for that case rather than "never run". If a future count is material
  (a tick backlog past its cap), the exit is a per-release grounding
  enqueue beside "Run the scope" — ledgered, not built.

**"Evaluate GO/NO-GO" refuses on non-current scope.** The web route
(`views.py:4655` `releases_evaluate_decision`) and the API route
(`release/routes.py:188`) resolve readiness for the release's scope
(its claims × the environments with evidence) BEFORE calling the
composer; any claim whose readiness is not CURRENT → the act is REFUSED:
no decision row is written, the response/flash names the items (key,
environment, state, the sentence), and the decision tab renders the
refusal block with the list and **"Run the scope"** beside Evaluate. "Run
the scope" is the existing `POST /releases/<id>/run` (`views.py:4577`,
`enqueue_claims_for_requirements` over the release's keys, env-gated,
SEC-4 production gate) surfaced beside Evaluate with its environment
picker — scoped by release, the surface plan §9 item and the Run
Planner's future entry (§f). The live card still renders the compute
(with the readiness lines) so the operator sees WHY before running. The
refusal is a readiness FACT; the policy object that will make it
configurable is Step 5.

## d. THE ORG-LESS CHECKPOINT MINT — removed at root

**Lands only with R-ground (§b):** under Step B's global org-axis
grounding check, an org-bound checkpoint would flip every grounding of
that org stale on every inventory cut; with the targeted predicate a
checkpoint that touches no covered read moves nothing.
`materialize_surface_entities(session, *, inventory_version,
connected_org_id)` — the org is a REQUIRED keyword (a `None` refuses with
`ValueError`); the checkpoint is written `logical_versions(…,
connected_org_id)` and the Surface entities carry the org. Which org: the
operator names the environment whose portal the inventory is cut for
(the conformance schedule's environment) and the D-286 seam resolves it —
the cut script gains `--environment-id`. No in-repo caller changes
(there is none). **No existing row is touched**: the four org-less
version rows (1, 2, 228, 241) and the six org-less Surface entities are
now-inert history (Step B already excludes them from every decision
read; §b's predicate looks only at covered ids in the run's org), ledgered
as such — no migration is needed for this item.

## e. SCREENS per the approved mock (fixture screenshots in the verification doc)

| surface | change |
|---|---|
| `/runs/substrate` rows (`runs/s4_list.html:181-189`) | a **Readiness** column beside Outcome: the pill (CURRENT green / STALE amber / NEVER RUN gray / CANNOT DETERMINE slate) with the sentence on hover and, for STALE, the changed reads; `data-readiness` on the row |
| run detail (`runs/s4_detail.html:69-73`) | the pill beside the outcome chip and the grey explanatory sentence under it; the claim-version note separate |
| requirement detail claim list (`requirements/detail.html:375-395`) | a readiness cell per claim: one pill per environment that holds any run for the requirement's claims, labelled by environment |
| release decision tab (`releases/detail.html:27-36`) | the **refusal block** above the card when the scope is non-current: the items (key · environment · pill · sentence) and **"Run the scope"** (the environment picker + POST to `/releases/<id>/run`) beside **Evaluate**; the card's `readiness` check line always |

**The sentences** (rendered grey; the CANNOT_DETERMINE / unstamped one
is the TA's Fork-3 wording, verbatim — it carries the action, and the
action is the point):

- CANNOT_DETERMINE / unstamped: "Freshness unknown — this run predates
  run-level environment stamping. Run again to establish current
  readiness."
- CANNOT_DETERMINE / no_coverage: "This test's reads are not recorded,
  so its currency cannot be determined."
- NEVER_RUN: "No run in this environment."
- STALE: "N thing(s) this test reads changed after this run (org seq
  S → C): …"
- CURRENT: "Current against org seq C."

## f. NON-GOALS (held exactly)

No Run Planner (Step 4 — "Run the scope" uses the existing release-run
path, scoped by release, and is ledgered as the planner's future entry).
No policy object (Step 5 — the refusal here is a readiness fact). No
layout change. No conformance change (the manifest's `org_env_snapshot`
is its stamp already; the a11y lane reads none of this).

## g. Migrations, classified at pre-flight (for the merge runbook)

| migration | content | class |
|---|---|---|
| tenant `20260909_0010_run_org_stamp` | two nullable columns + one index on `s4_execution_runs`; no existing row touched | ADDITIVE → dumpless (D-476), before deploy |
| checkpoint-mint change (§d) | a function signature; no migration; no existing row touched | code only |

No public migration. No ORM window: the old code never reads or writes
the two columns (nullable); the new code writes them on every new run and
reads them for readiness. Deploy-day: migration → merge → four services
→ the next run stamps itself; no data act, no backfill (Fork 3).

## h. Verification (VERIFICATION_STEP_2.md, on GO)

1. **The stamp equals the executor's value**: a fresh run through the
   real consumer carries `connected_org_id` = the env's org and
   `org_version_seq` = the sequence the world was planned at (asserted
   against the resolver's value in the same bracket); the metadata path
   and the 1-step create-rejected path stamp too; a resolver refusal
   leaves NULLs.
2. **Targeting**: after a run at seq S, close a COVERED entity row at
   S+1 → STALE for that claim only, naming the read; close an UNCOVERED
   entity of the same org → the claim stays CURRENT; a second claim that
   does not read the changed entity stays CURRENT.
3. **The vocabulary renders**: NEVER_RUN and CANNOT_DETERMINE carry the
   sentence on the list, the run detail and the claim list; STALE names
   its reads; the claim-version note renders separately.
4. **Evaluate refuses**: a release with one never-run claim in scope →
   both routes refuse, no decision row, the item named; "Run the scope"
   enqueues exactly the release's approved recipes in the chosen env;
   after the runs land, Evaluate proceeds.
5. **D9 holds**: NO GO over a graded blocker beside an ungraded claim;
   `cannot_determine` when only ungraded; STALE → at best conditional.
6. **The legacy 727**: every one reads CANNOT_DETERMINE / unstamped; zero
   rows relabelled (count of NULL stamps before = after; no UPDATE
   statement exists for the columns).
7. **The checkpoint mint**: `materialize_surface_entities` with an org
   writes an org-bound `logical_versions` row and org-bound Surfaces; the
   org-less call refuses; the four rows and six entities untouched.
8. The D-468 set (unit / DB-real / test_representation / pages /
   browser-gated); fixture screenshots over the real app on scratch (the
   runs list with all four pills, a run detail with the sentence, the
   claim list column, the refusal block with "Run the scope").

## Residual, stated plainly

- Coverage is written per claim version at `write_claim`; a claim
  approved before coverage existed (the 444 uncovered approved claims)
  reads CANNOT_DETERMINE / no_coverage the moment it runs. Regeneration
  records coverage; that is the D-353 deprecate-then-regen mechanism,
  not this step.
- The stamp names the org sequence at the START of the select bracket;
  a sync that lands between the bracket and the Salesforce call is a
  race the stamp records honestly as "planned at S" (the world WAS
  planned at S).
- `run_stamp` readiness reads the LATEST run per (claim, env) regardless
  of claim version; a superseded-version run that is current on the org
  axis reads CURRENT here and carries its version note beside it.
