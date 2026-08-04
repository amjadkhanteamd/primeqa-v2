# AfterSave Flow perturbation set — PLAN ONLY, needs an attended GO

> **Nothing here has been executed.** No org write, no run, no sync, no deploy.
> This document plans the perturbations that would satisfy
> `FEATURE_CAMPAIGN.md` §3 criterion 3 (**≥1 demonstrated decidable red per
> shape family**) for the campaign's #1-ranked feature, AfterSave Flows.
>
> **It also requires a may-touch extension that does not exist yet** (§1.3).
> Every candidate below edits *flow logic*; the signed-off protocol permits
> only flow *deactivation*. Executing any of §2 without AK's written scope
> extension violates protocol ground rule 5.
>
> **Measurement stamp.** Branch `docs-campaign-definition @49e5191`, tree clean;
> `main` unchanged at `2717da6`; `phase-1-s3-representation-checks` untouched at
> `ed802a5` (all VERIFIED by `git rev-parse`). Org facts measured 2026-08-01
> against `tenant_1` / env-59 (`902850e3-89c0-4d74-9141-66084045f439`) at
> current versions; code facts read at `main@2717da6`.

---

## 1. What the protocol actually specifies

Source: `docs/architecture/perturb_and_restore_protocol.md` (89 lines, **ACTIVE**,
§2 list signed off 2026-06-12) + the execution history in
`docs/architecture/dogfood_matrix_log.md`.

### 1.1 The six ground rules (§1, "non-negotiable")

1. **One perturbation live at a time** — attribution must be unambiguous.
2. **Record before touching** — capture current state into the session log, so
   restore is verified against a recorded baseline, not memory.
3. **Restore immediately after capture** — the window is *one engine
   observation*, not a work session.
4. **Re-sync + re-verify after restore** — fresh S1 sync → synced state matches
   baseline → one green re-run. "The sandbox is not restored until the engine
   itself observes fixture state again."
5. **Fixture-state artifacts only** — only §2 artifacts; anything else is out of
   scope **without a new sign-off**.
6. **No data perturbations** — metadata toggles/edits only.

Plus §4's five-item restore checklist and §5's exclusions: **no production org,
ever**; no user/profile surgery beyond P5; **no deletes** (deactivate/edit
only); no schedule-window overlap.

### 1.2 What is signed off, and its state today

| # | Artifact | Perturbation | Status (VERIFIED) |
|---|---|---|---|
| P1 | Flow `SQ205_Create_Case_SLA` | **Deactivate** | **Executed twice.** Window 1 exposed D-227.7 (a 0-row side-effect read errored instead of grading); window 2 captured `automation_not_triggered` live |
| P2 | VR `Opportunity.Contract_Value_Required_On_Closed_Won` | toggle `active=false` | **ON HOLD.** The signed artifact is exercised by **no approved behavioural claim** — toggling it flips nothing. The log requests substituting the Opportunity **`Amount`** VR (the claim that flips is `71583230`). Independently confirmed: the signed VR is **already `is_active=false`** in S1 |
| P3 | A VR with a derivable formula | edit the error-condition formula | **Executed** on `Opportunity.Amount`; captured `prohibition_not_enforced` and exposed Finding #2 (an unrelated `TODAY()` VR masked the precise `vr_formula_drift`) |
| P4 | Field `Case_SLA__c.Response_Hours__c` | change length/precision | not executed |
| P5 | Permission set of an FLS-restricted user | remove a field read | not executed — **AK must create the user first** |

**What P2's pending extension covers:** substituting a *different validation
rule* (Opportunity `Amount`) as the toggle target, so the `vr_inactive` cause
can be captured at all. **What it explicitly excludes:** everything else — it is
a one-line swap of one VR for another within the existing P2 row. It does **not**
extend to flows, to logic edits, or to any new artifact class.

### 1.3 THE GAP — the protocol does not cover flow metadata changes

**Stated plainly: no signed-off entry permits editing a flow's logic.**

- P1 is the only flow row, and its perturbation is exactly one thing —
  *deactivate* (status → Draft / `activeVersionNumber: 0`). It changes **no**
  decision boundary, assignment value, formula or entry criterion.
