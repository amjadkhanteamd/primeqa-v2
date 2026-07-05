# Plimsol (PrimeQA v2) — Full Codebase Review

> **READ-ONLY REVIEW.** This document is findings-only. Nothing in the tracked
> codebase was modified. Every proposed remediation is *described, not applied* —
> triage is the owner's next step. This file is intentionally left **uncommitted**.

| | |
|---|---|
| **HEAD** | `d462767` — impl(D-321): readable test-case body |
| **Branch** | `main` |
| **UTC date** | 2026-07-05 |
| **Working tree** | tracked files clean; untracked scratch/report/fixtures present (the known never-commit set) |
| **Scope** | `primeqa/` (283 files, ~74.8K LOC), `tests/` (302 files, ~84K LOC), `migrations/` (60), `alembic/` (~69 tenant revisions) |
| **Method** | 7 evidence-based passes + synthesis, multi-agent fan-out, adversarial verification of high/critical findings |

**Severity legend:** Critical = exploitable/data-loss/tenant-leak now · High = serious correctness/security defect on a real path · Medium = latent defect, drift, or robustness gap · Low = hygiene/maintainability.

Findings tagged `[UNVERIFIED HYPOTHESIS]` could not be pinned to cited executing code and are flagged as such per the review contract.

---

## PASS 1 — Inventory & Ground Truth

### Stack
- **Backend**: Python 3.11 (dev machine runs 3.14), Flask 3, SQLAlchemy 2, psycopg2, PostgreSQL (Railway) + pgvector.
- **Auth**: PyJWT (httponly cookies + Bearer), bcrypt, Fernet (`cryptography`) for credentials.
- **AI**: `anthropic` SDK via the single LLM gateway (`primeqa/intelligence/llm/`).
- **Scheduling**: apscheduler + croniter; Railway scheduler service.
- **Deploy**: Railway 3 services (web/worker/scheduler), Dockerfile + gunicorn, `Procfile`, `railway.toml`, `nixpacks.toml`.
- **Test runner**: pytest (`pytest.ini`), integration tests against the real Railway DB.

### Entry points
- `primeqa/app.py` (183 LOC) — Flask factory `create_app()`; registers 6 blueprints (`core_bp`, `test_management_bp`, `execution_bp`, `intelligence_bp`, `release_bp`, `views_bp`); installs observability + CSRF; global 404/405/500 handlers; `/health`. Boots fail-closed on missing prod secrets (`validate_boot_secrets`).
- `primeqa/worker.py` (1059 LOC) — background job consumer (`python -m primeqa.worker`): S1 sync jobs, S4 execution jobs, AI enrichment queue.
- `primeqa/scheduler.py` (330 LOC) — reaper + dead-man's switch + job-queue firer.
- `primeqa/views.py` (3708 LOC) — server-rendered web UI routes.

### Substrate → package map (S1–S8, live engine)
| Substrate | Package(s) | LOC (pkg) |
|---|---|---|
| S1 semantic org model | `semantic/` (4091) + `sync/` (9452) | 13.5K |
| S2 test representation | `test_representation/` | 9247 |
| S3 generation | `generation/` | 9664 |
| S4 execution | `execution_engine/` | 6470 |
| S5 knowledge | `knowledge/` | 862 |
| S6 interpretation | `interpretation/` | 1925 |
| S7 conversation | `conversation/` | 525 |
| S8 evolution | `evolution/` | 1270 |

Cross-cutting: `core/` (3219, auth/tenants/users/envs/connections), `intelligence/` (13106 — LLM gateway + `substrate_decision`), `release/` (1095), `runs/` (294), `integrations/` (2675 — `sf_client`), `metadata_bridge/` (976), `shared/` (762), `system_validation/` (422), `vector/` (72).

Legacy (present, mostly gutted post-v1): `metadata/` (1864 — `worker_runner._oauth_token` still live for credential resolution), `execution/` (189 — routes survive), `test_management/` (1261 — `service.py` survives).

### Data layer
- **Public/v2**: 60 numbered SQL migrations `migrations/001..059` (idempotent 016+), run via `psql`. Note two `053_*` files (`_drop_v1_product_tables` + `_scheduled_runs_substrate_test`).
- **Substrate**: schema-per-tenant DDL via alembic (`alembic/versions/tenant/` ~69 revisions + `shared/`).

### Raw-SQL surface (grounds Pass 2/6)
- `text()` used in **382** sites (parameterized SQLAlchemy text is the norm).
- **8** sites interpolate via f-string into SQL — all schema/tenant-context or DDL: `worker.py:186,203,204,240,324`, `semantic/connection.py:195`, `sync/materialize.py:847`, `sync/readiness.py:195`. The `_tid`/`schema_name` interpolations are the ones to scrutinize (see Pass 2).

### Exception surface (grounds Pass 5)
- **0** bare `except:`; **243** `except Exception`; **~28** `except … : pass` swallow candidates.

### Hygiene note (tracked-file cleanliness)
- Nothing improper is *tracked* — no `scratch_*.py`, `__pycache__`, `.pyc`, or the security-review report are committed (verified `git ls-files`). `.gitignore` covers `__pycache__/` + `*.py[codz]`. The ~32 `scratch_*.py`, `reports/security-review-2026-06-18.md`, and `sandbox_fixtures/` remain **untracked** (the documented never-commit set). This is clean, but the volume of untracked scratch in the repo root is a housekeeping smell (Pass 4 note).

_Passes 2–7 and synthesis follow. Written incrementally; a pass appears here only once complete._

---

## PASS 2 — Security (Salesforce / multi-tenant threat model)

Method: 5 area-scoped finders (credentials, tenant/org isolation, SQLi/SSRF, run-path auth, deps/vector) → each finding adversarially re-verified against the cited code. Dependency scan (`pip-audit -r requirements.txt`) = **no known CVEs**. Bandit = 0 high, 81 medium (all B608 f-string SQL — see SEC-R1). **17 raw findings → 9 CONFIRMED, 7 PLAUSIBLE, 1 REFUTED.**

| ID | Sev | Finding | Location |
|---|---|---|---|
| SEC-1 | **High** (Critical blast radius) | Any authenticated user (incl. `viewer`) reads **fully-decrypted** SF/Jira/LLM secrets | `core/routes.py:322` |
| SEC-2 | **High** | `add_member`/`add_environment` don't tenant-scope client-supplied ids → cross-tenant PII/org-URL disclosure | `core/service.py:537,549` |
| SEC-3 | **High** | SSRF via unvalidated `sf_instance_url` — server sends the org's access-token to an attacker host | `core/service.py:336` |
| SEC-4 | **High** | `/releases/<id>/run` enqueues live **production** runs with no `is_production` gate; any Member (ba/tester) | `views.py:3506` |
| SEC-5 | **Medium** | SSRF token POST exfiltrates OAuth `client_id`/`client_secret`(/password) to attacker host | `metadata/worker_runner.py:179` |
| SEC-6 | Low | `read_org_model` renders the org-blind blend of all orgs in the metadata browse UI | `metadata_bridge/s1_sync_console.py:301` |
| SEC-7 | Low | CI webhook enqueues runs against a caller-supplied production env with no `is_production` gate | `release/routes.py:454` |
| SEC-8 | Low | `views.get_current_user` uses forgeable `os.getenv("JWT_SECRET","dev-secret-change-me")` default (not the fail-closed chokepoint) | `views.py:25` |
| SEC-9 | Low | Raw Salesforce/OAuth error bodies echoed into a redirect query param (history/Referer/proxy logs) | `core/service.py:447` |

### SEC-1 — [High; Critical blast radius] Plaintext credential exfiltration by any authenticated role
`primeqa/core/routes.py:322`
```python
@core_bp.route("/api/connections/<int:conn_id>", methods=["GET"])
@require_auth
def get_connection(conn_id):
    conn = svc.get_connection(conn_id, request.user["tenant_id"])
    return jsonify(conn), 200
```
**Why it matters:** `@require_auth` requires only a valid JWT with `sub`+`tenant_id`; role defaults to `viewer` when absent (`core/auth.py:58`), so **no role is enforced**. `svc.get_connection` returns `get_connection_decrypted(...)` verbatim (`core/service.py:413`), whose `config` is fully decrypted — `client_secret`, org `password`, Jira `api_token`, Anthropic/Voyage `api_key`, `refresh_token` (`core/repository.py:363-399`). `jsonify(conn)` ships all of it in cleartext. Asymmetry confirms it's an oversight: the *list* endpoint uses the redacted `_conn_dict`, and the *web* detail view (`views.py:1492`) gates the same decrypted call behind `@role_required("admin")`. Result: any low-privilege user can steal every connected Salesforce org's credentials + all integration keys for their tenant → full compromise of those orgs. (Tenant-scoped, so not cross-tenant — hence "High," but the within-tenant blast radius is Critical.)
**Proposed remediation:** gate at `@require_role("admin")` to match the mutating connection routes, AND strip/mask secret fields from the HTTP response even for admins. Reserve `get_connection_decrypted()` for server-side credential resolution only (sync/execution/worker).

