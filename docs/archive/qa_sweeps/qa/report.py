"""Append section findings to QA_REPORT.md."""
import os
from typing import List, Dict

REPORT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "QA_REPORT.md")


def append_section(section_title: str, findings: List[Dict]):
    lines = [f"## {section_title}\n"]
    if not findings:
        lines.append("_(no checks recorded)_\n\n")
        with open(REPORT_PATH, "a") as fh:
            fh.write("\n".join(lines))
        return
    by_status = {}
    for f in findings:
        by_status.setdefault(f["status"], 0)
        by_status[f["status"]] += 1
    pill = " ".join(f"**{s}**: {n}" for s, n in sorted(by_status.items()))
    lines.append(f"{pill}\n")
    for f in findings:
        lines.append(f"### {f['id']}: {f['title']}")
        lines.append(f"- **Severity**: {f['severity']}")
        lines.append(f"- **Status**: {f['status']}")
        lines.append(f"- **URL**: {f['url']}")
        lines.append(f"- **Expected**: {f['expected']}")
        lines.append(f"- **Actual**: {f['actual']}")
        lines.append(f"- **Category**: {f['category']}")
        if f.get("evidence"):
            lines.append(f"- **Evidence**: {f['evidence']}")
        lines.append("")
    lines.append("---\n")
    with open(REPORT_PATH, "a") as fh:
        fh.write("\n".join(lines))


def append_summary(all_findings: List[Dict], recommendations: list):
    lines = ["\n## SUMMARY\n"]
    total = len(all_findings)
    by_status = {}
    for f in all_findings:
        by_status.setdefault(f["status"], 0)
        by_status[f["status"]] += 1
    lines.append(f"- Total checks: {total}")
    for s in ("PASS", "FAIL", "PARTIAL", "BLOCKED"):
        lines.append(f"- {s.title()}: {by_status.get(s, 0)}")
    lines.append("")

    for sev, label in [("P0", "P0 BUGS (fix before pilot)"),
                        ("P1", "P1 BUGS (fix within first week)"),
                        ("P2", "P2 BUGS (fix within first month)")]:
        in_this = [f for f in all_findings
                   if f["severity"] == sev and f["status"] in ("FAIL", "PARTIAL")]
        lines.append(f"## {label}")
        if in_this:
            for f in in_this:
                lines.append(f"- **{f['id']}**: {f['title']} \u2014 {f['actual'][:140]}")
        else:
            lines.append("_(none found)_")
        lines.append("")

    lines.append("## RECOMMENDATIONS")
    if recommendations:
        for rec in recommendations:
            lines.append(f"- {rec}")
    else:
        lines.append("_(see individual findings above)_")
    lines.append("")

    with open(REPORT_PATH, "a") as fh:
        fh.write("\n".join(lines))
