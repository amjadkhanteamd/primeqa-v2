# LLD — Phase 2.6: tenant boundary (arm I) + role-aware boot gate proposal

Status: DESIGN (this commit); arm I implementation and the `secrets.py`
change each get their OWN GO after review — no code before approval.
Scope anchor: UI programme HLD/SAD/TAD v1.1, step 2.6 (arm I: tenant
denial). Branch: `phase-ui-s2-spike`. Builds on 2.3 (queue), 2.5
(evidence store), and the session substrate.

Naming contract unchanged. Everything below is design-of-record for the
NEXT implementation commit; current-code facts are cited as they stand at
`70e8fde`.

## a. ARM I under schema-per-tenant — stated honestly

Cross-tenant access **via the queue/evidence pipeline is structurally
unexpressible**: tenant identity comes from the connection context —
`open_tenant_session` sets `search_path` to `tenant_<id>` and records
`session.info["tenant_schema"]` (queue.py) — never from a caller
parameter. `enqueue` / `claim_one` / `heartbeat` / `finalize_surface` /
`mark_*` / `reap_stalled` / `consume_job` / `create_manifest` /
`enqueue_for_manifest` take NO tenant id, NO schema, NO key. There is no
foreign tenant id to forge, so "attempt cross-tenant access through the
API" is not a writable test — the honest proof is two-part.

### Threat model (stated, not implied)

The boundary defends against **key-string confusion and API/CLI misuse
under a correctly-scoped session** — the realistic failure class for an
operator or product-code bug. It does NOT defend against an in-process
caller holding the raw DB password and bucket credentials: such a caller
can open any schema and read any object regardless of this module. The
DB-side wall is `search_path` per connection; the bucket-side wall today
is nothing per-tenant (one credential set spans the bucket) — which is
exactly why every key-string surface must deny + audit (part ii), and why
per-tenant object-store credentials are named future vault work, not
claimed now.

Inherited spike posture, restated: the spike tables carry NO `tenant_id`
column or GUC CHECK (2.3 decision; S1's belt-and-suspenders CHECK is a
possible later promotion). Isolation on the DB side is purely the
connection's `search_path`.

### (i) INVISIBILITY — proof plan

1. Migrate a second local schema: `alembic -x mode=tenant -x tenant_id=2
   upgrade tenant@head` on `plimsol_spike23` (with the documented FIX-1
   workaround for `20260817_0010`). Production untouched.
2. Populate BOTH schemas: one manifest + one fixture job + results in
   `tenant_1` and (differently shaped) in `tenant_2`, via each tenant's
   own session.
