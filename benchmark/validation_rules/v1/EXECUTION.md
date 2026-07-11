# VRB-V1 — Execution Runbook

How to rerun the frozen benchmark and score a Plimsol version against it.
Written for a rerun months from now, by someone who did not run V1. Nothing
here changes the benchmark; if any step *requires* changing the fixture, the
requirement, or the gold standard — stop, read
[`../../BENCHMARK_POLICY.md`](../../BENCHMARK_POLICY.md), and version instead.

## 1. Prerequisites

- A running Plimsol deployment (web + worker + scheduler) pointed at its
  database, with an Anthropic API key configured.
- A Salesforce **sandbox** you may create and delete records in, connected to
  Plimsol as an environment (a Connected App with API access; the integration
  user holds the `PLS_BM_Deal_Access` permission set).
- The environment must NOT be flagged `is_production`, and its execution
  policy must permit full runs.

## 2. The benchmark org

- The fixture's deployable source is `sandbox_fixtures/pls_benchmark_v1/`
  (SFDX). If the org already carries it, **verify rather than redeploy**:
  the object, all 12 fields, both record types, and all ten rules must match
  [`benchmark-v1.json`](benchmark-v1.json), every rule **active**, and nothing
  extra on the object (an extra rule changes every isolation result).
- If the org has drifted, restore it FROM the SFDX source. Never adjust the
  benchmark to match a drifted org.
- Sync the org into Plimsol (a full S1 metadata sync for that environment)
  and confirm the sync succeeded before generating.

## 3. The requirement

- Create (or verify) a **manual** requirement whose stored acceptance
  criteria match [`REQUIREMENT.md`](REQUIREMENT.md) **byte-for-byte**,
  including the projection tail. This text is the only business input the
  benchmark permits.
- Never provide [`GOLD_STANDARD.md`](GOLD_STANDARD.md), this runbook, or
  [`RULES.md`](RULES.md) as input in any form.

## 4. Generation

- Trigger generation for the requirement against the benchmark environment
  (the normal product path: the requirement page's Generate action, or the
  S3 generation queue), with boundary-value authoring enabled for the tenant.
- Generation is LLM-fed and varies run to run. The benchmark measures the
  *system*, so: up to **three** generation passes are within protocol; record
  how many passes each control needed (that number is itself a result). If a
  control's claim exists from a prior pass with a stale recipe, deprecate the
  stale claim and regenerate — regeneration then mints fresh by mechanism.
- To verify what a pass produced, read the generation outcome's
  `claims_written` / `equivalent_existing` references and inspect those
  claims' recipes directly. Do not rely on message-text searches or
  time-window queries — both false-negative against deduplication.

## 5. Execution

- Approve each benchmark claim, then run it against the benchmark environment
  (the claim run action; multi-probe claims run as one strict-AND batch).
- Runs create records on `PLS_BM_Deal__c` and tear them down best-effort.
  If a run errors with a setup/staging classification, the control was never
  exercised — fix the stated cause (usually org drift) and rerun; that is an
  error, not a failure.
- Avoid running within a few minutes of the org's midnight (the documented
  temporal exposure window for VR06's boundary arms).

## 6. Evidence collection

For every probe of every claim record, from the run's stored results:

- outcome (`passed` / `failed` / `errored`) and verdict
  (`prohibition_enforced` / `value_persisted` / …);
- for rejection arms: whether the error **matched the target rule's own
  message** (attribution) — a rejection by any other rule scores zero;
- for acceptance arms: the read-back assertion result;
- the posted payload per step (the staged state each arm actually exercised).

## 7. Expected outputs

The per-rule experiment shapes and evidence obligations are specified in
[`RULES.md`](RULES.md); the partition-level expected values in
[`GOLD_STANDARD.md`](GOLD_STANDARD.md). In aggregate, the V1-complete state
this benchmark froze at:

- **10/10 controls correctly exercised**, every rejection attributed;
- VR03: five arms (two isolated branch rejections + three gate-necessity
  acceptances); VR06: four temporal arms; VR08: three arms (boundary pair +
  record-type control); VR10: both directions; VR05: the two-arm prior-state
  differential; VR01/02/04/07/09: attributed isolated negatives.

## 8. Pass criteria

Score three numbers, side by side — never only the first:

1. **Apparent AC coverage** — what the coverage map claims.
2. **Trustworthy AC coverage** — ACs backed by executable, attributed
   evidence.
3. **Correctly-exercised controls (n/10)** — the headline: each rule counts
   only when live evidence shows it firing/admitting *for the intended
   reason*, attributed, with siblings silent.

A version **passes at parity** when all three match the V1-complete state
above with zero false tests (no probe passing for a wrong reason) and zero
unattributed rejections. A version **regresses** if any previously-exercised
control drops, even if the aggregate count holds.

## 9. Failure interpretation

| Symptom | Read it as |
|---|---|
| A claim refuses at generation with a named reason | Honest refusal — a selection/derivation capability gap for that control; record which and why. Not a broken benchmark. |
| A rejection arm passes but the message is another rule's | **False confidence** — isolation or attribution regression. Score zero for that control and investigate first. |
| An acceptance arm is rejected, classified as setup/staging | The fixture never reached the state under test — usually org drift or a sibling-isolation regression. Errored, not failed; fix and rerun. |
| A control needs >3 generation passes | Proposal-reliability regression (the v23–v27 contract territory) — record the shapes proposed each pass. |
| Everything passes but faster/with fewer probes | Verify probe **membership** per RULES.md before celebrating — fewer probes may mean a lost experiment arm (the strict-AND batch only grades what was authored). |
| The org rejects with a rule not in the ten | The org has drifted — restore from SFDX source; results from a drifted org are void. |
