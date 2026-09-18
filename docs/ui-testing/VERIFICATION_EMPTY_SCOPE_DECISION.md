# AUD-014 containment — a decision requires a non-empty graded scope

Branch `fix-empty-scope-decision` from `main` @6d09603. AK's GO of
2026-09-18, five numbered items. The rule, verbatim: *a decision requires a
non-empty graded scope. If the scope resolves to zero checks — no scope
declared, or scope entirely on inactive/excluded environments — Evaluate
REFUSES and writes NO decision row, with the reason naming what is empty. It
never grades, and it never records GO.*

## a. DESIGN

### a.1 What was true (reproduced on scratch against main, before any change)

Two shapes were planted on scratch (`AUD14-release-no-scope`, release 91:
zero requirements; `AUD14-release-inactive-env`, release 92: one requirement
holding three approved test cases, one declared target, an environment with
`is_active=false`). Then the two attacks, as admin, through the API:

| shape | `POST /api/releases/<id>/evaluate-decision` | rows written | the page's card | the board's card |
|---|---|---|---|---|
| no scope (91) | **200, `recommendation: go`**, `criteria_met` true on all seven axes | **1** (decision 34) | **GO** — "Grading: Nothing ungraded." | "no requirement in scope" (state `not_evaluated`) |
| inactive-only (92) | 409 `SCOPE_NOT_CURRENT` — three `NEVER_RUN` items, "No run in any environment." | 0 | **GO** — "targets (declared): AUD14-inactive: **0 of 0 checks current**" | "Evaluate will refuse — 3 items in scope are not current" |

So there are two defects, not one:

1. **The engine grades an empty input as satisfied.** `grade_release` assembles
   the evidence and hands it to the policy; with zero checks no rule's condition
   holds, `ungraded` is empty, and the policy's arithmetic reads *"no rule
   blocked, reviewed or conditioned"* → GO at 0.95. Step 2's readiness
   check (`release_scope_readiness`) is vacuous over an empty scope
   (`non_current: 0`), so the composer never refuses, and the row is written.
   This is the AUD-014 mechanism, and it is exactly the class AK named:
   **absence read as compliance**.

