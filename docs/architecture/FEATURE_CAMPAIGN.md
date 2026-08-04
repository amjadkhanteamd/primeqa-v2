# FEATURE_CAMPAIGN.md — the Salesforce feature-probe campaign, defined

> **Status: ACTIVE — this file is the campaign's source of truth.** Before it,
> no controlling document existed: the word "campaign" appeared nowhere in
> `docs/`, and the only cross-feature ranking lived in an **untracked** scratch
> file (`scratch/VR_ARC_RECON.md` §F, 2026-07-24) that ranked by *S1 capture
> readiness* rather than product priority. That file is a **superseded input**,
> retained unmodified for its seam analysis; where it and this document
> disagree on counts, this document wins (the picklist arc D-403…D-414 moved
> the numbers substantially after it was written).
>
> **Measurement stamp.** Every count below was measured on **2026-08-01**
> against the Railway `tenant_1` schema, org `902850e3-89c0-4d74-9141-66084045f439`
> (env-59 "Prime QA NEW"), at current versions (`valid_to_seq IS NULL` /
> `valid_to IS NULL`), or read from source at `main@2717da6`. Cells are
> **VERIFIED** (query output or `file:line`) or **UNKNOWN** with the reason.
> There are no ASSUMED cells; interpretation lives in the footnotes (§8).
>
> **Scope note.** Originally, D-426 (S3 representation checks) lived on an
> unmerged branch and its rows were marked *(unmerged)*. As of **2026-08-04
> both D-426 and D-427 are MERGED to main** (see the change log) — nothing
> cited in this document is unmerged any longer.

---

## 0. What the campaign is, and the one rule that governs it

The campaign turns Salesforce **features** into grounded, executable claims and
proves — on a live org — that the system can both *confirm* the feature works
and *detect* when it does not. It is sequenced feature by feature.

**The governing rule, stated once:**

> **A feature that has only ever gone green is NOT covered.**
> Coverage is demonstrated by attribution of a failure, not by accumulation of
> passes. 505 green runs on env-59 prove the machinery; they do not prove
> detection.

The rest of this document exists to make that rule measurable.

---

## 1. Per-feature capability ledger

### 1.1 Summary table

Columns: **S1** = captured in the org model · **S2** = expressible as a claim ·
**S4** = executable · **S6** = attributable · **RED?** = does a *decidable* red
exist. Detail per row follows in §1.2.

| Feature | S1 | S2 | S4 | S6 | RED? (approved corpus) |
|---|---|---|---|---|---|
| Validation Rules | ✅ 67 (52 active) | ✅ prohibition + acceptance | ✅ data-recipe | ✅ 6 VR-specific causes | ⚠️ **1** (`f2b072ac`, `other_vr_fired`) — test-side, not VR misbehaviour |
| BeforeSave Flows | ✅ 5 | ✅ automation-effect | ✅ data-recipe | ✅ post-D-425 splits | ❌ none |
| AfterSave Flows | ✅ 16 | ✅ automation-effect | ✅ data-recipe | ✅ post-D-425 splits | ⚠️ **2** (`3f6466bd`, `ff0cefc5`, `record_absent`) |
| Autolaunched Flows | ✅ 14 | ⚠️ no DML entry point | ❌ no trigger to fire | n/a | ❌ **0 claims, 0 runs** |
| Scheduled Flows | ❌ **0 exist on env-59** | ⚠️ `time-trigger` enum only | ❌ FL10 gated | n/a | ❌ none possible today |
| Approval Processes | ⚠️ 2, **no entry criteria** | ✅ automation-effect + arc | ✅ data-recipe (D-333 arc) | ✅ record_absent + D-427 mirror | ⚠️ **historical only** — the `record_absent` reds were the org's dead-entry-criteria defect, since FIXED; 2026-08-04 re-probe all green (§1.2) |
| Formula Fields | ⚠️ 52 calc / **0 dep edges** | ✅ automation-effect (`formula`) | ✅ data-recipe | ✅ divergent | ⚠️ **2** (`6156c71b`, `ae60e4aa`) — VR interference |
| Rollup Summaries | ❌ **not modelled at all** | ❌ no enum member | ❌ | ❌ | ❌ none possible |
| Picklists / Restricted | ✅ **377/377 decidable** | ✅ constraint layer | ✅ via D-413 gate | ✅ (generation-time) | n/a — a constraint, not a claim target |
| FLS / CRUD | ✅ 11,727 + 1,735 edges | ⚠️ positive only (D-415) | ❌ **run-as dormant** | ⚠️ D-290 never fired | ❌ **0 claims** |
| Record Types | ⚠️ 7, minimal edges | ⚠️ conditions only | ✅ staged (`RecordTypeId`) | ⚠️ no dedicated cause | ❌ none |
| Sharing / OWD | ❌ **absent from S1** | ⚠️ enum member, no body | ❌ | ❌ | ❌ none possible |
| Field Required / Default | ✅ attributes (3081 / 475) | ⚠️ no dedicated kind | ✅ incidental | ⚠️ `field_not_createable` | ❌ none |
| *(Config / Metadata)*¹ | ✅ | ✅ existence + property | ⚠️ **outside run-all** | ⚠️ inspection verdicts | ❌ **0 failures in 101 runs** |
| *(Field Value)*¹ | ✅ | ✅ value-claim | ✅ data-recipe | ✅ overwrote / not-createable | ❌ none approved |
| *(State Transition)*¹ | ✅ (VR + Flow) | ✅ state-transition | ✅ data-recipe | ✅ divergent | ❌ none approved |

¹ Not a Salesforce *feature* but a claim family the corpus reveals; carried so
the ledger accounts for all 185 approved claims.

**Headline: of 13 features, exactly 4 have any decidable red on a still-approved
claim, and none of those 4 reds indicts the feature's own behaviour.** See §1.3.

