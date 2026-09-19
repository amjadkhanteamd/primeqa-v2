# Triage batch — "guarded by convention, not by constraint"

Branch `triage-constraints` from `main` @b7d3976 (D-496). AK's GO of
2026-09-19: one slice, one class, six members (a–f), pushed at every
milestone, one HOLD before the merge. The audit's findings file and tooling
(branch `audit-findings` @2a33831, docs + scripts only) were merged in first
so the sweep could be repaired and `findings.json` updated in place.

The class: an invariant held by every reader's join, a route's form
handling, a caller's if-statement, or a hook's advertised step — never by
the table, the chokepoint, or a gate that can be shown to fail. Every fix
below carries a guard of the pyflakes-gate kind: proven red before, green
after, and failing (never skipping) when its tool or its target is absent.

## The five outcomes and the sweep

| member | outcome | where it holds now | guard |
|---|---|---|---|
| **a. AUD-026** — a PASS insertable for a run that does not exist | **FIXED.** Production zero-orphan check first, read-only under the proven guard: `s6_interpretations` 3,691 rows, **0 orphans**, 0 null run_ids. `fk_s6_interpretations_run` → `s4_execution_runs(run_id)`, NO ACTION (a run with verdicts cannot be deleted from under them) | tenant migration `20260919_0010` | the run_id CENSUS gate: every tenant table with a `run_id` column carries the FK or is named in a ledger with its reason; a new unledgered column fails (proven with a probe table) |
| **b. AUD-019** — a member activates a quality policy | **FIXED. Rule: ADMIN-only.** Activation decides what grades every release the tenant evaluates from then on — governance, not a member's act — and the Settings surface that offers it is admin's. Members record decisions and waivers; they do not choose the grader. | `@require_tier(Tier.ADMIN)` on the route (the DB does not know roles; no table-level shape exists for a role) | the route-authority TABLE, proven behaviourally: one tier below is refused, the minimum tier is not; red on the pre-change tree |
| **c. AUD-020** — a member revokes another's waiver with an empty reason | **FIXED. Rule: owner-or-admin, with a reason.** A waiver is a named human accepting a known state (D-488); its withdrawal is the same kind of act — the reviewer who accepted it, the person who recorded it, or an admin. Creation already requires a reason; withdrawal now does, symmetrically. | the service (`quality_policy.revoke_waiver` refuses a stranger and an empty reason before any write); the route passes `rank(role) >= ADMIN`; **the table**: `ck_quality_waivers_revocation_reason` | unit (refusal before any write, 8 cases), DB-real through the real routes as the scratch audit users (member refused, row unchanged; owner and admin act; empty reason refused for everyone), the CHECK attempted for real with '', '  ' and NULL |
| **d. AUD-023** — a member removes another's release target | **FIXED. Rule: declarer-or-admin, with a reason.** The declaration recorded its actor (D-486) for exactly this undo. | the planner (`remove_target` refuses a non-declarer and an empty reason before any write); the route passes the tier; **the table**: `ck_release_targets_deactivation_reason` | as (c): unit, DB-real routes, the CHECK attempted for real |
| **e. attack #5** — apply a SPECULATIVE and a SEMANTIC repair | **The four reachable paths REFUSE** — route (admin POST approve), service (`decide_proposal`), auto pass (`auto_apply_proposals`); no API route exists — each by the callers' verdict check ("SPECULATIVE/SEMANTIC: not applicable — only a DERIVED proposal with a recorded grounding source can be applied"), rows stay `proposed`, recipes unchanged. **The internal apply (`_apply`), called directly, MUTATED**: recipe versions 1 → 2 (actor s8, promoted to approved), re-verify job queued; only the stamp was refused, by the new table trigger. Not a reachable path (nothing reaches `_apply` without the check) — a chokepoint whose guard lived in its callers. **CONTAINED** the D-494 way: `_apply` now refuses first; re-run: refused, versions 1 → 1, no job. | `_apply` (the chokepoint) + **the table**: `repair_proposals_apply_guard` refuses `approved`/`applied` for any non-DERIVED or ungrounded row, on UPDATE and on INSERT | `test_repair_gate::test_c4` (the direct apply refuses, no version, no job, the stamp refused by the table); the trigger attempted for real: SPECULATIVE / SEMANTIC / unclassified → approved and applied (6 refusals), DERIVED without grounding refused, a direct INSERT as applied refused; reject and DERIVED-with-grounding allowed |
| **f. the sweep** — parameterised links excluded by its own regex | **REPAIRED, PROVEN, GATED.** See below. | `scripts/deadlinks_gate.py`; the unit gate; the pre-commit hook | `tests/unit/test_no_dead_links.py` plants a dead target of every kind (the two audit findings verbatim) and asserts each is reported BEFORE the repo is judged |

