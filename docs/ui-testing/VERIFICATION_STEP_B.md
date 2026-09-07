# VERIFICATION — Step B, the staleness pin resolved once, org-required

Executed 2026-09-07 on the local-PG substrate test database (the
`test_representation` rollback session, tenant_1 at the substrate head)
for the DB-real reproductions, on scratch (`plimsol_3a3`) for the
migration read-back and the fixture screenshots over the REAL Flask app,
and read-only against production for the facts cited. **No production
row was written and no Railway act was performed.** Branch
`step-b-staleness-pin` (from main @ddef3f9, D-481); design 284864c.
Merge gated — ONE public migration (071, a CHECK widening: no data
write, no ORM change) → dumpless per D-476, applied BEFORE deploy; no
tenant migration; no ORM window either way.

Re-runnable: `tests/unit/test_sequence_resolver.py`,
`tests/unit/test_substrate_decision_compute.py` (the Step B block),
`tests/unit/test_decision_composer.py`,
`tests/integration/test_representation/test_step_b_staleness_pin.py`
(+ the migrated `test_substrate_decision_evidence.py` /
`test_substrate_decision_multiorg.py`; local PG, no env needed).

---

## 0. The premise, corrected on the record (LLD §0)

The brief called the tenant-wide pin a latent wrong GO. Observed:

- **Production, read-only**: `s8_grounding_validity` on tenant 1 —
  env-78's 837 rows are ALL current for their own org (evaluated at 248 =
  org MAX) and ALL read stale under the tenant-wide 249. A release
  evidenced in env-78 only could never grade GO: a **false STALE**.
- **The real wrong GO** on the same branch is the unpinned grounding
  read (`read_grounding_validity_bulk(…, None)`: latest
  `(version_seq, connected_org_id)` across orgs, ties broken by the org
  uuid — on tenant 1, 902850e3 (env-59) sorts above 6af2fa4b (env-78)).
  Reproduced DB-real in §1c.
- **Releases carry no environment binding** (`releases` columns and
  `release/models.py` read: none) — the evidence IS the binding; zero
  evidence refuses.
- Every one of the 10 production releases with requirements is on the
  2+ env branch today (`[59, 78]`); 13 have no requirements. The org-less
  branch is latent on production.

## a. The resolver — `primeqa.sync.readiness.resolve_current_sequence`

Unit (`test_sequence_resolver.py`, 4): `None` org → `CANNOT_DETERMINE /
org_unbound` **without touching the session**; bound + no row →
`org_never_synced`; bound + row → `CURRENT` with the seq, its
`created_at`, `source = org_current`, `axis = org_sequence`; the SQL
binds `connected_org_id = CAST(:org AS uuid)` (org-less rows can never
match); `run_stamp` is a reserved constant nothing produces.

DB-real §2: org A synced at `seq_a`; five org-less `manual_checkpoint`
rows planted above it; the resolver returns `seq_a`; the old pin
(`SemanticOrgModel(conn).current_version_seq()`) returns the top
org-less checkpoint.

## b. The engine — every branch, the resolver, never a number of its own

`_current_s1_seq` is deleted (grep: one comment, zero callers).
`_assemble_claim_evidence` calls the resolver once and stamps
`row["sequence"]`; `grounding.stale` is computed only under `CURRENT`.
`compute_substrate_decision` evaluates `org_sequence` FIRST; a
CANNOT_DETERMINE (or a row set with no resolution — fail closed) →
`cannot_determine`, confidence 0.0, the sentence as the first reasoning
line, every other check still run and every metric still filled. A
CURRENT resolution adds NO reasoning line — every graded card renders
exactly as before (the standing UI rule). Ruling D9 in
`_ENV_SEVERITY`: `no_go < cannot_determine < conditional_go < go`.

`get_release_substrate_decision` → `_decide_for_session` (testable on
the rollback session): 0 envs → no org → refusal; 1 env → the evidence's
env, org via `get_connected_org_for_environment`, env- and org-scoped
assembly; 2+ envs → per env as before, an unprovisioned env now a
recorded per-env refusal instead of a silent tenant-wide read.

### The reproductions (`test_step_b_staleness_pin.py`, 6 DB-real; the OLD outcome executed beside the NEW in the same test)