### SEC-2 — [High] `add_member` / `add_environment` skip tenant-scoping of the linked id
`primeqa/core/service.py:537`
```python
def add_member(self, group_id, tenant_id, user_id, added_by):
    group = self.group_repo.get_group(group_id, tenant_id)
    if not group:
        raise ValueError("Group not found")
    self.group_repo.add_member(group_id, user_id, added_by)   # user_id never tenant-checked
```
**Why it matters:** the *group* is validated against the caller's tenant, but the client-supplied `user_id` (and `environment_id` in `add_environment`, `service.py:549`) is not. `group_members` has no tenant column, so — exactly as the sibling `release/service.py add_requirement` fix documents — an admin in tenant A can link tenant B's user/environment into a group and surface their PII / org URLs via the group detail view. Cross-tenant isolation breach.
**Proposed remediation:** mirror `release/service.py:108` — `query(User.id).filter(User.id==user_id, User.tenant_id==tenant_id).first()`; raise if absent. Same for `environment_id`.

### SEC-3 — [High] SSRF via unvalidated `sf_instance_url` (access-token attached)
`primeqa/core/service.py:336`
```python
url = f"{env.sf_instance_url.rstrip('/')}/services/data/v{env.sf_api_version}/"
resp = http_requests.get(url, headers={"Authorization": f"Bearer {creds['access_token']}"}, timeout=15)
```
**Why it matters:** `sf_instance_url` is user-supplied at environment creation (`core/routes.py:206`) and stored with **no** scheme/host validation, allowlist, or private-IP block (grep for `urlparse`/`169.254`/`is_private`/allowlist in the request path = nothing). The server then issues an authenticated request — attaching the org's decrypted access-token — to whatever host the caller chose (cloud metadata endpoint `169.254.169.254`, intranet, etc.).
**Proposed remediation:** one `validate_sf_instance_url()` guard at write time *and* before every outbound call: require `https`, resolve host and reject RFC-1918/loopback/link-local/ULA, allowlist Salesforce domains (`*.salesforce.com`/`*.force.com`/`*.lightning.force.com`).

### SEC-4 — [High] `/releases/<id>/run` enqueues production runs with no `is_production` gate
`primeqa/views.py:3506`
```python
@views_bp.route("/releases/<int:release_id>/run", methods=["POST"])
@role_required("admin", "tester")
def releases_run(release_id):
    ...  # no is_production check, no confirm_production field
```
**Why it matters:** `floor_tier(["admin","tester"]) = Tier.MEMBER` (both `ba` and `tester` map to Member), so any Member can POST this. It enqueues to `s4_execution_jobs` via `enqueue_claims_for_requirements` (no production gate downstream), and the env dropdown lists **all** accessible environments including production. This bypasses the `is_production` + `caller_tier < ADMIN` protection the constitution/authz model says every dispatch must honor, and that the synchronous run paths do enforce.
**Proposed remediation:** before enqueue, load the env and apply `environment_can_bulk_run(env, confirm_production)` requiring an explicit `confirm_production`, and fail closed on `env.is_production` for non-Admins — matching the other run paths.

### SEC-5 — [Medium] SSRF token POST exfiltrates OAuth client credentials
`primeqa/metadata/worker_runner.py:179` — the OAuth token POST targets `login_url = cfg.get("instance_url")` (unvalidated) with a body carrying `client_id`, `client_secret`, and in the password flow `username`+`password`. A caller who points `instance_url` at their host receives the org's OAuth secrets. Same root cause as SEC-3; **remediation:** the same `validate_sf_instance_url()` guard enforced at call time (secrets ride the request body).

### SEC-6 — [Low] `read_org_model` shows the org-blind blend
`metadata_bridge/s1_sync_console.py:301` builds `SemanticOrgModel(conn)` with no `connected_org_id`, so on a >1-org tenant the metadata browse UI (`views.py:1012`) shows the **union** of all orgs' objects/fields/VRs. Within-tenant only (no cross-tenant leak), but violates per-org isolation that `/ask` (`conversation_bridge.py:139`) and execution enforce. **Remediation:** thread the browsed env's `connected_org_id`, or label the view as a multi-org blend.

### SEC-7 — [Low] CI webhook lacks production gate
`release/routes.py:454` — `ci_webhook_trigger` tenant-scopes the `environment_id` (good) but never checks `env.is_production` before enqueuing mutating runs. A holder of the single global `WEBHOOK_SECRET` can trigger production data-recipe runs. HMAC-gated → Low. **Remediation:** reject/require explicit confirm when `env.is_production`.

### SEC-8 — [Low] Web JWT verify path uses a forgeable default secret
`views.py:25` — `JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me")` bound at import and used to verify every web cookie (`views.py:38`), bypassing the fail-closed `core/secrets.get_jwt_secret()` chokepoint (F-3). Mitigated in production by `validate_boot_secrets()` at `create_app` — hence Low — but it's a second, weaker secret-resolution path. **Remediation:** call `core.secrets.get_jwt_secret()` inside `get_current_user()`. (Related: SEC-P1 flags the *signing* path `core/service.py:28,224` with the same default.)

### SEC-9 — [Low] Upstream error bodies echoed into redirect query param
`core/service.py:447` returns `token_resp.text[:500]` / `str(e)` as `detail`, which `views.py:1521` places verbatim into `redirect(f"/connections/{id}?message={detail}")` → browser history, Referer, proxy logs. All confirmed error-path bodies (no token-bearing success body leaks; Jinja auto-escape blocks XSS). **Remediation:** generic user message; log the body server-side only.

### PLAUSIBLE (real smell, exploitability unproven / mitigated)
- **SEC-P1 [Low]** JWT *signing* path `core/service.py:28,224` also uses the `dev-secret-change-me` default, not the fail-closed chokepoint — same class as SEC-8, boot-guard-mitigated.
- **SEC-P2 [Low]** `app.tenant_id`/`search_path` set via **f-string** at `worker.py:186,203,204` + `semantic/connection.py:195` (while `worker.py:188` correctly binds `:tid`). Traced not currently reachable — values come from `_discover_tenant_schemas` (regex `^tenant_[0-9]+$`) / `_resolve_schema_name` (`int>0`). Latent injection footgun if a future caller passes user input. **Remediation:** bind `app.tenant_id` everywhere; add a `schema == f"tenant_{id}"` assertion at each SET site. _(This is the consolidation of three near-duplicate finder reports.)_
- **SEC-P3 [Low]** Decision engine `substrate_decision.py:218` reads the org-blind S1 seq for grounding-staleness on multi-org tenants → could mis-label a release decision as stale/fresh. Mitigated today by the S8 recompute guardrail. Same per-org-deferred root as SEC-6.
- **SEC-P4 [Low]** SSRF via unvalidated Jira `base_url` in `test_connection` (`core/service.py:457`) — admin-gated; same guard as SEC-3.
- **SEC-P5 [Low]** CSRF correctly skips safe methods (`core/csrf.py:107`); correctness depends on the invariant "no GET route mutates." Grep confirmed zero mutating GETs today. **Remediation:** add a CI guard test that fails if a GET route name matches a mutation verb.

### REFUTED (checked, cleared)
- **SEC-R1** — **No SQL injection.** Every f-string/`text()` SQL site (`worker.py:186,203,204,240,324`; `semantic/connection.py:195`; `sync/materialize.py:847`; `sync/readiness.py:195`; `sync/engine.py:819`; `semantic/query.py:417`; `semantic/derivation.py:84`; `intelligence/s4_execution_console.py:266`) interpolates only server-derived values (int-validated schema/tenant, static column lists). The 81 bandit B608 mediums all map here. Clean — but SEC-P2 records the hardening recommendation so it can't regress.

---

## PASS 3 — Substrate Integrity & Architecture (vs the constitution)

The highest-value pass. Five finders (semantic-leakage, boundary violations, taxonomy/identity, JSONB/graph, fail-loud) each surfaced the **same marquee semantic-leakage root** from a different angle, plus three other distinct violations. **18 raw finder reports → after de-dup: 4 substantive violations (3 High, 1 Medium) + 2 checked-and-cleared (by-design) + the marquee cluster.**

> **Note on verifier verdicts.** The marquee "empty `semantic_conditions`" issue was reported ~10 times; the adversarial verifiers split — CONFIRMED on the *identity-rides-prose* framing (High, twice), PLAUSIBLE on the *empty-conditions* framing, REFUTED on some *stranded-value* framings (arguing the empty-conditions is the intended "D-299 idiom"). I reconcile these as **one CONFIRMED root defect**: every load-bearing fact was verified *verbatim* by multiple agents (empty `SemanticConditionsBody` at `emission.py:1825`; the band value living only in `create_fields`; `triggering_action.description` folded into `identity_hash`; coverage omitting the entry field). The "it's the intended idiom" defense is itself the finding — it *contradicts* `primitives.py:191-195`, which declares that field non-identity-bearing, and the sibling acceptance archetype in the same file does it correctly. The task brief also names this as a known-real class to enumerate. It stands.