### 1.2 Per-row detail

#### Validation Rules
- **S1** — 67 `ValidationRule` entities (52 `is_active=true`, 15 false);
  67 `APPLIES_TO` edges. Formula + error message ride `entities.attributes`
  (`Metadata.errorConditionFormula` / `errorMessage`). **Capture gap:**
  `validation_rule_field_refs` holds **0 rows** and `REFERENCES` edges **0**,
  so the D-107 formula→field-dependency projection is not materialised on this
  org (VR field relevance is recomputed lexically at attribution time —
  `attribution.py:513 _formula_fields`).
- **S2** — `prohibition-claim` (v1, v2) + `acceptance-claim` (v1–v3).
  Boundary: `prohibition_mechanism` is a closed `Literal`
  (`prohibition_claim.py:74`) = `{validation_rule, sharing_rule, apex_trigger,
  system_enforced}` — **no rollup / formula / FLS / picklist member**, and the
  v2 body drops `sharing_rule` (`:125`). Logged limits: **D-296** (field-overlap
  VR selection is blind to cross-field `>` — "metric-blindness, permanent",
  line 16171), **D-330** (the taxonomy had no field-vs-field form until
  `ConditionV2.exceeds`, line 16696).
- **S4** — `data-recipe`, 41 approved (38 also carry a `metadata-recipe`
  inspection twin). Reached by the run-all probe path.
- **S6** — 6 dedicated causes (`vr_inactive`, `vr_formula_drift`,
  `vr_formula_indeterminate`, `no_active_vr`, `enforcement_gap`,
  `other_vr_fired`) + `platform_constraint`. **Decidable:** all but
  `vr_formula_indeterminate` (an honest "cannot evaluate").
- **LIVE** — 61 approved + 21 draft; 53 approved ran; **166 passed / 15 failed
  / 18 errored**. Effective causes on the 15 failures: `other_vr_fired` 9 runs
  (8 claims), `platform_constraint` 5 (5), `enforcement_gap` 1 (1).
  **Decidable red: yes but shallow** — only `f2b072ac` is still approved, and
  its cause is *a different rule fired*, i.e. a staging defect (D-398/D-399),
  not a VR failing to enforce. The single `enforcement_gap` (`71583230`) is a
  **D-425.1 re-read against drifted S1**, not a contemporaneous conclusion
  (footnote §8.2). **No VR has been shown to fail to enforce when it should.**

#### BeforeSave Flows
- **S1** — 5 entities, all active, `flow_details.trigger_type='BeforeSave'`.
- **S2** — `automation-effect-claim` (v1–v3); `automation_primitive='flow'`
  (`automation_effect_claim.py:71`). Limits: **D-307** absence needed body v2
  and *cardinality is still not expressible* (line 16474); a **field-conditional
  absence** is refused at the stash gate (`automation_effect_claim.py:122`,
  partially lifted by D-381).
- **S4** — `data-recipe`. **S6** — post-D-425 four-way split.
- **LIVE** — **5 of 5 exercised** (the only fully-exercised family in the
  campaign), 9 runs, 1 failed. That single failure re-reads to
  `automation_effect_divergent` on `5a01ebd9` — **deprecated**, and the defect
  is ours (asserted a stale band, D-425.1). **No decidable red on an approved
  claim.**

#### AfterSave Flows
- **S1** — 16 entities, all active.
- **S2 / S4** — as BeforeSave.
- **S6** — `automation_effect_record_absent` (decidable *what*, not *why* — S1
  captures no entry criteria), `automation_effect_divergent` (decidable *that*,
  not *who* — Apex triggers are uncaptured), `automation_effect_value_absent`
  (**still hedged**), `representation_mismatch`, `automation_inactive`.
- **LIVE** — **7 of 16 exercised**, 132 runs, 13 failed. Decidable reds on
  approved claims: `3f6466bd`, `ff0cefc5` (both `record_absent`). The richest
  red in the corpus — `d49719e2`, the `HL_Auto_Submit_Approval` dead-entry-
  criteria defect, a **genuine org finding** — sits on a *deprecated* claim.

#### Autolaunched Flows
- **S1** — 14 entities captured, all active, `trigger_type` NULL.
- **S2** — expressible in principle, but the claim shape requires a DML
  trigger the flow does not have; **UNKNOWN** whether any authorable shape
  exists (never attempted — no requirement targets them).
- **S4/S6/LIVE** — **0 claims, 0 runs.** All 14 are managed-package
  notification flows.

#### Scheduled Flows
- **S1** — **none exist on env-59** (0 of 35 flows are schedule-triggered), so
  capture cannot be assessed.
- **S2** — `trigger_kind` includes `time-trigger`; no claim has used it.
- **S4** — **BLOCKED**: FL10's `+2 day` observation needs an execution model
  that spans days (`FLOW_ARCHITECTURE_ROADMAP.md:166-173`, AK decision).

#### Approval Processes
- **S1** — 2 entities. **Capture gap, load-bearing:** the attributes carry
  `Id / Name / Type / State / Description / DeveloperName / TableEnumOrId` and
  **no `entryCriteria`** (verified by reading both rows) — the exact gap
  `substrate_3_generation/DEFERRED_ITEMS.md:91` names.
- **S2** — `automation-effect-claim` with `automation_primitive=
  'approval_process'`, plus the D-333 approval-action arc
  (`approval_actions` on prohibition/acceptance bodies, `body_schema_version`
  2/3). Limit: **D-312** — TC-055 needs an approve/reject/submit *action step
  type* that does not exist (line 16640).
