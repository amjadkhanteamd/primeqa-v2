# Perturb-and-restore protocol — env 59 ("Prime QA SFDC" sandbox)

Tier 0.2 of the dogfood development list (G-3 from the matrix audit). The
negative columns of the dogfood matrix — `prohibition_not_enforced`,
`automation_not_triggered`, `vr_inactive` / `vr_formula_drift` causes, S8
grounding drift — can only be exercised by deliberately breaking the sandbox
and watching the engine notice. This document is the **written procedure**:
what gets touched, how, the expected engine observation, and the restore
ritual that returns the org to fixture state.

**Status: ACTIVE — §2 "may touch" list signed off 2026-06-12 (see
`dogfood_matrix_log.md`). P1 and P3 windows have been executed; P2 is on hold
pending an AK may-touch extension.** Execution requires the sf CLI
(`primeqa-sandbox` alias, already authenticated) or AK's own Setup access.

**2026-08-05 (D-430/D-431): amendment drafts for P3 and P2 exist in §7/§8 —
UNSIGNED.** Until signed, no P3/P2 window should open under the 2026-06-12
rows either: they predate the open-snapshot verification discipline P6
taught, and P2's named rule is **already inactive org-side** (§8 finding), so
the signed P2 has no effect as written.

> A **P6 row** (flow-logic perturbations) exists in §6 below — **SIGNED by
> AK 2026-08-05, expires 2026-08-19**, with a revocation trigger (§6 clause
> (c)) — **met 2026-08-05: status REVOKED**. The session-start verifier
> reports its live status.

---

## 1. Ground rules (non-negotiable)

1. **One perturbation live at a time.** Never overlap two perturbations —
   verdict attribution must be unambiguous.
2. **Record before touching.** Every perturbation starts by capturing the
   current state of the artifact (`sf data query` / metadata read) into the
   session log, so restore is verified against a recorded baseline, not memory.
3. **Restore immediately after capture.** The window between perturb and
   restore is one engine observation (one run or one sync), not a work session.
4. **Re-sync + re-verify after restore.** Every restore ends with: fresh S1
   sync → confirm the synced state matches the recorded baseline → one green
   re-run of the affected claim. The sandbox is not "restored" until the
   engine itself observes fixture state again.
