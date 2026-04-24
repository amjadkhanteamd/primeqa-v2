"""Shared word-boundary keyword matching.

Extracted so DomainPackSelector (knowledge.domain_packs) and
detect_complexity (llm.prompts.test_plan_generation) use a single
inflection-aware implementation.

History: naive substring matching was abandoned once we noticed
"flow" silently matching "workflow" in detect_complexity — the tenant
was being routed to Opus because every Salesforce requirement mentions
workflows. Word-boundary regex plus common verbal inflections
(-s / -es / -ed / -ing) gives us the matching semantics we actually
want without building an English-language NLP pipeline.
"""

import re

# Suffix trailer allowed after the keyword. Keep this small and
# predictable; if a pack author wants a stemmed form (e.g. "creator"
# from "create") they should list it explicitly.
_KW_INFLECT = r"(?:s|es|ed|ing)?"


def kw_count(text: str, keywords) -> int:
    """Count distinct keywords appearing in `text` as whole words
    (with optional -s/-es/-ed/-ing suffixes). Case-insensitive.

    The returned value is the number of distinct keywords that matched
    at least once, NOT the total hit count — "escalate escalate" still
    contributes 1 for the keyword "escalate".
    """
    found = 0
    for kw in keywords:
        pattern = r"\b" + re.escape(kw) + _KW_INFLECT + r"\b"
        if re.search(pattern, text, re.IGNORECASE):
            found += 1
    return found


def matched_keywords(text: str, keywords):
    """Return the list of keywords that appear as whole words in `text`.

    Preserves input ordering. Used by the domain-pack selector for
    attribution logging + test assertions.
    """
    out = []
    for kw in keywords:
        pattern = r"\b" + re.escape(kw) + _KW_INFLECT + r"\b"
        if re.search(pattern, text, re.IGNORECASE):
            out.append(kw)
    return out


__all__ = ["kw_count", "matched_keywords"]