### The sweep in numbers

| | targets | alive | unresolvable (dynamic base) | dead |
|---|---|---|---|---|
| the OLD sweep on the unfixed tree | (href without Jinja only) | — | — | **0 — green, blind** |
| the REPAIRED sweep on the unfixed tree, first run | 283 | 256 | 20 | 7 |
| after two resolver blind spots closed (JS literals that append an id after a trailing `/`; `${…}` in JS template strings) | 283 | 254 | 25 | **4, all real** |
| after the fixes | **277** | **256** | **21** | **0** |

The four real dead targets: `releases/detail.html:74` "View Reasoning" →
`/releases/{{ release.id }}/decision` (AUD-003, fixed → `?tab=decision`);
`sections/list.html:46` "View TCs" → `/test-cases?section_id={{ node.id }}`
(AUD-004, fixed → `/requirements?section_id=`, labelled "View
requirements"); `claims/list.html:33` `action="/claims"` (an orphan
template — rendered by nothing — deleted); `static/js/tc_feedback.js:31`
`/api/test-cases/${tcId}/feedback` (a retired API called by an orphan
script — deleted with its orphan modal `feedback_modal.html` and the
CLAUDE.md component-kit line that advertised them). The 21 unresolvable
targets are `{{ href }}`-style dynamic bases (pagination, breadcrumbs,
item URLs) — counted, never believed alive.

