# System Architecture Delta — UI Testing Programme
**Applies to:** PLIMSOL_SYSTEM_ARCHITECTURE.md · **Status:** for TA review with HLD · 2026-08-21
Amendments only; the eight-substrate model and all existing boundary rules stand.

## A1 — Second grounding source (D1)
External standard catalogues ground conformance assertions; S1 grounds surface inventory. S3 refuses conformance claims for surfaces outside inventory, and refuses behavioural grounding of standards in S1. Neither source may substitute for the other.

## A2 — Execution service plane
S4 gains a remote execution plane: orchestrator + queue + ephemeral browser workers as a second service class. Boundary rule: workers execute recipes and capture truth only; no interpretation, no persistence decisions, no substrate logic in workers. S4 remains the sole dispatcher; k16 unchanged.

## A3 — Explicit mutation flag (D6)
The read-only/mutating classification at `_authorize_dispatch` becomes an explicit per-recipe-kind property. Inference from kind names is prohibited. `ui-inspection` = read-only; any Mode B kind = mutating.

## A4 — Versioned artifact store class (S5)
A new store class for pinned external artifacts (rule catalogues, engine bundles, standard maps) with immutable versions. Run-time fetching of external test logic is prohibited system-wide.

## A5 — Secrets boundary
Per-user external credentials/TOTP seeds: ciphertext in DB; decryption key exists only in the worker service environment. The web tier can never read a portal credential. (D4/D8 as amended; D-416 exception scope: external test personas only.)

## A6 — Sensitive evidence class
Authenticated-surface evidence is a named sensitivity class: violating-node fragments only, signed URLs, retention per D5. Full-page DOM persistence of authenticated surfaces is prohibited.

## A7 — Environment as first-class run object
Every run persists a structured Environment record (versions of org/release/site/packages/browser/engine/catalogue/inventory). Diffing Environment precedes any regression claim (change taxonomy CLIENT/ENVIRONMENT/TOOL).

## A8 — Tenant boundary extends to the browser plane
All per-tenant configuration lives exclusively in tenant-scoped records (sites, surfaces, personas, credentials, auth modes, bot-protection paths, profiles, custom rules, evidence policy, schedules). Deployment configuration holds platform infrastructure only. Every browser-plane object carries tenant ownership; browser contexts, sessions, credentials, and evidence namespaces are never shared across tenants; all queries are tenant-scoped.

## A9 — Manifest execution rule
S4's remote plane executes immutable Run Manifests only. A worker may not resolve inventory, rules, personas, or policy at run time; everything it needs is in the manifest. Retries re-execute the identical manifest.

## A10 — Interpretation stays out of workers (hardened per TA)
Workers produce raw **engine observations** and evidence only — an axe violation is an observation, never a verdict. Verdict, applicability, ownership, severity, regression, and attribution are computed exclusively in the result-processing path. Terminology rule codebase-wide: "engine observation" for worker output; "verdict" exists only after the Plimsol evaluator. This is a hard boundary, not a convention.
