# LLD Step A — the repair-proposal three-verdict gate

Status: IMPLEMENTED on the 2026-09-06 GO with rulings D1 (rerun/regenerate
DERIVED by construction, `no_recipe_mutation` recorded), D2 (both
thresholds dropped), D3 (refused auto-applied edits REVERTED with
`gate_retro_revert` provenance; the DERIVED one kept), D4 (R2 sole-active
clause included; two active values → SPECULATIVE); SEMANTIC-first
ratified. Transcript: VERIFICATION_STEP_A.md. Merge gated; switch-on its
own gated act.
Branch: `step-a-repair-gate` (from main @00f8e7d, D-479).
Derives from: PLIMSOL_SURFACE_PLAN.md §4 (Phase A — "the human path is
guarded by nothing"), D-236 (the LLM recipe-edit proposal + flag-gated
auto-apply), D-215.1 (the proposal-only spine), D-454 (the
staged+asserted pinned-field intersection this gate reuses), D-476 (the
dump/classification policy the migrations follow), the standing UI rule
(AK, 2026-09-06: no UI change lands without a mock AK has seen — the
panel below matches the approved mock; any deviation HOLDs with a
screenshot first).

**Thesis.** A repair proposal is an LLM guess until something recorded
says otherwise. Today the panel converts the guess into a recipe
mutation behind one button, and the autonomous pass grades the guess by
the guesser's own score. The gate makes every proposal carry a verdict
derived from recorded facts — S1, the platform error string, the claim's
asserted fields — and lets only a DERIVED remedy reach an apply action.
Nothing here computes a repair; it classifies the repair the agent
already proposed.

---

## 0. The defect statement — pre-flight facts, cited verbatim

From the 2026-09-06 read-only status check (each cite is a line that was
read this session):

- Proposal creation is ungated beyond mechanical checks
  (`repair_agent.py:257-337`; `agent_enabled` loaded at 282, never read
  in triage; `scheduler.py:236-237` calls triage for every tenant).
- "Approve & apply" is a plain POST, no confirm
  (`s4_list.html:330-334`), admin+ (`views.py:3681`),
  `decide_proposal(approve=True)` → immediate `write_recipe`
  (`repair_agent.py:416`, `468`) + re-verify enqueue (`479`).
- Confidence is parsed from LLM output (`repair_agent.py:236`),
  rendered as a percentage (`s4_list.html:312`), and compared against
  `threshold_high` by the auto pass (`repair_agent.py:565-574`).
- `trust_threshold_medium` is stored/edited/validated (`models.py:168`,
  `agent.html:38-39`, `views.py:2417`, `agent_settings.py:73-77`) with
  no consumer.
- The kill switch is split across `agent.html` (`agent_enabled`) and
  `llm_usage.html:201` (`repair_auto_apply`).
- Nothing from Phase A has been built since 2026-08-19.

**Two production facts the status check did not cover, measured
2026-09-06 (read-only, tenant 1):**

1. **The autonomous path is ARMED on the pilot tenant.**
   `tenant_agent_settings` for tenant 1 reads `agent_enabled = true`,
   `repair_auto_apply = true`, `trust_threshold_high = 0.90`,
   `trust_threshold_medium = 0.55`, `max_fix_attempts_per_run = 2`.
   env-59 is a sandbox (`is_production = false`), so the production
   skip does not apply to it.
