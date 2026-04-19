"""Redaction preprocessor \u2014 strips obvious PII before the prompt leaves
the building.

PrimeQA handles user-supplied text (requirements, acceptance criteria,
error messages) which can accidentally contain sensitive data. Anthropic
doesn't train on API data (per their policy), but regulators care about
transit, not training. Redaction is defence in depth:

  email addresses     \u2192 <email>
  SSNs (US)           \u2192 <ssn>
  US phone numbers    \u2192 <phone>
  credit-card-ish     \u2192 <ccn>
  AWS keys / tokens   \u2192 <token>
  UUIDs               \u2192 (left alone \u2014 often legitimate record IDs)

Designed to be fast (compiled regexes) and conservative (precision over
recall \u2014 if we're unsure, we don't redact, because destroying
legitimate content breaks generation quality).

Per-tenant extension: tenants can register custom regexes via
tenant_agent_settings.redaction_patterns (Phase 6). For now the rules
are global.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List


# Conservative regexes. Each replacement is a short token that shows the
# model "something was redacted here" so it doesn't treat a missing
# value as significant.
_PATTERNS: List[tuple] = [
    # Email
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
     "<email>"),
    # US SSN xxx-xx-xxxx
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "<ssn>"),
    # US phone (loose) xxx-xxx-xxxx or (xxx) xxx-xxxx
    (re.compile(r"\b\(?\d{3}\)?[ .\-]\d{3}[ .\-]\d{4}\b"), "<phone>"),
    # Credit-card-ish (16 digits with optional separators)
    (re.compile(r"\b(?:\d[ \-]?){15,16}\d\b"), "<ccn>"),
    # AWS access key id  (AKIA + 16 alnum)
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "<aws_key>"),
    # Anthropic-style token (starts with sk-ant-)
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]+"), "<anthropic_token>"),
    # Generic Bearer tokens
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"), "bearer <token>"),
]


def redact_text(text: str) -> str:
    """Return a redacted copy of `text`. Safe on None / empty."""
    if not text:
        return text
    out = text
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def redact_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Walk a message list and redact every string content / text block.
    Preserves structure (content blocks, cache_control, tool_use, etc.).
    Returns a NEW list; original is untouched.

    This is safe to run every call \u2014 the compiled regexes add < 1ms
    on typical prompt sizes.
    """
    if not messages:
        return messages
    out = []
    for msg in messages:
        new_msg = dict(msg)
        content = msg.get("content")
        if isinstance(content, str):
            new_msg["content"] = redact_text(content)
        elif isinstance(content, list):
            new_blocks = []
            for block in content:
                if isinstance(block, dict):
                    new_block = dict(block)
                    if new_block.get("type") == "text" and isinstance(new_block.get("text"), str):
                        new_block["text"] = redact_text(new_block["text"])
                    new_blocks.append(new_block)
                else:
                    new_blocks.append(block)
            new_msg["content"] = new_blocks
        out.append(new_msg)
    return out