- **S4** — `data-recipe` via `_run_approval_arc` (`data_executor.py:674`).
- **S6** — `automation_effect_record_absent` (the `exists`-0 shape).
- **LIVE** — 10 approved + 1 draft, 8 ran; **12 passed / 9 failed / 14 errored**
  — the worst error rate of any feature. Decidable red on an approved claim:
  `2b68e459`.
- **RE-PROBE 2026-08-04 (vs the FIXED org — the dead entry criteria were
  repaired org-side, confirmed by live retrieve: `Approval_Status__c IS NULL
  AND Loan_Amount__c > 5000000`, require-record-changed).** All 7 approved
  approval-primitive claims ran sequentially through the deployed worker
  (jobs 671–677): **6 passed / 0 failed / 1 errored** — probe error rate
  **1/7 (14%)** vs the recorded 40%. The submission side-effect fires
  pre-approval end-to-end (ProcessInstances actually created; teardown's
  uncleaned rows are the documented D-402 ProcessInstance class, org-verified
  not live); all 3 designed-absence claims correctly confirmed absence (the
  D-427 mirror armed but not fired — no red existed to attribute). The one
  error is `79bc47e5` `setup_rejection`: VR `Block_Approved_Without_Approval`
  correctly rejects the claim's own staging payload — a Plimsol staging
  defect (the D-337 vr-conflict class), **not** entry-criteria-related, same
  mechanism as its 07-27 error. **Consequence, stated plainly: the org fix
  resolved the `d49719e2` class (the fix evidently landed between 07-07 and
  07-27 — `2b68e459` already passed on 07-27, unnoticed then), and the
  approval family loses its only natural red specimen.** A future decidable
  red for this family requires a seeded perturbation or a new org defect;
  the corpus still contains **zero** decidable reds indicting live org
  behaviour on an approved claim.

#### Formula Fields
- **S1** — 52 fields `calculated=true`, 37 carry `calculatedFormula` text.
  **Capture gap:** `REFERENCES` edges = **0**, so formula→referenced-field
  dependencies are not derived (the writer is VR-only,
  `sync/validation_rule_refs.py`, and it too is producing 0 rows here).
- **S2** — `automation_primitive='formula'` (D-304).
- **S4** — `data-recipe` (create → read-back → assert the computed value).
- **S6** — `automation_effect_divergent` names both values.
- **LIVE** — 12 approved, 11 ran; 36 passed / 8 failed / 12 errored. The 8
  failures re-read to `automation_effect_divergent` (6 runs, 5 claims — all the
  **percent-scale representation** species, D-425.1) and `other_vr_fired` (2).
  The 2 approved-claim reds (`6156c71b`, `ae60e4aa`) are *VR interference*
  during staging, not formula misbehaviour.

#### Rollup Summaries
- **S1** — **not modelled**: `type='summary'` = **0 fields**, and the
  `summaryOperation` key appears on **0** field rows. Describe-based capture
  cannot see rollup configuration (D-380 shipped an S6 deterministic *fallback*
  precisely because rollups are ungrounded, line 16789).
- **S2** — `automation_primitive` has **no `rollup_summary` member**; adding one
  is an identity-touching body version bump (**D-306.1**, line 16453).
- **S4/S6/LIVE** — nothing. **Fully blocked at S1.**

#### Picklists / Restricted Values
- **S1** — the campaign's one **completed** capture arc: 6,958
  `PicklistValue` entities, 464 value sets, 369 `HAS_PICKLIST_VALUES` edges.
  Capture marks over the 377 enumerated fields: `inline_standard` 296,
  `inline` 56, `inline_truncated` 17, `no_values` 8 → **377/377 decidable**
  (D-414, line 16869). 263 of 377 are restricted picklists.
- **S2** — not a claim target; a **constraint layer** on every other claim's
  literals.
- **S4/S6** — enforced *pre-execution* at the D-413 seam
  (`finalize_outcome` → `_value_membership_gate`, `governance_core.py:6895`),
  routing INVALID to declination via `defer_class: "value-membership"`.
- **LIVE** — the D-413 gate is deployed; the corpus is clean of non-member
  literals (D-414). Its sibling representation checks are built but
  **unmerged** *(D-426)*.

#### FLS / CRUD
- **S1 — the strongest-captured unexercised feature**: 11,727
  `GRANTS_FIELD_ACCESS` edges (`can_read`/`can_edit`), 1,735
  `GRANTS_OBJECT_ACCESS` (`can_create`/`read`/`edit`/`delete`/`view_all`/
  `modify_all`), 61 PermissionSets, 19 Profiles, 4 Users, and
  `createable=true` on 2,584 fields.
- **S2** — `capability-claim` **is** emittable (`emission.py` EMITTABLE,
  `("permission","capability-claim")`, D-123). **Hard limit: the negative form
  is not expressible** — `CapabilityClaimBody` v1 has no polarity slot, so
  "identity X *cannot* do Y" needs a new body version = **a TA-review stop**
  (**D-415** line 16871, obeyed by **D-421** line 16883).
- **S4 — BLOCKED and dormant.** `run_as_user` / `identity_context` have
  **zero consumers** in `execution_engine/plan.py` and `data_executor.py`
  (verified by grep); every run executes as the single admin integration user.
  Blocked on the org-side JWT trust (**D-422**, line 16885).
- **S6** — D-290's `INSUFFICIENT_ACCESS*` grading branch exists
  (`data_executor.py:92-124`) but **has never fired live** (unreachable while
  every run is admin — D-422).
- **LIVE** — **0 capability claims exist** (verified: no `capability-claim`
  rows at any status).

#### Record Types
- **S1** — 7 entities, 12 `record_type_details` rows; **minimal edges** — only
  `BELONGS_TO`; `record_type_picklist_value_grants` holds **0 rows**, so
  record-type→picklist/profile/layout relationships are unmodelled.
- **S2** — no dedicated claim kind; expressed as a staged `RecordTypeId` value
  or a condition. D-348 built DeveloperName→Id derivation for S3.