2. **The page previews over different keys than the act evaluates.** The
   decision tab builds its keys from the release SERVICE's requirement dicts
   (`get_release_detail`), which carry `id, jira_key, jira_summary, is_stale`
   but not `external_key`. `key_for_requirement_row` therefore falls through
   to `jira_key` (NULL for a manual requirement) and then `req-<id>` — a key no
   link carries — so the preview's scope is empty ("0 of 0 checks") and reads
   GO, while the composer (ORM rows, `external_key` present) resolves the real
   scope and refuses. The board derives its keys with the same COALESCE the
   composer's builder uses, so it agrees with the act, not the page. This is
   the D-491 violation the audit saw ("the list says refuse, the page previews
   GO"), and the reason attack #2 looked like a GO-over-nothing on the page:
   on production every requirement is a Jira row whose `external_key` equals
   its `jira_key`, so the page happens to agree there — the defect is latent,
   not absent.

A third hole sits beside them: the composer proceeds to grade when the
readiness read is UNAVAILABLE (`available: False` → the refusal condition is
false → grade → record). An unknown scope is not a current scope. Same
class.

### a.2 The rule, as code

The **scope** of a release is: its live test cases (latest version not
deprecated), split by lane (functional / conformance), × its **target
environments** — the DECLARED targets (Step 4, `release_targets.active`),
else the ACTIVE environments that hold a run for the scope — minus the pairs
the latest EXECUTED plan excluded by reason. A **check** is one (functional
test case, active target) pair not excluded by the plan, or one conformance
test case (graded on the browser plane, target-independent).

**Emptiness** is a pure function over that resolution, one of four reasons,
each naming what is empty:

| reason | when | the sentence |
|---|---|---|
| `no_requirement` | the release has no requirement | "no requirement is in scope — nothing to grade" |
| `no_test_case` | requirements, but zero live test cases | "N requirement(s) in scope hold no current test case — nothing to grade" |
| `no_active_target` | functional test cases, but no ACTIVE target: every declared target is inactive, or nothing is declared and no active environment holds a run | "the only target environment(s), X, Y, are inactive — no active environment is in scope" / "no target environment: none declared, and no active environment holds a run for this scope" |
| `all_excluded` | functional test cases and active targets, but the latest executed plan excludes every test case on every active target, and no conformance test case remains | "the plan excludes every test case on every target — no check remains to grade" |

A conformance-only scope is NOT empty (its checks grade on the conformance
axis). A scope with one un-excluded (test case, active target) pair is not
empty — the pre-condition is about zero, not about few; "near-empty" is a
number the audit reports (a.6), not a rule.

### a.3 Where the pre-condition sits — the engine, before the policy runs

`quality_decision_console.grade_release` is the engine's entry (the composer
calls it to RECORD; the page calls it, through `preview_release_decision`, to
PREVIEW). It now resolves the scope FIRST and refuses on emptiness with
`{ok: False, reason: "scope_empty", empty: {reason, sentence, ...}, sentence}`
— before the policy is read, before the evidence is assembled, before
`qp.evaluate`. The refusal is a return value the existing `ok: False` path
already carries (ruling 3's no-active-policy refusal), so the composer, the
page's card (`data-testid="quality-refusal"`), and the API route need no new
branch to SHOW it — only the routes' answer needed a code.

Order in the composer (`evaluate_and_record`): **emptiness → readiness →
policy**. Emptiness first because a non-current scope presupposes a scope: a
refusal must name the TRUE reason (D-494's lesson — the earlier refusals
"for the wrong reason" were the audit's hardest finding to read). The
readiness read's `available: False` now refuses too (`scope_unavailable`)
instead of letting the act through.

The composer's own emptiness check and the engine's are the same function on
the same session (`release_scope`); the engine's is the guarantee (a caller
that skips the composer still cannot grade nothing), the composer's is the
ordering.

### a.4 One canonical read; the board and the page call it (item 2)

Canonical: `quality_evidence.resolve_release_scopes(session, tenant_id,
keys_by_release) -> {release_id: scope}` — bulk by construction (D-489's
shape), with `release_scope(session, tenant_id, release_id, keys)` as its
one-release form. `scope_emptiness(scope)` is the pure verdict over it.

- the engine (`grade_release`) and the composer call `release_scope`;
- `assemble` takes its scope and targets FROM `release_scope` (one read, not
  a second derivation) — its output dict is unchanged for a non-empty scope
  (proven byte-identical in §d);
- the board (`releases_board_console._tenant_reads`) calls
  `resolve_release_scopes` for the page's releases — its private links SQL
  goes; a release whose scope is empty reads **"Evaluate will refuse — <the
  same sentence>"** with state `refuses` (today: `not_evaluated`, "no
  requirement in scope" — a release Evaluate refuses must not read as merely
  un-evaluated).

Currency ("N items in scope are not current") is the SECOND refusal, Step 2's
(D-484). Canonical: `substrate_decision.release_scope_readiness`. The board
mirrored it in bulk (D-491 reconciled the environment filter) but counted
pairs from RUNS, where the canonical read counts every live test case × every
environment holding evidence — a test case that ran on one of two evidence
environments is one `NEVER_RUN` item on the page and zero on the board. This
slice gives the canonical read a bulk form (`release_scope_readiness_bulk`,
same body, one release = one entry) and makes the board call it. The
per-release output is byte-identical (§d, the parity test).

Also: the release service's requirement dicts gain `external_key`, so the
page's keys are the composer's keys (defect 2). `external_keys_for_requirements`
is the one builder (D-198); it was only ever handed an incomplete dict.

### a.5 The routes' answers

`POST /api/releases/<id>/evaluate-decision` answered every refusal as
`SCOPE_NOT_CURRENT` with "N item(s) in scope are not current" — for the
no-active-policy refusal that read "0 item(s) … not current", a wrong
message already. It now answers BY REASON, one 409 shape:

| reason | code | message |
|---|---|---|
| `scope_empty` | `SCOPE_EMPTY` | the emptiness sentence; `details.scope` |
| `scope_not_current` | `SCOPE_NOT_CURRENT` | unchanged; `details.items` |
| `scope_unavailable` | `SCOPE_UNAVAILABLE` | "the scope's readiness could not be read — evaluate refused" |
| `no_active_policy` / `policy_unavailable` | `NO_ACTIVE_POLICY` / `POLICY_UNAVAILABLE` | the policy sentence |

The web route already flashes `sentence or reason` for a non-readiness
refusal; the sentence now names the emptiness. The page's Evaluate button
carries the refusal in its title as it does for Step 2.

### a.6 The production audit (item 3) — read-only, count and list, alter nothing

Under the proven read-only guard (CREATE / INSERT / UPDATE attempted and
refused first): every `release_decisions` row with `recommendation = 'go'`,
per tenant schema, read with the checks it was graded over — from the row's
own `reasoning.decision.observations` / `evidence_lines` (what the engine saw
THEN), beside the release's scope resolved NOW by the canonical read. Empty =
zero checks at grading; near-empty = fewer than three, reported as a number.
The list goes in §g and, if non-empty, into the D-entry by decision id.

### a.7 Non-goals

- No backfill, no edit of any recorded decision (a recorded human decision is
  history, AK's rule).
- The policy engine (`quality_policy.evaluate`) is untouched: emptiness is a
  pre-condition of evaluation, not a rule outcome — a policy cannot be written
  that grades nothing.
- The env-78 evidence-panel fork (the list-bound HOLD) is not decided here;
  the substrate evidence panel's org-blind read is unchanged.
- The CI webhook does not evaluate (D-494 refuses it before any write).

## b. What changed

| file | change |
|---|---|
| `primeqa/intelligence/quality_evidence.py` | **`resolve_release_scopes`** (bulk, one links / latest-claims / declared-targets / evidence-environments / latest-plans read for a page of releases), **`release_scope`** (one release), **`scope_emptiness`** (pure; four reasons); `assemble` takes its scope, targets, plan and lane split FROM the canonical read (`scope=` seam) — its output for a non-empty scope is byte-identical (§d) |
| `primeqa/intelligence/quality_decision_console.py` | `grade_release` resolves the scope FIRST and refuses on emptiness (`ok: False, reason: scope_empty, empty, sentence "Evaluate will refuse — …", scope`) before the policy is read, the evidence assembled, or a rule evaluated; one environment reader shared with the assembly; the preview tolerates a refusal without evidence |
| `primeqa/release/decision_composer.py` | `_resolve_scope_and_readiness` (one tenant session for both pre-conditions; a failed read is RETURNED, never swallowed); `_refusal` (one envelope builder); order **emptiness → readiness → policy**; an unavailable readiness read refuses (`scope_unavailable`) instead of grading |
| `primeqa/intelligence/substrate_decision.py` | `_claim_matches` (memoised link read, sorted key set) + pure `_claim_ids_from_matches` — `_claim_test_ids` is now their composition; `_latest_claims` (memoised); `_environments_with_evidence_by_claim` (one query, per claim, same active filter); **`release_scope_readiness_bulk`** — `release_scope_readiness` is the one-entry form of it |
| `primeqa/intelligence/releases_board_console.py` | `_tenant_reads` calls `resolve_release_scopes` + `release_scope_readiness_bulk`; its private links/runs census is gone (a runs query remains for `latest_run_at`); an empty scope reads `refuses` "Evaluate will refuse — <sentence>"; its own session is a read scope (memo) |
| `primeqa/execution_engine/planner.py` | `read_env_info(..., warm=)` pre-reads a set of environments in one query; `read_env_info_many` |
| `primeqa/release/service.py` | the release detail's requirement dicts carry `external_key` (defect 2) |
| `primeqa/release/routes.py` | the API evaluate route answers by reason: `SCOPE_EMPTY` / `SCOPE_NOT_CURRENT` / `SCOPE_UNAVAILABLE` / `NO_ACTIVE_POLICY` / `POLICY_UNAVAILABLE`, one 409 shape |
| `primeqa/views.py`, `templates/releases/detail.html` | the web route's flash names the emptiness (existing generic branch); the Evaluate button's title carries the emptiness refusal as it carries Step 2's |
| tests | `tests/unit/test_scope_emptiness.py` (11), `test_decision_composer.py` (+7), `test_run_readiness.py` (2 re-seamed), `tests/integration/test_empty_scope_decision.py` (9, DB-real, plants and removes its own world), `test_step_5_pages.py` (the no-requirement fixture release now asserts the emptiness refusal) |
| `docs/reviews/PLIMSOL_FIX_PLAN.md` | the class entry "absence read as compliance" (item 5) + the three lows closed here |

Classification: **WRITE-FREE** — zero migration / alembic files; the verb census over every added line in `primeqa/` finds no `CREATE TABLE` / `ALTER` / `DROP` / `INSERT INTO` / `UPDATE … SET` / `DELETE FROM` (the board's and the scope's new SQL are SELECTs). No recorded decision is touched.

## c. Guards

- **The pre-condition is in the engine, not the caller.** `grade_release` refuses by construction; a caller that reaches the policy through any path other than the composer still cannot grade nothing. The composer's own check exists only to ORDER the refusal before Step 2's (`test_an_empty_scope_refuses_before_readiness_and_before_the_policy` proves neither the readiness verdict nor the substrate read is consulted).
- **Unknown is not satisfied.** A scope read that raises, or a readiness read that reports `available: False`, refuses (`scope_unavailable`). Before this slice the second let the act through.
- **One call, two surfaces.** The board's emptiness sentence is the engine's sentence (same function); the board's currency count is the per-release read's entry (bulk == single, `test_bulk_readiness_is_the_single_read_entry_by_entry`).
- **The policy engine is untouched.** `quality_policy.evaluate` still grades whatever it is handed; the emptiness verdict never reaches it.

## d. Verification — the two attacks re-run, the real scope unchanged (scratch)

Four releases were planted by hand on scratch (the same shapes the integration suite plants): no scope (93); one requirement of three test cases with an INACTIVE declared target (94); a REAL scope of two test cases with stamped current runs on env 4 (95); a SPLIT scope — test case a ran on env 4 and 5901, b on env 4 only (96). Everything below was captured from `main` @6d09603 and from the branch on the same rows; the plant was removed afterwards (residue 0).

**The attacks (API, as admin):**

| release | `main` | branch |
|---|---|---|
| no scope (93) | **200 GO, decision row written** (attack #9 reproduced) | **409 `SCOPE_EMPTY`** "Evaluate will refuse — no requirement is in scope — nothing to grade", `details.empty.reason = no_requirement`, **0 rows** |
| inactive-only (94) | 409 `SCOPE_NOT_CURRENT` (three NEVER_RUN items — the WRONG reason) | **409 `SCOPE_EMPTY`** "… every declared target environment is inactive (AUD14-inactive) — no active environment is in scope", `details.scope.active_targets = []`, **0 rows** |

The web form (double-submit CSRF) redirects to the decision tab with the flash "Evaluate refused — no requirement is in scope — nothing to grade"; 0 rows.

**The page and the board, same rows:**

| release | `main` page card / board card | branch page card / board card |
|---|---|---|
| no scope | GO "Nothing ungraded" / "no requirement in scope" (`not_evaluated`) | "Evaluate will refuse — no requirement is in scope — nothing to grade" (`data-recommendation="none"`) / the same words (`refuses`) |
| inactive-only | GO "AUD14-inactive: 0 of 0 checks current" / "Evaluate will refuse — 3 items … not current" | "Evaluate will refuse — every declared target environment is inactive (AUD14-inactive) — no active environment is in scope" / the same words |
| split | GO over "0 functional check(s)" (defect 2: the page's keys were `req-<id>`) / "not evaluated · 2 of 2 current" | cannot_determine over 2 checks, the Step 2 block naming 1 item / "Evaluate will refuse — 1 item in scope is not current · 1 of 2 current" |

**Byte-identity where the scope is real** (the composer's keys, inside one read scope; `assembled_at`/`evaluated_at` normalised):

| read | real (95) | split (96) |
|---|---|---|
| `preview_release_decision` dict | identical | identical |
| `release_scope_readiness` dict | identical | identical |
| queries: preview / readiness | 27 / 5 → 27 / 3 | 38 / 5 → 38 / 3 |

The real release's PAGE is not byte-identical on scratch — on `main` it previewed GO over zero checks (its requirement is manual, so the page's key was `req-<id>`); on the branch it previews the act's own cannot_determine over two ungrounded checks. That difference is defect 2 closing, and it cannot occur on production (§g: every production requirement's page key already equals its identity key).

Board on the four-release page: 12 → 16 queries, constant in the number of releases.

## e. Suites

| suite | result |
|---|---|
| sweeps first (D-490: undefined names, close-2 call sites, 6a routes) | 20 passed |
| unit | **5,169 passed** (18 new: 11 `test_scope_emptiness`, 7 `test_decision_composer`) |
| `tests/integration/test_empty_scope_decision.py` (DB-real, scratch) | **9 passed**, world removed (residue 0) |

## f. The D-468 set

| suite | result |
|---|---|
| unit | **5,169 passed** |
| DB-real corpus (24 suites + the new one, scratch, `REPORT_PAGES=1`) | **157 passed, 4 skipped, 3 failed** — each red on `main` @6d09603 against the same scratch (proven in a worktree, same run): `test_phase5_authoring::test_a` (the audit's tenant-2 schema exists on scratch), `test_report_slice::test_a` (the D-488 window artefact), `test_step_5_scope` (the seed policy left active by replay) |
| `tests/integration/test_representation` (harness) | **454 passed, 6 failed** — each red on `main` identically: the four `test_step_b_staleness_pin` fixtures and the two `test_step_5_policy` fixtures (calendar-rotted, ledgered 2026-09-15) |

Two honest notes from getting there. (1) The first corpus run showed four more reds — `test_step_2_scope`, three in `test_step_3_scope`, two in `test_step_1_identity` and `test_repair_gate::test_g` — every one caused by the hand-planted scratch world of §d (a second connected org per environment; manual requirements whose identity key is not the pre-072 derivation, which `test_1b` asserts row for row). The plant was removed, the integration fixture now reuses an environment's org and removes a half-planted world on any setup failure, and the reds went with it. (2) `test_3a4_processor::test_bcde` went red once, in the second corpus run only, and passes alone on both trees; it is order-dependent under the corpus and not this slice's.

## g. Production — read-only, the guard proven first

Guard: `CREATE TABLE`, `INSERT`, `UPDATE` each attempted on the read-only connection and each refused with `ReadOnlySqlTransaction` before any read, on every run.

**Item 3 — GO decisions over an empty or near-empty scope: NONE.** Production holds two decision rows in total (both `go`, one tenant schema):

| decision | release | recorded | graded over | scope now |
|---|---|---|---|---|
| 72 (policy row, Plimsol default v1, plan 9ea0c522) | 16 "Parity Window 1" | 2026-09-11 06:01Z, final GO by AK | 19 functional checks in scope · 19 counted runs · "targets (fallback:evidence-active): Prime QA NEW: 19 of 19 checks current" | 19 checks, not empty, 0 non-current |
| 57 (pre-policy substrate row) | 185 "ISO-REQ-a163caff" | 2026-06-18 04:31Z, no final decision | claim_count 14 | 15 checks, not empty |

Empty at grading: 0. Near-empty (fewer than three counted runs): 0. Nothing altered.

**Item 4 — decision 72's preview, byte-identical.** `preview_release_decision(1, 16, keys)` and `release_scope_readiness(1, keys)` captured from `main` @6d09603 (a worktree) and from the branch on the live rows: **identical** (GO 0.95, "no rule blocked, reviewed or conditioned", plan 9ea0c522, 19 items CURRENT on env 59). The page's keys equal the act's keys on both (five Jira rows).

**The board on production** (25 releases): no row's counts moved (the bulk readiness equals the old census on every production release); 13 rows moved from `not_evaluated` "no requirement in scope" to `refuses` "Evaluate will refuse — no requirement is in scope — nothing to grade" — which is what the act now does to them.