2. **Four recipe edits have already been auto-applied on env-59**
   (`repair_proposals.auto_applied = true`), all at self-reported
   confidence 0.90–0.95:

   | id | date | remedy | rationale (LLM, abridged) |
   |---|---|---|---|
   | 95189 | 2026-07-07 | `Loan_Type__c: __REMOVE__` | "Home Loan" is not a valid restricted-picklist value, so remove the field |
   | 95195 | 2026-07-07 | `Loan_Type__c: __REMOVE__` | "Personal Loan" is not a valid value, so remove the field |
   | 347418 | 2026-07-22 | `PLS_FB_Order_Line__c.PLS_FB_Line_Total__c: __REMOVE__` | read-only/non-createable (formula) field |
   | 465221 | 2026-07-29 | `PLS_BM_Deal__c.PLS_BM_Deal_Value__c: "1000"` | placeholder never substituted; the model chose 1000 |

   Row 465221 is the plan's exact failure: a CHOSEN value applied by
   the machine on the machine's own grade. Rows 95189/95195 removed the
   field the claim was about. Under the gate below, PREDICTED (to be
   measured by retro-classification, §e): 465221 → SPECULATIVE,
   95189/95195 → SEMANTIC or SPECULATIVE, 347418 → DERIVED (R1). Three
   of the four machine-applied edits would have been refused.

   The 21 open `recipe_edit` proposals sit at 0.30–0.85 — below 0.90 is
   the only thing holding them. Examples: `StageName = "Prospecting"`
   (chosen), `Loan_Amount__c = "450000000"` (invented),
   `Loan_Type__c = "Mortgage"` (chosen from the picklist).

   **Immediate ops lean (AK's act, not mine):** set `repair_auto_apply`
   OFF for tenant 1 today via the existing superadmin checkbox on
   `/settings/llm-usage`. Step A takes days; the armed path takes one
   tick. I have not touched it.

Proposal population on prod (retro-classification scope, §e): 132 rows
— applied 54 (`recipe_edit` 6, `regenerate_from_current_org` 1, `rerun`
47); proposed 78 (`recipe_edit` 21, `regenerate` 3, `rerun` 54).

---

## a. GATE CREATION — classification at creation, inside the triage tick

**Placement.** A new pure module `primeqa/intelligence/repair_gate.py`
(the cross-cutting `intelligence` package already hosts `repair_agent`;
it imports `generation.coverage_flag`'s pure helpers exactly as
`repair_agent` already imports `generation.intake`). `triage_new_failures`
calls `classify(...)` immediately before the `INSERT` at
`repair_agent.py:320-331`; the INSERT gains `gate_verdict`
(the existing `verdict` column holds the S6 verdict — the new column is
named apart from it), `grounding_source`, `classified_at`, `classifier_version`. **No proposal
row is written without a verdict** — the classifier is total: every
input shape maps to exactly one of the three verdicts, and every
"cannot classify" path lands on SEMANTIC (fail closed).

**Inputs available at creation (all recorded, none new):**

| input | source (read this session) |
|---|---|
| the remedy: `field_changes` (bare keys, values or `__REMOVE__`) | `repair_agent.py:236-240`; the LLM contract `prompts/repair_proposal.py:37-58` |
| the subject create's staged keys (qualified `Object.Field` or bare) | `_read_subject_create`, `repair_agent.py:174-196`; the qualified convention D-115.4 per `apply_field_changes` (89-110) |
| the failed create step's `error_code` / `error_message` / `error_fields` | `_error_evidence_for_run`, `repair_agent.py:161-171`; `error_fields` from `data_executor._named_fields` (1675-1686) |
| the claim's asserted fields | the D-454 pins builder `governance_core._coverage_pinned_fields` (7260-7276) restricted to its ASSERTED half — semantic-condition subjects — plus the recipe's `AssertStep.predicate.subject_ref` field segment (`data_recipe.py:157-161`, `primitives.py:421-446`) |
| S1 field facts | `SemanticOrgModel.get_entity_details` (`query.py:390`): `is_createable`, `is_nillable` (required = NOT nillable, `presentation.py:185`); `get_picklist_values` (`query.py:431`): `value_api_name`, `is_active`, `is_default` (`picklist_capture.py:118`) |
| the S6 cause and verdict | the proposal row's own `cause_kind` / `verdict` (`repair_agent.py:272-276`) |

**What S1 does NOT record (verified by grep over `sync/` and
`semantic/`):** field lineage — no successor / renamed-from attribute; no
general field default value (only the picklist `is_default` flag). So a
"successor field" remedy is **not derivable in v1**; any remedy that
ADDS or RENAMES a field is SPECULATIVE by construction.

**The three verdicts, evaluated in this order (first match wins):**

1. **SEMANTIC** — any key the remedy touches (set OR remove) is in the
   claim's asserted-field set. Computed as the D-454 intersection: the
   remedy's touched bare names ∩ the asserted bare names (both
   lowercased, object prefix stripped — the exact normalisation of
   `_coverage_pinned_fields`). **Fail-closed branches, all SEMANTIC:**
   - a touched key's staged form in the recipe is **unqualified** (no
     `Object.` prefix) — the object cannot be attributed, so the
     intersection cannot be trusted. Re-measured today over current
     recipe versions: **1,014 create-step keys, 105 bare, 909
     qualified** (the plan's figure, reproduced);
   - the recipe has no subject create, or the pinned recipe version
     cannot be read;
   - the claim kind has no asserted-field extractor (a new archetype);
   - the remedy is empty or malformed.
   Action: **Refused** — no apply action rendered or routable; the
   destination (the requirement) is rendered (§d).
2. **DERIVED** — the diagnosis is grounded in a recorded fact AND the
   remedy value is derived, not chosen. Exactly these rules in v1:
   - **R1 (attested removal):** the remedy is exactly
     `{F: __REMOVE__}` for ONE field F; F is named by the failed
     create's `error_fields` (or is the single field the `error_code`
     names); and S1 at the current sequence records F on the subject
     object with `is_createable = false`, OR records no such field on
     the object at all. Grounding source: `{rule: R1, error_code,
     error_fields, s1_entity_id, s1_fact: is_createable=false |
     absent, s1_seq}`.
   - **R2 (recorded picklist value):** `error_code =
     INVALID_OR_NULL_FOR_RESTRICTED_PICKLIST` on F; the remedy is
     exactly `{F: v}`; and S1's picklist set for F records `v` as
     `is_active = true` AND (`is_default = true` OR `v` is the ONLY
     active value). Grounding source: `{rule: R2, error_code,
     picklist_value_set_id, matched: default | sole_active, s1_seq}`.
   Action: **Approve & apply** — the existing path (new recipe version,
   prior preserved, re-verify enqueued: `repair_agent.py:433-487`),
   gated by the switch (§f).