5. **Fixture-state artifacts only.** Only the artifacts in §2 may be touched.
   Anything else (users, profiles, other objects' rules, org settings) is out
   of scope without a new sign-off.
6. **No data perturbations needed.** All listed perturbations are metadata
   toggles/edits; record data created by runs is cleaned by the engine's own
   teardown (s4_created_records).

## 2. The "may touch" list (AK signs off on exactly this)

| # | Artifact | Perturbation | Restore |
|---|---|---|---|
| P1 | Flow `SQ205_Create_Case_SLA` (the SQ-205 fixture Flow, sandbox_fixtures/sq205/; companion `SQ205_Escalation_Effects`) | Deactivate (Setup → Flows → Deactivate, or deploy with `status: Draft`) | Re-activate; confirm `IsEscalated`/`Last_Escalation_Date__c` stamping resumes on a probe Case |
| P2 | VR `Opportunity.Contract_Value_Required_On_Closed_Won` | Toggle `active=false` | Toggle back `active=true` |
| P3 | VR `Lead.RequireReason`-class rule (any one VR with a derivable formula; exact pick at execution) | Edit the error-condition formula to a non-equivalent comparison (e.g. threshold change) | Restore the recorded original formula text byte-for-byte |
| P4 | Field `Case_SLA__c.Response_Hours__c` (fixture custom field) | Change length/precision (e.g. precision 18→16) | Restore recorded original |
| P5 | Permission set of the **FLS-restricted user** (Tier 0.3 — only after AK creates it) | Remove read on `Case.Last_Escalation_Date__c` | This one is the CF-1 repro target and may be left in place as a standing fixture if AK prefers |

> **2026-08-05 note (D-430/D-431):** the P3 and P2 rows above are superseded
> by the amendment drafts in §7 and §8 once those are signed. The rows as
> signed predate the open-snapshot verification discipline (§6 rules 1–2),
> and P2's named rule `Opportunity.Contract_Value_Required_On_Closed_Won` is
> **already inactive** in the current org model (verified 2026-08-05:
> `attributes.active=false` AND `validation_rule_details.is_active=false`;
> the active row closed at the 2026-06-15/16 daily sync, S1 seq 66→67), so
> the signed P2 toggles off a rule that is already off. The rows themselves
> are left untouched as the signed record.

## 3. Per-perturbation expected observations (the matrix columns they close)

- **P1 (Flow off)** → rerun the SQ-205 automation-effect claim → run FAILS,
  S6 verdict `automation_not_triggered`. Also the staged state-transition
  claim on any fixture relying on the Flow.
- **P2 (VR off)** → rerun the Closed-Won prohibition claim → the negative
  create SUCCEEDS where rejection was asserted → verdict
  `prohibition_not_enforced`, cause `vr_inactive` (the D-111.1 cause we have
  never seen live).
- **P3 (formula edited)** → grounding drift needs a fresh S1 sync: only after
  the sync does S8's grounding-validity recompute flag the claim
  (`vr_formula_drift` family). A bare rerun without an intervening sync does
  **not** surface drift — the engine is still grounded against the pre-edit
  formula. (Corrected here per `dogfood_matrix_log.md`; the earlier
  drift-without-sync expectation was wrong.)
- **P4 (field property)** → the property-claim on that field (generate one
  first) flips from passed to failed; S8 grounding drift board shows the
  entity-level change after sync.
- **P5 (FLS)** → run a claim whose create writes the restricted field with
  the restricted user's connection → clean `platform_constraint` cause naming
  the code + field (D-225's deliberate attribution — the CF-1 acceptance).

## 4. The restore checklist (run after EVERY perturbation)

```
[ ] Perturbed artifact restored to the recorded baseline (diff shown in session)
[ ] Fresh S1 sync completed (new version_seq recorded)
[ ] Synced S1 state matches baseline (query the entity/edge attributes)
[ ] Affected claim re-run → green (verdict back to the fixture expectation)
[ ] Session log updated: perturbation id, run ids observed, restore evidence
```

## 5. What this protocol deliberately avoids

- **No production org. Ever.** env 59 only.
- **No user/profile surgery** beyond P5's single permset (AK-created).
- **No deletes** of fixture artifacts — deactivate/edit only, always reversible.
- **No schedule-window overlap**: perturbations pause if the 06:00 UTC daily
  schedule (s4_run_schedules id=1) would fire mid-window — its clean-fire
  streak gates the D-221 R5 DROP and must not be polluted; either finish the
  restore before 06:00 UTC or disable→re-enable the schedule around the window
  (recorded in the session log).

---

## 6. P6 — flow-logic perturbations  ✅ SIGNED

> **STATUS: SIGNED — AK, 2026-08-05.**
> **EXPIRES 2026-08-19** (hard expiry, clause (d) — after that date no P6
> window may be opened; re-authorisation requires a new signature and date).
> **REVOCATION (clause (c)):** VOID once the AfterSave divergent-value shape
> family has a decidable red recorded in the campaign ledger — that record is
> made machine-checkable by adding the line `P6 REVOCATION MET` to
> `FEATURE_CAMPAIGN.md`'s change log; the session-start verifier reads this
> marker and the dates above.
>
> Drafted 2026-08-04 per D-427; amended and signed 2026-08-05 (clauses
> (a)–(e) below). Design inputs: `FLOW_PERTURBATION_PLAN.md` (candidates
> F1–F8 + the §4.1 correction) and the campaign's exit criteria
> (`FEATURE_CAMPAIGN.md` §3).

| # | Artifact | Perturbation | Restore |
|---|---|---|---|
| **P6** | Fixture Flow **logic**, one flow per window, fixture flows only — **never managed-package or org-native automation** | Edit ONE value in the flow's logic (an accumulator increment, a formula offset, a lookup-filter value, or an assignment literal) so the flow **still fires but writes a different value** | Redeploy the pre-window logic; **restoration is verified ONLY by `scripts/verify_flow_fixture.py verify` reporting IDENTICAL against the window-open snapshot** |

**P6 rules (all mandatory, additive to §1's ground rules):**

1. **Baseline = the window-open retrieve snapshot, NOT committed source.**
   Hand-authored fixture bytes and Metadata API output are different
   serialisations of the same logic — element order, whitespace, and the org
   eliding default-valued elements (`storeOutputAutomatically=false`) — so a
   byte-diff against committed source diverges on 5 of 6 tracked candidates
   with zero logic drift (measured 2026-08-02). Same-serialiser round-trips
   are byte-stable: determinism was proven 2026-08-04 (snapshot → immediate
   re-verify, 8/8 IDENTICAL), so a DIVERGENT close-verify is a real
   unrestored delta, never serialiser noise.
2. **The verifier runs at window open AND window close, mandatory.**
   Open: `verify_flow_fixture.py snapshot <flow> --label p6-<id>-open`
   (a failed retrieve = the window MUST NOT open). Close:
   `verify_flow_fixture.py verify <flow>` — **any outcome other than
   IDENTICAL is UNVERIFIED and escalates to AK immediately**; a failed or
   empty retrieve is never treated as restored. The committed-source
   comparison is a secondary *logic-drift* signal only and never gates
   restoration.
3. **Collateral rule:** only flows whose **entire approved-claim footprint is
   one claim** are eligible (today: F1 `PLS_FB_FL07_Order_Rollup`, F3
   `PLS_FB_FL05_Cancellation_Sync` — re-measure before every window). A
   failed restore on such a flow is inert: exactly one known test goes red.
4. **Restricted-picklist rule:** any perturbation that writes a picklist
   value MUST use a captured **active** member of that field's value set per
   S1 (377/377 fields decidable since D-414). An out-of-set value errors the
   triggering DML (`INVALID_OR_NULL_FOR_RESTRICTED_PICKLIST`) → run
   `errored/not_evaluated` → the window teaches nothing.
5. **Abandonment.** If a session dies between perturb and restore, **nothing
   automatic notices**: S1 holds no flow logic (`parsed_logic` NULL for every
   candidate) and the daily 06:00 UTC schedule (`s4_run_schedules` id=1) is
   currently **disabled**, so no ambient re-run surfaces a stuck red. Until
   that changes, the MANDATORY manual check is: **the first action of any
   session following an aborted or unconfirmed window is
   `verify_flow_fixture.py verify` over every P6-eligible flow**, against the
   snapshot store (`~/.primeqa/flow_snapshots/` — snapshots are
   repo-external precisely so they survive a crashed session). Re-enabling
   the daily schedule would add ambient detection and is the named
   improvement.
6. **Baseline provenance note:** `sandbox_fixtures/home_loan/` is untracked,
   so the HL flows have **no committed baseline**; the open-snapshot design
   makes this non-blocking for restore verification (the baseline is
   org-derived), but the repo-logic secondary signal reports
   `no-committed-baseline` for them until the fixture directory is committed.

**Amendment (AK, signed 2026-08-05):**

(a) **SCHEDULE EXCLUSION.** The regression schedule must be disabled while
    any P6 window is open or unverified. A scheduled pass against a
    deliberately perturbed org records artifact reds indistinguishable from
    findings.

(b) **ABANDONMENT CHECK EXTENSION.** The post-abandonment procedure must
    additionally determine whether the regression schedule fired inside the
    window, and quarantine any runs that did.

(c) **REVOCATION TRIGGER.** This authorisation is VOID once the AfterSave
    divergent-value shape family has a decidable red recorded in the
    campaign ledger. P6 authorises proving that case; it does not authorise
    perturbation as an ongoing practice. Any further perturbation after that
    point requires a fresh signature.

(d) **HARD EXPIRY.** This authorisation expires on **2026-08-19** regardless
    of whether (c) has been met. After that date no P6 window may be opened.
    Re-authorisation requires a new signature and date; it is not renewed by
    assumption or by continued relevance.

(e) **STATUS VISIBILITY.** While P6 is live, its status must be checkable
    without reading the protocol document. The session-start verifier check
    must also report: whether P6 is signed, whether it has expired, and
    whether its revocation condition has been met.

**AK sign-off:** signed by **AK** (amjad.khan@teamd.co.in)  date: **2026-08-05**
— covering the P6 row, rules 1–6, and amendment clauses (a)–(e).

---

## 7. P3′ — VR formula-edit windows (amendment)  ⏳ DRAFT

> **STATUS: DRAFT, UNSIGNED — nothing may run under this section.** The
> signed 2026-06-12 P3 row remains the only signed VR-edit authorisation,
> and the §2 note of 2026-08-05 records why no window should open under it
> either: it predates the verification discipline below. This draft becomes
> operative only when AK signs the sign-off line at the bottom, with the
> clause-(d) expiry date filled in.
>
> Drafted 2026-08-05 per D-430/D-431. Design inputs: the executed P3 window
> (`dogfood_matrix_log.md` — the session-log baseline, the sync-first
> correction, Finding #2's masking), P6 §6 (the discipline carried over),
> and the VR re-exit assessment (`FEATURE_CAMPAIGN.md` §2.1).

| # | Artifact | Perturbation | Restore |
|---|---|---|---|
| **P3′** | VR **error-condition formula**, one rule per window, **from the eligible list in rule 4 only** — never managed-package rules | Edit the formula to a non-equivalent comparison (threshold or literal change) so the rule stays active but **no longer fires on the window claim's staged payload** | Redeploy the recorded original formula; **restoration is verified ONLY by retrieve-and-diff reporting IDENTICAL against the window-open snapshot** |

**P3′ rules (all mandatory, additive to §1's ground rules):**

1. **Baseline = the window-open retrieve snapshot — NOT the session log and
   NOT committed source.** The signed P3 row's "recorded original formula
   text" was a session-log capture, not a same-serialiser artifact, and the
   flow arc proved "deploy succeeded" is not evidence (§6 rule 1, D-427).
   A VR retrieves as `ValidationRule:<Object>.<Rule>` →
   `objects/<Object>/validationRules/<Rule>.validationRule-meta.xml`; the
   same-serialiser round-trip argument applies unchanged.
2. **Retrieve-and-diff at window open AND close, mandatory.** A failed open
   retrieve = the window MUST NOT open. Any close outcome other than
   IDENTICAL is **UNVERIFIED and escalates to AK immediately**; a failed or
   empty retrieve is never treated as restored. **PRECONDITION:**
   `scripts/verify_flow_fixture.py` covers only the Flow metadata type
   today — it must be extended to ValidationRule (scope recorded with
   D-431; deliberately NOT built with this draft) before any P3′ window
   can open.
3. **S1 formula text is a REAL secondary semantic signal.** Unlike flows
   (S1 holds no flow logic), S1 holds the actual formula text — the
   `vr_formula_text` extractor (`primeqa/semantic/entity_attributes.py`)
   over `entities.attributes` (`Metadata.errorConditionFormula`,
   shape-tolerant per D-203.1). The §4 checklist item "synced S1 state
   matches baseline" is therefore machine-checkable for VRs:
   post-restore-post-sync, S1's formula text must equal the snapshot's.
   It stays secondary — rule 2's byte-diff alone gates restoration.
4. **Scope: ground rule 5 GOVERNS.** The signed row's "any one VR with a
   derivable formula; exact pick at execution" is struck — open-ended
   artifact scope is what §6's clauses were written to kill, and it
   conflicts with ground rule 5's "fixture-state artifacts only". Eligible
   rules are NAMED, as P6 names its fixture flows:
   **`Case.Escalation_Reason_Required`** (primary — the only current VR on
   Case, so the drift determination cannot be masked) and
   **`Opportunity.Amount`** (secondary — window claim MUST be `94c34988`,
   see rule 5). Re-measure both before every window.
5. **Collateral + the D-229 masking hazard: the window claim is named in
   advance.** Eligible rules must carry a minimal covering approved-claim
   footprint, and the window claim's evidence payload must be verified
   evaluable-True against the original formula before the window opens.
   Claim choice decides whether the drift red is decidable or hedged: an
   Opportunity payload carrying `CloseDate` (e.g. `25a1757c`) re-triggers
   the Finding-#2 masking (`Close_Date_Cannot_Be_Future` is NonEvaluable
   and the indeterminate bucket outranks the drift determination), while
   `94c34988`'s minimal `{"Amount": 10001}` payload keeps it decidable.
   Named window claims: `1db82105` (Escalation_Reason_Required),
   `94c34988` (Amount).
6. **Sync BEFORE the graded run; expected cause `vr_formula_drift`.** The
   executed P3 window's correction stands: grading against a stale formula
   reads `enforcement_gap`, a manufactured divergence (D-430 — no pre-sync
   windows). Record with the window that `vr_formula_drift` carries
   `vr_name=None` — S6 says a rule drifted, not which; unambiguous on Case
   (1 VR), weak on Opportunity (8).

**Clauses (mirroring P6 (a)–(e); operative only on signature):**

(a) **SCHEDULE EXCLUSION.** The regression schedule must be disabled while
    any P3′ window is open or unverified.

(b) **ABANDONMENT CHECK EXTENSION.** The first action of any session after
    an aborted or unconfirmed window: retrieve-and-diff every eligible VR
    against the snapshot store, AND determine whether the schedule fired
    inside the window, quarantining any runs that did. (For VRs the daily
    S1 sync gives ambient detection flows lack — a perturbed formula
    surfaces as S8 grounding drift at the next sync — but ambient
    detection is not a substitute for the check.)

(c) **REVOCATION TRIGGER.** VOID once the shape family the window targets
    has its decidable red recorded in the campaign ledger — made
    machine-checkable by the line `P3 REVOCATION MET` in
    `FEATURE_CAMPAIGN.md`'s change log. P3′ authorises proving the
    reachable families' reds; it does not authorise perturbation as an
    ongoing practice.

(d) **HARD EXPIRY.** This authorisation expires on **____-__-__** (AK
    fills at signature) regardless of whether (c) has been met. It is not
    renewed by assumption or by continued relevance.

(e) **STATUS VISIBILITY.** The session-start verifier check must report
    this section's status (signed / expired / revoked) the way it reports
    P6's — which requires generalizing the verifier's §6-specific status
    parser to this section; that extension ships with the rule-2
    precondition, before any window.

**AK sign-off:** ____________________  date: ____-__-__
— covering the P3′ row, rules 1–6, and clauses (a)–(e).
**UNSIGNED as of 2026-08-05.**

---

## 8. P2′ — VR deactivation window (redraft)  ⏳ DRAFT

> **STATUS: DRAFT, UNSIGNED — nothing may run under this section.**
>
> Drafted 2026-08-05 per D-430/D-431.

**The finding that forces the redraft:** the signed P2 targets
`Opportunity.Contract_Value_Required_On_Closed_Won`, which is **already
inactive in the current org model** — verified 2026-08-05:
`attributes.active=false` AND `validation_rule_details.is_active=false`;
the active row closed at the 2026-06-15/16 daily sync (S1 seq 66→67),
three days after P2 was signed. The signed perturbation ("toggle
`active=false`") toggles off a rule that is already off; it can teach
nothing. Sharper: the org performed P2's exact perturbation **on us**,
org-side, in mid-June — and nothing surfaced it, because **zero approved
claims cover that rule** (no `errorMessage` match anywhere in run
evidence). That silence is the campaign §0 green-only blind spot observed
in the wild. AK should confirm whether that deactivation was intentional.

**DECISION (draft): REDRAFT, not withdraw.** A deactivation window is
worth keeping because `vr_inactive` is the **only named-rule decidable
cause on the not-enforced side** (`vr_formula_drift` carries
`vr_name=None`), it has never been seen live (the §3 P2 bullet's original
purpose — still true: the corpus's VR-family failures show
`other_vr_fired` 9, `platform_constraint` 5, `enforcement_gap` 1,
`vr_inactive` 0), and the attribution bucket order makes it immune to the
D-229 masking hazard (`violated_inactive` is checked before the
indeterminate hedge), so it is decidable even on Opportunity's 8-rule
object. Expected cause verified through the production
`_attribute_not_enforced`: deactivated `Opportunity.Amount` against
`94c34988`'s real payload → **`vr_inactive`, `vr_name=Opportunity.Amount`**.

| # | Artifact | Perturbation | Restore |
|---|---|---|---|
| **P2′** | VR **`Opportunity.Amount`** (active, evaluable, minimal footprint) | Toggle `active=false` via metadata deploy — one token in the retrieved XML | Toggle back `active=true`; **restoration verified ONLY by retrieve-and-diff IDENTICAL against the window-open snapshot** |

**Window claim: `94c34988`** (payload `{"Amount": 10001}` — the formula
evaluates True, so post-sync the bucket is `violated_inactive` → expected
cause **`vr_inactive` naming the rule**). Sync BEFORE the graded run, as
in §7 rule 6 — pre-sync the same red would read `enforcement_gap`, which
D-430 rules out. §7 rules 1–3 and clauses (a)–(e) apply by reference
(same snapshot discipline, same verifier preconditions, revocation marker
`P2 REVOCATION MET`, expiry filled at signature).

**AK sign-off:** ____________________  date: ____-__-__
— covering the P2′ row, its window claim, and the §7 clauses by reference.
**UNSIGNED as of 2026-08-05.**
