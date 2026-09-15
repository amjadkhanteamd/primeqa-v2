# The undefined-name gate — the retirement's survivors closed for good

Branch `undefined-names-gate` from `main` @f8adc12. AK's brief (2026-09-15):
"add undefined-name detection to the merge gate. It closes the
retirement-survivors class permanently and costs a second per run. Report the
eight sites; fix them under the gate."

---

## a. DESIGN

### a.1 The class

The v1 retirement (D-191…D-221) deleted definitions and left callers behind.
Nothing in the gate suite reads names against definitions, so a route that
raises `NameError` on every call stays green until a human calls it — and no
human has. The audit found eight sites at four locations in under a second
with `pyflakes`, which was not installed.

### a.2 The gate, and why it fails rather than skips

`tests/unit/test_no_undefined_names.py` runs pyflakes' API over `primeqa/` and
fails on any "undefined name". It **fails, never skips, when pyflakes is not
importable**: during the audit a pyflakes run reported "0 undefined names"
because the tool was absent — a gate that reports green when its tool is
missing is the failure class under audit, and this gate refuses to be one. A
second test proves the tool itself sees a planted undefined name. The
pre-commit hook runs the same check on staged Python; `requirements-dev.txt`
carries pytest and the pinned pyflakes so the gate environment has both.

### a.3 The eight sites, and the fix chosen for each

| site | finding | fix |
|---|---|---|
| `views.py:475, :585` `_hash_share_token` | AUD-006 — public share links and their mint 500 on every call | **restored verbatim** from 7fd518f^ (SHA-256 hex), so links hashed before the retirement still match |
| `views.py:2983` `logging` | AUD-008 — the "never breaks the page" handler raises inside its own `except` | `import logging` at module level; three other handlers in the file were already using a local import |
| `execution/routes.py:65, :70` `_jira_client`, `_jira_client_for_env` | AUD-009 — Jira ticket search 500s once a connection is named | the branch **answers honestly** ("ticket search is not available … enter keys directly") instead of dying — see a.4 |
| `metadata/service.py:955 ×3` `prev_version` | AUD-010 — legacy `refresh_metadata` references a name never bound | **deleted**: 333 lines, zero callers, in a module CLAUDE.md marks superseded |

### a.4 The lean on Jira search, stated so AK can overturn it

The retired helpers built a `JiraClient` that no longer exists anywhere in the
tree; the live code has only the single-issue fetch the import path uses. And
the requirements picker's search input sends `q` alone — it never sends the
connection it sits beside — so live search has not worked from the page since
the retirement regardless of the route. Rebuilding a search client is a feature
slice with a real Jira to test against; this slice fixes the name by making the
route tell the truth. Both the rebuild and the picker's missing include are in
the FIX PLAN.

### a.5 Non-goals

No other pyflakes classes are gated (unused imports, redefinitions); they are
noise here, not defects. No template or route beyond the four locations.

---

## b. What changed

| file | change |
|---|---|
| `tests/unit/test_no_undefined_names.py` | the gate (2 tests) |
| `tests/integration/test_undefined_names_pages.py` | AUD-008 on the page: readiness forced to raise → 200, not 500 |
| `primeqa/views.py` | `_hash_share_token` restored verbatim; `import logging` |
| `primeqa/execution/routes.py` | the dead search branch answers honestly; the import it used removed |
| `primeqa/metadata/service.py` | `refresh_metadata` deleted (−333) |
| `requirements-dev.txt` | new: pytest, pyflakes 3.4.0 |
| `.githooks/pre-commit` | the staged-Python check |
| `docs/CONVENTIONS.md` | the gate named beside the decision-number check |

Code, tests and docs. No migration.

## c. Proof the guards guard

* The gate on the UNFIXED tree: **red, naming all eight sites**. On the fixed
  tree: green.
* The AUD-008 page test on the fixed tree: **200**. The same test against
  `main`'s tree in a worktree: **500** — "assert 500 == 200". The net now holds
  where it used to break.

## d. Suites

| suite | result |
|---|---|
| sweeps first (D-490), now including the plan-required guard | 70 passed |
| unit | **5,151 passed** (2 new: the gate and its self-check) |
| `tests/integration/test_representation` | 455 passed, 5 failed — the same five calendar-rotted fixtures as D-494, identical on `main` |
| page suites incl. the AUD-008 page test | 46 passed, 2 skipped |

## e. An incident during this slice, recorded because it is the class under audit

While taking a read-only production baseline I piped the production
`JWT_SECRET` into `python -` together with a heredoc. The pipe won, Python read
the secret as the program, and the resulting `NameError` printed the secret's
value into the session transcript. No request was made with it afterwards and
nothing was written anywhere else, but a signing secret that has been printed
must be treated as compromised. **Rotating it is AK's act**, not mine; it is
the first line of the HOLD. The rule this adds: a secret goes to a script
FILE on stdin, never to `python -`; and the earlier proofs in this session did
exactly that — the failure was one command, not the method.