3. **SPECULATIVE** — everything else: LLM inference only, or a derived
   diagnosis with a CHOSEN remedy value. This includes, by construction:
   every `automation_effect_*` cause (the create SUCCEEDED, no platform
   error string exists, the diagnosis can only be inference — 19 of the
   27 recipe-edit proposals on prod); every placeholder substitution
   (`JSON_PARSER_ERROR`, value invented); every picklist replacement
   with a non-default, non-sole value. Action: **Open recipe** — the
   operator edits (link to the claim page); no apply action rendered or
   routable.

**Non-recipe kinds get a verdict by construction** (every proposal
carries one): `rerun` → DERIVED with grounding `{rule: K-rerun,
outcome, failure_category}` — the remedy is a re-execution, no value is
chosen; `regenerate_from_current_org` → DERIVED with grounding `{rule:
K-regen, cause_kind, claim_version_seq, s1_seq_current}` — the remedy is
an S3 regeneration whose output stays DRAFT (never approved by the
agent). **Decision D1 for AK** (§h): confirm this, or route the two
deterministic kinds outside the gate.

**Grounding source is recorded on every row** (`grounding_source`
JSONB): which rule fired, which S1 fact (entity id + sequence) or which
error string, and for SEMANTIC the intersecting field names and the
resolved destination. A DERIVED row with an empty grounding source is a
classifier bug; the route treats it as not applicable (§f).

**Classifier version** (`classifier_version` TEXT, e.g. `gate@v1`) rides
on the row so a later rule change re-classifies deliberately, never
silently.

---

## b. CONFIDENCE — removed from the operator surface and from the gate

- `repair_proposals.confidence` stays as an audit column. Its model
  docstring and the table comment say **non-decisional**: it is the
  LLM's self-report, parsed at `repair_agent.py:236`, and no code path
  reads it for a decision.
- The panel renders no percentage (`s4_list.html:312` goes).
- `auto_apply_proposals` (`repair_agent.py:547-600`) reads: the switch
  (§f), `repair_auto_apply`, `agent_enabled`, `gate_verdict = 'DERIVED'`, a
  non-empty grounding source, the production skip, the attempt cap.
  The `conf < threshold_high` comparison at 571-574 is deleted.
- Test: a planted `SPECULATIVE` row with `confidence = 0.99` on a
  sandbox env, both flags ON, switch ON → `auto_apply_proposals` skips
  it; the row stays `proposed`; nothing is written to `test_recipes`.

**Consequence AK must rule on (Decision D2):** once the auto pass reads
the verdict, `trust_threshold_high` ALSO has no consumer. The brief
removes `trust_threshold_medium` because a dead safety control is worse
than none; the same argument now covers `high`. Lean: **drop both** in
the same destructive migration and remove both fields from the form.
Alternative: keep `high` for a future consumer — rejected by the same
rule the brief states.

