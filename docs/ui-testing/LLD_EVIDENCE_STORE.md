# LLD — Phase 2.5: evidence store (R2 upload lifecycle, tenant-prefixed keys, signed URLs, arm J)

Status: DESIGN (this commit); implementation follows on GO.
Scope anchor: UI programme HLD/SAD/TAD v1.1 — evidence store; arm J
completion (an interrupted upload never yields a falsely-complete result).
Branch: `phase-ui-s2-spike`. Builds on 2.3 (queue), 2.4 (manifests), and
the session substrate (context-scoped scans).

Naming contract unchanged: screenshots and observation JSON are
OBSERVATION ARTIFACTS; evidence states describe artifact custody, not
page quality. "verdict" appears nowhere.

## Credentials and client

- Env names (run time only, never persisted or logged):
  `EVIDENCE_S3_ENDPOINT`, `EVIDENCE_S3_BUCKET`, `EVIDENCE_S3_ACCESS_KEY_ID`,
  `EVIDENCE_S3_SECRET_ACCESS_KEY`. Locally sourced from
  `~/.plimsol/evidence.env` (chmod 600) in the run command; the same four
  names exist on the Railway browser-worker service.
- Client: `boto3` (pinned in `requirements-browser.txt` — the worker
  uploads; the web tier only signs/reads later, but for the spike
  everything lives in the worker package), S3-compatible against R2:
  `endpoint_url=EVIDENCE_S3_ENDPOINT`, region `auto`, SigV4. The bucket is
  PRIVATE; nothing is ever made public.

## Object key scheme (DE-19 — tenant isolation INSIDE the bucket)

```
{tenant_schema}/{manifest_id}/{job_id}/{surface_key}/{attempt}/screenshot.png
{tenant_schema}/{manifest_id}/{job_id}/{surface_key}/{attempt}/observation.json
```

- `tenant_schema` (e.g. `tenant_1`) is DERIVED from the job's tenant
  context — `open_tenant_session` records the schema it scoped to on the
  session (`session.info["tenant_schema"]`), and `evidence.key_prefix`
  reads it from there. Callers never pass a tenant prefix; a caller
  cannot write into another tenant's prefix by argument.
- `attempt` in the path means a retried surface writes NEW objects; the
  DB row's keys move to the new objects (the previous attempt's objects
  become unreferenced — see orphan sweep; retention policy D5 decides
  their fate post-spike).
- Content types: `image/png`, `application/json`.

## Evidence lifecycle per result (TAD)

```
CAPTURED → UPLOADED → VERIFIED → REFERENCED
```

- **CAPTURED**: the worker holds the screenshot bytes + observation JSON
  in memory (nothing persisted yet).
- **UPLOADED**: both objects PUT to their keys; local sha256 + md5 + byte
  size computed; the sha256 is also written as object metadata
  (`x-amz-meta-sha256`).
- **VERIFIED**: a HEAD (`head_object`) confirmed key existence + byte size
  + checksum match — the checksum match is (a) the object's
  `x-amz-meta-sha256` equals the stored sha256 and (b) where the ETag is a
  plain MD5 (single-part PUT), the ETag equals the stored md5. Either
  mismatch ⇒ not VERIFIED.
- **REFERENCED**: the result row references the verified objects — the
  row is COMPLETE only here.

Persisted on `s4_ui_inspection_results` (migration chained on the
current tenant head):

| column | type | meaning |
|---|---|---|
| `evidence_state` | VARCHAR(24) NOT NULL DEFAULT `'EVIDENCE_INCOMPLETE'`, CHECK ∈ {CAPTURED, UPLOADED, VERIFIED, REFERENCED, EVIDENCE_INCOMPLETE} | custody stage reached, or the honest terminal |
| `evidence_keys` | JSONB NULL | `{"screenshot": key, "observation": key}` |
| `evidence_checksums` | JSONB NULL | `{"screenshot": {"sha256", "md5"}, "observation": {...}}` |
| `evidence_sizes` | JSONB NULL | `{"screenshot": bytes, "observation": bytes}` |
| `evidence_content_types` | JSONB NULL | `{"screenshot": "image/png", "observation": "application/json"}` |
| `evidence_detail` | JSONB NULL | `{"reached": <stage>, "error": <non-secret text>}` on incompleteness |
| `evidence_verified_at` | TIMESTAMPTZ NULL | set at VERIFIED |

