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

(filled at build)

## c. Guards

(filled at build)

## d. Verification — the two attacks re-run, the real scope unchanged

(filled at build)

## e. Suites

(filled at build)

## f. The D-468 set

(filled at build)

## g. Production audit of GO decisions over an empty or near-empty scope

(filled at build)