---

## c. KILL SWITCH — one page, one home

- `/settings/agent` (superadmin; `views.py:2387-2426`) becomes
  the ONE home: `agent_enabled` (existing), `repair_auto_apply` (moved
  here), `repair_gate_apply_enabled` (new, §f), `max_fix_attempts_per_run`
  (existing). The threshold fields leave the form (D2).
- `/settings/llm-usage` loses the per-tenant "Auto-fix" checkbox
  (`llm_usage.html:201-205`, the write at `views.py:2201, 2281-2300`);
  in its place a one-line note: "Repair agent controls moved to Settings
  › Agent." **Consequence, stated:** that page edited ANY tenant's flag
  from one table; `/settings/agent` edits the caller's tenant only. That
  matches the role model (superadmin is god mode PER TENANT, CLAUDE.md)
  — the cross-tenant edit was the anomaly.
- `repair_auto_apply` is currently DELIBERATELY not ORM-mapped
  (`models.py:213-216`, deploy-ordering safety for migration 054).
  Migration 054 is applied on production (the row read above proves the
  column). Under MIGRATE-FIRST this slice maps it in the ORM together
  with the new switch column, and `_repair_settings` /
  `views.py:2009-2017` drop their raw-SQL best-effort reads. Scratch
  has NO `tenant_agent_settings` table at all (public holds only
  `activity_log environments tenants users`) — the DB-real tests plant
  it from the migration files (§i).
- **`agent_enabled = false` now gates CREATION.** `triage_new_failures`
  returns `{proposed: 0, scanned: 0, disabled: true}` before the scan
  when the tenant's `agent_enabled` is false, logging once per (tenant)
  per process — the `_WARNED_UNPROVISIONED` posture of
  `ui_schedules.py:48` / `stale_tenants.py:19-31`, applied to a policy
  skip. The auto pass keeps its existing check (`repair_agent.py:555`).
- `trust_threshold_medium`: REMOVE. Zero readers confirmed (the only
  code touching it is the validation invariant `agent_settings.py:73-77`
  and the form). The DB CHECK `trust_bands_sane` (migration 019:23-25,
  live on prod) references it and must drop first.

---

## d. HEADER + PANEL — per the approved mock

- The header sentence "real defects never appear here" (`s4_list.html:293`)
  is removed. In its place the verdict-counts line over OPEN proposals:
  `N derived · N speculative · N refused (semantic)` — computed in
  `list_proposals` (one `GROUP BY verdict` over `status = 'proposed'`),
  never in the template.
- Row layout (matches the mock AK approved; deviation → HOLD with
  screenshot):
  - **Verdict column, left**: `DERIVED` / `SPECULATIVE` / `SEMANTIC`
    badge, with the grounding source's one-line summary under it (rule
    id + the fact: "S1: PLS_FB_Line_Total__c is_createable=false @seq
    249" / "error: INVALID_OR_NULL_FOR_RESTRICTED_PICKLIST" / "touches
    asserted field Loan_Type__c").
  - claim + run links (as today, `s4_list.html:300-302`).
  - diagnosis: S6 verdict · cause (as today, 304-306).
  - proposed edit: the field changes + rationale (as today, 309-322),
    **no percentage**.
  - **Action column, right, decided by verdict**:
    - DERIVED → "Approve & apply" + "Reject" (switch ON); switch OFF →
      "Apply actions dormant — Settings › Agent" and no form.
    - SPECULATIVE → "Open recipe" (the claim page, where the recipe is
      edited) + "Reject". No apply form.
    - SEMANTIC → "Refused — this changes what the claim asserts. Route
      to requirement `<key>`" with the requirement link, + "Dismiss".
      Destination resolution reuses the regenerate path's read
      (`repair_agent.py:508-513`: the `generated_from` link's
      `external_key`): `req-N` → `/requirements/N`; a Jira key →
      `/requirements?q=<key>`; a dangling key renders the key text with
      "no requirement record" (the absence rule — never a blank).
- Server side mirrors the surface: `decide_proposal(approve=True)`
  refuses any row whose verdict is not `DERIVED`, whose grounding source
  is empty, or whose tenant switch is OFF — returning
  `{ok: False, error: "<verdict>: not applicable — ..."}`; the route
  flashes it. Hiding the button is presentation; the refusal is the
  control.