| ID | Sev | Finding | Location | Principle |
|---|---|---|---|---|
| SUB-1 | **High** | **Marquee semantic leakage** — automation-effect claims strand the asserted boundary value in the operational recipe (empty `semantic_conditions`) across **all** sub-shapes → identity rides non-identity-bearing prose (governance/approval risk) + derived coverage under-reports | `generation/emission.py:1825,1644,1654` + `models/primitives.py:191` | Semantic Discipline Rule; claims identity-bearing; coverage derived |
| SUB-2 | **High** | Flow write-effects re-parsed from a raw Metadata **JSONB blob in app-layer Python** at generation time instead of normalized typed edges | `semantic/entity_attributes.py:556` → `generation/governance_core.py:600` | DB = graph store: normalized entities + typed edges, not app-layer JSONB traversal |
| SUB-3 | **High** | Automation-effect resolver's **no-name branch silently binds `flows[0]`** when no Flow verifiably produces the claimed effect (degrade instead of refuse; asymmetric with the named branch which refuses) | `generation/governance_core.py:1725` | Ground-or-refuse / fail loud, no silent fallback |
| SUB-4 | **Medium** | S6 interpretation reader **fabricates `is_active=True` / `is_createable=True`** on missing S1 detail — and that default drives cause attribution | `interpretation/s1_reader.py:66` | Fail loud, never hallucinate metadata; interpretation must not invent truth |

### SUB-1 — [High] Marquee semantic-vs-operational leakage (automation-effect claims)
`primeqa/generation/emission.py:1825` (+ `:1644`, `:1654`; `primitives.py:191`)
```python
conditions = SemanticConditionsBody(conditions=[])          # emission.py:1825 — ALWAYS empty
...
sr_event = EventDescriptor(trigger_kind="data-mutation-trigger",
    description=f"creating a {object_api} with {gate} — {g.requirement_excerpt}")  # :1644 — value lives here, in prose
...
affected_fields=[field_ref],   # :1654 — EFFECT field only; entry-gate g.trigger_fields never added
```
**The defect.** An automation-effect *band* claim asserts *"under entry state X (e.g. `Loan_Amount=649`) the Flow produces effect E (`Risk_Rating='High'`)."* The entry value **X is the semantic scope of the assertion** — but `_author_automation_effect` writes X **only** into the recipe's `create_fields` (operational / `causal_initiation` plane) and narrates it into the free-text `triggering_action.description`, while `semantic_conditions` is emitted **empty**. Three verified consequences:
1. **Identity rides non-identity-bearing prose (High).** With X absent from `semantic_conditions`, the *only* thing distinguishing two band claims (`649→High` vs `680→High`, identical in every structured field) is the `gate` substring inside `triggering_action.description`. That field is documented "Diagnostic, not identity-bearing" (`primitives.py:191-195`), yet the canonicalizer walks `triggering_action` generically so the prose **is** folded into `identity_hash` (`identity_hash.py:116`). Since `identity_hash` gates approvals, **rewording the human description or reformatting `gate` silently re-keys the claim and invalidates prior approvals** — and two same-output bands can collide/dedup on identical prose. The internal contradiction is stark: the sibling acceptance archetype in the same file rides `semantic_conditions` correctly and comments "just-below and at-threshold are distinct claims"; the automation-effect path does not.
2. **Derived coverage under-reports (Medium).** `extract_coverage` walks only `asserted_truth` + `semantic_conditions` (`coverage.py:76`). The entry-gate fields (`g.trigger_fields`) are in neither, so they produce **zero** `test_claim_coverage` rows — a schema/field change to an entry-gate field won't see this claim as depending on it (feeds S8 grounding + the decision engine wrong coverage).
3. **Spread.** Confirmed across the same-record, parent-stamp (`:1684`), and cross-object (`:1745`) create-shapes; the finders also traced the same stranding into the D-306 update-observe (`update_trigger_fields`, `:1629`) and D-307 absence (staged state) shapes. It is a *class*, not a single site — exactly as the brief anticipated.

**Proposed remediation:** author the grounded `g.trigger_fields` / `update_trigger_fields` into `semantic_conditions` via the existing `_conditions_body(...)` helper as `Condition(subject=<S1 field ref>, predicate='equals', value=<band value>)`. Then (a) identity is carried by the structured predicate (prose becomes pure diagnostics), and (b) the `subject` refs are automatically walked by `extract_coverage` — one change fixes both consequences. Apply uniformly to all five automation-effect sub-shapes.

### SUB-2 — [High] App-layer graph traversal over a raw JSONB blob (should be typed edges)
`semantic/entity_attributes.py:556` → consumed by `generation/governance_core.py:600` — `flow_effects` walks the Flow's raw Metadata JSONB nested structure (`Metadata.recordUpdates[].inputAssignments[]` for same-record write-effects; `Metadata.recordCreates[].object` for cross-object creates) in **Python at generation time**. Its consumer `_flows_producing_effect` re-parses this on every automation-effect grounding decision. Per the constitution, these are semantic relationships that should be **normalized typed edges** materialized during sync (Postgres as a graph store), not app-layer nested-key traversal of a JSONB dump. **Remediation:** materialize the Flow effect-set as typed edges at sync time (e.g. `WRITES_FIELD` Flow→Field with a value property, `CREATES_RECORD_OF` Flow→Object); derive grounding by querying edges, not by re-parsing JSONB. (Cross-references DATA-8/normalization theme.)

### SUB-3 — [High] Degrade-instead-of-refuse in automation-effect grounding
`generation/governance_core.py:1725` — the **named** branch (`:1665-1692`) correctly refuses (`emission_deferred`) when `_flows_producing_effect` finds no Flow whose parsed Metadata produces the claimed effect. But the **no-name** branch (`:1705-1725`) falls through to `flow_ent = flows[0]` — binding the *first* Flow on the subject even when **none verifiably produces the effect**. This grounds an assertion against an automation that may not cause it — the substrate then reports a green/verifiable claim that isn't actually grounded, violating ground-or-refuse. **Remediation:** make the no-name branch symmetric — when the producer set is empty, refuse (`emission_deferred`, "no Flow on the subject produces the claimed effect — name the automation").

### SUB-4 — [Medium] Interpretation fabricates active/createable metadata on missing grounding
`interpretation/s1_reader.py:66` — the S6 reader defaults a ValidationRule/Flow to `is_active=True` and a field to `is_createable=True` when the S1 detail row is absent/unsynced. This is **not** diagnostic: `attribution.py:209` consumes it to decide causes (`active = [f for f in ... if f.is_active]`). So an *unsynced* rule is treated as an *active* one, and cause attribution — the product's core value — is computed on invented metadata. Execution captures truth; interpretation must not invent it. **Remediation:** treat a missing `is_active`/`is_createable` as **UNKNOWN** (tri-state), and have attribution surface "grounding incomplete" rather than silently assuming active/createable.

### CHECKED & CLEARED (by-design — recorded so they're not re-flagged)
- **Release decision reads S6 verdict alongside but decides `Verified` on the raw S4 outcome** (`substrate_decision.py:182`). Finder flagged a possible execution/interpretation boundary blur; verifier **refuted** — the `_CLAIM_RUNS_SQL` LEFT-JOINs `s6_interpretations.verdict` and the current `Verified` predicate on `lr['outcome']` is the intended contract. *Worth a design conversation* given CLAUDE.md theme #3 ("close the decision loop on the new engine") — routing `Verified` through the S6 interpreted verdict/`failure_category` would make a `normalization=permanent` errored run be decided by interpretation rather than the raw string — but it is not a violation today.
- **`entities.attributes` stores the full normalized raw Tooling record as JSONB** (`materialize.py:687`). Looks like "denormalized dumping," but verifier **refuted** — it's the *ratified* storage contract (D-20x) with an explicit promotion rule (D-018/D-025: hot, cross-population-queried fields get promoted to detail-table columns/edges). Intended layering, not drift. (If specific nested keys become hot-path SQL filters, promote them per that rule — see DATA/normalization notes.)

### Taxonomy verdict (checked): **no explosion.** 17 claim kinds over 5 archetypes, and automation-effect correctly uses an `automation_primitive` sub-discriminator (`validation_rule`/`flow`/`apex_trigger`/`process_builder`/`approval_process`/`formula`) rather than a kind-per-mechanism — this is the constitution's "fewer kinds, richer sub-discriminators" done right. The one drift *hazard* (not yet a defect): sibling `data_behavior` kinds are inconsistent about where a staged/entry value lives (some in `semantic_conditions`, automation-effect in prose) — SUB-1's fix also resolves this by establishing `semantic_conditions` as the single home.

---

## PASS 4 — Dead / Unused Code

