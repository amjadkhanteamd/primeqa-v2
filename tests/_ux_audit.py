"""One-shot UX audit: render every primary page via the test client and
grep the HTML for the CRUD affordances we expect.

Produces a table showing which pages have:
  create button, row click-through, row delete, search, pagination,
  sort headers, trash view, breadcrumbs, filter sidebar, import/generate

Not a unit test \u2014 just a diagnostic. Run with:
    python tests/_ux_audit.py
"""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv; load_dotenv()

from primeqa.app import app


# (url, label)
PAGES = [
    ("/", "Dashboard"),
    ("/releases", "Releases list"),
    ("/requirements", "Requirements list"),
    ("/runs", "Runs list"),
    ("/runs/new", "Run Wizard"),
    ("/runs/scheduled", "Scheduled runs"),
    ("/test-cases", "Test Library"),
    ("/suites", "Suites list"),
    ("/milestones", "Milestones"),
    ("/reviews", "Reviews queue"),
    ("/impacts", "Impacts"),
    ("/settings", "Settings home"),
    ("/settings/connections", "Settings: Connections"),
    ("/settings/environments", "Settings: Environments"),
    ("/settings/groups", "Settings: Groups"),
    ("/settings/users", "Settings: Users"),
    ("/settings/test-data", "Settings: Test Data"),
    ("/settings/agent", "Settings: Agent autonomy"),
]


# Check fns: given the HTML body, return True if the affordance is present.
CHECKS = [
    ("create", lambda h: any(p in h for p in [
        ">+ New", ">New ", 'href="/runs/new"', 'href="/releases/new"',
        'href="/suites/new"', 'href="/requirements/new"', 'href="/settings/connections/new"',
        'href="/settings/environments/new"', 'href="/settings/groups/new"',
        'href="/settings/users/new"', 'Trigger a new run',
        'Create Release', 'New Release', 'New Requirement',
        'New Test Case', 'New Suite', 'New Milestone',
        'Import from Jira', 'Generate', 'Add Connection', 'Add Environment',
        'Add Group', 'Add User', 'New Schedule', 'Schedule a run',
    ])),
    ("row link", lambda h: any(p in h for p in [
        'href="/requirements/', 'href="/test-cases/', 'href="/suites/',
        'href="/reviews/', 'href="/releases/', 'href="/impacts/',
        'href="/runs/', 'href="/settings/environments/', 'href="/settings/connections/',
        'href="/settings/groups/', 'href="/settings/users/',
        'onclick="window.location', 'data-href=',
    ])),
    ("row delete", lambda h: any(p in h for p in [
        'data-delete-id', 'data-confirm', '/delete',
        'hx-delete', 'method="DELETE"',
    ])),
    ("search", lambda h: any(p in h for p in [
        'name="q"', 'name="search"', 'placeholder="Search',
    ])),
    ("pagination", lambda h: any(p in h for p in [
        "render_pagination", "render_meta_pagination",
        "?page=2", "page=1", 'class="pagination', 'Per page',
    ])),
    ("sort headers", lambda h: "sort=" in h or "data-sort=" in h),
    ("trash view", lambda h: "deleted=1" in h or "View Trash" in h or "Trash" in h),
    ("breadcrumbs", lambda h: 'aria-label="Breadcrumb"' in h),
    ("filters / sidebar", lambda h: any(p in h for p in [
        "status chip", "section", 'name="section_id"', 'name="status"',
        "sidebar", "filter",
    ])),
    ("import/generate", lambda h: any(p in h for p in [
        "Import from Jira", "Generate", "import-jira", "/generate",
    ])),
]


def main():
    c = app.test_client()
    # Log in as admin (now superadmin post-migration-017)
    r = c.post("/api/auth/login", json={
        "email": "admin@primeqa.io", "password": "changeme123", "tenant_id": 1,
    })
    tok = (r.get_json() or {}).get("access_token")
    if not tok:
        print("FATAL: cannot log in"); sys.exit(2)
    c.set_cookie("access_token", tok)

    # Column widths for the output table
    name_w = max(len(label) for _, label in PAGES)
    chk_w = max(len(n) for n, _ in CHECKS)

    # Header
    hdr = ["Page".ljust(name_w), "sts"] + [n[:chk_w] for n, _ in CHECKS]
    print("  ".join(hdr))
    print("  ".join(["-" * name_w, "---"] + ["-" * len(n) for n, _ in CHECKS]))

    summary = []
    for url, label in PAGES:
        r = c.get(url)
        status = r.status_code
        html = r.data.decode("utf-8", errors="replace") if status == 200 else ""
        marks = []
        missing = []
        for name, fn in CHECKS:
            ok = bool(html) and fn(html)
            marks.append(("Y" if ok else ".").center(len(name)))
            if not ok:
                missing.append(name)
        print("  ".join([label.ljust(name_w), str(status).rjust(3)] + marks))
        if status == 200:
            summary.append((label, url, missing))

    # Per-page summary of missing affordances
    print("\n" + "=" * 60)
    print("Missing affordances per page (only MEANINGFUL ones — ignore false negatives):")
    print("=" * 60)
    for label, url, missing in summary:
        if missing:
            print(f"\n  {label}  ({url})")
            for m in missing:
                print(f"     \u2717 {m}")
        else:
            print(f"\n  {label}  ({url})")
            print("     all affordances present")


if __name__ == "__main__":
    main()
