# Substrate 4 — Execution Engine — Glossary

Terms specific to substrate-4. Cross-cutting terms live in the relevant substrate's glossary.

---

**RunEvidence.** S4's in-memory, S4-owned record of one execution run: the grounded `outcome` (`passed`/`failed`/`errored`), run-level identity (`run_id`, recipe + claim refs, `environment_id`), and a tuple of per-step evidence. The gate S6 consumes (read-only) to interpret what the run means. Distinct from `run_events` (the v1 cross-service event log).

**Grounded outcome.** A run outcome (`passed`/`failed`/`errored`/`skipped`) verified against the *specific claim* the recipe operationalizes — not a bare "the step did not error." For the behavioral negative, grounding = the actual rejection `error_code` matched the recipe's `RejectionExpectation`. The antithesis of v1's ungrounded `expect_fail` flip.

**Metadata-inspection vertical.** S4's first vertical (D-108): translate an S1 edge → Tooling SOQL, read the subject's metadata, assert a relationship `exists`. A read-and-assert verification (`metadata-recipe`), no record mutation.

**Behavioral negative.** S4's second vertical (D-110.2): a `create` the org should *reject*. The grounded 4-way create-reject eval (success / expected-match / wrong-reason / error) is strictly stronger than v1's flag-flip.

**`CreateAttemptEvidence`.** The per-step evidence for a behavioral-negative create: `success`, `matched`, `error_code`, `message`, the full `rejection_body`, `http_status`, and the cleanup record.

**finalize / posture.** `finalize_run` persists the evidence then reports the grounded outcome as *posture* to S2 (`report_run_outcome`, `actor='s4'`) on the same session — atomically. Posture is the S4→S2 write boundary; it updates `test_recipe_runtime_state`, not the semantic claim.

**Transaction boundary A.** One tenant-scoped session/transaction spans the whole run path (select → execute → finalize), committed once on clean exit (the `LedgerPersister` idiom). `search_path = "tenant_<id>", public` lets one session reach both the per-tenant S2/S4 tables and the v1 `environments`/`connections`.

**Read-through boundary (S4→S2).** S4 consumes S2's typed `RecipeRead` via the Coordinator (`select_recipe_for_execution`), never re-decoding raw JSONB; it writes outcomes via `report_run_outcome`. Read on the way in, write on the way out — both through the Coordinator.

**k16 (the writable-set boundary).** For the positive vertical (D-115): S4 resolves *operational* validity but never the *semantic* value under test. S4's writable set = (object's required fields) − (the semantic fields); the field-under-test is recipe-set and never in S4's writable set, so S4 *structurally cannot* choose it.

**k14 (teardown as execution-isolation).** Cleanup (delete the created record) is framed as leaving the org as found — an isolation concern, never part of the semantic verdict.

**Docs MCP (the design-time-only boundary).** Docs MCP is a design-time input. It is never a product-runtime dependency. It is never an evidence source. (Cross-cutting — binds S3, S4 and S6 alike; see D-448.)