---

## e. EXISTING ROWS — retro-classification

An idempotent script `python -m primeqa.intelligence.repair_gate retro
--tenant-id N` (non-secret argv), run on scratch in the suite and on
prod at deploy (§f):

- Scope: every `repair_proposals` row (all 132 on tenant 1), applied
  rows included — an applied row gains a verdict for the record; no
  reversal is performed by the script (**Decision D3**: whether the
  three predicted-refused auto-applied edits are reverted through the
  preserved prior recipe version is AK's call, a separate act).
- Inputs per `recipe_edit` row: the recipe at the run's pinned
  `recipe_version_seq` (`s4_execution_runs.recipe_id/recipe_version_seq`
  → `coordinator.get_recipe_version`, `coordinator.py:1222`), the run's
  failed create error, the claim's current asserted fields, S1 at the
  CURRENT sequence (the run does not stamp an org sequence — Phase 2's
  gap — so `grounding_source.s1_seq` records the sequence actually used
  and `s1_as_of = "current"`). Measured availability on prod for the 27
  recipe-edit rows: pinned recipe version present **27/27**; failed
  create step present **7/27**; `error_fields` named **4/27**; claims
  with semantic conditions **8/27**. So at most 7 rows can reach R1/R2;
  the other 20 are SPECULATIVE or SEMANTIC by construction.
- Non-recipe rows (105): DERIVED by construction (D1).
- Unclassifiable → SEMANTIC, grounding `{reason}`, quarantined: never
  applicable, rendered refused.
- Idempotent: a row is written only when `gate_verdict IS NULL` or
  `classifier_version` differs; a second run reports identical counts
  and zero writes. Output: counts by (status, kind, verdict) — the table
  AK reviews before switch-on.

---

## f. DEPLOY-DAY / DORMANT-FIRST

The switch: `tenant_agent_settings.repair_gate_apply_enabled BOOLEAN NOT
NULL DEFAULT false` (public, additive). It gates **both** apply paths —
the human route and the auto pass — because dormant-first means no
apply anywhere until AK has seen the counts. Superadmin-only on
`/settings/agent`, audited to `activity_log` on change.

Order of acts, each read back before the next:

1. Pre-flight classification + dump (§g).
2. Public 069 (additive) → tenant migration 20260906_0010 → read-backs.
3. Merge `--no-ff` → push → four services SUCCESS → public 070 (the
   destructive drop, only now that no running ORM maps the columns) →
   read-backs. From this moment:
   every new proposal carries a verdict; `NULL` verdict (pre-retro rows)
   is not applicable; the switch is OFF, so no apply path is reachable
   by anyone; the auto pass returns dormant; the panel shows verdicts
   and "Apply actions dormant".
4. Retro-classification on prod; the counts table; secret scan.
5. HOLD. AK reviews the counts.
6. **Switch-on is its own GO**: AK (or I, on that GO) flips
   `repair_gate_apply_enabled` through `/settings/agent`; the audit row
   is the record. Only DERIVED rows with a grounding source become
   applicable, by human click; the auto pass additionally needs
   `repair_auto_apply` (see the ops lean in §0).

There is no window in which an unclassified proposal is applicable: old
code never reads the new columns (additive migration first); new code
refuses `NULL` and non-DERIVED; the switch is OFF until step 6.

---

## g. MIGRATIONS — classified at pre-flight

| migration | content | class | policy |
|---|---|---|---|
| tenant alembic `20260906_0010_repair_gate.py` (head after `20260904_0010`) | `repair_proposals` + `gate_verdict TEXT NULL CHECK IN ('DERIVED','SPECULATIVE','SEMANTIC')` (named apart from the existing S6 `verdict` column), `grounding_source JSONB`, `classified_at TIMESTAMPTZ`, `classifier_version TEXT`; index `(status, gate_verdict)`; table comment updated (confidence non-decisional) | ADDITIVE | dumpless on its own (D-476) |
| public `069_repair_gate_settings.sql` | `ADD COLUMN IF NOT EXISTS repair_gate_apply_enabled BOOLEAN NOT NULL DEFAULT false` — applied BEFORE the deploy (the old ORM does not map it; harmless) | ADDITIVE | dumpless on its own; the whole merge is dump-first because of 070 |
| public `070_repair_gate_drop_thresholds.sql` | `DROP CONSTRAINT IF EXISTS trust_bands_sane`; `DROP COLUMN IF EXISTS trust_threshold_high, trust_threshold_medium` (D2) — applied ONLY AFTER every service runs the new code (the old ORM maps both columns and the LLM gateway loads that row per call; the new ORM does not map them) | **DESTRUCTIVE** | **dump-first** (D-285 MIGRATE-FIRST, D-476) |

`gate_verdict` is nullable at migration time on purpose: a default verdict
would be a lie about 132 existing rows. Retro-classification fills it;
the verification asserts zero `NULL` after retro; a follow-up may set
`NOT NULL` once every tenant has run retro.

---

## h. Decisions for AK (forks, with leans)

- **D1 — non-recipe kinds.** Lean: DERIVED by construction with a
  recorded grounding (§a). Alternative: keep `rerun`/`regenerate`
  outside the gate — rejected because "every proposal carries a
  verdict" is the invariant the tests prove.
- **D2 — `trust_threshold_high`.** Lean: drop it with `medium` (§b).
- **D3 — the three predicted-refused auto-applied edits.** Lean: leave
  applied, verdict recorded; revert only on AK's word (the prior recipe
  version is preserved, so it is reversible).