3. Show, from a `tenant_1` session: `claim_one` drains only tenant_1 jobs
   (tenant_2's pending job is never claimed); manifest/job/result counts
   see ZERO tenant_2 rows. Repeat from a `tenant_2` session for the
   converse. Same queries, both directions, row counts transcripted.
4. Evidence prefix derivation: run one fixture batch per tenant, then
   list the bucket — every object a tenant_1 job wrote sits under
   `tenant_1/…`, every tenant_2 object under `tenant_2/…`. A tenant_1 job
   CANNOT write under `tenant_2/` because `_EvidenceSink` derives the
   prefix from `evidence.key_prefix(session)` — callers never pass one.

### (ii) DENY + AUDIT at every key-accepting surface

Enumeration of every entry point in `primeqa/browser_worker` that DOES
accept a key or prefix string (current signatures at `70e8fde`):

| # | surface | key material | today | 2.6 binding |
|---|---|---|---|---|
| 1 | `evidence.build_keys(prefix, …)` | caller prefix | internal callers derive via `key_prefix(session)`; raw form is public | becomes `build_keys(session, …)` (prefix derived inside); raw form demoted to `_build_keys` (tests only) |
| 2 | `evidence.put_evidence(s3, bucket, keys, …)` | caller keys dict | trusted | gains `session` + guard over every key (defence in depth — the sink already derives, the guard makes it structural) |
| 3 | `evidence.sign_url(s3, bucket, key)` | raw key | signs ANY key | becomes `sign_url(session, s3, bucket, key, …)`: guard, then presign; the pure presigner is demoted to `_presign` |
| 4 | `evidence.list_keys(s3, bucket, prefix)` | raw prefix | lists ANY prefix | demoted to `_list_keys` (module-private; public access via `sweep_orphans` only) |
| 5 | `evidence.sweep_orphans(session, s3, bucket, prefix)` | raw prefix | has the session but does NOT check the prefix against it | guard: refuse a prefix whose first path segment ≠ session tenant |
| 6 | CLI `evidence sign --tenant --job --surface` | key read from the tenant-scoped DB row | signs whatever the row holds | guard the row's keys before signing (belt: a corrupted row cannot leak a foreign signed URL) |
| 7 | CLI `evidence sweep --tenant --prefix P` | raw argv prefix | **accepts ANY prefix — a tenant_1 session can list `tenant_2/` today** (bucket-wide credentials). The concrete arm-I surface. | guard `--prefix`; a cross-tenant attempt exits non-zero with the refusal + audit event |

The guard (one function, used by all bindings):

```python
class TenantBoundaryError(RuntimeError):
    """A key/prefix names a tenant other than the session's. Refused."""

def assert_tenant_scoped(session, key_or_prefix: str, op: str) -> None:
    expected = key_prefix(session)              # from session.info, never args
    head = (key_or_prefix or "").lstrip("/").split("/", 1)[0]
    if not head or head != expected or ".." in key_or_prefix:
        log.warning("TENANT_BOUNDARY_REFUSED op=%s tenant=%s offending=%s",
                    op, expected, head or "<empty>")
        raise TenantBoundaryError(f"{op}: key prefix {head!r} is not {expected!r}")
```

- **The refusal + audit IS arm I's demonstrable half**: the verification
  transcript shows, from a live tenant_1 session, `sweep --prefix
  tenant_2/` → non-zero exit, `TENANT_BOUNDARY_REFUSED op=sweep
  tenant=tenant_1 offending=tenant_2` in the log, zero objects listed;
  and `sign_url` against a hand-built `tenant_2/...` key → same class.
- **Audit channel (spike-grade)**: the named, structured log event via
  `logging.getLogger("primeqa.browser_worker.evidence")` — machine-
  greppable, asserted by tests (`caplog`), printed by the CLI. Keys and
  prefixes are not secrets, so the event may name them. Promotion path:
  product wiring routes the event to `activity_log` through the core
  service layer — deliberately OUT of the spike (browser_worker keeps its
  no-core-imports boundary); noted as the integration step.
- Non-goals: no per-tenant bucket credentials/STS (future vault work); no
  DB-level tenant_id/CHECK promotion on spike tables; no dispatch or
  claim-kind work; nothing scheduled — manual + tests only.

### Tests (arm I implementation commit)

- Pure: guard matrix (match passes; foreign prefix, empty, `..`,
  absolute-ish keys refused; audit event emitted with op + both tenants).
- DB-gated (SPIKE_DB_TESTS_OK discipline unchanged): invisibility counts
  in both directions on tenant_1/tenant_2 local schemas.
- Bucket-gated: sweep + sign refusals against the real bucket from a
  scoped session; the fixture-batch prefix-containment check.

## b. ROLE-AWARE BOOT GATE — proposal only (NO code before its own GO)

Touches `primeqa/core/secrets.py` — the FIRST existing-core file of the
spike. Current fact (read at `70e8fde`): `validate_boot_secrets()`
requires JWT_SECRET + CREDENTIAL_ENCRYPTION_KEY in production for EVERY
entrypoint, and per-service secret separation is ABSENT by mandate ("Set
it in Railway for every service"). This proposal is the first sanctioned
divergence; those docstrings update with the implementation.

- **`PLIMSOL_SERVICE_ROLE`** env ∈ {`web`, `worker`, `scheduler`,
  `browser-worker`}.
- **Full effective set per role** (resolved 2026-08-23 — what the gate
  ENFORCES when validation runs in production under that role; everything
  else a service needs, e.g. `DATABASE_URL`, is checked by its own existing
  gate and is unchanged by this proposal):

  | role | enforced secret set | notes |
  |---|---|---|
  | *(unset — legacy)* | `{JWT_SECRET, CREDENTIAL_ENCRYPTION_KEY}` | byte-identical to today; valid forever |
  | `web` | `{JWT_SECRET, CREDENTIAL_ENCRYPTION_KEY}` | = legacy → tagging is a no-op, deferred |
  | `worker` | `{JWT_SECRET, CREDENTIAL_ENCRYPTION_KEY}` | = legacy → tagging is a no-op, deferred |
  | `scheduler` | `{JWT_SECRET, CREDENTIAL_ENCRYPTION_KEY}` | = legacy → tagging is a no-op, deferred |
  | `browser-worker` | `{PORTAL_FERNET_KEY}` | ENFORCED whenever validation runs under this role in production; the Railway tagging waits for the vault key to exist. JWT/CEK deliberately excluded (least privilege). |
  | *(unknown value)* | — | fail closed: `SecretConfigError` naming the valid roles |

- **Role → required secret set** (proposal):
  - `web` / `worker` / `scheduler`: `{JWT_SECRET,
    CREDENTIAL_ENCRYPTION_KEY}` — identical to legacy, so tagging them is
    a behavioural no-op (and therefore deferred indefinitely).
  - `browser-worker`: `{PORTAL_FERNET_KEY}` — DECLARED now, ENFORCED when
    the vault work lands and the key exists on the Railway service (the
    key itself arrives with the vault work, not now; until then the
    browser-worker role validates an empty set). Deliberately EXCLUDED:
    `JWT_SECRET` (serves no HTTP, verifies no tokens) and
    `CREDENTIAL_ENCRYPTION_KEY` (never touches connected-app credential
    rows) — least privilege: a browser-worker compromise must not yield
    token-minting or credential-decryption capability.
  - Runtime env (`PORTAL_*`, `EVIDENCE_S3_*`) stays NON-boot-mandatory:
    absence already degrades honestly at run time
    (`CREDENTIAL_NOT_CONFIGURED` / `EVIDENCE_INCOMPLETE`).
- **Absent role variable = legacy behaviour**: exactly today's
  `validate_boot_secrets()` — ALL mandatory secrets required. Existing
  services untouched until explicitly role-tagged. **The legacy default
  is valid forever** — tagging is opt-in, never forced.
- **Unknown role value**: fail closed — `SecretConfigError` naming the
  valid roles (a typo must not silently become legacy-lenient or
  role-lenient).
- **Blast radius**: one file (`secrets.py`), one additive branch keyed on
  a new env var, plus unit tests for the role table. The three deployed
  services do not set the variable → zero behaviour change at merge.
  `validate_summary_model` / `validate_tenant_model_overrides` untouched
  (separate gates, not secrets).
- **Migration order**: (1) code lands, legacy default everywhere;
  (2) the browser-worker Railway service is tagged FIRST — declarative
  today (its CMD is `sleep infinity`; the gate attaches when the
  browser-worker gains a real service entrypoint post-spike);
  (3) web/worker/scheduler stay untagged indefinitely, tagged only if
  their sets ever diverge.

## Verification plan (arm I commit)

Transcripted: (i) two-schema invisibility counts both directions +
bucket prefix containment; (ii) live refusal + audit for CLI sweep
cross-prefix, library sign_url cross-key, and the guard test matrix;
regression: full browser_worker suite green; the (a)-style fixture batch
still ends REFERENCED under the session-derived prefix.
