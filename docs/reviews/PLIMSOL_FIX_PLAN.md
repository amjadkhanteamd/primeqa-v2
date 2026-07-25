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