The sweep resolves: `href`, `action` (with the form's method), `formaction`,
`hx-get/post/put/patch/delete`, `data-url/href/confirm-form`, `url_for(ep,
kw=…)` (the endpoint must exist and the kwargs must cover the rule's
parameters — there are **zero** `url_for` uses in the tree; the arm is
proven on planted templates), and JS `fetch` / `location` literals in
templates and static JS. Jinja collapses to wildcard segments; `{% if %}`
branches both resolve; the METHOD is checked (a form posting to a GET-only
route is dead).

**Proof it can fail.** The old test passes on the unfixed tree (1 passed);
the repaired gate's exit on the same tree is 1, naming both known links; the
pre-commit hook goes red with the dead "View Reasoning" link staged and
green after; `test_the_sweep_reports_every_planted_dead_target` asserts nine
planted shapes are each reported, `test_the_sweep_does_not_cry_wolf` asserts
five live shapes are not.

**Beside the pyflakes gate.** The hook's step 3 runs the sweep when a
template, static JS or route module is staged. And the hook's first form
ended step 1 with `exit 0`, so the pyflakes step it advertised never ran
(AUD-027); on revival it called the system `python3` where pyflakes is not
installed and refused this batch's own commit — the steps now fall through
to one final exit, using the venv interpreter. CONVENTIONS documents both
gates (its paragraph had been garbled by the D-495 insert, AUD-030).

## Every new guard

| guard | kind | fails when |
|---|---|---|
| `tests/unit/test_no_dead_links.py` (3) | sweep + proof of failure | any internal target is dead; the sweep collects <200 targets; a planted dead shape is not reported |
| `tests/unit/test_step_6a_routes.py::test_no_template_links…` | delegates to the same module | as above (the 6a gate and the merge gate cannot disagree) |
| `.githooks/pre-commit` step 3 | commit-time sweep | a dead target is staged |
| `tests/unit/test_route_authority_table.py` (13) | behavioural tier table | a route in the table admits one tier below its minimum, refuses its minimum, or no longer exists |
| `tests/unit/test_authority_rules.py` (16) | service rules | a stranger's revoke/remove, or an empty reason, reaches a write |
| `tests/integration/test_authority_triage.py` (8, DB-real) | the real routes as the scratch audit users | a member's POST succeeds; a row changes; the owner or admin is refused |
| `tests/integration/test_constraints_triage.py` (20, DB-real) | the census gate + every constraint attempted for real | a new unledgered `run_id` column; a ledger entry that now carries an FK; any forbidden write goes through; any allowed write is refused |
| `tests/integration/test_repair_gate.py::test_c4` (DB-real) | the chokepoint | the direct apply writes; the stamp is not refused by the table |
| `repair_proposals_apply_guard` (table trigger) | constraint | a non-DERIVED or ungrounded row enters approved/applied by any path |
| `ck_quality_waivers_revocation_reason`, `ck_release_targets_deactivation_reason` (CHECKs) | constraint | an empty reason by any path |
| `fk_s6_interpretations_run` | constraint | an orphan verdict; a run deleted from under its verdicts |

## Classification and the one production act

`git diff main...triage-constraints`: one alembic tenant migration
(`20260919_0010`), **REJECTING** — the FK and the two CHECKs refuse rows
that violate them (none exist on production, proven) and the trigger refuses
transitions; per AK and D-476 the merge is **dump-first** regardless of the
zero-violation proof. No public migration. Runtime changes add SELECTs only
(the planner's declared_by read, the chokepoint's applicability re-read).
No production data is written by this slice; the migration is applied to
`tenant_1` at the merge runbook after the dump, and is the one production
act.

Production facts read for the batch (all read-only, guard proven each run
by attempting CREATE / INSERT / UPDATE):

| fact | value |
|---|---|
| `s6_interpretations` | 3,691 rows, 0 orphans, 0 null run_ids; `s4_execution_runs` PK `run_id` |
| `quality_waivers` revoked / with empty reason | 0 / 0 |
| `release_targets` inactive / with empty reason | 0 / 0 |
| `repair_proposals` | 140 rows: recipe_edit 6 applied 28 proposed; regenerate 1/3; rerun 48/54 — existing `applied` rows are history; the trigger judges transitions only |
| the three run_id siblings (AUD-028, **open**) | `s4_created_records` 3,547 rows / **3 orphans**; `repair_proposals` 140 / **4 orphans**; `s6_reinterpretations` 50 / 0 — orphans exist, so per the rule for (a) their FKs are NOT added: stop and report |
| alembic head | `20260912_0010` (this migration is next) |

## findings.json

30 findings. This batch **closed 7** (AUD-002/003/004/019/020/023/026),
**narrowed 1** (AUD-005: the three orphans with dead targets removed; four
orphan templates remain, not this class), **added 4** (AUD-027 hook `exit 0`
— closed here as part of (f); AUD-028 the run_id siblings with orphans —
**open**; AUD-029 the unguarded chokepoint — closed; AUD-030 the garbled
CONVENTIONS paragraph — closed), and recorded the closures the earlier
slices had earned (AUD-006/008/009/010/013 by D-494/D-495, AUD-014 by
D-496). **Open: 13** — AUD-001, 005, 007, 011, 012, 015, 016, 017, 018,
021, 022, 024, 025, 028. Nothing outside the class was fixed: the orphan
file deletions and the hook repair are the sweep's own findings and the
gate's own plumbing.

## Suites

| suite | result |
|---|---|
| sweeps first (D-490: dead links, undefined names, the authority table, close-2 call sites, 6a routes) | 36 passed |
| unit | **5,201 passed** (32 new: 3 dead-link, 13 authority table, 16 authority rules) |
| DB-real corpus (24 suites + the two new ones, scratch, `REPORT_PAGES=1`) | **186 passed, 4 skipped, 3 failed** — the three known reds, each red on `main` against the same scratch (`test_phase5_authoring::test_a`, `test_report_slice::test_a`, `test_step_5_scope`) |
| `test_repair_gate` + `test_repair_reverify` | 25 passed (one new) |
| harness (`tests/integration/test_representation`) | **454 passed, 6 failed** — the six calendar-rotted fixtures, identical on `main` |

**The constraints found their own class inside the harness.** The first
harness run after the migration showed 21 NEW reds: every `s6` suite seeded
verdicts for runs that did not exist (`run_id=uuid4()` with no
`s4_execution_runs` row) — the exact shape AUD-026 names, held up until now
only by the missing key — and the Step 4 planner test removed a target as a
different user than the one who declared it. The fixtures were repaired,
not the constraints: `persist_interpretation_with_run` (in `_fixtures.py`)
seeds the run a verdict belongs to and every `s6` seed goes through it; the
planner test now proves the stranger and the empty reason are refused, and
removes as an admin. Likewise two repair-gate fixtures that plant HISTORY
(July-shaped rows auto-applied before the gate) now plant under the replica
role, the way every never-delete fixture does — the trigger refusing that
insert is the point.

## Merge plan (for the runbook after GO)

1. Pre-flight as standard (trees, main delta, classification with the verb census, window against the deployed tree, production probes).
2. **Dump first**: `pg_dump` of `tenant_1.s6_interpretations`, `tenant_1.quality_waivers`, `tenant_1.release_targets`, `tenant_1.repair_proposals` (schema + data) to a dated file outside the repo, before the migration.
3. Re-run the read-only zero-violation checks (orphans, empty reasons) immediately before applying — production is live (the sibling counts moved between two reads in one day).
4. `alembic -x mode=tenant -x tenant_id=1 upgrade tenant@head` against production, DSN from `.env`, never printed; read back the three constraints and the trigger.
5. Prove the constraints on production by attempting the forbidden writes inside rolled-back transactions (the same harness as scratch, prechecks included).
6. Merge `--no-ff`, push, four-service watch, logs, health, read-only proof (the release page's "View Reasoning" now lands on the decision tab; a member's activate POST redirected home).
