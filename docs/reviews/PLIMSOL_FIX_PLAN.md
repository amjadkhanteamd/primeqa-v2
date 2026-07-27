# Parked items — S3 token-cost arc (2026-07-25)

> Parked from the D-387/D-388/D-389 session (`S3_TOKEN_COST_REVIEW` + the two
> deployed impl commits `72eed6c`/`3cfd9e6`). Each is a scoped follow-up, none
> started.

- **MIGRATE-FIRST migration (pre-pilot):** drop the three dead `llm_usage_log`
  columns (`run_id`, `test_case_id`, `generation_batch_id` — targets dropped in
  migration 053), promote `environment_id` from `context` JSONB to a typed
  column; optionally a decoded `requirement_id` where the public row exists
  (decode law in D-388). One migration, applied to Railway before code merges.
- **`failure_mode_framing`:** zero repo-wide consumers (grep: tools.py only),
  populated on 13.8% of intents (~7k output tokens corpus-wide). Drop from the
  descriptor schema — LOW risk (needs a proposal-quality A/B, review rec D-5).
- **Layer-A schema rejection rate as a quality signal:** 15% of calls (67/449)
  burn a full corrective turn on structural failures. The only fidelity-neutral
  discard category (D-389); track per prompt-version, treat a rise as a
  regression signal, not just a cost line.
- **Cost of discard (review Gap 4):** rates are known, cost is not isolated.
  Now unblocked — the commit-1 join attributes cost per run; a per-outcome
  roll-up can separate first-pass vs recovery-hop spend.
- **`select_canonical` reachability (review C-6):** zero firings in 449 calls;
  842 tokens riding every request's cached prefix. A dead-substrate question
  (is AWAIT_SELECTION reachable in practice?), not primarily a token one.
- **Under-specified test doubles:** the SimpleNamespace jobs in
  `tests/unit/generation/test_s3_intake.py` diverged from the real
  `GenerationJob` dataclass and would not have caught a missing `created_by`.
  Consider a factory helper that mirrors the dataclass fields.
- **`prior_request_id` / intake.py:72 lineage never populated (D-071):** every
  regeneration is a full from-scratch run (req-320: 71 generations). The
  structural root cause of aggregate spend; HIGH semantic risk to change —
  needs the S1-version-delta gate design. Untouched this arc, deliberately.
- **`content_error` vs `generation_error` taxonomy split (S6):** the consumer's
  `_classify_error` folds provider content errors into the generic bucket;
  worth a distinct code so ops can tell a prompt/contract failure from an
  infrastructure one.

## Pilot gate cluster (identity + dormancy) — 2026-07-25

> One triage group, not scattered items: everything here gates pilot
> credibility (identity that survives mutation, machinery that is honest about
> whether it has ever run). Sources: D-393/D-394/D-395/D-396.

- **Persist `requirement_key` at creation** (D-393): the key becomes immutable
  data, not a derived expression recomputed per enqueue. Migration — combine
  with D-388's follow-up (drop the 3 dead `llm_usage_log` columns + promote
  `environment_id`) into ONE MIGRATE-FIRST migration. **Repro first**: set
  jira_key on a covered requirement, re-generate, assert coverage reads
  uncovered — the mechanism is code-read, not runtime-confirmed.
- **Purge referential guard** (D-394): purge refuses when substrate references
  exist (jobs / outcomes / links, both branches of the key encoding).
  Soft-delete is the default; its infrastructure already exists.
- **Typed task registry** (D-395): the root fix for the v1-residue class — a
  rename must not silently orphan a dispatch site. Fixing individual strings
  guarantees a fourth instance.
- **gateway.py:153 dormant feedback gate** (D-395) — **NEEDS EVALUATION, not a
  fix.** Flipping it to the live task name is a generation-semantics change
  (the model sees new prompt content on every generation): before/after output
  comparison + grounding-validator check required. Dormant-first.
- **Escalation reachability** (D-395): determine whether escalation is
  reachable at all on the live paths (tool_turn has no chain; which llm_call
  tasks both support it and run?) — or name it dead substrate.
- **router._CHAINS v1 entries + `llm/__init__.py` docstring example** (D-395):
  unreachable routing config and a dead-name example; sweep residue.
- **Dormancy liveness rule** (D-395 corollary): every dispatch-keyed mechanism
  exposes a last-fired signal; never-fired renders as never-fired — not zero,
  never healthy.
- **Attempt creation inside `run_generation`** (D-390/D-396): every generation
  is recorded regardless of caller — closes the direct-invocation residual
  ($0.3405 today) at the resolver, not per-caller.
- **"Generation" denominator reconciliation** (D-396): after the above, the
  settings panel (distinct runs) and Generation history (attempts) converge on
  one denominator; reconcile the panel wording then, not before.

### Added 2026-07-27 — from the stale-cohort re-run (D-397…D-402)

- **Value-membership validation at generation time** (D-399) — **HIGHEST PRIORITY in
  this cluster.** Validate every literal asserted/staged against a picklist,
  multipicklist or restricted field for MEMBERSHIP in S1's active value set; fail
  loud (refuse to emit) rather than emitting. The grounding validator validates
  FIELD grounding, not enumerated VALUE membership. A claim asserting a nonexistent
  value **manufactures wrong-reds by construction** — it can never pass, and burns a
  live org run plus an S6 investigation on every execution. Live instance:
  `31eaa21e` stages `Loan_Type__c = "Home Loan"`; the org holds `Home`, `Personal`,
  `Business`.
- **`AssertEvidence`: persist the ASSERTED and OBSERVED values** (D-400) —
  DEFERRED_ITEMS §3 trace enrichment, **PROMOTED to the pilot gate on production
  evidence**. Without it, value-mismatch reds are undecidable (`9ba2d3d2`,
  `0d81c6f9`): genuine org-behaviour finding vs wrong claim cannot be told apart, and
  "why did this fail" — the product's core promise — is unanswerable.
- **Teardown outcome invisible for D-210 read-registered records** (D-400): cleanup
  rides `CreateAttemptEvidence.cleanup`; a read-registered record has no create step,
  so its delete failure appears nowhere in the trace — only as a `cleaned=false` row.
- **Metadata-recipe unreachable via queue run-all** (D-401): by design (D-300.1
  wrong-green prevention), not dead code — 148 real runs via the single path.
  **Coverage-reporting implication:** a queue-driven run executed the DATA probes;
  never report it as "all recipes executed". 9 of 25 cohort claims own a
  metadata-recipe whose assertion was not re-verified.
- **`79bc47e5` — structurally undecidable `AmbiguousRejection` on an APPROVED claim.**
  The org rejects the create with no field attribution, which is precisely the case
  S4 refuses to ascribe — so this claim can never produce a verdict on current
  evidence. An approved claim that is permanently unverdictable is an **S2 status
  question**, not an S4 one.
- **`f2b072ac`, `31eaa21e` — claim status decision PENDING** the value-membership
  audit. Both are verified-bad (D-399); whether they are deprecated, regenerated, or
  corrected is the owner's call, and the audit sizes the class first.
