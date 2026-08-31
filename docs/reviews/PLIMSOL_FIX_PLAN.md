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

- ~~**Value-membership validation at generation time** (D-399)~~ — **RESOLVED
  2026-07-28 (D-412/D-413, main @5f3aea2).** Validator built (three verdicts,
  capture-gated per D-399.1) and wired as a DECLINATION on the D-302
  `partial_refusals` surface (`defer_class: "value-membership"`); the 10
  approved offenders (incl. `31eaa21e`) deprecated and their requirements
  regenerated post-v32 — 29 new claims, all membership-VALID, zero recurrence;
  approved corpus at 0 INVALID. Prerequisite S1 capture completeness shipped
  as D-407/D-408 (369/377). Remaining relatives live in their own bullets:
  the 17 `inline_truncated` fields stay CANNOT_VALIDATE by design; NULL-capture
  orgs (e.g. env-78 pre-re-capture) validate nothing until re-captured (D-411).
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

### Added 2026-07-27 — from the picklist-capture arc (D-407…D-411)

- **Version-mismatched sync resume** (D-411) — **root cause of the 07-27 incident.**
  A partial run must record the code identity that started it (build SHA /
  extraction-contract version) and REFUSE resume by any other; a foreign resume
  fails the job rather than silently completing the fragment under different
  extraction semantics. Observed live: new code failed mid-Field, the deployed
  old code resumed within 2 s and silently stripped 46 fields' picklist
  grounding (102 → 56 populated).
- **Per-org re-capture step; skip gate blind to code changes** (D-411). The
  SetupAuditTrail gate asks "has the ORG changed?" — never "has what S1
  EXTRACTS changed?" — so deploying a capture fix re-captures nothing on a
  quiet org (observed: 19 s no-op, describes=0). Every extraction-semantics
  deploy needs an explicit per-org re-capture; a `capture_generation` counter
  on `connected_orgs` compared in the gate would make it self-healing.
  Correctness gap, not an optimisation.
- **`inline_truncated` disclosure audit** (D-407/D-408). 17 fields carry the
  200-value cap (e.g. `Task.RecurrenceTimeZoneSidKey` at 424 org values).
  Confirm every consumer that treats capture as complete — S3 vocabulary,
  governance metadata, S4 k16 padding — reads `picklist_capture` and treats
  `inline_truncated` (and NULL) as SUBSET / not-authoritative, never as the
  full set. ~~the D-399 validator~~ — DONE (D-412: `inline_truncated` →
  CANNOT_VALIDATE always, unit-pinned). Truncation only ever REMOVES values,
  so the failure mode is refusal/degradation, not wrong-green — but the
  disclosure must be read to hold that property.
- ~~8 honest `no_values` fields~~ — **CLOSED at source, no work.** All 8 were
  verified against the live org describe (0 values each, incl. the one
  required+createable survivor `Location.LocationType`); `no_values` is
  genuine absence, not a fourth silent exit.

## Added 2026-08-21 — from ui-s2.3 verification

- **FIX-1: fresh tenant-chain provisioning crashes at revision 20260817_0010
  (D-454).** `autocommit_block()` asserts alembic owns the transaction;
  `alembic/env.py:113` opens its own, so the assertion fires on any
  fresh-chain run. Existing tenants are unaffected (revision already
  applied); ANY NEW TENANT PROVISION IN PRODUCTION WILL CRASH MID-CHAIN.
  Severity: blocks client onboarding — must be fixed before the first new
  tenant is provisioned. Found during ui-s2.3 verification (local fresh
  chain, 2026-08-21). Root cause is the env.py/autocommit_block
  transaction-ownership contract mismatch; candidate fixes: (a) env.py
  yields transaction ownership to alembic, (b) rewrite 20260817_0010
  without autocommit_block. Fix needs its own diagnosis pass + decision;
  do NOT patch as part of unrelated work.
  **RESOLVED 2026-08-24 — recommendation (b)+(c): revision rewritten to
  plain in-transaction ADD VALUE IF NOT EXISTS; guard test prevents
  recurrence. Fresh-chain + no-op verification below.** Diagnosis pass
  confirmed a one-off, not a class (exactly one autocommit_block user in
  both chains); fresh-chain crash rolls back atomically (empty schema, no
  partial state); already-applied tenants never re-execute the revision.
  Decision: D-459. Verification: fresh scratch-DB chain to head
  20260823_0020 with coverage_flag present; existing-tenant upgrade head
  no-op (no re-execution); guard test + full tests/unit green.

## Added 2026-08-24 — from 3A-1 scratch verification

- **Low: migration 017 is pre-016 non-idempotent on fresh public-chain
  replay.** A fresh scratch replay (001 → 017 → 062/063, during 3A-1
  verification) hit failures in 017 requiring its role-CHECK statements
  (`users_role_check` widen to superadmin, 017:18-20) to be re-applied
  standalone. No production impact — 017 has long been applied and the
  public chain has no fresh-replay consumer today. Worth an idempotence
  guard (or a documented fresh-replay runbook) when the public chain next
  gains a fresh-replay consumer (e.g. scratch-env provisioning tooling).

## Added 2026-08-26 — from the Phase 3A merge deploy watch

- **Low: stale active-tenant rows without provisioned schemas generate
  recurring scheduler repair-triage tracebacks.** `public.tenants` holds
  15 `active` rows but only `tenant_1` has a provisioned substrate
  schema (the other 14 are years-old integration-test tenants); the
  scheduler's repair-triage tick iterates active tenants and logs an
  `UndefinedTable: s6_interpretations` traceback per unprovisioned
  tenant per tick. Pre-existing (predates the 3A merge; the failing path
  is untouched by it). Remedy options: deactivate the stale rows, or
  make repair-triage skip unprovisioned schemas loudly-once (one
  warning per tenant per process lifetime, not a traceback per tick).

