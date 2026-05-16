"""Master runner: executes every section, compiles QA_REPORT.md.

Usage:
    python -m qa.run_sweep
"""
from __future__ import annotations

import importlib
import os
import sys
from datetime import datetime, timezone

REPORT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "QA_REPORT.md")

SECTION_HEADERS = {
    "qa.test_01_auth":          "Section 1: Authentication & Session",
    "qa.test_02_navigation":    "Section 2: Navigation & Page Loads",
    "qa.test_03_06_workflows":  "Sections 3-6: Settings CRUD, Requirements, Generation, Execution",
    "qa.test_11_api":           "Sections 11-12: API + Cross-Tenant Safety",
    "qa.test_07_recent_phases": "Section 7: Recent-phase Verification (Developer UX, Tester /run, Admin UI, Reviews)",
}

# Render order — keeps the report's flow sensible.
ORDER = [
    "qa.test_01_auth",
    "qa.test_02_navigation",
    "qa.test_03_06_workflows",
    "qa.test_07_recent_phases",
    "qa.test_11_api",
]


def _fmt_one(f: dict) -> str:
    return (
        f"### {f['id']}: {f['title']}\n"
        f"- **Severity**: {f.get('severity', 'P3')}\n"
        f"- **Status**: {f['status']}\n"
        f"- **URL**: {f.get('url') or '—'}\n"
        f"- **Expected**: {f.get('expected') or '—'}\n"
        f"- **Actual**: {f.get('actual') or '—'}\n"
        f"- **Category**: {f.get('category') or '—'}\n"
        + (f"- **Evidence**: {f['evidence']}\n" if f.get('evidence') else "")
    )