| # | world | OLD (observed) | NEW (observed) |
|---|---|---|---|
| 1a false STALE | org B synced after org A; release evidenced in A only; A's grounding evaluated at A's current | tenant MAX = B's seq; A's row `evaluated < pin` → stale → conditional_go | resolver = A's seq; `stale False`; **go**; `environments[0].connected_org_id = A` |
| 1b silent None | A bound, never synced | `current_version_seq()` raises → the engine swallowed to `None` → `stale None` → the same rows with a CURRENT stamp grade **go** | **cannot_determine / org_never_synced**, "has never synced" sentence |
| 1c the real wrong GO | claim with no approved version; grounding for both orgs at the same version — A intact, B BROKEN; ids planted so A's uuid sorts higher; release evidenced in B only | `read_grounding_validity_bulk(…, None)` returns **A's intact row** → the rows grade **go** | B's own row → **no_go**, `grounding_integrity` blocker, `outcome = grounding_broken` |
| 2 org-less rows | five org-less checkpoints above A's seq | old pin = the top checkpoint | resolver = A's seq, `as_of` set |
| 3 org unbound | (i) zero evidence; (ii) one env with no `connected_orgs` row; (iii) roll-up of a green provisioned org + an unprovisioned env | (i) `no_go` via has_runs; (ii) tenant-wide; (iii) tenant-wide for the unprovisioned env | (i) `cannot_determine`, `environments = []`; (ii) `cannot_determine`, `connected_org_id None`, `pass_rate 100.0` still a fact; (iii) **cannot_determine** (D9) with `{A: go, 9002: cannot_determine}`; none of the three is go, conditional_go or no_go; confidence 0.0 |
| 4 2+ env byte-identical | two provisioned orgs, each claim's grounding evaluated at its own org's seq; A green, B red | — | the wrapper's dict == the per-env compute rolled up (dict equality); each org's number == `SemanticOrgModel(conn, org).current_version_seq()`; `grounding.stale == 0`; `no_go` (B's failure) |

Migrated suites: `test_substrate_decision_evidence.py` (versions seeded
on the org; nine org-bound assemblies — 11 green),
`test_substrate_decision_multiorg.py` (the pre-B "env=None is the
identity" contract REPLACED by the refusal; the roll-up world provisions
two orgs; the staleness test shows the old arithmetic beside the
refusal — 5 green); the three live-parity tests
(`test_substrate_decision_compute.py`, `test_strategy.py`,
`test_run_router.py`) now resolve the claims' own evidence env through
the seam — they need production-shaped data (SQ-205 / SQ-212 have no
claims on scratch, before or after Step B) and were run read-only
against production: 3 passed.

## c. The vocabulary — F1a, migration 071

Scratch: the release-side public tables built from the 003 / 009 / 016
/ 019 / 055 table portions (scratch had none); `071` applied → the
`recommendation` CHECK reads
`('go','conditional_go','no_go','cannot_determine')`;
`final_decision` CHECK unchanged (three values); second apply clean
(idempotent). Composer unit: `cannot_determine` recorded verbatim,
confidence 0.0, never mapped. `substrate_dashboard._STATE_BY_RECOMMENDATION`
gains `CANNOT DETERMINE`. `notify_release_decision` already treats
anything but a clean `go` as a heads-up (no change).

## d. The card — the approved mock (`step-b-fixtures/`)

Over the REAL app on scratch (`flask-scratch` on :5055, superadmin JWT
with the dev secret; the launch config reverted, never committed): one
release, one requirement `STEPB-1`, one APPROVED claim that never ran.

| file | shows |
|---|---|
| `decision_card_cannot_determine.png` | slate card, pill "CANNOT DETERMINE" (no percentage); "✗ org sequence — Cannot determine staleness: no connected org is bound to this release's evidence, so the engine will not grade grounding against a tenant-wide sequence." first; the resolver line "CANNOT_DETERMINE · source org_current · axis org_sequence"; the footer "evidence: 1 claim(s), 0 with runs · orgs: none bound · reason: org_unbound · recorded as: cannot_determine" |
| `decision_card_cannot_determine_page.png` | the tab before any evaluation (no banner) |
| `decision_card_after_evaluate.png`, `_page.png` | after `POST /api/releases/1/evaluate-decision` (200, `recommendation cannot_determine`, `confidence 0.0`, `recommendation_source substrate`): the banner "AI Recommendation: CANNOT DETERMINE", the ledger snapshot with the `sequence` block, the card unchanged |

Ledger read-back on scratch: one `release_decisions` row,
`recommendation = cannot_determine`, `confidence = 0`,
`reasoning->substrate->sequence = {state CANNOT_DETERMINE, reason
org_unbound, source org_current, axis org_sequence, …}`,
`environments = []`.

**Visible in the same fixture and NOT closed here**: "✓ grounding
integrity — All claim groundings intact and current" over a claim with
no grounding row, and the evidence panel's "No claims at risk — all
grounded claims are intact" beside "1 not computed" — the HIGH FIX PLAN
entry (absent grounding graded as fine), assigned to Step 2/5.

## e. Blast radius, executed (LLD §d)

| reader | done |
|---|---|
| `_current_s1_seq` | deleted; one comment remains |
| `get_release_substrate_decision` 0 / 1 / 2+ | `_decide_for_session`; all three read the resolver |
| the grounding read on the 0–1 branch | org-exact (same org as the resolver) |
| `substrate_dashboard` (env dashboard, landing stats) | unchanged call sites; the resolver refuses inside the assembler; the landing page reads only `pass_rate` (a fact) |
| three live-parity tests | migrated (the claims' own evidence env through the seam) |
| `test_substrate_decision_evidence.py`, `_multiorg.py` | migrated (org-bound versions; the identity contract replaced) |
| `tests/unit/test_substrate_decision_compute.py`, `test_quarantine_harmonization.py` | hand-built rows carry a CURRENT resolution; Step B cases added |

Org-blind by design and left, named: `metadata_bridge/s1_reader.py:125`
(advisory picker), `generation/eval/runner.py:178, 306` (offline eval
harness). Neither feeds a decision.

## f. Suites (D-468) at the implementation commit

- **Unit: 5,014 passed** (no DB; the three live-parity tests skip by
  design and passed read-only against production: 3).
- **test_representation (local PG): 408 passed, 3 skipped** (the
  decision suites: 22 green — evidence 11, multi-org 5, Step B 6).
- **DB-real corpus on scratch: 91 passed, 7 skipped, 1 red** across the
  sixteen DSN-gated files (the pages file skips here) — the red is the
  report-slice runs-list window artefact ledgered at D-480 (the B-1 run
  falls outside the 50-row window on scratch), not this slice.
- **Pages: 5 passed. Browser-gated: 63 passed, 11 skipped** (SPIKE_BROWSER=1).

## g. Merge classification (for the runbook)

| migration | content | class |
|---|---|---|
| public `071_release_decisions_cannot_determine.sql` | swap the `recommendation` CHECK for the four-value set; comment | ADDITIVE (constraint widening; no data write; no ORM change) → dumpless (D-476), BEFORE deploy |

No tenant migration. No ORM window: the old code never writes the value;
the new code needs the column to accept it. Deploy-day: 071 → merge →
four services → `Evaluate GO/NO-GO` is a human act, so nothing on
production changes until someone evaluates a release; the ten live
releases stay on the 2+ env branch whose numbers are unchanged.

## Residual, stated plainly

- The 0-env branch changes vocabulary (`no_go` → `cannot_determine`) for
  a release with requirements but no runs; no production release is on
  it today.
- `run_stamp` is reserved in the shape; nothing produces or reads it
  until Step 2.
- The two FIX PLAN entries (absent grounding HIGH; eight logical-version
  rows, hygiene) stand open.

## h. Production transcript (2026-09-07, GO #1 + GO #2)

| act | record |
|---|---|
| pre-flight | trees clean; main unmoved at `ddef3f9`; branch 2 commits over it; deployed `ddef3f9` on all four services with `_current_s1_seq` at :228 / reader :322; ledger CHECK three values on both columns; 1 decision row (`go`); baseline re-measured: 59 → 249, 78 → 248, org-less 4 (max 241); env-78 grounding 837/837 below the tenant MAX, 0 below its own; 23 releases / 10 with requirements, all `[59, 78]` |
| 071 classified | the only migration / alembic / ORM-model file in the diff; DROP CONSTRAINT IF EXISTS + ADD CONSTRAINT + COMMENT; zero data-write statements → ADDITIVE, dumpless (D-476), BEFORE deploy |
| the window | safe in ONE direction only: old code writes only the three values the wider CHECK still accepts; new code writing `cannot_determine` against the old CHECK would 500 the evaluate route. 071 goes first, always |
| finding 1 (ruled) | the ORM declared the three-value CHECK (`release/models.py:120`, table-creation only, zero runtime effect) → widened on the branch, `9e08ca8`; unit gate 5,014 |
| finding 2 (corrected) | the four "orphan" version rows (37, 60, 61, 62) belong to ENV-LESS TEST-FIXTURE orgs whose `connected_orgs` rows exist (`_test_sync_object_phase_*`, `_scen3_multi_org_*`); the first probe joined to `environments` and hid the parents — wording corrected in the LLD and the FIX PLAN, marked as a correction |
| 071 on production | applied; read-back: `recommendation` CHECK four values, `final_decision` three (F1a), column comment set, the one decision row untouched, three CHECKs remain; second apply clean on scratch earlier (idempotent) |
| post-apply | web health 200; 0 web error lines; 0 scheduler failure lines (old code still deployed, unaffected) |
| merge | `c485ce6` (parents `ddef3f9` + `9e08ca8`), author AK, 0 trailers; pushed |
| deploy | web, worker, scheduler, browser-worker all SUCCESS at `c485ce6` |
| service logs since boot | web 0 / worker 0 / scheduler 0 / browser-worker 0 tracebacks or failures (scheduler: 56 loudly-once skips only) |
| web tier, read-only | `/api/_internal/health` 200; unauthenticated decision tab → 302 `/login`; MEMBER session: release 16 → 200, 2 verdict cards + 1 roll-up (`conditional_go` × 1, `no_go` × 2), release 229 → 200, 2 cards + 1 roll-up (all `no_go`); "CANNOT DETERMINE" text 0, resolver block 0, inline signatures 0 on both |
| data act | none — evaluating a release is a human act; no decision row was written by this merge |

**What changed for production today: nothing visible.** Every live
release is on the two-env branch, whose number is each org's own and
whose render is unchanged. The next `Evaluate GO/NO-GO` on a release
with evidence in one env, or none, records `cannot_determine` where it
would have graded against a tenant-wide sequence.
