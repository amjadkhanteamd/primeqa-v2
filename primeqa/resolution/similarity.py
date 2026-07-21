"""Pure lexical engine for resolution candidates — deterministic, import-free.

Mirrors the B0 engine (``primeqa.generation.recovery``) in SPIRIT: SF-suffix
stripping, camel/underscore tokenization, equal-weight token-Dice +
char-trigram-Dice, requirement-context overlap. It deliberately does NOT
import that module — ``resolution`` is a cross-cutting package and must not
depend on a substrate; consolidation of the two engines is deferred (D-376).

Everything here is a total function of its inputs: no S1, no LLM, no
randomness. Rankings built on these scores are total orders.
"""
from __future__ import annotations

import re
from typing import Optional

MIN_SCORE = 0.35

_SF_SUFFIXES = ("__c", "__r", "__e", "__mdt", "__x", "__b")
_SPLIT_RE = re.compile(r"[^0-9a-zA-Z]+")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def tokenize(name: Optional[str]) -> tuple[str, ...]:
    """Lowercased semantic tokens: SF suffixes stripped (including on dotted
    parts), camelCase and non-alphanumerics split. Deterministic."""
    if not name:
        return ()
    s = name
    for suf in _SF_SUFFIXES:
        s = s.replace(suf + ".", ".")
        if s.endswith(suf):
            s = s[: -len(suf)]
    s = _CAMEL_RE.sub(" ", s)
    return tuple(t.lower() for t in _SPLIT_RE.split(s) if t)


def trigrams(tokens: tuple[str, ...]) -> frozenset:
    joined = " ".join(tokens)
    if len(joined) < 3:
        return frozenset({joined} if joined else ())
    return frozenset(joined[i:i + 3] for i in range(len(joined) - 2))


def dice(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return 2.0 * len(a & b) / (len(a) + len(b))


def similarity(proposed: str, candidate_api: str,
               candidate_label: Optional[str] = None) -> float:
    """Deterministic similarity in [0, 1]: equal-weight blend of token-set
    Dice and character-trigram Dice, max over api name and display label."""
    p_tok = tokenize(proposed)
    p_tri = trigrams(p_tok)
    best = 0.0
    for comparand in (candidate_api, candidate_label):
        if not comparand:
            continue
        c_tok = tokenize(comparand)
        score = (0.5 * dice(frozenset(p_tok), frozenset(c_tok))
                 + 0.5 * dice(p_tri, trigrams(c_tok)))
        if score > best:
            best = score
    return best


def context_overlap(api: str, label: Optional[str],
                    ctx_tokens: frozenset) -> int:
    """How many of the candidate's own tokens (api or label, whichever matches
    more) appear in the supplied context tokens. 0 when no context."""
    if not ctx_tokens:
        return 0
    api_hits = len(frozenset(tokenize(api)) & ctx_tokens)
    label_hits = len(frozenset(tokenize(label)) & ctx_tokens) if label else 0
    return max(api_hits, label_hits)


def norm_label(text: Optional[str]) -> str:
    """Case/space/suffix-insensitive label normalization for exact-label
    comparison: SF suffixes stripped, underscores/whitespace collapsed."""
    if not text:
        return ""
    return " ".join(tokenize(text))
