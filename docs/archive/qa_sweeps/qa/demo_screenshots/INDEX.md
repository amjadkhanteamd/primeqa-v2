# Demo Walkthrough — HTML snapshots (de-facto screenshots)

Captured via curl on 2026-04-23 against production:
`https://primeqa-v2-production.up.railway.app`

The Claude_in_Chrome / Playwright MCP was unresponsive during this
session (tabs_context_mcp timed out after 5 min), so HTML snapshots
with assertion checks stand in for pixel screenshots. Tomorrow
morning, open these in a browser or re-run the walkthrough via the
UI to confirm the visual state matches.

## Walkthrough index

| Step | File | HTTP | Notes |
|------|------|------|-------|
| 1a. GET / unauthed                | `01_root_unauthed.html`       | 302 | Redirects to /login |
| 1b. GET /login                    | `02a_login_page.html`         | 200 | Login form + CSRF cookie set |
| 1c. POST /login (admin)           | `02b_post_login.html`         | 302 | Redirects to /dashboard; `access_token` cookie set |
| 2.  GET /dashboard                | `03_dashboard.html`           | 200 | `data-page="release-dashboard"`, Go/No-Go badge present, sparkline/svg rendered, all 8 ACME-* tickets visible |
| 3a. GET /run                      | `04_run_page.html`            | 200 | Four mode tabs present (`data-mode="sprint/tickets/suite/release"`), prod-gate hidden by default, 3 envs in dropdown |
| 3b. GET /api/jira/sprints         | `05_sprints_api.json`         | 200 | 1 sprint returned from env's Jira connection |
| 3c. GET /api/jira/tickets/recent  | `06_recent_tickets.json`      | 200 | Empty — fresh session, nothing tracked yet |
| 3d. GET /api/jira/tickets/search  | `07_search_tickets.json`      | 200 | Seeded ACME-* keys aren't real Jira tickets so search is empty; use `PA-` for live Jira items |
| 4.  GET /results                  | `08_results.html`             | 302 | → /runs (as designed) |
| 5.  GET /runs/<id>                | `09_run_detail.html`          | 200 | Step-level results + status badges present |
| 6.  GET /reviews                  | `10_reviews.html`             | 200 | Renders (may be empty-state) |
| 7.  GET /settings/users           | `11_settings_users.html`      | 200 | Demo users visible: Amanda Rivera, Priya Sharma, Jordan Chen, Michael Okoye, Elena Voss |
| 8.  GET /releases                 | `12_releases.html`            | 200 | "Acme Release 2026.04" visible |
| 9.  GET /logout                   | `13_logout.html`              | 302 | Redirects to /login |

Polish snapshots of every primary nav route at polish_*.html.