- P3 covers editing a **validation-rule formula**; P4 a **field property**;
  neither generalises to flows (ground rule 5 admits no analogy — "only the
  artifacts in §2").
- Consequently **every candidate in §2 is out of scope today** and needs a new
  AK sign-off adding a P6-class row. The draft wording is in §7.

There is a second, subtler gap. P1's flow perturbation is the *"didn't fire"*
case — which `FEATURE_CAMPAIGN.md` notes already has natural specimens and which
D-425 handles as `automation_inactive` / `automation_effect_record_absent`. The
campaign's stated preference is the **harder** case: *fired and wrote the wrong
thing* → `automation_effect_divergent`. **No signed-off perturbation can produce
a divergent value.** That is precisely the hole this plan asks to fill.

### 1.4 Two operational lessons the log already paid for

- **`sf project deploy` silently no-opped.** P1's source-deploy path "was
  skipped by source tracking — *Unchanged*"; the reliable mechanism was a
  **Tooling API PATCH**. A logic edit *does* change file content so source
  deploy should engage — but "deploy reported success" is **not** evidence the
  org changed. Every perturb and every restore must be verified by **reading the
  metadata back** (§4.3).
- **A metadata edit needs an intervening S1 sync before the run** — P3's
  correction: without it S6 evaluates the *stale* formula and grades a false
  defect. **This does not apply to flow-logic edits** (see §4.2: S1 captures no
  flow logic, so S6 cannot be stale about it) — but it *does* apply to the
  version-drift detection, which is the only automatic restore check available.

---

## 2. Candidate perturbations — one per AfterSave shape family

The AfterSave effect-shape families (`FEATURE_CAMPAIGN.md` §2.2), and one
candidate each. **The expected-cause column is the gate**: a candidate whose
expected cause is a hedge proves nothing and is marked DO-NOT-RUN.

| # | Shape family | Flow | Claims | Expected cause | Verdict |
|---|---|---|---|---|---|
| **F1** | cross-object parent aggregate | `PLS_FB_FL07_Order_Rollup` | 1 | `automation_effect_divergent` | ✅ **decidable** |
| **F2** | cross-object creation, temporal field | `PLS_FB_FL04_Confirmation_Task` | 3 (1 affected) | `automation_effect_divergent` | ✅ decidable |
| **F3** | set cardinality | `PLS_FB_FL05_Cancellation_Sync` | 1 | `automation_effect_divergent` | ✅ decidable |
| **F4** | same-record field effect | `PLS_FB_FL09_Reopen_Guard` | 2 + 1 transition | `automation_effect_divergent` | ✅ decidable |
| **F5** | cross-object creation, status field | `SQ205_Create_Case_SLA` | 5 | `automation_effect_divergent` | ✅ decidable, high collateral |
| **F6** | same-record banding | `HL_Auto_Risk_Rating` | 22 | `automation_effect_divergent` | ⚠️ decidable, **collateral 22** |
| **F7** | value-free stamp | `SQ205_Escalation_Effects` | 2 | `automation_effect_value_absent` | ❌ **DO NOT RUN — hedged** |
| **F8** | designed absence | `HL_Auto_Risk_Rating` / `HL_High_Risk_Task` | 3 | **no cause_kind at all** | ❌ **DO NOT RUN — see §5.2** |

### F1 — `PLS_FB_FL07_Order_Rollup` (RECOMMENDED, see §6)

- **Claim:** `c6c4d1e1` (approved, automation-effect). Recipe creates an order,
  two order lines, then reads the **parent order** and asserts
  `read-effect.PLS_FB_Line_Count__c == 2`.
- **Exact change:** `sandbox_fixtures/pls_fb_benchmark_v1/force-app/main/default/flows/PLS_FB_FL07_Order_Rollup.flow-meta.xml`
  **line 20** — the `varCount` accumulator increment inside the `Accumulate`
  assignment: `<numberValue>1.0</numberValue>` → `<numberValue>2.0</numberValue>`.
  One token. `varTotal` (line 13) is **not** touched, so
  `PLS_FB_Order_Total__c` stays correct and the blast is confined to one field.
- **Why it fires anyway:** the loop, the parent lookup and the record update are
  untouched — the flow runs to completion and writes. It writes **4**.
- **Expected S6 cause — `automation_effect_divergent`.** Trace: read-effect
  returns 1 row → `observed_kind='field_value'`, `observed_value=4`,
  `asserted_value=2`; `observed_value` is numeric so the Id-shape branch cannot
  fire; `_classify_effect_observation` → `divergent`
  (`attribution.py`, D-425). Verdict `automation_not_triggered`.
- **What the human should read:** *"active automation exists on
  PLS_FB_Order__c (…); a different value was observed — PLS_FB_Line_Count__c was
  asserted 2 but the org holds 4. Something wrote a value other than the
  asserted one: the grounding automation under different logic, or another
  writer (…; Apex triggers are not captured in the org model, so candidate
  writers cannot be exhaustively enumerated)."*
- **Restore:** `git checkout` the fixture file (it is committed and unmodified),
  redeploy, then **verify by retrieve-and-diff** (§4.3) — not by the deploy's
  own success message.

### F2 — `PLS_FB_FL04_Confirmation_Task`

- **Claims:** `17044ee4` (asserts `PLS_FB_Due_Date__c == RUN_DATE + 3` via the
  relative-date envelope), plus `5117f355` / `9c80f1d7` (both `exists` only).
- **Exact change:** the flow's formula `fDueDate`, line 9:
  `{!$Flow.CurrentDate} + 3` → `+ 5`.
- **Expected cause — `automation_effect_divergent`**, and uniquely valuable: it
  exercises the **C4 temporal materialisation** path, so the envelope carries
  `asserted_value` (the materialised date), `asserted_value_symbolic` (the
  `$relative_date` dict) and `observed_value` (the +5 date). The observed string
  is a 10-char date, not Id-shaped, so it lands on `divergent`.
- **Collateral:** the two `exists` claims still pass — the task is still
  created, only its due date moves. **1 affected claim.**
- **Restore:** revert one character in the committed fixture; retrieve-and-diff.

### F3 — `PLS_FB_FL05_Cancellation_Sync`

- **Claim:** `feaea316` (approved), steps `create-record, create-child-1,
  create-child-2, create-distractor, update-record, read-effect, assert-effect`;
  asserts `count_equals 2` on the cancelled-task set.
- **What the claim counts (VERIFIED, and it dictates the lever):** the read is
  `SELECT Id, PLS_FB_Status__c FROM PLS_FB_Fulfilment_Task__c WHERE
  PLS_FB_Order__c = '$create-record.id' AND PLS_FB_Status__c = 'Cancelled'`.
  So the count is driven by the **`Cancel_Open_Tasks` update's
  `inputAssignments` value**, not by the `Get_Open_Tasks` lookup filter — the
  flow has both, and only the assignment changes what the read sees.
- **Exact change:** `Cancel_Open_Tasks` → `inputAssignments` →
  `<stringValue>Cancelled</stringValue>` → `<stringValue>Completed</stringValue>`.
  The flow still fires and still updates both tasks; they land on `Completed`,
  so the claim's read matches **0** rows instead of 2.
- ⚠️ **`Completed` is not an arbitrary choice.**
  `PLS_FB_Fulfilment_Task__c.PLS_FB_Status__c` is a **restricted** picklist whose
  captured active set is exactly `{Cancelled, Completed, Open}` (capture mark
  `inline`, VERIFIED). An out-of-set value (`Void`, `Blocked`, …) would make the
  flow's update fail at runtime with
  `INVALID_OR_NULL_FOR_RESTRICTED_PICKLIST`, which fails the *triggering* DML →
  the run **errors** → verdict `not_evaluated` → **the window teaches nothing**.
  See §5.2 — this applies to every picklist-valued perturbation.
- **Expected cause — `automation_effect_divergent`.** Two VERIFIED
  preconditions: (i) the recipe has a `create-distractor` step but **no
  `create-protected` step**, so the D-381 conditional-absence vocabulary does
  *not* engage (`interpreter.py` keys it on `step_id == "create-protected"`) and
  the claim keeps the plain automation-effect vocabulary, whose failed verdict
  **is** enriched; (ii) the `count_equals` producer branch sets
  `observed_kind='row_count'` **unconditionally**, including at 0 rows
  (`data_executor.py` `_run_ground`) — so a 0-row result is `row_count 0`, never
  `no_row`, and the classifier returns `divergent`, not a hedge.
- **Collateral: 1.**

### F4 — `PLS_FB_FL09_Reopen_Guard`

- **Claims:** `3a265a5e`, `918fd8eb` (both assert the audit-log
  `PLS_FB_Kind__c == 'Reopen'`) **and** `daf9be04` — the approved
  state-transition claim asserting `PLS_FB_Order__c.PLS_FB_Reopened__c == true`
  (the D-386.1 live-green specimen).
- **Exact change:** `Mark_Reopened` assignment, line 13:
  `<booleanValue>true</booleanValue>` → `false`.
- **Expected cause — `automation_effect_divergent`** on `daf9be04`
  (`observed_value=False`, `asserted_value=True`; verdict
  `state_not_transitioned`, which **is** in `_POSITIVE_ENRICHED`). Note the
  claim carries no `automation` ref, so attribution takes the unbound
  flow-scan fallback — still refined by D-425.
- **Collateral:** 1 affected (`daf9be04`); the two `Kind__c` claims are
  unaffected (the audit log is still created with `Reopen`).
- **Value:** this is the only candidate that would **re-red a claim D-386.1
  proved green**, which is a strong end-to-end signal — and a strong argument
  for running it *second*, not first.

### F5 — `SQ205_Create_Case_SLA`

- **Claims:** 5 approved, all asserting `read-effect.Status__c == 'Active'` on
  the created `Case_SLA__c`.
- **Exact change:** the record-create's `Status__c` input assignment
  `'Active'` → **`'Breached'`**. `Case_SLA__c.Status__c` is a **restricted**
  picklist with captured active set `{Active, Breached, Met}` (VERIFIED) —
  `'Pending'` would error the create instead of diverging its value (§5.2).
- **Expected cause — `automation_effect_divergent`** (observed `'Breached'`,
  8 chars, not Id-shaped).
- **Collateral: 5** — all identical, so the ledger stays legible, but it is
  five reds for one lesson. Also the P1 artifact: perturbing it twice in
  different ways muddies that row's history.

### F6 — `HL_Auto_Risk_Rating`

- **Claims: 22 approved** across bands Low/Medium/High + 3 drafts.
- **Exact change:** shift a band boundary in the decision element.
- **Expected cause — `automation_effect_divergent`**, and it is the campaign's
  canonical shape (`9ba2d3d2` lives here).
- **Collateral: up to 22.** Defer until the protocol is proven on a
  1-claim flow.

### F7 — `SQ205_Escalation_Effects` — ❌ DO NOT RUN

- Claims `8fa528fc`, `be56416d` assert `not_null` on
  `Account.Last_Escalation_Date__c`.
- **Every** way of breaking this stamp — removing the assignment, retargeting
  the field, blocking the entry — ends with the asserted field observed **null**
  on a row that exists → `observed_kind='field_value'`, `observed_value=None`
  → **`automation_effect_value_absent`**, the D-425 ambiguous-null hedge.
- Per the campaign's own rule, a perturbation whose expected cause is a hedge
  proves nothing. **The value-free stamp shape cannot be shown decidable until
  the parked sibling-field-capture arc lands** (D-425.1).

### F8 — designed absence (`not_exists`) — ❌ DO NOT RUN (as a cause test)

- Claims `66462fbe`, `e9193a7e` (HL_Auto_Risk_Rating), `6632f29d`
  (HL_High_Risk_Task).
- Widening entry criteria so the automation fires where it must not gives
  verdict **`automation_fired_unexpectedly`** — which is unambiguous and
  self-describing, **but carries no `cause_kind`**: `_POSITIVE_ENRICHED` is
  `("automation_not_triggered", "state_not_transitioned",
  "value_not_persisted")` only (`attribution.py:59-60`), so the absence-mirror
  verdicts are never enriched and `cause` stays `None`.
- **This is a real gap in `FEATURE_CAMPAIGN.md` §3.1 criterion 3**, which
  demands a cause from the decidable set. Two ways out, both AK/TA calls, both
  out of scope here: (a) amend criterion 3 to accept a self-describing verdict
  where no cause is defined; (b) extend `_POSITIVE_ENRICHED` to the absence
  mirror. Until one lands, this family cannot satisfy the exit criterion **by
  construction, not by lack of effort**.

---

## 3. Blast radius

Measured over the **185 approved** claims. *Bound* = claims whose
`automation.external_id` is that flow. *Same object* = other approved claims
whose recipe body names the object (a weak upper bound — a flow-logic change
does not affect a VR claim on the same object unless it writes a field that
claim reads). *Affected* = the count I actually expect to flip, reasoned per
candidate from the fields the perturbation touches.

| # | Flow | Bound | Same object | **Affected (expected)** |
|---|---|---|---|---|
| **F1** | `PLS_FB_FL07_Order_Rollup` | **1** | 28 | **1** ✅ |
| F3 | `PLS_FB_FL05_Cancellation_Sync` | 1 | 28 | **1** ✅ |
| F2 | `PLS_FB_FL04_Confirmation_Task` | 3 | 26 | **1** ✅ |
| F4 | `PLS_FB_FL09_Reopen_Guard` | 2 | 27 | **1** (+2 unaffected) |
| F7 | `SQ205_Escalation_Effects` | 2 | 16 | 2 — *not run* |
| F5 | `SQ205_Create_Case_SLA` | 5 | 11 | **5** ⚠️ |
| F6 | `HL_Auto_Risk_Rating` | 22 | 78 | **up to 22** ⚠️ |
| F8 | `HL_High_Risk_Task` | 11 | 89 | up to 11 — *not run* |

Two measurement caveats, stated so the numbers are not over-trusted:

- The *same object* column is a **substring match over recipe bodies** and
  over-counts badly for generic names (`HL_High_Risk_Task`'s "same field" probe
  on `Subject` matched 171 claims because `subject_ref` contains the token).
  It is an upper bound, not a prediction. **UNKNOWN** precisely which of those
  would flip without running them.
- `PLS_FB_Order_Total__c`, the other field FL07 writes, is asserted by
  **0 approved claims** (VERIFIED) — so even a botched FL07 edit that disturbed
  `varTotal` would flip nothing extra.

---

## 4. Restore failure — the safety analysis

### 4.1 Is restore deterministic?

> **CORRECTION (2026-08-02, verifier run — supersedes the two claims below it
> corrects; the original text is retained for the record).**
> The read-half verifier this section demanded was built
> (`scripts/verify_flow_fixture.py`) and run against all 8 candidates in their
> unperturbed state. Two of this section's load-bearing claims are wrong:
>
> 1. **"Every candidate flow exists as committed source" is FALSE for the HL
>    family.** `sandbox_fixtures/home_loan/` is **untracked** (`git ls-files`
>    returns only `pls_benchmark_v1`, `pls_fb_benchmark_v1`, `sq205`).
>    `HL_Auto_Risk_Rating`, `HL_High_Risk_Task` (and `HL_Auto_Submit_Approval`)
>    have **no committed baseline**; `git checkout`-restore does not exist for
>    them. F6/F8 are out of any committed-fixture scope until that directory
>    is committed.
>    **RESOLVED 2026-08-04 (`chore-commit-hl-fixtures`):** the directory is
>    now tracked (22 vetted files; nothing sensitive found). Pre-commit
>    org-match: `HL_Auto_Risk_Rating` and `HL_High_Risk_Task` logic-identical
>    to the org; `HL_Auto_Submit_Approval`'s working-tree copy was STALE —
>    the ORG had since been FIXED (entry criteria now `Approval_Status__c IS
>    NULL AND Loan_Amount__c > 5000000`, require-record-changed — the
>    `d49719e2` dead-config repair), so that one file was committed as the
>    org's retrieved state rather than the stale authored copy. The verifier's
>    repo-logic signal now reports `logic matches committed fixture` for both
>    P6-relevant HL flows.
> 2. **Byte-diff against the hand-authored fixture is NOT a usable
>    verification, even unperturbed.** Verifier result: **5 of 6 tracked
>    candidates are byte-DIVERGENT from committed source right now** (only
>    `PLS_FB_FL05_Cancellation_Sync` is IDENTICAL — its fixture happens to be
>    in canonical form). Semantic characterization: **all 8 flows are
>    logic-identical to their fixtures** — every byte delta is Metadata API
>    canonicalization (element reordering, whitespace, and elision of
>    default-valued elements: the org omits `storeOutputAutomatically=false`
>    on retrieve). Zero org drift; the *baseline design* was wrong, not the
>    org.
>
> **Corrected restore-verification design:** the byte-stable baseline is a
> **retrieve-at-window-open snapshot** (canonical vs canonical round-trips
> byte-identically), so a P6 window must: retrieve → record the canonical
> baseline → perturb → observe → restore → retrieve again → **byte-diff
> close-retrieval against open-retrieval**. The committed fixture remains the
> *semantic* source of truth but is not the byte baseline. Consequently the
> P6 row was **NOT drafted into the protocol** (the plan's §7 wording is
> superseded on the verification clause); it awaits AK's review of this
> correction. The original §4.1–§4.3 text below describes the superseded
> design.

**Yes, for all six runnable candidates — and this is the strongest safety fact
in the plan.** Every candidate flow exists as **committed source** in the repo:

```
sandbox_fixtures/pls_fb_benchmark_v1/force-app/main/default/flows/*.flow-meta.xml
sandbox_fixtures/sq205/force-app/main/default/flows/*.flow-meta.xml
sandbox_fixtures/home_loan/force-app/main/default/flows/*.flow-meta.xml
```

Restore is therefore **`git checkout <file>` + redeploy**, a byte-exact return
to a version-controlled baseline — not "restore the recorded original from the
session log" (P3's mechanism, which depends on transcription fidelity). The
perturbation itself should be made **as a working-tree edit that is never
committed**, so `git status` showing a clean tree *is itself* evidence that the
source-side perturbation is gone.

It does **not** depend on a half-completable round trip: the flow file is a
single XML document deployed atomically. The realistic failure is not "half
deployed" but **"not deployed at all"** — precisely P1's `Unchanged`
source-tracking no-op (§1.4). That failure is *safe on perturb* (nothing
changed) and *dangerous on restore* (the org keeps the perturbation while the
operator believes it is restored). §4.3 is the answer to that.

### 4.2 Is the pre-perturbation state capturable? — S1 is NOT sufficient

**VERIFIED: `flow_details.parsed_logic` is NULL for every candidate**
(`PLS_FB_FL07_Order_Rollup`, `PLS_FB_FL04_Confirmation_Task`,
`PLS_FB_FL05_Cancellation_Sync`, `SQ205_Create_Case_SLA` — all
`has_parsed = false`, length 0). S1 holds only: `sf_api_name`, `flow_type`,
`trigger_type`, `is_active`, `version_number` (=1 for all four), and the
description-derived summary.

Three consequences, all load-bearing:

1. **S1 cannot verify a logic restore.** Protocol §4's "synced S1 state matches
   baseline" check is *vacuous* for flow logic — S1 would report an identical
   row whether or not the logic came back. The protocol's own restore checklist
   is therefore **insufficient as written for this class of perturbation**.
2. **The repo is the baseline**, not S1 and not the session log. Sufficient
   because the fixtures are the deployed source of truth; the committed XML *is*
   the recorded pre-state.
3. **A pre-flight `sf project retrieve` is still required** — to prove the org
   currently matches the repo *before* perturbing. If the org has drifted from
   the fixture (an out-of-band edit), "restore to repo" would silently change
   behaviour rather than restore it. **UNKNOWN today whether org == repo for
   these four flows**; establishing it is step 0 of the window.

### 4.3 Detection if a restore silently fails

Ranked strongest first. The protocol's existing checks are **not** sufficient
(§4.2), so this list is an addition the extension must carry.

1. **Retrieve-and-diff (authoritative).** After restore, `sf project retrieve`
   the flow and `diff` it byte-for-byte against the committed fixture. This
   compares *actual org state* to the source of truth and is the only check
   immune to a deploy that reported success without acting.
2. **`version_number` drift in S1 (automatic, cheap).** Every candidate is at
   `version_number = 1` today. A perturb deploy makes 2, a restore deploy makes
   3. A post-window S1 sync showing a version ≠ the recorded expectation is a
   loud signal that *something* was deployed — it cannot say *what*, but paired
   with (1) it closes the loop.
3. **The claim re-run (protocol §4, already required).** A green re-run of the
   affected claim proves the behaviour is back. Weakest of the three because it
   is the check most likely to be skipped when a session is cut short — which is
   exactly when a restore fails.
4. **Clean `git status`.** Proves the *source* is unperturbed. Necessary, not
   sufficient — it says nothing about the org.

**The honest residual risk:** if the operator perturbs, observes, and then the
session ends abnormally before restore, **nothing automatic notices.** No
scheduled job compares org flow logic to the fixtures — S1 cannot, because it
does not capture logic. The daily 06:00 UTC run schedule that might otherwise
have surfaced a stuck red is **currently disabled** (`s4_run_schedules` id=1,
env 59, `0 6 * * *`, `enabled = false`, last fired 2026-06-17 — VERIFIED), so
there is no ambient re-run to catch it either. This is the single strongest
argument for §4.4.

### 4.4 Scoping so a failed restore is inert

**Yes, and it should be the selection criterion.** Choose a flow whose *entire*
approved footprint is one claim, and an unrestored perturbation leaves exactly
one test red — loudly, attributably, and touching nothing else. **F1 (FL07) has
exactly this property:** 1 bound approved claim, 0 other approved claims
asserting either field it writes. A stuck F1 perturbation degrades the corpus
by one known claim whose cause string names the field and both values.

By contrast a stuck **F6** (HL_Auto_Risk_Rating) would leave **up to 22**
approved claims red with a plausible-looking divergent cause each — the failure
mode that would poison the ledger the campaign was just built to make honest.

---

## 5. What cannot be perturbed safely

### 5.1 Not safely, or not usefully

| Flow / shape | Why | Alternative |
|---|---|---|
| `SQ205_Escalation_Effects` (value-free stamp) | Every failure mode lands on the ambiguous-null hedge (F7) | Wait for the parked sibling-field-capture arc (D-425.1); until then this shape's exit criterion is **unmeetable** and should be recorded as such rather than faked |
| Designed absence (`not_exists`, F8) | Produces no `cause_kind` — the verdict is enriched by nothing | Amend criterion 3 to accept self-describing verdicts, **or** extend `_POSITIVE_ENRICHED`. AK/TA call |
| `HL_Auto_Risk_Rating` (F6) | Collateral up to 22 approved claims | Run only after the protocol is proven on a 1-claim flow; or first narrow the fixture so fewer claims share the boundary |
| `HL_High_Risk_Task` | 11 bound claims; also the historical home of the label-vs-Id defect (`0d81c6f9`/`6ee124fd`) — a red here is ambiguous between the perturbation and known claim defects | Prefer a flow with no defect history |
| `HL_Auto_Submit_Approval` | Already carries a **live unfixed org defect** (dead entry criteria, `d49719e2`) — perturbing an already-broken artifact cannot yield a clean signal | Fix the org defect first (AK's pending task), then treat its red as a *natural* specimen — no perturbation needed |
| Autolaunched flows (14) | No DML entry point, 0 claims, 0 runs — nothing to perturb *into* | Out of scope until a requirement targets them |
| Scheduled flows | **None exist on env-59** to perturb, and FL10's execution model is an open AK decision | Blocked upstream |
| Anything on env-78 / any production org | Protocol §5: "No production org. Ever." | — |

### 5.2 A perturbation-design rule this plan discovered

**Any perturbation that writes a picklist value MUST use a member of that
field's captured active set.** The fixture objects are built on **restricted**
picklists — VERIFIED: `PLS_FB_Fulfilment_Task__c.PLS_FB_Status__c` =
`{Cancelled, Completed, Open}`, `Case_SLA__c.Status__c` =
`{Active, Breached, Met}`, `PLS_FB_Audit_Log__c.PLS_FB_Kind__c` =
`{AsyncEnrichment, CloseoutFault, LedgerFault, Reopen}`, all `restricted=true`.

An out-of-set value does not produce a divergent read — it makes the flow's DML
fail with `INVALID_OR_NULL_FOR_RESTRICTED_PICKLIST`, which fails the triggering
transaction, so the run comes back **errored / `not_evaluated`** and the window
is wasted. My first drafts of F3 (`'Void'`) and F5 (`'Pending'`) both had this
defect; both are corrected above.

The authority to check against is S1's own picklist capture — complete at
**377/377 fields decidable** since D-414 — so this check is a query, not a
guess. It should be a step-0 item in every future perturbation of this class.

### 5.3 A structural note for the campaign

Two of eight AfterSave shape families (**F7 value-free stamp**, **F8 designed
absence**) **cannot currently reach a decidable cause by any perturbation**.
That is not a planning failure; it is a measurement of where S6's attribution
still stops. `FEATURE_CAMPAIGN.md` §3 should be amended to record that
AfterSave's exit is **6 of 8 families achievable today**, with the remaining two
gated on: the sibling-field capture arc (F7) and an enrichment/criterion
decision (F8). Recording that honestly is preferable to an exit that quietly
counts 6 as 8.

---

## 6. The one-perturbation recommendation

### **F1 — `PLS_FB_FL07_Order_Rollup`, `varCount` increment `1.0` → `2.0`**

This is the pilot for the protocol extension itself, chosen on the three
criteria in priority order:

1. **Lowest collateral in the set — 1.** One bound approved claim
   (`c6c4d1e1`); **0** other approved claims assert `PLS_FB_Line_Count__c`;
   **0** assert `PLS_FB_Order_Total__c`, the other field the flow writes. A
   failed restore is *inert* by construction (§4.4) — the property no other
   candidate matches as cleanly.
2. **Deterministic, single-token restore.** One `<numberValue>` on line 20 of a
   127-line committed XML; restore is `git checkout` + redeploy + retrieve-diff
   against the same committed bytes. No transcription, no formula grammar, no
   multi-element edit.
3. **The expected cause is genuinely decidable, and it is the *hard* case.**
   `automation_effect_divergent` — the flow **fires and writes the wrong
   value** (4 where 2 was asserted). This is exactly the case D-425 newly
   handles and which has **no natural specimen on an approved claim**: every
   existing divergent red sits on a deprecated claim. It is also numeric, so it
   cannot be confused with the representation-mismatch class.

**Secondary reasons:** F1 writes a **number**, so it is structurally immune to
the restricted-picklist hazard that would have wasted a window on F3 or F5
(§5.2) — there is no legal-value set to get wrong. FL07 is also a
*cross-object parent aggregate* — the shape
D-371's completion program called "the genuinely NEW evidence class" — so the
pilot exercises the most architecturally interesting AfterSave family rather
than the easiest. And it is not the P1 artifact, so `SQ205_Create_Case_SLA`'s
perturbation history stays single-purpose.

**Proposed order once F1 succeeds:** F3 (cardinality, collateral 1) → F2
(temporal, collateral 1, exercises the C4 materialisation envelope) → F4
(re-reds a D-386.1 green, collateral 1) → F5 (collateral 5) → F6 (collateral
22, last).

### The F1 window, step by step

```
[ ] 0. Pre-flight: sf project retrieve FL07 → diff vs committed fixture.
       ABORT if the org has drifted (restore-to-repo would not be a restore).
[ ] 1. Record baseline: S1 version_number (expect 1), the retrieved XML,
       and c6c4d1e1's last green run id — into dogfood_matrix_log.md.
[ ] 2. Perturb: working-tree edit line 20 → 2.0. DO NOT COMMIT. Deploy.
[ ] 3. Verify the perturb landed: retrieve + diff shows 2.0 (guards the
       P1 'Unchanged' no-op).
[ ] 4. One observation: enqueue c6c4d1e1 on env-59 through the deployed
       worker. Expect failed / automation_not_triggered /
       automation_effect_divergent, observed 4 vs asserted 2.
[ ] 5. Restore: git checkout the fixture; deploy.
[ ] 6. Verify restore AUTHORITATIVELY: retrieve + diff == committed bytes.
[ ] 7. S1 sync; record the new version_number (expect 3).
[ ] 8. Re-run c6c4d1e1 → green (automation_triggered).
[ ] 9. Log: perturbation id, both run ids, the cause string as a human
       reads it, restore diff evidence. Clean git status.
```

Schedule-window rule (protocol §5) is **currently moot** — the 06:00 UTC
schedule is disabled (§4.3). Re-check `enabled` before the window rather than
assuming it stays that way.

---

## 7. What AK must approve before any of this runs

A **P6 row** extending the §2 may-touch list. Draft wording:

> **P6** | Fixture Flow logic, one flow per window, from
> `sandbox_fixtures/**/flows/*.flow-meta.xml` | Edit ONE value in the committed
> fixture XML (an accumulator increment, a formula offset, a lookup filter
> value, or an assignment literal) so the flow still fires but writes a
> different value | `git checkout` the fixture + redeploy; **restore verified by
> `sf project retrieve` + byte diff against the committed file**, not by the
> deploy result, and not by S1 (which captures no flow logic)

Also needed, separately and independently of this plan:

- **The pending P2 extension** (substitute the Opportunity `Amount` VR) — a
  one-line approval the dogfood log has been waiting on since the P3 window.
- A decision on **F8** (§5.1): amend criterion 3, or enrich the absence-mirror
  verdicts.

**Nothing in §2 may be executed until P6 is signed.** This document is the
proposal, not the authorisation.
