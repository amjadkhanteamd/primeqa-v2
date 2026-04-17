# PrimeQA Self-Validation Suite

## Concept

A canonical, declarative end-to-end test suite that exercises PrimeQA
**through PrimeQA's own conventions** — API-first, workflow-driven,
state-validating. The suite is expressed in a PrimeQA-flavoured step
grammar (similar to the Salesforce step schema, but adapted for HTTP +
DB assertions) and executed by a Python runner.

> Before every deploy: **run PrimeQA on PrimeQA.**

This is not a replacement for the domain integration suites
(`tests/test_*.py`). Those are fast, surgical, and run against the
Railway DB. The system-validation suite is **coarser and workflow-driven**
— it proves that the buttons and flows a real user would trigger
actually do what the UI promises.

## What it covers

Eight categories, mirroring the user journey:

1. **Requirements** — create, edit, soft-delete, restore round-trip
2. **Test Library** — create, fetch, version, execute-preview
3. **Runs** — create a manual run, verify it queues with correct provenance
4. **Jira integration** — search endpoint (dual-mode), import flow
5. **Preview engine** — THE canary: `preview.test_case_count == resolved.test_count`
6. **Metadata** — per-category sync status, DAG fallback behaviour
7. **Agent** — triage classification, trust bands, audit ledger
8. **UI navigation** — every key page renders 200 with its primary affordance

## Step grammar

Every test is a list of steps. Each step has an `action` and shape-specific
fields. Variables are `$name` (read) / `save_as: "name"` (write), mirroring
the SF step grammar.

### Actions

| Action | Purpose | Required fields | Stores into `$state_ref` |
|---|---|---|---|
| `http` | Issue an HTTP request against the Flask app | `method`, `url` | `{status_code, body, headers}` |
| `verify` | Assert on a saved value | `target` (dotted path) + one of `equals`, `in`, `gte`, `lte`, `contains`, `matches` | — |
| `save` | Extract a sub-field into its own variable | `from` (dotted path), `as` | sub-value |
| `login` | Convenience: POST /api/auth/login, store access_token + set cookie | `email`, `password`, `tenant_id` | token string |
| `wait` | Sleep | `seconds` | — |
| `assert_db` | Raw SQL assertion (test-only escape hatch) | `sql`, `expect_rows` | — |
| `python` *(banned)* | Would execute arbitrary code — not supported | — | — |

### Variable substitution

Any string field can reference a prior variable via `$name` or dotted
`$name.sub.field`. A special `$uuid` token expands to a unique 8-char hex
for idempotency (`"name": "SysVal $uuid"` generates a new name per run).

### Example

```json
{
  "steps": [
    {"action": "login", "email": "admin@primeqa.io", "password": "changeme123",
     "tenant_id": 1},
    {"action": "http", "method": "POST", "url": "/api/sections",
     "body": {"name": "SysVal $uuid"},
     "save_as": "new_section"},
    {"action": "verify", "target": "$new_section.status_code", "equals": 201},
    {"action": "save", "from": "$new_section.body.id", "as": "section_id"},
    {"action": "http", "method": "DELETE",
     "url": "/api/sections/$section_id",
     "save_as": "del_resp"},
    {"action": "verify", "target": "$del_resp.status_code", "equals": 200}
  ]
}
```

## Suite shape

```json
{
  "name": "PrimeQA Core Validation",
  "version": "1.0",
  "tags": ["system", "e2e", "core"],
  "categories": [
    {
      "name": "Requirements",
      "tests": [
        {
          "name": "CRUD round-trip",
          "skip_reason": null,
          "steps": [ ... ]
        }
      ]
    }
  ]
}
```

- `skip_reason` — if non-null the runner skips the test and reports as
  `skipped` (e.g. "needs SF sandbox"). Prevents CI red on environmental
  gaps while keeping the test in the canonical suite.
- Tests are idempotent: they create their own data (`$uuid` suffix) and
  soft-delete at the end.

## How to run

```bash
# Against the running Flask app via test_client (default; CI-friendly)
python tests/test_system_validation.py

# Or programmatically
python -c "
from primeqa.system_validation.runner import load_suite, run_suite
suite = load_suite('primeqa/system_validation/suites/primeqa_core.json')
report = run_suite(suite)
print(report.render())
"
```

The runner produces a structured report with per-test pass/fail/skip,
the failed step's details, and the assertion diff when applicable.

## Roadmap

- **v1 (this commit)**: JSON-driven runner, canonical 8-category suite,
  Python test wrapper
- **v2**: Ingest the JSON suites into PrimeQA's own `test_cases` table so
  they appear in the Test Library. Requires extending `step_schema.py`
  with `http` and `verify` actions and teaching the existing executor to
  dispatch on action type.
- **v3**: Agent-assisted self-repair — when the self-validation suite
  fails, the R5 agent proposes a fix (likely a code change or a config
  tweak). Closes the loop.
- **v4**: Public "PrimeQA tests itself" dashboard — run the suite on
  every deploy, publish pass-rate + flakiness metrics.

## Why JSON, not pytest

- **Authorable by non-engineers** — PMs / BAs can add a test by writing JSON
- **LLM-authorable** — the grammar is structured enough for Claude to
  generate new tests from a one-line description ("add a test that
  verifies the `/runs/:id/compare` page shows flipped-red tests")
- **Re-ingestable** — the JSON is trivially loaded into the test_cases
  table (roadmap v2), letting PrimeQA literally store and run tests of
  itself
- **Stable across refactors** — no Python imports to update when code
  moves; the suite is purely about the HTTP + DB contract
