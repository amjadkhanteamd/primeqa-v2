# LLD Step B — the decision-engine staleness pin, resolved once, org-required

Status: BUILT 2026-09-07 on the AK GO (design commit 284864c; build commit follows); pushed for review; merge gated. Transcript: VERIFICATION_STEP_B.md.
Rulings: **F1a** (`cannot_determine` is the fourth recommendation value;
public migration 071 widens the ledger CHECK — additive, before deploy,
dumpless); **D9** (NO GO stands over an ungraded org; an ungraded org
blocks GO and CONDITIONAL GO); **Card** per the approved mock — slate,
the explanatory sentence, the resolver line, an evidence / orgs / reason
/ recorded-as footer; fixture screenshot in the verification doc.
The D-entry records, as a correction to the ratified record, that the
surface plan's fork-3 mechanism was wrong (false STALE, not false GO;
the real wrong GO is the unpinned cross-org grounding read) and that
releases carry no environment binding — the evidence IS the binding.
Branch: `step-b-staleness-pin` (from main @ddef3f9, D-481).
Derives from: D-479 (the unified sequence; Step B = "one staleness
resolver … the engine reads run-level readiness"), the surface plan
§2A/§7 ("one resolver, not two — replace the pin"), D-256 (org-scoped
`SemanticOrgModel`, "a later slice makes `connected_org_id` mandatory"),
D-286 (the ONE fail-loud env→org resolver), D-292 residual / the 3e
multi-org closure (the 2+ env branch already reads org-bound), D-476
(dumpless classification), D-172 (the unpinned-grounding idiom).

**Thesis.** The decision engine computes a "current S1 sequence" itself
(`_current_s1_seq`, substrate_decision.py:228-241) and, on the 0–1
environment branch, computes it with no org (:766-786 pass an org only
with 2+ envs) — the tenant-wide `MAX(version_seq)` (query.py:247-250
adds the org WHERE only when bound). Step B removes that computation
from the engine: ONE org-required resolver in S1's readiness home is the
only place a decision-facing current sequence exists; a caller without
an org gets a recorded refusal (`CANNOT_DETERMINE: org_unbound`), never
a tenant-wide number; the engine renders the refusal and never grades
GO over it. The resolver's shape reserves the `run_stamp` source Step 2
adds.

---

## 0. Pre-flight — the mechanism, cited, and one premise corrected

**The sites (verified this session).**

| site | what it does today |
|---|---|
| `substrate_decision.py:228-241` `_current_s1_seq(session, connected_org_id=None)` | builds `SemanticOrgModel(conn, connected_org_id)`; `None` → org-less model; `VersionNotFoundError` → `None` ("staleness unknowable") |
| `query.py:237-255` `current_version_seq` | `SELECT MAX(version_seq) FROM logical_versions` + `WHERE connected_org_id = :org` ONLY when bound (:247-250) |
| `substrate_decision.py:322` | the only reader: `current_seq = _current_s1_seq(session, connected_org_id)` inside `_assemble_claim_evidence`; `:365-368` stamps `grounding.stale = evaluated_at < current_seq`, or `None` when the pin is `None` |
| `substrate_decision.py:766-786` `get_release_substrate_decision` | `len(envs) <= 1` → `_assemble_claim_evidence(session, keys, tenant_id=…)` with NO env and NO org (:768); 2+ envs → per env, `org = get_connected_org_for_environment(conn, env)` (:783) — `None` when the env has no `connected_orgs` row, silently org-less |
| `evolution/result_store.py:152-200` `read_grounding_validity_bulk(connected_org_id=None)` | the SECOND org-less read on the same branch: pinned → WORST verdict across orgs; unpinned (no approved claim version, D-172) → latest `(version_seq, connected_org_id)` across orgs — tie on version broken by the org uuid |
| `substrate_dashboard.py:207, :337` | two more `_assemble_claim_evidence` callers: the env dashboard (org from env; `None` if unprovisioned) and the landing stats (`environment_id=None` from views.py:198-208 → org-less by default) |
| `semantic/surface_entities.py:56-64` | mints an ORG-LESS `manual_checkpoint` logical version for every inventory cut that declares a new Surface (seq 228 = inv1, 241 = inv2 on tenant 1) |

**Production (read-only, 2026-09-07).** tenant 1 `logical_versions`: org
902850e3 (env-59) MAX 249; org 6af2fa4b (env-78) MAX 248; FOUR org-less
rows (1 genesis "Phase 0 smoke", 2 "derivation smoke", 228 and 241
"3A-5 declared Surface entities"); FOUR orphan org-bound rows (seq 37,
60, 61, 62) whose `connected_orgs` rows no longer exist. `releases` has
NO environment column and no ORM binding (schema + `release/models.py`
verified); the only environment a release has is the one its claims'
runs carry (`_environments_with_evidence`). Every one of the 10
releases with requirements takes the 2+ env branch today (all
`[59, 78]`); 13 have no requirements. So the org-less branch is latent
on production — no live release exercises it.

**Premise corrected — the direction of the defect.** The brief calls the
pin a latent wrong GO. Code arithmetic: `MAX` over every org is ≥ any
one org's own MAX, so the tenant-wide pin can only make `evaluated_at <
current` TRUE more often, never less. The pin over-reports staleness;
it cannot under-report it. Observed, not inferred:

- `s8_grounding_validity` on tenant 1: env-78's 837 rows are ALL current
  for their own org (evaluated at 248 = org MAX) and ALL read stale under
  the tenant-wide 249. A release with evidence in env-78 only would grade
  837/837 stale → `grounding_integrity` WARN → `conditional_go`, never
  `go`. That is a false STALE, i.e. a wrong CONDITIONAL_GO.
- `test_org_bound_staleness_uses_the_orgs_own_seq`
  (test_substrate_decision_multiorg.py:121-162) asserts exactly this
  (`blind["grounding"]["stale"] is True`, `bound … is False`) and passes
  on local PG (run this session: 2 passed).
- It is one ACT away, not one sync away: the next inventory cut
  (`surface_entities.py:59`) mints an org-less checkpoint at `MAX+1`,
  after which BOTH orgs' rows read stale under the tenant-wide pin.

Where a wrong-GO-class silence DOES live on the same branch (and the
same fix closes it):

1. `_current_s1_seq → None` (org bound but never synced, or an empty
   store) → `grounding.stale = None` → NO warning: "unknowable" is
   graded as current (substrate_decision.py:365-368, 546-548).
2. The unpinned grounding read (D-172 idiom, no approved claim version)
   picks the latest `(version_seq, connected_org_id)` across orgs — on a
   version tie the higher org uuid wins. On tenant 1, 902850e3 (env-59)
   > 6af2fa4b (env-78): a release with evidence in env-78 only and a
   claim without an approved version reads env-59's verdict. env-59
   intact + env-78 broken → GO for env-78. That is the wrong GO this
   branch can produce; it comes from the org-less verdict read, not from
   the pin.

Not in Step B (ledgered, §f): grounding ABSENT for the org (`gv is
None`) is graded as fine on every branch — no `broken`, no `stale`, no
check line — an honesty gap independent of the sequence.

## a. ONE resolver — `primeqa.sync.readiness.resolve_current_sequence`

**Home and name.** `primeqa/sync/readiness.py` — S1's readiness module
(§24). It is the module that already answers "what state is THIS org's
model in" (`compute_org_status`, `apply_org_status`,
`resolve_active_sync_run_id`, … every function takes `connected_org_id`
and none has an org-less mode), and `logical_versions` is written by the
sync engine it serves. It does NOT live in the decision engine because
the TA condition is that the engine READS readiness and never computes a
substrate fact — a second computation is the second staleness answer
the surface plan forbids; and it does not live in `semantic/query.py`
because that file is the read primitive (`current_version_seq`, S1
primitive 5, which S3/S4/S6/S8 keep for their execution-time pins),
whereas the resolver is the decision-facing readiness read that carries
the refusal vocabulary. Named `resolve_current_sequence` (not `resolve`)
because `readiness.resolve_active_sync_run_id` already exists beside it.

**Signature.** `resolve_current_sequence(session, *, connected_org_id)
-> SequenceResolution`. `connected_org_id` is keyword-only and required
by the signature; there is NO org-less mode. A caller that resolved
env→org and got `None` passes `None` and receives the refusal — the
refusal is the resolver's act, not a branch every caller re-implements.
The resolver never raises on a refusal path (only a DB error propagates;
the engine's best-effort wrapper then reports `available: False` as it
does for any read error).

```python
@dataclass(frozen=True)
class SequenceResolution:
    state: str            # 'CURRENT' | 'CANNOT_DETERMINE'
    current_seq: int | None
    as_of: datetime | None        # created_at of the resolved version row
    source: str           # 'org_current' today; 'run_stamp' RESERVED (Step 2)
    axis: str = "org_sequence"    # never the claim-version axis (see §c)
    connected_org_id: str | None = None
    reason: str | None = None     # 'org_unbound' | 'org_never_synced'
```

**Semantics.** `connected_org_id is None` → `CANNOT_DETERMINE /
org_unbound`. Bound → ONE read: `SELECT version_seq, created_at FROM
logical_versions WHERE connected_org_id = CAST(:org AS uuid) ORDER BY
version_seq DESC LIMIT 1` (the same number `current_version_seq()`
returns for a bound model; here with its timestamp). No row →
`CANNOT_DETERMINE / org_never_synced`. Row → `CURRENT`, `source =
'org_current'`.

**Org-less rows are excluded by construction**: the WHERE binds an org
uuid; a NULL `connected_org_id` can never match. What those rows ARE on
tenant 1 (four): the Phase-0 genesis (seq 1, 2026-04-27), a derivation
smoke checkpoint (seq 2), and the two `surface-materialization-inv{1,2}`
checkpoints (228, 241) minted by `surface_entities.py:59-64` because
declared portal Surfaces are not org metadata and the writer gives them
no org. They are legitimate S1 rows for their own purpose and illegitimate
as ANY org's currency; the resolver makes them inert for decisions.
Ledgered as data hygiene in §f together with the four orphan rows of
departed orgs (37, 60, 61, 62) — neither is touched by Step B.

## b. The engine consumes the resolver on EVERY branch

`_current_s1_seq` is deleted. `_assemble_claim_evidence` calls
`resolve_current_sequence(session, connected_org_id=connected_org_id)`
ONCE per assembly and stamps every evidence row with the resolution
(`row["sequence"] = {state, current_seq, as_of, source, axis, reason}`);
`grounding.stale` is computed only when `state == 'CURRENT'`, else
`None` — and `None` is no longer silent because the row carries the
reason. The grounding read on the same branch becomes org-exact for the
same org (the second org-less read in §0 closes with the first).

- **2+ env branch** (`:775-790`): unchanged shape; `org` from
  `get_connected_org_for_environment(conn, env)` as today, now passed
  into the resolver; an env whose `connected_orgs` row is missing was
  silently tenant-wide and becomes a per-env `CANNOT_DETERMINE`
  (`org_unbound`). For a provisioned env the number is the one the
  branch already used → byte-identical evidence and decision (test 4).
- **1 env branch**: the environment is the evidence's environment
  (`envs[0]`) — there is no release/environment binding to read (§0), so
  the binding IS the evidence; org via the same resolver seam; the
  assembly is scoped `environment_id=env, connected_org_id=org` (the run
  window is unchanged in effect — only that env holds evidence — and the
  grounding read becomes org-exact). Output shape unchanged: the
  one-element `environments` echo stays (no roll-up of one — a roll-up
  replaces the per-check reasoning with an `environment` line and would
  change the single-org decision tab).
- **0 env branch**: nothing to bind → the resolver's `org_unbound`
  refusal → `CANNOT_DETERMINE`. Today this branch is `no_go` via the
  `has_runs` blocker; it becomes an ungraded release (the honest
  statement: nothing ran, so the engine cannot grade staleness or
  anything else), never GO.

**The pure compute.** `compute_substrate_decision` gains an
`org_sequence` check evaluated FIRST: any row with `sequence.state ==
'CANNOT_DETERMINE'` → `{"check": "org_sequence", "status": "fail",
"detail": <sentence>}`, `criteria_met.org_sequence = False`, and the
recommendation is `cannot_determine` (confidence 0.0; the other checks
still run and their metrics still fill — facts stay facts; only the
grade refuses). Sentences: `org_unbound` → "Cannot determine staleness:
no connected org is bound to this release's evidence, so the engine
will not grade grounding against a tenant-wide sequence."
`org_never_synced` → "Cannot determine staleness: the bound org has
never synced, so no current sequence exists." Roll-up severity order
becomes `no_go < cannot_determine < conditional_go < go`: a definite
NO-GO stands over an ungraded org (the evidence says stop), and an
ungraded org blocks GO / CONDITIONAL GO. **Ruling D9 for AK.**

**Vocabulary and the ledger — fork F1.** `recommendation` today is
`go | conditional_go | no_go`; `release_decisions` CHECKs exactly those
(migration 009:98; prod constraint read back). Options:

- **F1a (lean): `cannot_determine` first-class.** Public migration
  `071_release_decisions_cannot_determine.sql` swaps the
  `recommendation` CHECK for the four-value set (`final_decision` — the
  HUMAN's decision — stays three-valued: a human does not "decide"
  CANNOT_DETERMINE). Composer records it verbatim; template renders the
  grey card; `/status` passes it through (a fail-closed CI consumer that
  gates on `== 'go'` is unaffected; one that gates on `!= 'no_go'` was
  already wrong and is named in the verification). Classification:
  constraint widening, no data write, no ORM change (`ReleaseDecision`
  carries no CHECK) → dumpless per D-476, applied BEFORE deploy (old
  code never writes the value; new code needs the column to accept it);
  no ORM window either way.
- F1b: keep three values; the composer records `no_go` with the
  envelope carrying `substrate.recommendation = 'cannot_determine'`.
  Rejected on the absence rule: the release header and the CI field
  would say NO GO where the engine said "I cannot grade this" — a
  verdict rendered for an absence.

**Rendering (decision tab; NEW — standing UI rule applies).** The card
macro (`releases/detail.html:91-149`) has `go` green / `no_go` red /
else amber. `cannot_determine` gets a fourth branch: slate border and
pill, label "CANNOT DETERMINE", no confidence percentage, the
`org_sequence` sentence as the first reasoning line, the metrics line
kept (facts), no "Blocked by" list. The roll-up strip (`:150-175`) and
the release header (`:41-50`) gain the same slate branch;
`substrate_dashboard._STATE_BY_RECOMMENDATION` gains `cannot_determine
→ "CANNOT DETERMINE"` (today an unknown value falls to "UNKNOWN").
Text mock of the single card:

```
┌ ▌ Release recommendation                     [ CANNOT DETERMINE ]
│   The new engine's verdict over its own evidence — risk 0/100 (low)
│   · 0 of 2 claims have current runs · pass rate 0.0%.
│   ✗ org sequence — Cannot determine staleness: no connected org is
│     bound to this release's evidence, so the engine will not grade
│     grounding against a tenant-wide sequence.
│   ✗ has runs — No substrate runs exist for any of this release's claims
└
```

This rendering had no AK-seen mock: the fixture screenshot rides with
the build push (`docs/ui-testing/step-b-fixtures/`), and the merge stays
gated on AK seeing it — the A.1 precedent.

## c. The interface Step 2 feeds

`SequenceResolution.source` is `'org_current'` today (the org's latest
synced version). Step 2 adds `'run_stamp'`: the org sequence persisted on
each run at execution (the executor computes it and discards it; no such
column exists on `s4_execution_runs` today — verified: only
`recipe_version_seq` and `claim_version_seq`). Under Step 2 the same
resolver returns the org's current sequence and the run's readiness is
`run_stamp` vs `org_current`, both on the `org_sequence` axis.

The two drift axes stay explicit and separate in the shape: `axis =
"org_sequence"` is the only thing this resolver ever speaks about (the
org changed under the claim → `grounding.stale`); claim-version drift
(the claim or recipe changed under the run) stays on the evidence row —
`approved_seq`, `latest_run.version_unknown`, `superseded_newer_run` —
and is graded by the existing `version_currency` check, which Step B
does not touch. A resolution never carries a claim version; an evidence
row's `sequence` never carries a claim version.

## d. Blast radius — every reader, migrated or refused

| reader | today | Step B |
|---|---|---|
| `_current_s1_seq` (:228) — 1 reader, `:322` | org-less when `None` | DELETED; the assembler calls the resolver |
| `get_release_substrate_decision` 0–1 env (:766-775) | no env, no org | 1 env → env from evidence, org via seam, resolver; 0 env → refusal |
| `get_release_substrate_decision` 2+ env (:777-790) | org from env, `None` silently tenant-wide | resolver; `None` → per-env CANNOT_DETERMINE |
| `read_grounding_validity_bulk(None)` on the 0–1 branch | worst-of / uuid tie-break across orgs | org-exact (same org as the resolver) |
| `substrate_dashboard.get_substrate_dashboard_data` (:207) | env-scoped; org `None` if unprovisioned → tenant-wide pin | resolver; unprovisioned env → state "CANNOT DETERMINE" with the sentence |
| `substrate_dashboard.get_landing_substrate_stats` (:337) | `environment_id=None` by default → org-less | the resolver refuses; the page reads ONLY `pass_rate` (a fact, still computed) — no number changes on the landing page; it never rendered staleness |
| three live-parity tests calling the assembler org-less (`test_substrate_decision_compute.py:421`, `test_strategy.py:105`, `test_run_router.py:217`) | tenant-wide | pass env-59's org through the seam (they assert `verified`, unaffected either way — migrated for honesty) |
| `test_substrate_decision_evidence.py` (`_seed_s1_version` org-less; 8 org-less assemblies) | rely on the tenant-wide pin | versions seeded on `grounding_org`; assemblies pass the org |
| `test_substrate_decision_multiorg.py:76` "env=None is the identity" | pins the org-less contract | REWRITTEN: env=None/org=None is the refusal, never a number; `:87` rollup test plants two orgs |

The other `SemanticOrgModel` constructions (15 sites) are NOT decision
reads and compute no decision-facing sequence: S4 run (`run.py:352,
684`, `_resolve_run_org` → D-286 fail-loud), S3 intake / run (fail-loud
resolver), `repair_gate._s1_facts` (empty facts when no org),
`conversation_bridge` (`_no_org()` refusal), `s1_sync_console` (unavailable
when no org), `evolution/recompute` (iterates provisioned orgs),
`evolution/interpretation/data_executor/retrieval` (consume an org-bound
model built upstream). Two are org-blind BY DESIGN and stay so, named:
`metadata_bridge/s1_reader.py:125` (advisory whole-tenant metadata
picker, documented org-blind, no fail-loud) and
`generation/eval/runner.py:178, 306` (the offline eval harness over
fixture data). Neither feeds a decision; Step B lists them and leaves
them.

## e. Verification (VERIFICATION_STEP_B.md, on GO)

1. **The reproduction, in both directions the branch actually has.**
   (1a, the pin) two orgs A (env-59-shaped) and B (env-78-shaped); B ahead
   by one; a release whose claims ran in A only; A's grounding evaluated
   at A's current. OLD: tenant-wide pin = B's → `stale True` →
   `conditional_go` (the false STALE; production shows 837/837 for
   env-78 today). NEW: A's own sequence → `stale False` → `go`. (1b, the
   silent None) A bound but never synced: OLD `stale None` → `go`; NEW →
   `cannot_determine`. (1c, the real wrong GO) a claim with no approved
   version, grounding rows for both orgs at the same version, B's
   BROKEN, A's intact, org ids planted so A's uuid sorts higher; a
   release with evidence in B only. OLD: the unpinned org-less read
   returns A's row → `go`. NEW: B's row → `no_go` with the grounding
   blocker. Each case asserts the OLD outcome by executing the pre-B
   read shape (`SemanticOrgModel(conn).current_version_seq()` /
   `read_grounding_validity_bulk(…, None)`) beside the NEW path in the
   same test, so the direction is observed, not asserted.
2. **Org-less rows never counted**: plant an org-less
   `manual_checkpoint` at `MAX+5`; the resolver for A still returns A's
   sequence; the tenant-wide MAX would have been `MAX+5`.
3. **Org unbound → CANNOT_DETERMINE, never GO**: 0-env release; 1-env
   release whose env has no `connected_orgs` row; the composer records
   `cannot_determine` (after 071 on scratch); `/status` carries it; the
   pure compute refuses with the sentence; a roll-up with one ungraded
   org never says GO (D9 order).
4. **2+ env branch byte-identical**: the multi-org world with two
   provisioned orgs — the per-env decision dicts from the wrapper equal
   the dicts computed by the pre-B call shape
   (`_assemble_claim_evidence(environment_id=env, connected_org_id=org)`
   whose number is `current_version_seq()` on the bound model) — dict
   equality, and the existing rollup test stays green.
5. Suites: unit (pure compute + composer mapping + resolver table),
   DB-real (the D-468 corpus + these), pages, browser-gated; the decision
   tab fixture screenshot over scratch; `scripts/authz_inventory.py`
   unchanged (no new route).

## f. Residual, stated plainly (→ PLIMSOL_FIX_PLAN.md at build)

- **Data hygiene**: four org-less `logical_versions` rows (1, 2, 228,
  241) and four orphan org-bound rows (37, 60, 61, 62; departed orgs)
  on tenant 1. Inert for decisions after Step B; a cleanup ruling is
  AK's (delete, or re-key the surface checkpoints to a non-org
  namespace). `surface_entities.py` keeps minting org-less checkpoints
  by design — if that is wrong, it is an S1 ruling, not Step B.
- **Grounding absent graded as fine** — HIGH: `gv is None` yields no
  check line on any branch; the vacuous-green class at the decision
  layer. Not a sequence question; ledgered and assigned to Step 2/5
  (NEVER_RUN must block GO).
- **Step 2 owns the stamp**: no run carries an org sequence today; the
  `run_stamp` source is reserved in the shape and nothing reads it.
- The 0-env branch changes vocabulary (`no_go` → `cannot_determine`) for
  a release with requirements but no runs; no production release is on
  that branch today.