- **S4** — proven: one approved claim (`e7d4c607`) stages real
  `RecordTypeId`s on create **and** update.
- **S6** — no record-type-specific cause; a failure attributes generically.
- **LIVE** — exercised only *incidentally* (via `PLS_BM_VR08`, the
  RecordType-conditional VR, 1 run). No red.

#### Sharing / OWD
- **S1** — **absent**: zero entity types matching sharing/OWD, and
  `object_details` carries no OWD column (`entity_id, key_prefix, is_custom,
  is_queryable, is_createable, is_updateable, is_deletable, created_at`).
- **S2** — `sharing-rule-claim` is in the `claim_kind` enum but is **not in
  EMITTABLE** and has **no registered body** — an enum member with no
  implementation.
- **Blocked at S1 (Tier-2 absent)**; the S3 deferred item names the upgrade
  path — *"when S1 Tier 2 ships sharing rules / OWD / Apex sharing, the
  currently-refused complex permission claims … upgrade, verified by a
  run-as-execution recipe"* (`substrate_3_generation/DEFERRED_ITEMS.md:89`,
  D-080 / D-123).

#### Field Required / Default
- **S1** — attribute-carried, no edges needed: `nillable=false` on **3,081**
  fields, `defaultValue` on 475, `defaultValueFormula` on 1.
- **S2** — no dedicated claim kind; a required-field violation is expressible
  only as a `prohibition-claim` with `prohibition_mechanism='system_enforced'`
  (never used in the corpus — all 41 approved prohibitions pin
  `validation_rule`).
- **S4/S6** — incidental; `field_not_createable` is the nearest cause.
- **LIVE** — no dedicated claims, no reds.

### 1.3 The decidable-red column, stated honestly

Across **615 runs** on env-59 (505 passed / 57 failed / 53 errored) and the 50
D-425.1 re-reads:

| | Count |
|---|---|
| Failed runs on env-59 | 57 |
| …with a **decidable** effective cause | **52** (the 5 non-qualifying: 4 `automation_effect_value_absent`, 1 with no cause) |
| Distinct claims carrying a decidable red | **39** |
| …still **approved** | **6** — `2b68e459`, `3f6466bd`, `6156c71b`, `ae60e4aa`, `f2b072ac`, `ff0cefc5` |
| …that indict **the org's behaviour** rather than our own claim *(judgment, not a measurement — see §8.6)* | **2**, both on **deprecated** claims: `62ebcc91` (before-save overwrite) and `d49719e2` (dead approval entry criteria) |

**The finding this ledger exists to record:** the campaign can now *name* why a
red happened for most shapes, but nearly every named cause indicts **our own
claim** (representation mismatch, invented values, staging that trips a
different rule) or the **environment** (integration-user FLS), not the feature
under test. Not one feature has a live, approved claim that caught the org
misbehaving *and* explained why. That is the campaign's actual coverage
position on 2026-08-01.

---

## 2. Shape matrices

**Shape coverage is the first-class metric; instance count is second.** A
feature with 40 instances of one shape is less covered than one with 6 shapes
exercised once each.

### 2.1 Validation-rule shapes (52 active VRs)

| Shape | Active | Exercised | Mark | Exercised instances |
|---|---|---|---|---|
| cross-field conditional | 20 | 7 | ✅ exercised | Credit_Assessment_Prerequisites, Escalation_Reason_Required, Home_Loan_Required_Fields, VR02, VR03, VR04, VR07 |
| single-field required | 17 | **1** | ⚠️ once | Opportunity.Amount |
| ISCHANGED | 5 | **1** | ⚠️ once | PLS_BM_VR10_Enterprise_Approval |
| REGEX | 5 | **1** | ⚠️ once | PLS_FB_VR01_External_Ref_Format |
| date / temporal | 2 | **1** | ⚠️ once | PLS_BM_VR06_Contract_Start_Date |
| field-vs-field numeric | 1 | 1 | ✅ (exhaustive) | Loan_Exceeds_Property_Value |
| PRIORVALUE | 1 | 1 | ✅ (exhaustive) | PLS_BM_VR05_Approved_Lock |
| RecordType-conditional | 1 | 1 | ✅ (exhaustive) | PLS_BM_VR08_Enterprise_Discount |
| **Total** | **52** | **14** | | **27% instance coverage** |

Every shape family has ≥1 exercised instance — but **5 of 8 families rest on a
single instance**, and **38 active VRs have never been exercised**. The
unexercised mass is concentrated in managed packages (CHANNEL_ORDERS, sfFma) on
objects no requirement targets.

*Method:* a VR counts as exercised when its own `errorMessage` appears in run
evidence — the `benchmark/validation_rules/v1/README.md:19-22` "attributed to
that rule's own error message" standard. One further VR
(`Opportunity.Close_Date_Cannot_Be_Future`) was named by S6 `vr_name`
attribution without an evidence-text match → **15 distinct VRs total**; the
matrix counts the 14 evidence-text matches (§8.3).

### 2.2 Flow shapes (35 captured flows)

| Trigger shape | Captured | Active | Exercised | Runs | Failed |
|---|---|---|---|---|---|
| AfterSave (record-triggered) | 16 | 16 | 7 | 132 | 13 |
| BeforeSave (record-triggered) | 5 | 5 | **5** ✅ | 9 | 1 |
| Autolaunched | 14 | 14 | **0** ❌ | 0 | 0 |
| Schedule-triggered | **0** | — | — | — | — |
| **Total** | **35** | **35** | **12** | **141** | **14** |

Effect shapes, by the assert predicate the claim uses (all automation-effect
claims, any status):