## Added 2026-08-27 — from the P-1 acceptance run + its adversarial verification

- **Medium: `FLASK_ENV` absent on the browser-worker service leaves the
  fail-closed portal-key gate UNARMED.** `secrets.is_production()` reads
  `FLASK_ENV == 'production'`; the browser-worker service does not carry
  that variable, so `get_portal_fernet_key()` never raises and
  `validate_boot_secrets()` under the `browser-worker` role reduces to
  the unknown-role check. If `PORTAL_FERNET_KEY` were removed the worker
  would boot normally and fail per job with a generic `VaultError`.
  *Correction on record: the D2 arming report of 2026-08-27 stated the
  gate "PASSED" and would have refused to start without the key — that
  was wrong, and is corrected here.* P-1 was unaffected (the key was
  set). The `totp_env` dev-only refusal is separately armed (it gates on
  `PLIMSOL_SERVICE_ROLE`). Remedy: set `FLASK_ENV=production` on the
  browser-worker service (an ops act), and consider making the role gate
  independent of the production flag.
- **Medium: `claimed_by` is NULLed on success, leaving no permanent
  worker identity in the DB.** `queue.mark_succeeded` clears
  `claimed_by`, so a completed job's row cannot say which host or
  process ran it; provenance rests entirely on the Railway deployment
  log, which is retained only for the current deployment. For P-1 the
  log was captured to `docs/ui-testing/p1-evidence/`. Remedy: persist a
  worker identity (a `ran_by` column, or the identity on the result row).
- **Low: 24 unreferenced evidence objects share the production
  `tenant_1/` prefix.** Spike and development debris (`portal-home`,
  `example-home`, `fx-b`, `fx-bad`, `127.0.0.1:8642|…`) sit alongside
  P-1's four objects in the production bucket; isolation rests on UUID
  uniqueness rather than a design boundary, `sweep_orphans` is
  report-only, and there is no retention story. Remedy: sweep the
  orphans, then decide bucket/prefix separation for dev vs production.
- **Low: the DE-11 ownership marker set lacks Aura framework classes.**
  Both P-1 FAILs carry `ownership=UNKNOWN`; the failing nodes are
  Salesforce's own Aura chrome (`class="forceSkipLink"
  data-aura-class="forceSkipLink"`). `_PLATFORM_MARKERS` matches only
  `<force-…>`-style element tags or `slds-` classes, so Aura's
  `force`-prefixed CLASS names slip through. Conservative-safe (UNKNOWN
  never over-claims) but attribution is weaker than it looks. Remedy:
  extend the platform marker set to Aura class prefixes.
- **Low: `observation.url` records the REQUESTED url, not the LANDED
  one.** `spike.py` sets it from the input argument; `page.url` after
  redirects is never stored. Had the browser been bounced to `/s/login`
  the row would still read `/s/`. C3 in the P-1 transcript is carried by
  the fingerprint, pass counts and screenshots instead. Remedy: record a
  `landed_url` alongside the requested one.

## Added 2026-08-28 — from the 3ba0c9f web-deploy failure

- **Low: the build depends on a PUBLIC registry being reachable at
  build time.** The web service's deploy of merge `3ba0c9f` failed twice
  with `failed to resolve source metadata for
  docker.io/library/python:3.11-slim … dial tcp <ip>:443: i/o timeout`
  (two different Docker Hub IPs, 30 s timeouts) — a reachability
  failure, not throttling (that returns 429) and not our code (the same
  commit built cleanly on the other three services). Both Dockerfiles
  now pull the identical base from AWS public ECR, which mirrors the
  official images without anonymous throttling. **ECR mitigates
  reachability; it does not remove the class** — any public registry can
  become unreachable from a builder we do not control. If this recurs
  (on ECR or elsewhere), the durable answer is a vendored or private
  base image published to a registry we own, so a deploy never depends
  on a third party's availability.

## Added 2026-08-31 — from Phase 4 (multi-standard views)

- **Medium: the standard-view NOT_COVERED denominator is an engine-census
  lower bound.** `standard_view` derives each standard's criterion
  denominator from the vendored engine's tag census — the criteria the
  ENGINE knows about that fall inside the standard's bound WCAG version.
  A criterion that no engine rule addresses is therefore absent from the
  denominator and can never render NOT_COVERED. The view returns
  `denominator_complete: false` with the limitation, and the honesty
  header carries it, so nothing implies full-scope coverage — but a
  report cannot yet state "N of M criteria in this standard". Remedy: a
  ratified criterion catalogue (WCAG SC number, title, level, version
  membership) seeded and lifecycle-managed like the rules, so the
  denominator is the standard's true scope.
- **Low: WCAG `level` on standard maps is rule-derived, not
  per-criterion.** Migration 063 propagated each axe RULE's level tag to
  every criterion of that rule, so a rule covering both an A and a AAA
  criterion records both as A (e.g. `scrollable-region-focusable`
  records 2.1.3 as A, where WCAG makes it AAA). Phase 4's A+AA scope
  gate is applied against this data honestly, but some AAA criteria can
  pass the gate. Remedy: correct per-criterion levels as a catalogue
  content change with its own review — deliberately NOT done as a side
  effect of Phase 4.
- **Low: integration suites can silently encode superseded semantics.**
  The Phase 7 and 3A-4 DB-real suites were red from the
  verdict-semantics merge (`3ba0c9f`) until Phase 4, because their
  planted observations predate attestation and the post-merge
  verification ran only `test_prod_vault` plus the browser test. Fixed
  in place (planted observations now carry attestation). Remedy for the
  class: when a slice changes decision semantics, re-run EVERY DB-real
  suite, not the ones the slice touched.
