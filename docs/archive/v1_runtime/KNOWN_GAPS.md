# PrimeQA v1 Runtime — Known Gaps

> **ARCHIVED 2026-06-15.** The v1 runtime this ledger covers was retired
> (D-191…D-221): `executor.py`, `worker._run_execute_stage`, `submit_review`,
> `BAReview`, the `pipeline_runs` web — all deleted, and the v1 product tables
> dropped in migration 053. Every gap below anchors to a symbol that no longer
> exists, so the ledger is preserved as history rather than updated. Any gap
> with a live successor on the substrate engine belongs in that substrate's
> `DEFERRED_ITEMS.md`, not here.

A living ledger of known gaps in the **v1 runtime** — the Flask app under
`primeqa/` (execution, test_management, intelligence, runs, core, …). This is the
durable, deduplicated home; it is distinct from:

- `reports/triage/*.md` — dated, point-in-time triage snapshots (where several of
  these gaps were first surfaced). Snapshots come and go; this ledger persists.
- `docs/architecture/substrate_*/DEFERRED_ITEMS.md` — the S1–S3 substrate
  architecture deferrals (a different subsystem and roadmap).

**Convention.** Append new gaps. When one is fixed, move it to **CLOSED** with the
resolving commit — don't delete it (keep the history). Each entry records: what
the gap is, the evidence, an honest blast radius, a severity, and the scope to
close it.

**Severity scale.**
- **HIGH** — silent-correctness / data-integrity (something reports success it
  didn't achieve, or corrupts state).
- **MEDIUM** — advertised-but-dormant capability, or a real coverage gap.
- **LOW** — hygiene, UX, or unconfirmed (needs reproduction).

---

## OPEN

### G-001 — Agent fix-and-rerun loop is dormant (trigger seam never wired)

**Severity:** MEDIUM (product-completeness — advertised + default-on, yet dormant).
**Surfaced:** `reports/triage/2026-05-24.md` (rec #6); investigated 2026-05-25.

**Mechanism.** R5 (`479e483`, "Agent fix-and-rerun loop with sandbox auto-apply")
built `AgentOrchestrator.handle_failure` + the trust-band gate + the accept/revert
endpoints + the Agent-fixes UI + unit tests — but the worker/executor failure path
**never calls `handle_failure`**. The trigger seam was never wired:
`git log -S AgentOrchestrator -- primeqa/execution/executor.py primeqa/worker.py`
is empty (never built, not removed). `tests/test_r5_agent.py` drives
`handle_failure` directly, which masked the missing seam in the green suite.

**Meant to be live.** `agent_enabled` defaults `true`
(`migrations/019_agent_settings.sql`, `primeqa/core/models.py`); `docs/design/run-experience.md`
(R5) + `CLAUDE.md` + the Agent-fixes tab all present it as a shipped capability.

**Honest blast radius.** Dormant, not lying. A failed step records its failure and
the worker continues (`primeqa/worker.py:_run_execute_stage`); nothing falsely
claims the agent ran — the Agent-fixes card is gated `{% if agent_fixes %}`
(`primeqa/templates/runs/detail.html`) on rows that are never created, and
`accept`/`revert` (`primeqa/views.py`) operate on an always-empty table. There is
no fabricated "agent ran" state, so this is **not** a correctness lie.

**Scope to close.** Call `handle_failure` from the worker's per-TC failure branch
(`primeqa/worker.py:_run_execute_stage`), passing run / test_case / step / tenant /
environment / env_type / error / pipeline_run; add a worker-level integration test
asserting the trigger fires on a sandbox step failure; decide whether
`agent_enabled` should stay default-true given the loop has shipped dormant. This
is v1-runtime work — independent of the S3 generation engine's D-106.4
production-integration (different subsystem, no shared code or sequencing).

### G-002 — Approval → promotion atomicity (review approve → TC promote)

**Severity:** LOW-MED.
**Surfaced:** `reports/triage/2026-05-24.md` (#3); partial fix `b5d6db8`.

The silent no-op — an `if tcv:` guard that let a review record `approved` without
ever flipping the TestCase out of `draft` — was fixed at `b5d6db8`
("fix(test-mgmt): resolve silent draft-status stickiness on review approval").
**Residual:** confirm the review-status write and the TC promotion happen in **one
transaction**. A crash between them would leave an `approved` `BAReview` paired
with a still-`draft` TestCase. Verify `primeqa/test_management/service.py:submit_review`
commits both atomically (or wrap them in a single transaction).

### G-003 — `test_executor.py` non-idempotent setup

**Severity:** LOW (test hygiene).
**Surfaced:** noted during the 2026-05 v1 cleanup work.

The integration test's setup collides on `meta_versions_env_label_unique` on a
second run against the same database, blocking clean re-runs. This fails loudly
(not a product bug), but it prevents repeatable verification of the executor
suite. Make the setup idempotent — a unique env label per run, or teardown-first.

### G-004 — Suite dropdown "not populating" (unconfirmed)

**Severity:** LOW (UX — no code bug found).
**Surfaced:** `reports/triage/2026-05-24.md` (#4 / bug 4).

Triage found **no code-level bug**. Suites are tenant-scoped by design (no
`environment_id` filter on the `/run` Mode-C picker), so an empty dropdown most
likely means: (a) the tenant has no suites, (b) the user lacks the `run_suite`
permission (the tab is hidden entirely), or (c) a cross-environment last-run
display is confusing the reporter. Needs reporter triage to confirm the real
symptom, then close or convert to a concrete bug.

---

## CLOSED

_(none yet — move entries here with the resolving commit when fixed)_
