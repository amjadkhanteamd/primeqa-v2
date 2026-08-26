# VERIFICATION Productionisation — the Vault, the Armed Worker

Executed 2026-08-27 on the scratch DB `plimsol_3a3` (tenant_1 at
`20260826_0020`), local machine only — **no production or Railway act
was performed**: the arming sequence (prod migration, key generation,
service env, start-command flip, persona registration, the P-1 run) is
the NEXT step, gated as its own runbook.
Re-runnable: unit = `tests/unit/test_prod_vault_gate.py`; DB-real =
`tests/integration/test_prod_vault.py` gated on
`S3A3_TEST_DATABASE_URL`.

## What landed

- Migration `20260826_0020`: `portal_personas` — auth_mode CHECK of
  the three storable modes; the totp-seed-iff-provisioned CHECK;
  username stored as ciphertext like the other secrets.
- `browser_worker/vault.py`: register / resolve / deactivate / list /
  rotate-key + the CLI (secrets via getpass or PORTAL_REG_* session
  env — NEVER argv, per the GO amendment).
- `browser_worker/audit.py`: `record_event` — best-effort activity_log
  through the tenant session, MANDATORY structured-log first channel
  for the two security events, system-as-actor semantics
  (user_id NULL + details.actor; `activity_log.user_id` is nullable —
  verified live by the worker-event writes, no column change needed).
- Descriptor evolution: manifest auth `{"mode": "vault",
  "persona": …}` (persona required at enqueue); `totp_env` demoted
  DEV-ONLY (refused under `PLIMSOL_SERVICE_ROLE=browser-worker` with
  the named permanent class `DEV_AUTH_MODE_REFUSED`); two new named
  permanent classes `PERSONA_NOT_FOUND` / `PERSONA_INACTIVE`.
- `enqueue_ui_run` — authorize(subject, MEMBER) + the D6 consult +
  the `ui.run_enqueued` audit with the real actor.
- The consumer entrypoint: `python -m primeqa.browser_worker` is the
  loop (fail-closed boot, egress print, schema-discovered tenant tick,
  reap → claim → consume, SIGTERM finish-current-surface); the spike
  probe moved behind `probe`, behavior unchanged.
- `assert_tenant_scoped` now writes the `ui.tenant_boundary_refused`
  activity row (best-effort) beside its MANDATORY structured line.

## One hardening found by the tests (flagged)

`rotate_key` is table-wide by design (production holds ONE key). A row
that does not decrypt under the OLD key — a half-done prior rotation
or a lost-key row — now REFUSES the whole rotation LOUDLY, naming the
persona, BEFORE any write (pre-verify pass): a partial re-encrypt
would corrupt recoverability. Asserted in the suite with a planted
foreign-key row.

## a. Migration transcripts

Apply `20260826_0010 → 20260826_0020` clean; apply-twice = 0
migrations; downgrade→re-upgrade 1/1; CHECK read-back shows the
three-mode list (UNSUPPORTED structurally impossible) + the
totp-iff-provisioned constraint. D-459 guard green. Direct INSERTs
with `UNSUPPORTED` and with a seedless `TOTP_PROVISIONED` both hit
their named CheckViolations. The SERVICE refusal is separate and
earlier: `register_persona(auth_mode="UNSUPPORTED")` raises before
touching the DB ("a fact to report, never a credential to store") —
unit-pinned.

## b. Vault round-trip (scratch)

- CLI register with SOURCED-ENV secrets (never argv; the structural
  unit test pins that no argparse argument names a secret):
  `ui.persona_registered`, exit 0, followed by the unset discipline.
- Real-actor audit row: `user_id=7`, persona_key + auth_mode in
  details, NO secret in any details blob (scanned).
- Worker-side resolve feeds the EXISTING `Credentials` — values match,
  `repr = Credentials(<redacted>)`; `login()` is untouched (the unit
  path unchanged — consume passes the same object it always did).
- Hygiene scan: no plaintext username/password/seed substring in any
  vault or audit row — ciphertext only.
- Re-registration = credential rotation (`ui.persona_rotated`,
  stamps); `rotate-key` re-encrypts under NEW, stamps rotated_by/at,
  resolve works under the new key, mixed-key refusal proven.

## c. Named refusal classes

- `totp_env` + `PLIMSOL_SERVICE_ROLE=browser-worker` →
  `DEV_AUTH_MODE_REFUSED`, permanent (never resubmits).
- vault mode, absent persona → `PERSONA_NOT_FOUND`; deactivated →
  `PERSONA_INACTIVE` — distinct, both permanent (in `_PERMANENT`,
  unit-pinned).

## d. The enqueue boundary

- viewer subject → `AuthorizationError` "deny: role 'viewer' (tier
  VIEWER) < required MEMBER" — the 403-envelope carrier.
- tester (MEMBER) passes; the returned `authorized` reason carries the
  allow; a job exists ⇒ the D6 RECIPE_MODES consult ran (its refusal
  path is 3A-2 unit-pinned); `ui.run_enqueued` audit row with the real
  user id.

## e. Consumer-loop mechanics

- Schema-discovered tick: a planted schemaless ACTIVE tenant row
  (id 99) is excluded and warned **once per process** — the second
  discovery is silent (the recorded stale-tenant FIX-PLAN posture
  applied from birth).
- claim → consume with `should_stop` firing after the first surface:
  the CURRENT surface finalizes (1 result row), the job stays
  `in_progress` ("died_reason=SIGTERM" on the line), the aged lease
  reaps back to `pending` with `attempts=1` (claim-only charging), and
  the re-claimed job runs to completion.
- **Live local pass (transcript script): `run_loop(once=True)` drove
  the REAL browser** — fixture manifest (2 surfaces), claim → scan →
  both surfaces `OK` with evidence **REFERENCED on the live R2
  bucket** → job `succeeded`. The egress print emitted on boot
  (UNKNOWN locally — the sandbox blocks the probe; on Railway it
  prints the real IP, which is exactly the P-2/D9 observation P-1
  records).

## f. Suites

- Unit: **4,885 passed** (+6 vault-gate tests); both boundary guards
  (verdict string-ban, import-ban) green over every worker change.
- DB-real: **22 passed** across all six suites (3A-3, 3A-4, 3A-5,
  Phase 7, prod-vault) — no regression.
- Zero production or Railway interaction in any transcript.
