#!/usr/bin/env python3
"""PASS 3c — secrets in output. Scans rendered bodies, captured logs and API
JSON for credential shapes. Every hit is printed with its source and a
redacted excerpt; the audit's own scratch test secret is excluded by value."""
import json, os, re, sys
PAT = {
    "jwt": re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    "bcrypt": re.compile(r"\$2[aby]\$\d\d\$[./A-Za-z0-9]{53}"),
    "fernet": re.compile(r"gAAAAA[A-Za-z0-9_-]{40,}"),
    "aws": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "openai_like": re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    "slack": re.compile(r"\bxox[bp]-[A-Za-z0-9-]{20,}\b"),
    "pg_url_with_password": re.compile(r"postgres(?:ql)?://[^:\s/]+:[^@\s]+@"),
    "sf_session": re.compile(r"\b00D[A-Za-z0-9]{12,15}![A-Za-z0-9._]{30,}"),
    "hex64_secret": re.compile(r"(?i)(?:secret|token|key)[\"'=:\s]{1,6}([0-9a-f]{64})\b"),
    "password_field_value": re.compile(r'(?i)(?:password|api_token|client_secret|consumer_secret)["\']?\s*[:=]\s*["\']([^"\']{6,})["\']'),
}
EXCLUDE = {"0123456789abcdef" * 4}
def scan(name, text):
    hits = []
    for k, rx in PAT.items():
        for m in rx.finditer(text):
            v = m.group(1) if m.groups() else m.group(0)
            if v in EXCLUDE: continue
            hits.append((k, v[:6] + "…" + v[-4:], text[max(0, m.start()-40):m.start()].replace("\n", " ")[-40:]))
    return hits
total = 0
for path in sys.argv[1:]:
    if os.path.isdir(path):
        files = [os.path.join(path, f) for f in sorted(os.listdir(path))]
    else:
        files = [path]
    for f in files:
        try: txt = open(f, errors="replace").read()
        except Exception: continue
        for k, v, ctx in scan(f, txt):
            total += 1; print("  HIT %-22s %-14s %s  ...%s" % (k, v, os.path.basename(f)[:60], ctx))
print("  total hits:", total)