| Effect shape | Predicate | Claims |
|---|---|---|
| same-record field effect | `equals` | 116 |
| cross-object record creation | `exists` | 18 |
| designed absence (D-307) | `not_exists` | 9 |
| set cardinality / conditional absence (D-381) | `count_equals` | 6 |
| stamp, value-free (D-227) | `not_null` | 4 |

18 approved automation-effect claims are cross-object (they read `Task`).
**Multi-outcome flows** (one flow, several branch outcomes asserted separately)
are **UNKNOWN as a measured shape** — no field distinguishes them; the FL03
tier-banding family is the intended representative (§8.4).

**Unexercised mass:** 23 of 35 flows (66%) — the entire autolaunched family
(14), 9 AfterSave flows including FL10–FL15, and every schedule-triggered shape
(none exists to capture).

**Decidable-cause reachability (added 2026-08-02).** Of the eight AfterSave
effect/shape families the perturbation plan enumerates
(`FLOW_PERTURBATION_PLAN.md` §2), **six can reach a decidable cause; two
cannot, by construction**:

| Family | Why no decidable cause is reachable | Mark |
|---|---|---|
| value-free stamp (`not_null`) | every failure mode lands on `automation_effect_value_absent` — the D-425 ambiguous null, disqualified by §3.1 | **BLOCKED** (sibling-field-capture arc, parked D-425.1) |
| designed absence (`not_exists`) | ~~its failing verdict `automation_fired_unexpectedly` could carry no `cause_kind`~~ **blocker LIFTED (D-427)**: the verdict has its own attribution arm (`_attribute_unexpected_presence`) with the decidable `other_writer_produced_record` and the honest `automation_effect_record_present` — **merged to main 2026-08-04** | **REACHABLE** — no cause-side blocker remains; the family still needs its first live decidable red (a P6 window, once signed) |

AfterSave's exit therefore runs over **7 of 8 reachable families** (since the
2026-08-04 merge of the D-427 enrichment), with the value-free stamp the one
remaining BLOCKED row, carried explicitly per the §3.1 amendment below.

---

## 3. Exit criteria — pre-stated, and they demand a red

**Why pre-stated from here.** The VR arc's exit was declared **post-hoc**:
D-420 (line 16881) recorded *"25/25 prohibition claims PASSED — the VR arc's
live exit"* against no criterion stated in advance, and §1.3 shows what that
exit did not establish. Every feature from here states its criteria **before**
work begins.

### 3.1 The criteria (all four required)

A feature is **COVERED** when:

1. **Instance coverage** — N distinct instances exercised *for the intended
   reason*, i.e. the pass or rejection is **attributed** to that specific
   instance (the `benchmark/validation_rules/v1` standard), not merely "some
   rule rejected the save". Default N = **5**, or the whole population where
   fewer than 5 exist.