The default `EVIDENCE_INCOMPLETE` is deliberate: a row that carries no
evidence record is incomplete by definition — never silently "complete".

### Completion rule (finalize_surface refuses false completeness)

`finalize_surface` writes the row with the evidence state the surface
actually REACHED. It never writes REFERENCED itself. REFERENCED is set
only by `mark_evidence_referenced`, which the consumer calls only after
`verify_evidence` returned VERIFIED. If the surface's evidence is still
below VERIFIED when the batch moves on, the row is written/updated with
`evidence_state = EVIDENCE_INCOMPLETE` and `evidence_detail =
{"reached": ..., "error": ...}` — the honest state — and the JOB
CONTINUES to the next surface. The surface stays re-doable idempotently
(2.3 UPSERT; a retry writes attempt-N+1 objects and re-references).

## Capture phase in `scan_page`

A `capture` phase is added AFTER stabilise (and after the landed-page
check) and BEFORE axe injection: `page.screenshot(full_page=True)` →
PNG bytes returned IN the scan result (`screenshot_png`), never written
to disk. The consumer pops it before the observation JSON is serialised
(bytes never enter the DB row or the JSON object). It is an observation
artifact of the stabilised page as the engine saw it.

## Signed URLs

- Presigned GET (`generate_presigned_url("get_object", ...)`), default
  expiry 24 h, generated ON DEMAND by a pure function `sign_url(key,
  expires_s=86400)`; nothing is stored. Spike surface: a CLI subcommand
  that prints a signed URL for a given result row's screenshot.
- Never a public URL; the bucket stays private; a past-expiry URL is
  refused by the store (asserted by test).

## Upload discipline and the two failure windows (stated explicitly)

Order per surface: capture → **upload** → **DB result write** (records
keys + checksums + sizes + content types + `evidence_state=UPLOADED`) →
**verify** (HEAD) → `mark_evidence_referenced` (→ `REFERENCED`).

1. **Crash between upload and DB write** ⇒ the objects exist, no row
   references them ⇒ ORPHANED OBJECTS. Handled by `sweep_orphans(prefix)`
   — lists the prefix and reports every object key that no result row's
   `evidence_keys` references (report-only in the spike: a function + a
   test, not a scheduled task, not a delete).
2. **Crash between DB write and verification** ⇒ the row exists with
   `evidence_state = UPLOADED` (below VERIFIED) ⇒ VISIBLY INCOMPLETE,
   never falsely complete. A later pass may verify and reference it, or
   a retry supersedes it.

Upload/verify FAILURE (not a crash — e.g. the endpoint is unroutable,
the bucket rejects, the checksum mismatches) ⇒ the row is written/updated
with `EVIDENCE_INCOMPLETE` + detail; the batch continues; the job's own
status is unaffected (the batch ran; its evidence custody is recorded
per surface). Arm J asserts: zero false REFERENCED, batch finishes,
every affected surface shows EVIDENCE_INCOMPLETE.

## Non-goals

- No retention automation (D5 policy is recorded; enforcement is
  post-spike). No deletes in the spike — the sweep reports.
- No web-tier viewer; the signed-URL CLI is the only read surface.
- No DOM-fragment capture yet (arrives with verdict-bearing results in
  Phase 3A); the spike proves the pipeline with screenshot + observation
  JSON.
- No scheduler/worker wiring; manual consume only; service CMD stays
  `sleep infinity`.

## Verification plan (transcripted)

a. A full batch (guest fixtures suffice) with evidence on: every surface
   ends REFERENCED; keys follow the scheme; one screenshot's 24 h signed
   URL opens in a browser.
b. ARM J: `EVIDENCE_S3_ENDPOINT` pointed at an unroutable address for one
   run ⇒ every surface EVIDENCE_INCOMPLETE (reached=CAPTURED, error
   names the connection failure), zero REFERENCED, batch succeeded.
c. Orphan sweep on the test prefix reports the planted orphan and
   nothing else.
Tests (gated on the env file): upload/verify/sign round-trip under
`{tenant}/spike-test/...` with cleanup; checksum-mismatch ⇒ not
VERIFIED; sweep finds a planted orphan; signed URL fetches via plain
urllib and a past-expiry URL is refused.