Method: 4 finders (dead modules, orphaned migrations/tables, dead flags/duplicated logic, unused imports/deps) → each candidate adversarially re-checked for dynamic-dispatch reachability (getattr/importlib/blueprint/Jinja/event-listener/registry) before confirming. **30 raw → 27 CONFIRMED, 1 PLAUSIBLE, 2 REFUTED.** Nothing improper is *tracked* (no scratch/pycache committed — Pass 1); this is legitimate accumulated post-v1-retirement debt. Grouped by cluster below (individual Low items rolled up).

| ID | Sev | Cluster | Key locations |
|---|---|---|---|
| DEAD-1 | **Medium** | v1 `metadata` module is dead except `_oauth_token` — the whole sync-driver chain + `MetadataService` never execute | `metadata/worker_runner.py:33`, `metadata/service.py` |
| DEAD-2 | **Medium** | Three **dead feature flags** with no live gate + one orphaned enrichment lane | `core/models.py:190,200`; `intelligence/interpretation_phrasing.py:128` |
| DEAD-3 | Low | ~8 **orphaned v1 ORM models / undropped tables** (no live writer or reader) | `intelligence/models.py`, `test_management/models.py`, `vector/models.py`, `core/permissions.py:50` |
| DEAD-4 | Low | `vector/` service+repo are **`pass`-only stubs**, zero call sites (pre-verified in Pass 1) | `vector/service.py`, `vector/repository.py` |
| DEAD-5 | Low | Two **declared dependencies never imported**: `numpy`, `apscheduler` | `requirements.txt:10,14` |
| DEAD-6 | Low | Small dead code: unused imports (F401), duplicate imports (F811), a discarded DB call, dead assignments, an uninstantiated class | multiple |
| DEAD-7 | Low | Stale **permission-set remnants in comments** after the D-245 deletion (code correct, comments misdescribe) | `app.py:75` |
| DEAD-8 | Low (plausible) | Duplicated credential/SF-client resolver across `sync` and `execution_engine` (constitution: one resolver, not two) | `execution_engine/credentials.py:35` + `sync/credentials.py` |

### DEAD-1 — [Medium] The v1 `metadata` module is dead except the credential shim
`primeqa/metadata/worker_runner.py:33` — the module docstring claims "The Railway `worker` process calls `poll_and_run_once(...)` on each tick," but `worker.py:worker_tick` calls only `s3/s4/s1_sync/enrichment` ticks; `poll_and_run_once` / `_claim_next` / `_run_claimed` / `reap_stalled_jobs` have **zero** callers repo-wide (grep returns only defs + the stale docstring). Consequently `MetadataService` (`metadata/service.py`, ~52KB) is instantiated only inside that dead chain (+ one unit test) — 6 of its 9 public methods have zero callers, and `list_pending_impacts` even imports `MetadataImpact`, a class dropped with the v1 tables (a latent `ImportError` if ever reached). The live sync path is the separate `SyncEngine.run_sync`. Corroborated by `DECISIONS_LOG` D-221 R3 ("worker_runner claim/run functions become dead code") and the 2026-06-18 security review. **Only `_oauth_token` is live** (imported by `execution_engine/credentials.py:46` + `sync/credentials.py:65`).
**Proposed remediation:** delete the dead driver chain + `MetadataService` (after confirming its `meta_*` tables are gone), keep `_oauth_token`, and rewrite the module docstring to describe it as the credential shim only. Migrate/drop the sole `check_drift` unit test.

### DEAD-2 — [Medium] Dead feature flags
`core/models.py:190,200` + `intelligence/interpretation_phrasing.py:128` — three tenant flags gate nothing live:
- **`llm_enable_story_enrichment`** — the story-view enricher retired with v1 (migration 053); no live reader.
- **`llm_enable_domain_packs`** — the S5→S3 domain-pack consumption was never wired (D-156 "settled as not wired"); the flag has no effect.
- **`llm_enable_interpretation_phrasing`** + the whole `interpretation_phrasing.py` lane — no live caller.

These are misleading: a superadmin can toggle them and nothing changes. **Proposed remediation:** remove the flags (and the orphaned `interpretation_phrasing` lane), or wire the one that's intended (domain packs is the plausible keep — it's a documented deferred feature, not retired).

### DEAD-3 / DEAD-4 — [Low] Orphaned v1 ORM models + undropped tables
No live writer *and*/or reader (verified — grep hits were name-collisions on `sf.`/`reader.`/`metadata_repo.`, not these classes): `AgentFixAttempt`+`agent_fix_attempts` (v1 fix-and-rerun), `explanation_requests`+`step_causal_links` (v1 explanation engine, superseded by S6), `Embedding`+`embeddings` (real path writes `entities.embedding`), `NotificationPreference`+`notification_preferences` (registered only), `entity_dependencies`+`failure_patterns` (no reachable INSERT), `CustomField`/`CustomFieldValue`/`StepTemplate`/`Tag` (v1 test-mgmt), `S4CreatedRecord` (ORM unused; the table is driven entirely by raw SQL — a real inconsistency worth noting). Plus **DEAD-4** the `vector/` stubs. **Proposed remediation:** drop the orphaned models; schedule `DROP TABLE` migrations for the tables the v1 retirement missed (053/056/057 dropped many but not these). Verify no external analytics reads them first.

### DEAD-5 → DEAD-8 — [Low] Deps, small dead code, stale comments, duplicated resolver
- **DEAD-5** `requirements.txt` declares `numpy` and `apscheduler` but neither is imported anywhere (verified: `croniter` is the real scheduler; the `numpy` grep hits were `i`+`np.get`). Remove both from `requirements.txt`.
- **DEAD-6** F401 unused imports (e.g. `views.py:10 url_for`, `gateway.py:25 provider_invoke`, `test_management/routes.py:23`), F811 duplicate imports (`execution_engine/run.py:34`, `governance_core.py:2483`), a fetched-then-discarded `test_plan` DB call (`release/service.py:79` — dead work), a dead `semantic_fields` assignment (`data_executor.py:277`), and an uninstantiated `StepValidator` (`test_management/step_schema.py:86`). All safe prunes.
- **DEAD-7** `app.py:75` (+ neighbors) comments still describe the deleted "permission-set union" (migration 039); the code is correct post-D-245 but the comments misdescribe it. Update comments.
- **DEAD-8 [plausible]** credential/SF-client resolution is duplicated across `execution_engine/credentials.py` and `sync/credentials.py` (both resolve env→connection→`_oauth_token`→client). The constitution's "one resolver, not two" argues for a shared resolver; the current split is a deliberate boundary-decoupling (documented in `sync/credentials.py`), so this is a judgment call, not a clear defect — flagged for the owner.

### REFUTED (not actually dead)
- `MetadataRepository` — verifier found it reachable (not solely via the dead path).
- `MetadataParityChecker`/`ParityReport` (`metadata_bridge/parity.py`) — legitimate test infrastructure, not dead product code.