- **D4 — R2's `sole_active` clause.** A picklist with exactly one active
  value makes the remedy derived by exhaustion. Lean: include it; the
  grounding names it, so it is auditable. Alternative: default-only.
- **Immediate ops lean (§0):** `repair_auto_apply` OFF on tenant 1
  today, by AK.

---

## i. Verification plan (VERIFICATION_STEP_A.md on the implementation GO)

DB-real (scratch `plimsol_3a3`; `tenant_1.repair_proposals` exists with
0 rows; `tenant_agent_settings` is planted from the migration files by
the suite fixture since scratch's public schema lacks it):

1. A remedy touching a claim-asserted field → `SEMANTIC`, refused, the
   requirement destination resolved (req-N, Jira key, dangling — all
   three).
2. `SPECULATIVE` renders no apply form; `POST /runs/substrate/repairs/<id>`
   with `action=approve` returns the refusal and writes nothing.
3. `DERIVED` requires a recorded grounding source — a planted DERIVED
   row with empty grounding is refused by the route and by the auto
   pass.
4. A bare staged key on a touched field → `SEMANTIC` (fail closed).
5. R1 and R2 positive cases against planted S1 facts (createable=false;
   absent field; default picklist value; sole active value).
6. The auto pass ignores confidence: planted 0.99 `SPECULATIVE`, flags
   ON, switch ON, sandbox env → not applied.
7. `agent_enabled = false` → triage writes zero rows and logs once.
8. The tick writes zero unclassified rows (every inserted row has a
   gate_verdict + classifier_version).
9. Retro-classification idempotent: run twice, identical counts, zero
   writes on the second run.
10. Switch OFF: route refuses DERIVED; auto pass returns dormant.
11. Fixture screenshots (real browser over the real app on scratch, the
    report-slice precedent): the panel with a DERIVED, a SPECULATIVE and
    a SEMANTIC row; the consolidated settings page; the llm-usage note.
12. Unit: classifier rules as a pure table (input shape → verdict);
    normalisation parity with `_coverage_pinned_fields`.
13. Suites: full merge-gate suite (`tests/unit`), the D-468 DB-real set
    (thirteen suites + this slice's), pages, browser-gated.

Merge: standard runbook, dump-first (the destructive drop). Then
switch-on: its own GO after AK reviews the retro counts.

---

## j. Residual, stated plainly

- Successor-field derivation is not buildable until S1 records lineage;
  every rename/add remedy is SPECULATIVE in v1.
- S1 facts are read at the CURRENT sequence, not the run's — the run
  stamps no org sequence (Phase 2). The grounding records which
  sequence it used.
- `automation_effect_*` proposals (19 of 27 on prod) can never be
  DERIVED with today's inputs: no platform error string exists when the
  create succeeds. The gate is honest about that; a future derived
  signal (the automation's recorded entry criteria in S1) would be its
  own slice.
- The applied-row reversal (D3) and the `NOT NULL` tightening are
  follow-ups, not this slice.
