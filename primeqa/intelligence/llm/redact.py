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

import hashlib
import re
from typing import Any, Dict, List


# Conservative regexes, one per PII kind. Order matters (longer shapes
# before substrings of themselves) and is shared by both redaction modes.
_KINDS: List[tuple] = [
    # Email
    ("email", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")),
    # US SSN xxx-xx-xxxx
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    # US phone (loose) xxx-xxx-xxxx or (xxx) xxx-xxxx
    ("phone", re.compile(r"\b\(?\d{3}\)?[ .\-]\d{3}[ .\-]\d{4}\b")),
    # Credit-card-ish (16 digits with optional separators)
    ("ccn", re.compile(r"\b(?:\d[ \-]?){15,16}\d\b")),
    # AWS access key id  (AKIA + 16 alnum)
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    # Anthropic-style token (starts with sk-ant-)
    ("anthropic_token", re.compile(r"sk-ant-[A-Za-z0-9_\-]+")),
    # Generic Bearer tokens
    ("token", re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+")),
]

# Legacy flat replacements (pre-D-217 behavior, kept for back-compat).
_LEGACY_REPLACEMENT: Dict[str, str] = {
    "email": "<email>", "ssn": "<ssn>", "phone": "<phone>", "ccn": "<ccn>",
    "aws_key": "<aws_key>", "anthropic_token": "<anthropic_token>",
    "token": "bearer <token>",
}
_PATTERNS: List[tuple] = [
    (pattern, _LEGACY_REPLACEMENT[kind]) for kind, pattern in _KINDS
]

# D-217: indexed tokens — `<kind:sha8>`. Deterministic BY VALUE (no counter
# state) so the same value yields the same token across the separate
# system/messages redaction calls and across the S3 runtime's multi-turn
# re-redaction of the full history. No _KINDS pattern matches a token, so a
# second redaction pass over tokenized text is a no-op.
_TOKEN_RE = re.compile(
    r"<(?:email|ssn|phone|ccn|aws_key|anthropic_token|token):[0-9a-f]{8}>")


def _indexed_token(kind: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return f"<{kind}:{digest}>"


def redact_text(text: str) -> str:
    """Return a redacted copy of `text`. Safe on None / empty."""
    if not text:
        return text
    out = text
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def redact_text_indexed(text: str, mapping: Dict[str, str]) -> str:
    """D-217: redact `text` with indexed tokens, recording each
    `token -> original` into `mapping` (mutated). Safe on None / empty."""
    if not text:
        return text
    out = text
    for kind, pattern in _KINDS:
        def _sub(m, _kind=kind):
            token = _indexed_token(_kind, m.group(0))
            mapping[token] = m.group(0)
            return token
        out = pattern.sub(_sub, out)
    return out


def _walk_messages(messages: List[Dict[str, Any]], text_fn) -> List[Dict[str, Any]]:
    """Shared message-walk: apply `text_fn` to every string content / text
    block. Preserves structure (content blocks, cache_control, tool_use,
    etc.). Returns a NEW list; original is untouched.

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
            new_msg["content"] = text_fn(content)
        elif isinstance(content, list):
            new_blocks = []
            for block in content:
                if isinstance(block, dict):
                    new_block = dict(block)
                    if new_block.get("type") == "text" and isinstance(new_block.get("text"), str):
                        new_block["text"] = text_fn(new_block["text"])
                    new_blocks.append(new_block)
                else:
                    new_blocks.append(block)
            new_msg["content"] = new_blocks
        out.append(new_msg)
    return out


def redact_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Legacy flat-token redaction (pre-D-217 behavior, byte-identical)."""
    return _walk_messages(messages, redact_text)


def redact_messages_indexed(
    messages: List[Dict[str, Any]], mapping: Dict[str, str],
) -> List[Dict[str, Any]]:
    """D-217: indexed redaction over a message list; tokens recorded into
    `mapping` (mutated). Pass the SAME mapping for the system redaction call
    so one map covers the whole outbound prompt."""
    return _walk_messages(messages, lambda t: redact_text_indexed(t, mapping))


def rehydrate_text(text: str, mapping: Dict[str, str]) -> str:
    """D-217: replace every issued token in `text` with its original value.
    Unknown / model-mangled tokens are left as-is (graceful degradation).
    Safe on None / empty; identity when `mapping` is empty."""
    if not text or not mapping:
        return text
    return _TOKEN_RE.sub(lambda m: mapping.get(m.group(0), m.group(0)), text)


def rehydrate_value(obj: Any, mapping: Dict[str, str]) -> Any:
    """D-217: recursively rehydrate strings inside a parsed structure
    (dict / list / str; keys included). Non-string leaves pass through."""
    if not mapping:
        return obj
    if isinstance(obj, str):
        return rehydrate_text(obj, mapping)
    if isinstance(obj, list):
        return [rehydrate_value(x, mapping) for x in obj]
    if isinstance(obj, dict):
        return {
            (rehydrate_text(k, mapping) if isinstance(k, str) else k):
                rehydrate_value(v, mapping)
            for k, v in obj.items()
        }
    return obj


def rehydrate_content_blocks(blocks: List[Any], mapping: Dict[str, str]) -> List[Any]:
    """D-217: rehydrate a response's content blocks (Anthropic SDK objects or
    plain dicts) into PLAIN DICTS with real values \u2014 consumers duck-type both
    shapes (runtime.py `_battr`). Identity when `mapping` is empty; unknown
    block types pass through untouched."""
    if not mapping:
        return blocks
    out: List[Any] = []
    for b in blocks or []:
        get = (lambda k, _b=b: _b.get(k)) if isinstance(b, dict) \
            else (lambda k, _b=b: getattr(_b, k, None))
        btype = get("type")
        if btype == "tool_use":
            out.append({"type": "tool_use", "id": get("id"), "name": get("name"),
                        "input": rehydrate_value(get("input") or {}, mapping)})
        elif btype == "text":
            out.append({"type": "text",
                        "text": rehydrate_text(get("text") or "", mapping)})
        else:
            out.append(b)
    return out