_(The 053 migration collision also surfaced here as a dead-migration finding; it's reported once as DATA-2 in Pass 6.)_

---

## PASS 5 — Correctness & Robustness

Method: 4 finders (swallowed exceptions, None/parse hazards, transaction integrity, concurrency/versioning) focused on the run + generation + sync paths → adversarial verify. **11 raw → 8 CONFIRMED, 2 PLAUSIBLE, 1 REFUTED.**

| ID | Sev | Finding | Location |
|---|---|---|---|
| CORR-1 | **High** | Sync gap-flush `except Exception` can finalize a **data-losing sync as `success`** instead of `partial_success` | `sync/engine.py:572` |
| CORR-2 | **High** | S4 job `complete()` has **no terminal-state guard** — a slow worker resurrects the reaper's `failed` job back to `completed` | `execution_engine/jobs.py:208` |
| CORR-3 | Medium | S4→S2 `report_run_outcome` is read-then-INSERT with **no ON CONFLICT** — two concurrent first-runs collide on `recipe_id` PK and roll back the run evidence | `test_representation/coordinator.py:1801` |
| CORR-4 | Medium | LLM rate-limit `check()` **fails OPEN** on any read error → calls bypass per-tenant spend/rate caps | `intelligence/llm/limits.py:265` |
| CORR-5 | Low | Substrate-decision read errors are **indistinguishable from "no evidence"** → mislabels a failure as absence (fail-closed but misleading) | `intelligence/substrate_decision.py:606` |
| CORR-6 | Low | `llm_usage_log` write failure silently drops cost/usage accounting (gateway says "always written") | `intelligence/llm/usage.py:91` |
| CORR-7 | Low | `enrichment_tick` crash between subtick commit and counter commit permanently under-counts telemetry | `worker.py:719` |
| CORR-8 | Low | Recipe-defect assertion raised after teardown discards the run evidence (no `errored` row) in the sync path | `execution_engine/data_executor.py:798` |

### CORR-1 — [High] Data-losing sync finalizes as `success`
`primeqa/sync/engine.py:572`
```python
try:
    with conn.begin_nested():
        readiness.record_run_gaps(conn, connected_org_id, genuine_gap_count(gaps), gaps)
except Exception as e:
    logger.warning("could not record %d metadata gap(s) ... finalizing without them ...", len(gaps), ...)
```
**Why it matters:** this is the Phase-2 Slice-B fail-loud chokepoint. `record_run_gaps` writes the genuine-gap count to `sync_runs.permission_gaps`; `maybe_finalize_run` then finalizes `partial_success` iff `permission_gaps > 0`, else `success`. The `begin_nested()` savepoint means a `record_run_gaps` failure rolls back only the savepoint, leaving the outer txn healthy — so the bare `except Exception` swallows the failure, `permission_gaps` stays at its DEFAULT 0, and the run finalizes clean `success` **even though real Salesforce metadata was dropped**. The catch is far broader than its stated benign cause (a missing column) — `readiness.py:296` even asserts "migrate-first guarantees the column," so on a correct deployment it only ever swallows *transient* DB errors. Downstream S3/S4 then run against a silently-incomplete org model believing the sync succeeded — the exact false-success the design exists to prevent. (Compound trigger: genuine gaps present *and* the gap-write itself failing — hence not every-run, but High-impact when it hits.)
**Proposed remediation:** narrow the catch to the missing-column case (or pre-check the column once); on any other failure, re-raise (finalize `failed`) or force `partial_success` explicitly. Invariant: a run that surfaced ≥1 genuine gap must never finalize `success`.

### CORR-2 — [High] S4 job `complete()` can overwrite the reaper's `failed`
`primeqa/execution_engine/jobs.py:208`
**Why it matters:** for any live-org run exceeding the stale-timeout (provisioning + multiple SF REST roundtrips + reverse-order cleanup — the docstring itself warns "the timeout must exceed the longest legitimate run"), the reaper calls `fail(jid, 'stale_timeout')` while the worker is still alive. When the worker finishes, `_finish`/`complete()` has **no terminal-state guard**, so it overwrites `failed` → `completed`. The job now reports success despite having been declared dead — and any compensating action the reaper triggered (retry, alert) is now inconsistent with a "completed" row. **Remediation:** add `AND status NOT IN (_TERMINAL)` to `_finish` so a reaper-terminalized job can't be resurrected; consider a longer S4 stale-timeout.

### CORR-3 — [Medium] Concurrent first-run collides on `recipe_id` PK, rolling back evidence
`test_representation/coordinator.py:1801` — the manual/UI sync run path (`run_recipe_execution_for_tenant`) bypasses the `s4_execution_jobs` active-partial-unique interlock (only the async queued path dedupes by `(test_id, environment_id)`). Two concurrent first-time runs of the same recipe both do read-then-INSERT into `test_recipe_runtime_state` with no `ON CONFLICT`, so one hits the `recipe_id` PK, raises, and rolls back that run's evidence. **Remediation:** make the first-write an idempotent `INSERT ... ON CONFLICT (recipe_id) DO UPDATE` (respecting the documented first-writer semantics). _(CORR-P2 is the same class on the S8 grounding store.)_

### CORR-4 → CORR-8 — [Medium/Low] Silent-degradation + partial-state cluster
- **CORR-4** `limits.py:265` — `check()` returns `allowed=True` on any exception reading `llm_usage_log`, so a DB hiccup lets a tenant blow past minute/hour/daily-spend caps. The fail-open is only justified for the "gateway tables absent" case. **Fix:** detect tables-absent explicitly; fail-closed/retry on a transient error where the tables exist.
- **CORR-5** `substrate_decision.py:606` — a broad catch returns `available: False`, which the composer maps to `no_go` "No substrate test evidence." Safety direction is right (fail-closed) but a *read error* is reported as *evidence absence*. **Fix:** return `error: True` so the composer can say "decision temporarily unavailable" instead.
- **CORR-6** `usage.py:91` — the "always written" usage/cost row is silently dropped on write failure (warn only). Bounded impact. **Fix:** outbox/retry or a drop counter so accounting gaps are observable.
- **CORR-7** `worker.py:719` — queue rows are durably `succeeded` after the subtick commit but `sync_runs.embeddings_generated`/`summaries_generated` increment in a *later* commit; a crash between them permanently under-counts. **Fix:** fold counter increments into the subtick transaction.
- **CORR-8** `data_executor.py:798` — a malformed recipe raises after teardown in the sync entry path, rolling back the boundary-A txn so **no `s4_execution_runs` errored-row persists** (contrast the run-all path which synthesizes an `errored` RunEvidence). **Fix:** convert assertion/predicate defects to an `errored` RunEvidence in the sync path too.

### PLAUSIBLE
- **CORR-P1 [Low]** `data_executor.py:763` — in the D-306 update-then-observe path, a successful `client.update` can fire org automation that creates real SF side-effect records; if the read-back then transport-fails, those records are never registered on the `CreatedRecordTracker` → **orphaned live-org data** the reverse-order cleanup won't remove. **Fix:** on a successful trigger phase, reconcile/scan for implied side-effect records before cleanup.
- **CORR-P2 [Low]** `evolution/result_store.py:78` — S8 grounding persist is read-then-INSERT with no `ON CONFLICT` on `(test_id, version_seq)`; safe today only by single-writer scheduler discipline (not by code). **Fix:** `INSERT ... ON CONFLICT (test_id, version_seq) DO UPDATE`.

### REFUTED
- **CORR-R1** — claim re-versioning dedup race (`generation/persistence.py:125`): the finder claimed concurrent equivalent generations mint duplicate tests; verifier **refuted** the live race (the current writers don't actually interleave to produce it). The suggested hardening — a partial-unique index on `(identity_hash, identity_hash_version) WHERE valid_to IS NULL` — is still a reasonable defense-in-depth to make the invariant DB-enforced, but there is no confirmed defect today.

---

## PASS 6 — Data Layer

Method: 3 finders (schema/migration health, JSONB-vs-normalized + indexes/FKs, bitemporal + cost-join + per-org keying) → adversarial verify (searched both `migrations/` and `alembic/versions/` before confirming any "missing index/FK"). **12 raw → 11 CONFIRMED, 1 PLAUSIBLE, 0 REFUTED.**

| ID | Sev | Finding | Location |
|---|---|---|---|
| DATA-1 | **High** | ApprovalProcess deletion-reconcile can **mass-close every live approval** on an empty/partial fetch (missing the D-253 `if present_ids:` fail-safe) | `sync/phases.py:2129` |
| DATA-2 | Medium | Migration **number collision**: two `053_*` files, mutually contradictory (one DROPs `scheduled_runs`, the other ALTERs it) → replay failure + broken ordering invariant | `migrations/053_*` |
| DATA-3 | Medium | S3-generation LLM cost joined to outcomes by a **time-window**, not a stable key → mis-/double-attribution under any concurrency | `intelligence/s3_generation_console.py:293` |
| DATA-4 | Medium | S3/S4 LLM cost rows are **orphaned from their outcomes** in `llm_usage_log` (every legacy FK column points at v1 tables dropped in 053) | `generation/gateway_binding.py:38` |
| DATA-5 | Medium | S8 grounding-validity PK is **org-blind** `(test_id, version_seq)` — a multi-org tenant can't hold a per-org verdict (known-deferred; the decision engine reads this store) | `alembic/.../20260603_0030_s8_grounding_validity.py:62` |
| DATA-6 | Medium | `generation_outcomes` filtered by JSONB `requirement_ref->>'key'` with **no index** → full scan + sort of an append-only ledger on the requirement detail page | `intelligence/s3_generation_console.py:636` |
| DATA-7 | Medium | `s4_execution_runs` runs-list/dashboard filter by `environment_id`/`finished_at` with **no supporting index** | `intelligence/s4_execution_console.py:248` |
| DATA-8 | Medium (plausible) | `entities` has **no usable index leading with `connected_org_id`** → every per-org read scans all orgs' current rows | `alembic/.../20260622_0020_s1_connected_org_id_columns.py` |
| DATA-9 | Low | Dead migration work: `053_scheduled_runs_substrate_test.sql` adds a column (`substrate_test_id`) with **zero readers** to a table its sibling drops | `migrations/053_scheduled_runs_substrate_test.sql:8` |
| DATA-10 | Low | Migrations **035/037 no longer re-runnable** after 053 (bare `ALTER` on a dropped table) — violates the stated 016+ idempotency guarantee | `migrations/035_*.sql:25` |
| DATA-11 | Low | `test_requirement_links` coverage query filters `link_kind` but the index stops at `(external_system, external_key)` | `intelligence/s3_generation_console.py:690` |

### DATA-1 — [High] ApprovalProcess deletion-reconcile lacks the empty-fetch fail-safe → data loss
`primeqa/sync/phases.py:2129`
**Why it matters:** on a malformed-cursor or otherwise-empty `ProcessDefinition` response **that does not raise**, `present_ids` becomes an empty (or truncated) set. `reconcile_deletions_by_sf_id` then SCD-2-closes **every** active ApprovalProcess entity for the org (sets `valid_to_seq`) *and* closes every active edge touching them (`_close_edges_for_entities`) — leaving live approvals with zero current versions. The `if present_ids:` truthiness gate that protects the ValidationRule path (D-253) is missing here. A transient empty fetch silently wipes the org's approval-process history that downstream grounding/decision then reads as "these approvals don't exist." This is the exact fail-loud-vs-silent-corruption class the constitution warns against, on a bitemporal write path.
**Proposed remediation:** mirror D-253 — gate the reconcile on truthy `present_ids` (empty set → skip, fail-safe to no-reconcile), and make `fetch_process_definitions` raise (not return empty) on a malformed/short response so the sync fails loud rather than reconciling against nothing.

### DATA-2 — [Medium] `053_*` migration number collision (+ DATA-9/DATA-10 fallout)
`migrations/053_drop_v1_product_tables.sql` (`DROP TABLE ... scheduled_runs`) vs `migrations/053_scheduled_runs_substrate_test.sql` (`ALTER TABLE scheduled_runs ADD COLUMN ...`) — same number, contradictory, committed 2 days apart (D-199 then D-221). `ls` sorts the DROP first, so any glob-apply (`psql -f migrations/*.sql`) or future migration runner errors with `relation "scheduled_runs" does not exist`, and the numbered-sequence total-order invariant is permanently broken (which 053 runs relative to 054?). **Not a production risk today** only because migrations are hand-applied one file at a time (verified: no runner in `Procfile`/`railway.toml`/`nixpacks.toml`/`Dockerfile`, no `schema_migrations` table). Two related confirmed sub-findings:
- **DATA-9 [Low]** the ALTER migration is **dead work** — `substrate_test_id` has zero readers anywhere; the table it targets is dropped; the live scheduled trigger is a different table (`s4_run_schedules`). Delete it.
- **DATA-10 [Low]** migrations 035/037 (bare `ALTER` on `run_test_results`, dropped by 053) are **no longer idempotent** after 053 — re-running them raises. Latently affects 028/036/039/043 too (their `IF NOT EXISTS pg_constraint` DO-blocks short-circuit TRUE when the table is gone). Contradicts the "016+ idempotent" convention.

**Proposed remediation:** delete `053_scheduled_runs_substrate_test.sql` (dead); document the resolved order; if the 016+ guarantee is to hold literally, wrap post-drop ALTERs in `IF to_regclass('public.<table>') IS NOT NULL`.

### DATA-3 / DATA-4 — [Medium] Cost is orphaned from outcome (the cost-outcome join-key gap)
- **DATA-3** `intelligence/s3_generation_console.py:293` — generation LLM cost is attributed to an attempt purely by **temporal proximity** of `ts` to `[started_at, finished_at]`; correctness rests entirely on the "single-worker queue, windows never overlap" comment. Any concurrency (multi-worker, future parallelism) overlaps the windows → cost mis-attributed/double-counted with no key to reconcile.
- **DATA-4** `generation/gateway_binding.py:38` — S3 generation and S4/S6 execution rows in `llm_usage_log` carry NULL in **every** join-key column (the legacy FK columns point at v1 tables dropped in 053). Migration 058 added `sync_run_id` for the sync lane but generation/execution got no equivalent, so `cost_usd`+cache stats (which live only in `llm_usage_log`) can't be attributed back to the `generation_outcome` / recipe / run — undercutting the superadmin cost-visibility surface and the cost half of the decision loop.
**Proposed remediation:** add a stable soft-FK (`generation_outcome_id` / `s4_run_id`) on `llm_usage_log`, set through the gateway context, mirroring the 058 `sync_run_id` approach; join on it instead of the time window.

### DATA-5 / DATA-8 — [Medium] Per-org keying gaps (connects to the security per-org theme)
- **DATA-5** `s8_grounding_validity` PK is `(test_id, version_seq)` with no `connected_org_id`; upstream `test_claims`/`test_recipes` are also org-blind, so one claim → one grounding row across **all** orgs. On a multi-org tenant this collapses per-org grounding into one blend, and the **decision engine reads this store** for GO/NO-GO. Decision-neutral today only because of the refuse-all guardrail (memory D-265). This is the schema root of SEC-6 / SEC-P3.
- **DATA-8 [plausible]** `entities` has no index leading with `connected_org_id`; on a multi-org tenant two orgs share `(entity_type, sf_api_name)` so `idx_entities_current_api_name` matches rows for all orgs then discards the wrong org in a heap fetch — a scan on every per-org read/diff/materialize.
**Proposed remediation:** when 3f Slices 2-3 land, re-key `s8_grounding_validity` to `(test_id, version_seq, connected_org_id)` and add `CREATE INDEX ... ON entities(connected_org_id, entity_type) WHERE valid_to_seq IS NULL`.

### DATA-6 / DATA-7 / DATA-11 — [Medium/Low] Missing indexes on hot list/detail surfaces
- **DATA-6** `generation_outcomes ((requirement_ref->>'key'), created_at DESC)` — JSONB equality on a hot page, no expression/GIN index → full ledger scan + sort.
- **DATA-7** `s4_execution_runs(environment_id, finished_at DESC)` — the `/runs` failures front door + dashboard filter/sort with no covering index (the existing `(claim_test_id, finished_at)` can't serve an env filter or bare range).
- **DATA-11** extend `test_requirement_links` index to `(external_system, external_key, link_kind)` (low priority at current row counts).
**Proposed remediation:** add the three indexes above (all additive, idempotent `CREATE INDEX IF NOT EXISTS`).

---

## PASS 7 — Test-Suite Health

Method: 3 finders (coverage gaps, tautological/assert-nothing, flaky/shared-state) → adversarial verify. **Static only — the suite was never executed** (it mutates the real Railway DB). **10 raw → 7 CONFIRMED, 1 PLAUSIBLE, 2 REFUTED.** Two of the confirmed are High-impact structural facts about the suite itself.

| ID | Sev | Finding | Location |
|---|---|---|---|
| TEST-1 | **High** | `/api/webhooks/ci-trigger` has **zero** test coverage — HMAC, fail-closed 503, and the A5 cross-tenant env guard all unasserted | `release/routes.py:412` |
| TEST-2 | **High** | Critical-invariant suites are **invisible to a plain `pytest` run** — they never execute; "green board" is misleading | `tests/test_tenant_isolation.py:48` + 29 root files |
| TEST-3 | **High** | Two generation/sync suites share a fixed-name DB and `pg_terminate_backend`+`DROP DATABASE` at teardown → concurrent runs mutually corrupt | `tests/integration/generation/conftest.py:117` |
| TEST-4 | **High** | `test_auth.py`: ordered, module-global suite vs the real Railway DB with fixed emails + the 20-user cap → order-dependent + races | `tests/test_auth.py:207` |
| TEST-5 | Medium | Repair-agent **sandbox auto-apply** "flag-on" test seeds zero proposals → asserts the same empty result as flag-off; the security-sensitive path is never exercised | `tests/unit/test_repair_agent_llm.py:114` |
| TEST-6 | Medium | `test_management.py`: fixed-name sections vs real DB, no teardown, asserts exact child count → non-deterministic | `tests/test_management.py:133` |
| TEST-7 | Medium | Live-sandbox integration tests skip only via an in-body `HAS_SANDBOX_CREDS` check — with `SF_*` in `.env`, `pytest tests/integration` **mutates the real org** | `tests/integration/test_sync_object_phase_live.py` |

### TEST-2 — [High] Critical-invariant suites never run under `pytest`
`pytest.ini` + `tests/test_tenant_isolation.py:48`
```ini
testpaths = tests/unit tests/integration
python_functions = test_*
```
**Why it matters:** all ~30 top-level `tests/test_*.py` files sit at `tests/` root — **outside `testpaths`** — so a plain `pytest` never collects them. Worse, several (`test_tenant_isolation.py`, `test_r2_superadmin.py`, `test_release_audit.py`, `test_sf_access_attribution.py`, `test_jira_search_branching.py`, `test_system_validation.py`) have **zero** `test_`-prefixed functions — their real assertions live in `t_*` helpers called only from `if __name__ == "__main__"`. So cross-tenant isolation, the superadmin 20-user-cap exclusion, admin-action `activity_log` logging, and the JSON self-validation suite are **silently disabled** unless each file is run by hand (`python tests/test_X.py`). No `.github/workflows`, `Makefile`, or Dockerfile pytest invocation runs them. The tests contain strong assertions — they just don't fire. (The lone `def test(name, fn)` runner helper at line 48 would also be mis-collected as a test if pytest ever pointed here.) This directly undermines the "~3,300 green tests" confidence: the named invariants aren't in that number.
**Proposed remediation:** move these files under `tests/integration/` and rename `t_*`→`test_*` for collection, OR add explicit CI invocation of each root file; rename the `def test(name,fn)` helper to `_run`; document in `pytest.ini` that root `tests/*.py` need manual invocation so a green board isn't misread.

### TEST-1 — [High] CI webhook ingress has no test
`primeqa/release/routes.py:412` — `ci_webhook_trigger` (externally reachable, one global `WEBHOOK_SECRET`) has four security branches: fail-closed 503 on unset secret, `hmac.compare_digest` 401, the **A5 cross-tenant guard** (`Environment.tenant_id == release.tenant_id`, whose own comment warns a foreign `environment_id` would "enqueue claims against ANY tenant's environment"), and the enqueue. Grep of `tests/` for the route/function/`X-PrimeQA-Signature`/`compare_digest` = **nothing**; only the isolated helpers `get_webhook_secret` + `enqueue_claims_for_keys` are unit-tested. A dropped predicate, `==` swapped for `compare_digest` (timing-attackable), or an inverted fail-closed would ship green. _(Complements SEC-7, which flags this route's missing production gate — same untested ingress, two defects.)_
**Proposed remediation:** Flask `test_client` test asserting all four outcomes; the must-have is a valid-signed request whose `environment_id` belongs to a different tenant → 404 (pins the cross-tenant execution-injection guard).

### TEST-3 — [High] Shared fixed-name test DB with destructive teardown
`tests/integration/generation/conftest.py:117` + `sync/conftest.py:25` — both hardcode `postgresql://localhost/primeqa_test_governance` and, at session teardown, `pg_terminate_backend` every *other* connection and `DROP DATABASE`. Two concurrent pytest processes (or a manual harness running alongside) kill each other's live connections mid-test and drop the DB out from under the other → non-deterministic `terminating connection due to administrator command` errors. **Remediation:** derive the DB name per worker/pid (`PYTEST_XDIST_WORKER` + pid/uuid) so sessions never share.

### TEST-4 — [High] `test_auth.py` ordered global-state suite vs shared Railway DB
`tests/test_auth.py:207` — module globals (`admin_tokens`, `created_user_ids`), fixed emails (`tester@primeqa.io`, `user{i}@primeqa.io`), strict inter-test ordering, and an assertion on the tenant-wide **20-user cap** — all against the shared Railway `tenant_1`. Two concurrent runs race the same fixed-email rows and the same cap; `pytest tests/test_auth.py` (vs the hand-rolled `run_tests()`) would also reorder and break it. **Remediation:** per-run uuid email suffixes; derive the cap assertion from the actual pre-existing count.

### TEST-5 — [Medium] Sandbox auto-apply "flag-on" test proves nothing
`tests/unit/test_repair_agent_llm.py:114` — `test_auto_apply_runs_when_flag_on` stubs `.mappings().all()` to `[]`, so the `for row in rows:` apply-loop never executes; it asserts the same empty result as the flag-off dormant test. D-236 sandbox auto-apply (an agent mutating a live org unattended) is thus never exercised — a regression making `auto_apply_proposals` a no-op would pass. **Remediation:** stub a non-empty proposal list + a stubbed `_apply`, assert applied-count > 0 and sandbox-only targeting.

### TEST-6 / TEST-7 — [Medium] Real-DB / live-org test hygiene
- **TEST-6** `tests/test_management.py:133` — fixed-name sections vs real DB, no teardown, `assert len(children)==1` under an idempotently-shared parent → fails on re-run/concurrency. (Also targets v1 `test_*` tables — may be dead post-053; if so, delete.)
- **TEST-7** `tests/integration/test_sync_object_phase_live.py` — `pytest.ini addopts` lacks `-m 'not sandbox and not live'`; the live suites skip only via an in-body `HAS_SANDBOX_CREDS` check, so `pytest tests/integration` on a box with `SF_*`/`VOYAGE`/`ANTHROPIC` keys in `.env` executes real network calls **and mutates the live org** (slow, costs money, non-deterministic). **Remediation:** add `-m 'not sandbox and not live'` to `addopts`.

### PLAUSIBLE
- **TEST-P1 [Low]** `sync/materialize.py:204` bitemporal SCD-2 close-out/insert/edge-close SQL has no fast deterministic unit guard — only live integration tests exercise it (verifier notes two of three SQL sites *do* have some coverage, so "overstated" but the fast-guard gap is real). **Remediation:** unit-assert the emitted SQL text + `valid_to_seq == ctx.logical_version_seq`.

### REFUTED
- Governance `clean_ledger` "leaks orgs/versions" — verifier found the defect claim inverted (idempotent seed rows, not a flake). 
- `time.sleep(0.05)` + `updated_at` equality no-op test — technically correct today (no-op early-returns before the write); fragile but not a current defect.

---

## SYNTHESIS

Across 7 passes, ~35 finder + verifier agents produced **88 raw finding reports → 66 confirmed, 16 plausible, 6 refuted/cleared** after adversarial verification and cross-pass de-duplication. Every finding in this report cites executing code verified at least twice (finder + adversarial verifier). Counting confirmed defects by verified severity: **~14 High, ~16 Medium, ~25 Low. No finding was rated Critical by the adversarial verifiers** — but **SEC-1 has Critical real-world blast radius** (plaintext theft of every connected Salesforce org's credentials by the lowest-privilege authenticated role), so I rank it #1 regardless of the label.

Two cross-cutting root themes tie many findings together:
- **Per-org isolation is half-built.** The per-org restructure (D-255…D-260) landed for sync/execution but S8 grounding, the decision engine, and several read surfaces still operate org-blind on multi-org tenants. Manifestations: SEC-6, SEC-P3, DATA-5, DATA-8. This is *known-deferred* (3f Slices 2-3) and mitigated by a refuse-all guardrail, but it's a coherent gap, not four separate ones.
- **Fail-loud has soft spots.** The system's stated discipline (fail loud, ground-or-refuse) is violated on real paths: CORR-1 (data-losing sync → `success`), SUB-3 (degrade to `flows[0]`), SUB-4 (fabricate active/createable), CORR-4 (rate-limit fails open). Each turns a missing/failed signal into a false-positive rather than a refusal.

### Master severity-ranked table (deduplicated)

| # | Sev | ID | Finding | File:line | Pass |
|---|---|---|---|---|---|
| 1 | **High** ⚠️Crit-blast | SEC-1 | Plaintext SF/Jira/LLM secret exfiltration by any authenticated user | `core/routes.py:322` | 2 |
| 2 | **High** | SUB-1 | Automation-effect semantic leakage → identity rides prose (approval-invalidation) + coverage under-report | `generation/emission.py:1825` | 3 |
| 3 | **High** | SEC-4 | `/releases/<id>/run` runs production orgs with no `is_production` gate (any Member) | `views.py:3506` | 2 |
| 4 | **High** | DATA-1 | ApprovalProcess deletion-reconcile mass-closes live approvals on empty fetch (data loss) | `sync/phases.py:2129` | 6 |
| 5 | **High** | CORR-1 | Data-losing sync finalizes as `success` (swallowed gap-write) | `sync/engine.py:572` | 5 |
| 6 | **High** | SEC-2 | `add_member`/`add_environment` cross-tenant PII/org disclosure | `core/service.py:537` | 2 |
| 7 | **High** | SEC-3 | SSRF: server sends org access-token to attacker-chosen `instance_url` | `core/service.py:336` | 2 |
| 8 | **High** | CORR-2 | S4 job `complete()` resurrects the reaper's `failed` → false `completed` | `execution_engine/jobs.py:208` | 5 |
| 9 | **High** | SUB-2 | Flow write-effects re-parsed from raw JSONB in app-layer Python (should be typed edges) | `semantic/entity_attributes.py:556` | 3 |
| 10 | **High** | SUB-3 | Automation-effect no-name branch binds `flows[0]` instead of refusing | `generation/governance_core.py:1725` | 3 |
| 11 | **High** | TEST-2 | Critical-invariant suites never run under `pytest` (isolation/authz/audit unexecuted) | `pytest.ini` + `tests/test_*.py` | 7 |
| 12 | **High** | TEST-1 | CI webhook ingress (HMAC + A5 cross-tenant guard) has zero tests | `release/routes.py:412` | 7 |
| 13 | **High** | TEST-3 | Shared fixed-name test DB with `DROP DATABASE` teardown → concurrent corruption | `tests/integration/generation/conftest.py:117` | 7 |
| 14 | **High** | TEST-4 | `test_auth.py` ordered global-state vs shared Railway DB → races/order-dependent | `tests/test_auth.py:207` | 7 |
| 15 | Medium | SEC-5 | SSRF token POST leaks OAuth `client_secret`/password | `metadata/worker_runner.py:179` | 2 |
| 16 | Medium | SUB-4 | S6 interpretation fabricates `is_active`/`is_createable` → drives cause attribution | `interpretation/s1_reader.py:66` | 3 |
| 17 | Medium | CORR-3 | Concurrent first-run collides on `recipe_id` PK, rolls back evidence | `test_representation/coordinator.py:1801` | 5 |
| 18 | Medium | CORR-4 | LLM rate-limit `check()` fails open → caps bypassed on DB error | `intelligence/llm/limits.py:265` | 5 |
| 19 | Medium | DATA-5 | S8 grounding-validity PK org-blind (decision engine reads it) | `alembic/.../s8_grounding_validity.py` | 6 |
| 20 | Medium | DATA-3/4 | Cost orphaned from outcome (time-window join + NULL join keys) | `s3_generation_console.py:293`, `gateway_binding.py:38` | 6 |
| 21 | Medium | DATA-6/7 | Missing indexes on hot list/detail surfaces (JSONB key scan; env/finished_at) | `s3_generation_console.py:636`, `s4_execution_console.py:248` | 6 |
| 22 | Medium | DATA-8 | `entities` has no index leading with `connected_org_id` → per-org scans | `alembic/.../s1_connected_org_id_columns.py` | 6 |
| 23 | Medium | DATA-2 | `053_*` migration number collision (+ dead work + broken 016+ idempotency) | `migrations/053_*` | 6/4 |
| 24 | Medium | DEAD-1 | v1 `metadata` module dead except `_oauth_token` (misleading docstring) | `metadata/worker_runner.py:33` | 4 |
| 25 | Medium | DEAD-2 | Three dead feature flags + orphaned `interpretation_phrasing` lane | `core/models.py:190,200` | 4 |
| 26 | Medium | TEST-5 | Sandbox auto-apply "flag-on" test proves nothing (seeds zero proposals) | `tests/unit/test_repair_agent_llm.py:114` | 7 |
| 27 | Medium | TEST-6/7 | Real-DB/live-org test hygiene (no teardown; live suites run with `.env` creds) | `tests/test_management.py:133`, `test_sync_object_phase_live.py` | 7 |
| — | Low (×25) | SEC-6…9, SEC-P1…5, CORR-5…8, CORR-P1…2, DATA-9…11, DEAD-3…8, TEST-P1 | See per-pass sections | 2/5/6/4/7 |

### Top 10 to act on first

1. **SEC-1 — Plaintext credential exfiltration (`core/routes.py:322`).** One decorator + response masking. Any `viewer` can currently steal every connected Salesforce org's credentials. Highest blast radius, lowest fix cost.
2. **SUB-1 — Automation-effect semantic leakage (`generation/emission.py:1825`).** The product's core artifact (identity-bearing claims) has identity riding on non-identity-bearing prose → silent approval invalidation + wrong coverage. Fix is structural but well-scoped (author `trigger_fields` into `semantic_conditions`).
3. **SEC-4 — Production runs with no gate (`views.py:3506`).** Any ba/tester can trigger mutating recipe runs against a live production org. Mirror the existing `environment_can_bulk_run` gate.
4. **DATA-1 — Approval mass-close on empty fetch (`sync/phases.py:2129`).** A transient empty fetch silently wipes approval-process history. Add the D-253 `if present_ids:` fail-safe.
5. **CORR-1 — Data-losing sync → `success` (`sync/engine.py:572`).** A swallowed exception lets an incomplete org model report clean success; every downstream decision then runs on corrupted evidence. Narrow the catch.
6. **SEC-2 — Cross-tenant disclosure via group membership (`core/service.py:537`).** Tenant-scope the linked `user_id`/`environment_id` (the sibling `add_requirement` fix is the template).
7. **SEC-3/SEC-5 — SSRF (`core/service.py:336`, `worker_runner.py:179`).** Add one `validate_sf_instance_url()` guard (https + private-IP block + Salesforce allowlist) at write time and before every outbound call.
8. **TEST-2 — Invariant suites don't run (`pytest.ini`).** The safety net for tenant isolation/authz/audit is silently disabled. Wire the root `tests/*.py` into CI (or move under `testpaths` + rename `t_*`→`test_*`). Without this, regressions in #1/#3/#6 ship green.
9. **CORR-2 — S4 job terminal-state race (`execution_engine/jobs.py:208`).** Add `AND status NOT IN (_TERMINAL)` to `_finish` so a reaped job can't report false success.
10. **SUB-3 — Degrade-not-refuse in grounding (`governance_core.py:1725`).** Make the no-name branch refuse (like the named branch) so a claim can't be grounded against an automation that doesn't produce its effect (wrong-green).

### Quick wins (low effort / high value)
- **SEC-1**: add `@require_role("admin")` + strip secrets from the response on `GET /api/connections/<id>`.
- **SEC-8/SEC-P1**: route `views.py:25` + `core/service.py:28` JWT resolution through the fail-closed `core.secrets.get_jwt_secret()` (delete the `dev-secret-change-me` defaults).
- **TEST-7**: add `-m 'not sandbox and not live'` to `pytest.ini` `addopts` — one line that prevents accidental live-org mutation from a dev `.env`.
- **DATA-6/7/DATA-8**: three additive `CREATE INDEX IF NOT EXISTS` on the hot query surfaces.
- **DATA-9 / DATA-2**: delete `migrations/053_scheduled_runs_substrate_test.sql` (dead work — resolves the number collision).
- **DEAD-5**: drop `numpy` + `apscheduler` from `requirements.txt` (verified unused).
- **DEAD-6**: prune F401/F811 unused/duplicate imports; delete the discarded `test_plan` fetch (`release/service.py:79`).
- **SEC-P5**: add a CI guard test that fails if any GET route name matches a mutation verb (locks the CSRF safe-methods invariant).
- **Supply-chain hygiene**: `requirements.txt` uses unpinned `>=` minimums with no lockfile; `pip-audit` was clean against *installed* versions, but a fresh build could pull an untested/vulnerable future release. Pin or add a lockfile (`pip-compile`/`uv.lock`).

### Deep structural (needs design + care)
- **SUB-1 refactor** — authoring `trigger_fields`/`update_trigger_fields` into `semantic_conditions` across all five automation-effect sub-shapes. Per the memory's own laws, a new body field re-keys existing claims → requires a `body_schema_version` bump + a claim-migration plan. High value; do it deliberately.
- **Per-org completion (3f Slices 2-3)** — re-key `s8_grounding_validity` to include `connected_org_id`, scope the decision engine + metadata browse per org, add the `entities(connected_org_id, …)` index. Resolves SEC-6, SEC-P3, DATA-5, DATA-8 together.
- **SUB-2** — materialize Flow effects as typed edges at sync time instead of app-layer JSONB re-parse.
- **Cost-outcome join (DATA-3/4)** — add stable soft-FKs (`generation_outcome_id`/`s4_run_id`) on `llm_usage_log`, mirroring the 058 `sync_run_id` approach.
- **v1 `metadata` module removal (DEAD-1)** + orphaned-table drops (DEAD-3) — large but mechanical; retires ~52KB of misleading dead code and several undropped v1 tables.
- **Test-suite structural fix (TEST-2/3/4)** — a real CI harness that runs the invariant suites hermetically (per-run namespacing, isolated test DBs) so the ~3,300-green board actually protects the substrate invariants.

### What I could NOT review (blockers / gaps needing follow-up)
- **No dynamic/runtime analysis.** The test suite was never executed (it mutates the real Railway DB — prohibited by the read-only mandate), and nothing was run against live orgs (env-59/env-78). All findings are **static**. The concurrency/race findings (CORR-2, CORR-3, DATA-1, TEST-3/4) are reasoned from code, not reproduced — they warrant a controlled dynamic repro before/after any fix.
- **Live DB schema not verified.** Migrations + alembic DDL were read statically; I did not connect to Postgres to confirm the deployed schema matches, that all migrations were applied, or the real index set. The "missing index" findings (DATA-6/7/8) should be confirmed with `\d`/`pg_indexes` against the live DB.
- **Frontend not deeply reviewed.** Jinja templates + `static/js` got only targeted checks (CSRF, XSS auto-escape on the SEC-9 path). No dedicated pass on the HTMX/JS layer, client-side authz assumptions, or template injection beyond spot checks.
- **LLM prompt/eval quality out of scope.** This was a code review, not a model evaluation; prompt correctness, the eval harness's own validity, and generation quality were not assessed (the memory's known live-eval drift items are separately tracked).
- **SSRF exploitability confirmed only statically.** SEC-3/5 confirm the *missing validation*; no outbound request was actually issued to prove reachability of an internal target.
- **Transitive dependency tree.** `pip-audit` covered declared deps; there is no SCA over the full transitive/pinned tree (no lockfile exists — see quick-wins).

### Triage is the owner's next step
Per the review mandate, this report proposes **no remediation plan beyond the per-finding fixes above**. Nothing here was changed, committed, or pushed. Recommended first move: triage the Top 10, starting with the SEC-1 quick win and wiring TEST-2 so the isolation/authz safety net actually runs before other fixes land.

---

_End of review. Generated by a 7-pass, multi-agent, adversarially-verified static analysis at HEAD `d462767`. Report is uncommitted by design._