2. **Shape coverage** — every in-scope shape family in §2 exercised **≥1 time**.
3. **Detection** — **≥1 DEMONSTRATED DECIDABLE RED per shape family**: a run
   that failed *and* whose S6 cause names the mechanism, from the decidable set
   (`enforcement_gap`, `vr_inactive`, `no_active_vr`, `other_vr_fired`,
   `automation_inactive`, `automation_effect_record_absent`,
   `automation_effect_divergent`, `before_save_automation_overwrote`,
   `field_not_createable`, `platform_constraint`; since D-427 also
   `other_writer_produced_record` and `automation_effect_record_present` —
   the absence mirror's WHAT-decided pair). **Excluded as non-qualifying:**
   `automation_effect_absent` (legacy hedge), `automation_effect_value_absent`
   (ambiguous null, D-425), `vr_formula_indeterminate`, `grounding_incomplete`
   — these are honest "don't knows", not detections.
4. **Honest-refusal check** — where the feature has a designed evidence limit
   (FL15's email, FL10's schedule), the passing result is a **documented
   refusal**, and a fabricated pass scores zero
   (`benchmark/flows/v1/README.md:45-51`).

**A feature that has only ever gone green is NOT covered.** Criterion 3 has no
waiver.

**BLOCKED shape families (amendment, 2026-08-02).** A shape family in which no
decidable cause is *reachable* — because its failing verdict carries no
`cause_kind`, or because every failure mode lands on a disqualified hedge — is
marked **BLOCKED(«blocker»)** in the §2 matrix and in the exit dossier. A
BLOCKED family is **neither counted as met nor silently dropped from the
denominator**: the exit is recorded as *"N of M families demonstrated, K
BLOCKED(«blockers»)"* and remains **PARTIAL** until each blocker is lifted, or
AK explicitly waives the family with the waiver recorded in the dossier. A
feature must never exit by counting an unmeetable family as met — that would
reproduce, one level up, exactly the green-only fallacy this document exists to
forbid. First application: AfterSave Flows, 2 of 8 families BLOCKED (§2.2).

### 3.2 Seeding a red where the org offers no defect

Most orgs are correct most of the time, so criterion 3 usually requires a
**deliberate** defect. The instrument already exists and is signed off:
`docs/architecture/perturb_and_restore_protocol.md` (**ACTIVE**, may-touch list
signed 2026-06-12; P1 and P3 windows executed; **P2 on hold pending an AK
may-touch extension**).

Its non-negotiable ground rules apply unchanged: one perturbation live at a
time; record before touching; restore immediately after one engine observation;
re-sync and re-verify until the engine itself observes fixture state again;
fixture-state artifacts only.

**Campaign addition:** each feature's exit dossier names, in advance, which
perturbation seeds its red — and any artifact not already on the §2 may-touch
list requires a **new AK sign-off before the arc starts**, not during it.

**A recorded coherence failure (2026-08-02).** This section, as written on
2026-08-01, demanded a divergent-value red for flows while the protocol it
cites (signed 2026-06-12) permits exactly one flow perturbation —
*deactivation* — which can never produce a divergent value. The citation
existed; the **scope check did not**: this document named the instrument
without verifying its signed scope could produce the red class criterion 3
demands, and the protocol, written seven weeks earlier, could not have
anticipated the demand. Nobody noticed until `FLOW_PERTURBATION_PLAN.md`
(2026-08-01) put the two side by side. The rule this failure buys: **an exit
criterion that names an instrument must cite the instrument's signed scope,
and any needed scope extension is part of the criterion's cost, stated up
front — not an afterthought discovered at execution time.** The P6 scope
extension that would close this gap is drafted-in-principle in the plan's §7
but is **deliberately not written into the protocol yet**: the 2026-08-02
verifier run (`scripts/verify_flow_fixture.py`) found the plan's own restore
baseline wrong (hand-authored fixture bytes are not byte-comparable to
Metadata API retrievals — see the plan's correction block), and a P6 row will
only be drafted once its verification design reflects that finding.

### 3.3 Recording an exit

An exit is a dated DECISIONS_LOG entry citing: the instance list with
attribution evidence, the shape matrix with every family marked, the decidable
red per family (run id + cause), the perturbation used and its verified
restore, and any honest refusal. A `PASSED` claim count alone is not an exit.

---

## 4. Isolation vs composition — campaign-level

Today this discipline exists **only inside the FB-V1 flow benchmark**
(`benchmark/flows/v1/README.md:71-74` "One capability per flow… complexity
composes only in the designed capstone (FL12)"; ROADMAP Waves 1–4 isolation,
Wave 5 composition). It is promoted here to the campaign.

### Phase 1 — Isolation (campaign-wide, default)
**One mechanism per claim.** A claim's failure must be attributable to exactly
one feature. Where two mechanisms touch the same transition, the claim under
test names one and the other is treated as interference to be neutralised (the
VR-satisfying padding of D-119/R1), never as co-tested behaviour. **Every
feature in §1 is in Phase 1.** No feature has exited it.

### Phase 2 — Composition (gated, not yet entered)
Entered per pair, and **only when both member features have passed their Phase-1
exit** (§3). Two compositions are already named as debt:

| # | Composition | Source | Entry gate | Owner |
|---|---|---|---|---|
| C1 | **Order-of-execution** — FL02×VR01 ("lowercase input is accepted *because* normalization precedes validation") | `DEBT_REGISTER.md` R2 | BeforeSave Flows **and** Validation Rules both Phase-1 exited | needs a claim-kind design for *rule interactions* (TA) |
| C2 | **Journeys** — multi-transition accumulated state | `DEBT_REGISTER.md` R3, D-310 (line 16608) / D-312 (line 16640) | AfterSave Flows **and** Approval Processes both Phase-1 exited | **AK greenlight** — a run-model product decision |

Both are *expressiveness*-blocked as well as gate-blocked: D-310 records that
decomposition-as-journey **loses the cross-intent state carry-over** that is a
journey's whole value, and D-312 that TC-055 needs an action step type that does
not exist. **Do not enter Phase 2 by accident** — a claim that quietly asserts
two mechanisms is a Phase-1 violation, not an early Phase 2.

---

## 5. Coverage metering

**The problem.** Claims do not pin the instance they exercise: all 41 approved
prohibition claims carry `expected_rejection.error_message_pattern = NULL` and
the generic `FIELD_CUSTOM_VALIDATION_EXCEPTION` code (D-113's deferred VR-pin).
VR coverage is therefore **forensic** — reconstructed by matching a VR's
`errorMessage` against run-evidence text (the method of §2.1), which is
retrospective, collision-prone, and invisible to any dashboard.

Three costed options. **None is implemented; this section records them for a
future GO.**

| | Option | Cost | Verdict |
|---|---|---|---|
| **(a)** | **S6 message-match at pass time, persisting `vr_name`** — extend the existing `_match_vr_by_message` (`attribution.py:499`) to *passed* prohibition runs and write the already-existing `s6_interpretations.vr_name` column | **Zero migration** (column exists, indexed), retroactive over stored evidence, no claim identity touched | ✅ **the lean** — do this first |
| **(b)** | **Pin on the recipe at emission** — D-295 already selects the VR deterministically; the recipe is the precedented non-identity home (D-100.2's own rationale) | Small S3 change; **new claims only**, no backfill | ✅ complement to (a) for forward claims |
| **(c)** | **Populate `error_message_pattern` on the claim body** | The field exists in v1 (no schema bump) **but the body is identity-bearing** — every approved prohibition claim re-keys, invoking the deprecate-then-regen law (D-353) across 41 claims | ❌ **rejected** — enormous cost for a coverage meter (a) and (b) provide free |

**Metering the rest.** Flows are already pinned by construction (the claim body
carries `automation.external_id` — a pinned S1 ref), which is why §2.2 is exact
and §2.1 is forensic. Any new feature's claims **must pin the instance they
exercise** at emission; that is the design rule this section establishes.

---

## 6. Priority order and blocked register

### 6.1 Ranking

Ranked by **product priority** — what a release-decision buyer needs to trust —
with S1 capture readiness as *one input*, not the ranking. (The superseded
scratch ranking inverted this: it ordered by capture readiness alone, which put
FLS/CRUD first while its executor was dormant.)

| # | Feature | Why here |
|---|---|---|
| **1** | **AfterSave Flows** | The richest live corpus (132 runs), the only feature holding a *genuine org-behaviour* red (`d49719e2`), and the one whose hedge D-425 just split. Closest to a real exit — needs shape breadth (7/16) and a seeded red per family. |
| **2** | **Validation Rules** | Largest approved population and best-attributed causes, but §1.3 shows the arc's declared exit did not demonstrate detection. Re-exit under §3 is cheap: P2/P3 perturbations are already written, and 5 of 8 shape families need a second instance. |
| **3** | **Approval Processes** | Business-critical and already claim-bearing, but the **worst error rate** (14 errored of 35 runs) and an S1 entry-criteria gap that caps attribution at "record absent". Fixing capture unblocks both the arc and the D-333 entry-criteria guard. |
| **4** | **BeforeSave Flows** | Cheapest remaining exit — **5/5 instances already exercised**; needs only a decidable red per shape (the FL02 normalization path is a natural perturbation target). |
| **5** | **FLS / CRUD** | Highest *latent* value (best-captured surface in S1: 13k+ permission edges) and the whole point of run-as, but **hard-blocked** on the JWT handshake and capped by D-415's polarity limit. Unblocks two arcs the moment the org-side fact resolves. |
| **6** | **Formula Fields** | Executable today and cheap to exercise, but attribution stops at "a different value" while `REFERENCES` edges are 0 — the org cannot tell us which inputs a formula reads. |
| **7** | **Field Required / Default** | Fully captured, zero claims, no dedicated expression. Low ceiling (a required-field violation is a thin test) but nearly free. |
| **8** | **Record Types** | Proven executable incidentally; deeper coverage needs the missing record-type→picklist/profile/layout edges (P4/P5, deferred to explicit GO). |
| **9** | **Autolaunched Flows** | 14 captured, all managed-package notification flows; no DML entry point and no requirement demand. Revisit only if a pilot org uses them. |
| **10** | **Rollup Summaries** | Real customer value, **fully blocked at S1** — describe cannot see rollup config; needs a new capture path *and* an `automation_primitive` member (identity-touching). |
| **11** | **Sharing / OWD** | Same shape as rollups but larger: S1 Tier-2 does not exist and `sharing-rule-claim` has no body. |
| **12** | **Scheduled Flows** | No instance exists on env-59 to capture, and the execution model is an open AK decision (FL10). |

*Picklists are excluded from the ranking:* they are a **completed constraint
layer** (D-414), not a claim target, and their gate already runs at emission.

### 6.2 Blocked register

| Feature / capability | Blocker | Kind | Owner |
|---|---|---|---|
| FLS / CRUD live proof | JWT Bearer exchange returns `invalid_grant: invalid assertion` for **admin and test identity alike** → app-level trust, not user authorization (D-422, line 16885). Resume: compare the app's Consumer Key, then `sf org login jwt` | org config | **AK** |
| Negative capability claims ("X *cannot* do Y") | `CapabilityClaimBody` v1 has no polarity slot; needs a new body version = claim identity (D-415 line 16871 / D-421 line 16883) | S2 schema | **TA review** |
| Scheduled flows (FL10) | Execution model spanning days — deferred-observation jobs vs documented refusal (`FLOW_ARCHITECTURE_ROADMAP.md:166-173`) | product decision | **AK** |
| Rollup summaries | Not modelled in S1 (`type='summary'` = 0, `summaryOperation` = 0) **and** no `automation_primitive` member | S1 capture + S2 | unassigned |
| Formula dependencies | `REFERENCES` edges = **0**; no formula ref-writer (`validation_rule_field_refs` also 0 rows) | S1 capture | unassigned |
| Sharing / OWD | S1 Tier-2 absent entirely; `sharing-rule-claim` has no registered body | S1 capture + S2 | unassigned |
| Approval entry criteria | S1 captures **no** `entryCriteria` on `ApprovalProcess` (verified on both env-59 rows) — caps attribution and blocks the D-333 arm-check | S1 capture | unassigned |
| Journeys (multi-transition state) | Run-model product decision; D-310 decomposition loses accumulated state; D-312 needs an action step type | product decision | **AK** |
| Metadata-only claims in run-all | D-401 (line 16841): `_probe_recipes` (`run.py:827`) filters to `data-recipe` **by design** — 24 approved existence/property claims are outside the probe path | by design | — (state, don't "fix") |
| Ambiguous-null Flow attribution | `automation_effect_value_absent` cannot split never-written from written-blank; needs sibling-field capture in the recipe read scope | S3/S4 arc | **parked** (D-425.1 — 3 runs, 1 claim) |
| Representation checks | Built but **unmerged** on `phase-1-s3-representation-checks @ed802a5` *(D-426)* | merge GO | **AK** |

---

## 7. Pilot alignment — deliberately empty

**Nothing in this repository records any pilot customer's org, or which
Salesforce features they use.**

This section was searched for and found empty, not omitted. Every occurrence of
"pilot" in `docs/` is one of three things: a future gate ("first pilot tenant
onboarded"), a feedback source that never arrived ("Pilot customer feedback just
starting", 2026-05, no follow-up), or a parking-lot trigger condition. The only
orgs named anywhere are internal sandboxes — env-59 and env-78.
`PARKING_LOT.md:117` confirms no second tenant has been onboarded.

**The consequence, stated plainly:** the priority order in §6.1 is reasoned from
*product judgment plus sandbox readiness*, because no customer evidence exists
to reason from. Two specific distortions follow, and they should be assumed
until this section is filled:

1. **env-59 is not a customer org.** Its feature mix is an artifact of what the
   campaign itself built (the PLS_BM / PLS_FB / HL fixture families) plus two
   managed packages. The 38 unexercised VRs are mostly managed-package rules no
   pilot may ever care about; the exercised 14 are largely our own fixtures.
2. **Absent features cannot be prioritised by evidence.** Rollups and sharing
   rank low partly because env-59 has none — but a pilot org built on rollup
   summaries would invert that ranking overnight.

**Fill this section the moment a pilot org connects**, with: the org's feature
census (counts per §1's row set), which features its release decisions actually
gate on, and a re-ranked §6.1. Until then, treat §6.1 as a defensible default,
not a validated sequence.

---

## 8. Footnotes — assumptions and method

**8.1 Feature attribution of claims.** A claim's feature is derived from
`claim_kind` + `asserted_truth->>'automation_primitive'` +
`asserted_truth ? 'approval_actions'` (the D-333 arc marker). This is a
**derived** mapping, not a stored column — S4 has no per-claim feature field.
Prohibition/acceptance claims without an approval marker are attributed to
Validation Rules, which is correct for all 41 approved (every one pins
`prohibition_mechanism='validation_rule'`).

**8.2 Re-read vs original causes.** §1.2 and §1.3 use the **effective** cause:
the D-425.1 `s6_reinterpretations` row where one exists (50 runs), else the
original `s6_interpretations` row. Originals are never overwritten (D-425.1
append-only law). One consequence is load-bearing: `71583230`'s
`enforcement_gap` is a re-read against **current** S1, which drifted since June
— it is *not* evidence that a VR failed to enforce at run time, and this
document does not count it as a qualifying detection.

**8.3 VR exercised-ness is forensic.** Because no claim pins a VR (§5), a VR
counts as exercised when its `errorMessage` appears in run evidence — a
substring match that assumes error messages are unique per rule. Two rules
sharing a message would be conflated. This is exactly the weakness option (a)
in §5 removes.

**8.4 Multi-outcome flows are UNMEASURED.** No stored field distinguishes a
flow with several branch outcomes from a single-outcome flow, and
`flow_details.parsed_logic` is populated only where the FB-V1 IR grounded it.
§2.2 therefore reports effect shapes by assert predicate as the nearest
measurable proxy and marks multi-outcome UNKNOWN rather than guessing.

**8.5 "Active" is org state, not claim state.** VR/Flow active counts are S1's
current view; a claim may target an instance deactivated since generation —
which is precisely what `automation_inactive` / `vr_inactive` detect.

**8.6 "Indicts the org" is a judgment.** The §1.3 split between reds that
indict the org and reds that indict our own claim is **interpretation applied
to measured causes**, not a stored field. The rule applied:
`representation_mismatch` and most `automation_effect_divergent` (percent-scale,
near-miss display text, stale band — D-425.1's five specimens) are **ours**;
`other_vr_fired` is **ours** (staging tripped a rule the claim did not assert);
`platform_constraint` is the **environment** (integration-user FLS);
`before_save_automation_overwrote` and `d49719e2`'s `record_absent` (a Flow
whose entry criteria are dead config) are the **org's**. A different reader
could argue `platform_constraint` belongs to the org's permission model — the
count would then be 2 org + 5 environment. The direction of the finding does
not change under either reading.

**8.7 Counts move.** All §1–§2 numbers are the 2026-08-01 measurement. The
picklist arc (D-403…D-414) moved `PicklistValue` from 728 to 6,958 in four
days; re-measure before citing this document in a decision.

---

## 9. Change log

| Date | Change |
|---|---|
| 2026-08-01 | Created. First campaign definition; supersedes `scratch/VR_ARC_RECON.md` §F as the cross-feature ranking. Establishes: the green-only rule (§0), pre-stated exit criteria demanding a decidable red (§3), campaign-level isolation/composition phases (§4), the pin-the-instance metering rule (§5), and an explicitly empty pilot section (§7). |
| 2026-08-02 | §2.2: AfterSave decidable-cause reachability recorded — 6 of 8 families reachable, value-free stamp + designed absence BLOCKED with named blockers. §3.1: BLOCKED-family exit rule (never counted as met, never silently dropped; exits record "N of M, K BLOCKED"). §3.2: the campaign-vs-protocol coherence failure recorded, with the cite-the-signed-scope rule it buys. Restore-verifier harness landed (`scripts/verify_flow_fixture.py`); its first run found zero org drift but a wrong byte-baseline design — P6 drafting deferred (see `FLOW_PERTURBATION_PLAN.md` §4.1 correction). |
| 2026-08-04 | D-427: verifier rebuilt on the open-snapshot baseline (snapshot/verify modes; determinism proven — 8/8 IDENTICAL on immediate re-verify, incl. the untracked HL flows); DRAFT P6 written into the protocol, UNSIGNED. `automation_fired_unexpectedly` enriched prospectively (own dispatch arm; `other_writer_produced_record` decidable + `automation_effect_record_present` honest; NOT recipe-edit triage — a record where none should exist is the shape of a genuine org regression). §2.2 designed-absence blocker → BLOCKED-pending-merge; §3.1 decidable set extended with the two new causes. |
| 2026-08-04 (split) | This branch split per the merge-collision analysis: the S6 enrichment + the D-427 ledger entry moved to **`phase-1-substrate-6-absence-mirror`** (design `64f013a` + impl; merging THAT branch is the deploy). This branch is now deploy-inert: campaign docs, the perturbation plan, the protocol's unsigned P6 §6, and the read-only verifier script. §2.2 updated to name the code branch. |
| 2026-08-04 (merge) | All three branches merged to main (repchk `ed802a5` → substrate-6 `6179449` → docs `8f5ef15`), single ledger-tail conflict resolved 426-before-427. §2.2: designed absence → **REACHABLE** (7 of 8 families); the value-free stamp is the sole remaining BLOCKED family. Deployed on push. |
| 2026-08-04 (re-probe) | Approval family re-probed vs the FIXED org (jobs 671–677, deployed worker, sequential, abort gates clean): 6P/0F/1E; probe error rate 14% vs the recorded 40%; the d49719e2 dead-entry-criteria class is RESOLVED org-side and the family loses its natural red specimen. The one error is the D-337-class staging conflict on `79bc47e5` (Plimsol-side). D-427 absence-mirror armed, not fired (no red to attribute). Zero decidable org-indicting reds remain — the campaign's measured position stands. |