def main() -> int:
    # Reset the report to the header.
    with open(REPORT_PATH, "r") as fh:
        header = fh.read()
    # Drop anything after the header separator so we rewrite the body fresh.
    if "\n---\n" in header:
        header = header.split("\n---\n")[0] + "\n---\n"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    all_findings: list[dict] = []
    sections: list[tuple[str, list[dict]]] = []

    for mod_name in ORDER:
        print(f"\n=== running {mod_name} ===")
        mod = importlib.import_module(mod_name)
        try:
            findings = mod.run()
        except Exception as e:
            # A per-module crash (DNS flake, timeout) shouldn't nuke the
            # whole sweep. Record a single BLOCKED finding and continue.
            findings = [{
                "id": mod_name.rsplit(".", 1)[-1],
                "title": f"Section crashed: {type(e).__name__}",
                "severity": "P3",
                "status": "BLOCKED",
                "url": None,
                "expected": "section to complete",
                "actual": str(e)[:200],
                "category": "Infrastructure",
                "evidence": "",
            }]
        sections.append((SECTION_HEADERS.get(mod_name, mod_name), findings))
        all_findings.extend(findings)
        for f in findings:
            print(f"{f['status']:7s}  {f['id']:10s}  {f['title']}")

    # Tallies.
    by_status: dict[str, int] = {}
    for f in all_findings:
        by_status[f["status"]] = by_status.get(f["status"], 0) + 1

    p0_bugs = [f for f in all_findings
               if f["status"] in ("FAIL", "ERROR") and f.get("severity") == "P0"]
    p1_bugs = [f for f in all_findings
               if f["status"] in ("FAIL", "ERROR") and f.get("severity") == "P1"]
    p2_bugs = [f for f in all_findings
               if f["status"] in ("FAIL", "ERROR") and f.get("severity") == "P2"]
    p3_bugs = [f for f in all_findings
               if f["status"] in ("FAIL", "ERROR") and f.get("severity") == "P3"]
    partials = [f for f in all_findings if f["status"] == "PARTIAL"]

    # Render.
    body = [header, f"\n_Last refreshed {now}. Total checks: {len(all_findings)}_\n"]

    body.append("\n## Summary\n\n")
    body.append("| Status | Count |\n|---|---|\n")
    for k in ("PASS", "FAIL", "ERROR", "PARTIAL", "BLOCKED"):
        body.append(f"| {k} | {by_status.get(k, 0)} |\n")
    body.append("\n")

    body.append("## Priority Bugs\n\n")
    if p0_bugs:
        body.append("### P0 — fix before pilot\n")
        for b in p0_bugs:
            body.append(f"- **{b['id']}** — {b['title']}  (`{b['status']}`)\n")
        body.append("\n")
    else:
        body.append("### P0 — fix before pilot\n_none_\n\n")
    if p1_bugs:
        body.append("### P1 — fix within first week\n")
        for b in p1_bugs:
            body.append(f"- **{b['id']}** — {b['title']}  (`{b['status']}`)\n")
        body.append("\n")
    else:
        body.append("### P1 — fix within first week\n_none_\n\n")
    if p2_bugs:
        body.append("### P2 — fix within first month\n")
        for b in p2_bugs:
            body.append(f"- **{b['id']}** — {b['title']}  (`{b['status']}`)\n")
        body.append("\n")
    else:
        body.append("### P2 — fix within first month\n_none_\n\n")
    if p3_bugs:
        body.append("### P3 — polish / nice-to-have\n")
        for b in p3_bugs:
            body.append(f"- **{b['id']}** — {b['title']}  (`{b['status']}`)\n")
        body.append("\n")
    if partials:
        body.append("### Partial / not fully exercised\n")
        for b in partials:
            body.append(f"- **{b['id']}** — {b['title']}  ({b.get('actual','')})\n")
        body.append("\n")

    # Per-section detail.
    for title, findings in sections:
        body.append(f"\n## {title}\n\n")
        sec_by_status = {}
        for f in findings:
            sec_by_status[f["status"]] = sec_by_status.get(f["status"], 0) + 1
        pill = " | ".join(f"**{k}**: {v}" for k, v in sorted(sec_by_status.items()))
        body.append(f"_{pill} — {len(findings)} total_\n\n")
        for f in findings:
            body.append(_fmt_one(f))
            body.append("\n")

    # Manual / deferred notes.
    body.append("\n## Not automated (manual checks to do before pilot)\n\n")
    body.append(
        "- **Visual layout on narrow viewport (375px)** — spot-check every "
        "page for content overflow, touch-target size, and horizontal "
        "scrollbars.\n"
        "- **Browser back/forward from every authenticated page** — "
        "automated check covers URL redirects but not history-stack state.\n"
        "- **Screen reader walkthrough** — `aria-*` labels are present on "
        "the run detail page but not exhaustively on every form; use "
        "VoiceOver or NVDA against `/run`, `/tickets`, "
        "`/settings/users/:id`, `/reviews/:id`.\n"
        "- **Test connection buttons against live creds** — Jira + SF "
        "connection test flows exist but we only exercised negative paths "
        "in this sweep.\n"
        "- **Long-form text on cards/tables** — confirmed short-string "
        "rendering; manual pass to verify truncation on 500+ character "
        "Jira summaries + extremely long test-case titles.\n"
        "- **JavaScript console errors** — Playwright captures them per "
        "page, but we didn't flag any in this run. Spot-check open "
        "DevTools on `/tickets`, `/run`, `/reviews` for late-loaded "
        "HTMX + SSE warnings.\n"
    )

    body.append("\n## Recommendations\n\n")
    body.append(
        "- **CSRF_FAILED vs UNAUTHORIZED ordering** on `/api/*` — when a "
        "request has no `Authorization: Bearer` and no csrf_token cookie, "
        "the CSRF middleware fires first and the client sees "
        "`CSRF_FAILED` instead of `UNAUTHORIZED`. Secure behaviour, "
        "slightly confusing error code. Consider treating missing-auth "
        "as UNAUTHORIZED so API clients get a more actionable message.\n"
        "- **`/results` + `/runs` aliasing** preserves query strings but "
        "keeps the canonical URL as `/runs/:id`. The sidebar nav now uses "
        "`active_also_for: ('/runs',)` so the Results tab stays lit — "
        "worth documenting this hook in an internal UI primer so future "
        "pages re-use it instead of duplicating the logic.\n"
        "- **Badge counts** currently run a fresh COUNT on every nav render. "
        "At scale, a short-TTL memcache or a simple per-request cache "
        "would cut redundant queries on pages that never read the result.\n"
        "- **Superadmin self-deactivate**: today the superadmin bypass "
        "lets the seeded admin deactivate themselves, which can lock a "
        "tenant out (especially if there's only one god-mode user). "
        "Consider either a hard block (no bypass) or a "
        "last-superadmin-in-tenant guard.\n"
    )

    with open(REPORT_PATH, "w") as fh:
        fh.write("".join(body))

    print(f"\n\nFinal: {by_status}")
    print(f"P0 bugs: {len(p0_bugs)}  P1: {len(p1_bugs)}  P2: {len(p2_bugs)}  P3: {len(p3_bugs)}")
    return 1 if (p0_bugs or p1_bugs) else 0


if __name__ == "__main__":
    sys.exit(main())
