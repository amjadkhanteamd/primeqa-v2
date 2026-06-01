# Substrate 4 — Execution Engine — BACKGROUND

## Why this substrate exists

PrimeQA's value loop turns a generated test into a release decision only if *running* it produces **trustworthy truth** about the org. A test that reports `passed` without genuinely verifying its claim is worse than no test — it manufactures false confidence. Substrate 4 is the layer that runs an S2 recipe against a real Salesforce org and captures **what actually happened** as durable, grounded evidence: not "the step did not error" but "the specific S3 claim the recipe operationalizes held (or did not), and here is the observation that proves it."

The boundary is the substrate's reason for being: **S4 captures truth and owns the grounded outcome; it does not interpret it.** Classification, root-cause, explanation, and clustering are S6's job. S4's discipline is to render an outcome that is *grounded* — tied to the claim — and stop there.

## What substrate-4 replaces

PrimeQA v2's execution path (`primeqa/execution/` — the per-step REST runner driven by `worker.py`) renders outcomes, but its central sin is the **ungrounded `expect_fail` flag-flip**: a step marked `expect_fail` is flipped to `passed` when the create is rejected, with no check that it was rejected *for the asserted reason*. A create rejected for a missing required field counts the same as one rejected by the validation rule under test — the outcome is reported without grounding. The result is a `passed` that does not mean what a reviewer thinks it means.

Substrate-4 keeps v1's mechanical primitives (the REST / Tooling call surface beneath `execute_step`) but owns the layer above: orchestration, the grounded outcome, and an **evidence-first result model**. The grounded create-reject eval — match the actual `error_code` against the recipe's `RejectionExpectation` — is what makes the outcome strictly stronger than the flag-flip.

## What substrate-4 is for

- **Run an S2 recipe against a live org** — select the eligible recipe (via the S2 Coordinator's read-through boundary), translate it into an executable plan, resolve credentials, execute against Salesforce, and capture per-step evidence.
- **Render a grounded run outcome** — `passed` / `failed` / `errored` / `skipped`, verified against the specific claim, never a bare did-not-error flip.
- **Persist captured truth** — a per-tenant result store (typed identity/outcome columns + an extensible `evidence` JSONB), and report the outcome as *posture* back to S2 (`report_run_outcome`) on the same transaction.
- **Feed S6** — the captured `RunEvidence` is the gate S6 consumes to interpret what the run *means*.

S4 is built vertical-by-vertical, each thin and live-proven: metadata-inspection (read + assert) first, the behavioral negative (create-expect-reject) second, positive create-and-verify (D-115) next. The full execution scope (positive CRUD, UI, event, callout, the fix-and-rerun loop) accrues incrementally; S4 captures failure-truth but does **not** remediate (F7).
