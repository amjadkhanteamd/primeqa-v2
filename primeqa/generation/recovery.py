"""B0 Grounded Recovery — deterministic near-miss candidates for a failed
semantic resolution (FB-V1 Wave-1 finding: the substrate could VETO a wrong
entity reference but could not help the model RECOVER, so a wrong first guess
decayed into a blanket model self-refusal — the req-320 job-76 class).

This module is the pure engine: contract types, tokenization, deterministic
similarity, ranking, and the model-facing feedback formatting. It reads no S1,
calls no LLM, and uses no randomness — every ranking is a total order (score
descending, then api-name ascending). The S1-reading producer that assembles a
candidate POOL from the pinned snapshot is
``AdmissibilityEngine.recover_reference`` (``governance_core``); keeping the
engine import-free makes the ranking behaviour unit-testable in isolation.

Design contract (B0):
  - the substrate provides grounded ALTERNATIVES, never conclusions — the
    model must explicitly re-propose its choice, which then re-enters normal
    resolution (no silent substitution anywhere);
  - candidates come only from the pinned semantic snapshot (the caller builds
    the pool from S1 reads at the run's ``s1_version_seq``);
  - the list stays deliberately small (``DEFAULT_LIMIT``) and thresholded
    (``DEFAULT_MIN_SCORE``) so unrelated entities are never exposed — an
    unrecognizable reference yields NOT_FOUND, not a directory listing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

# Recovery-resolution statuses (the B0 contract).
RESOLVED = "resolved"
NOT_FOUND = "not_found"
CANDIDATES = "candidates"

DEFAULT_LIMIT = 3
DEFAULT_MIN_SCORE = 0.35

# Salesforce api-name suffixes that carry no semantic signal for matching.
_SF_SUFFIXES = ("__c", "__r", "__e", "__mdt", "__x", "__b")

_SPLIT_RE = re.compile(r"[^0-9a-zA-Z]+")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


@dataclass(frozen=True)
class RecoveryCandidate:
    """One grounded near-miss: the stored api name (re-proposable verbatim),
    its display label (may be None), and the deterministic similarity score."""

    sf_api_name: str
    display_name: Optional[str]
    score: float


@dataclass(frozen=True)
class RecoveryResolution:
    """Outcome of a recovery attempt: RESOLVED (the reference exists after
    all), NOT_FOUND (no grounded near-miss clears the threshold), or
    CANDIDATES (a small ranked set the model may choose from)."""

    status: str
    candidates: tuple[RecoveryCandidate, ...] = field(default=())


def _tokenize(name: Optional[str]) -> tuple[str, ...]:
    """Lowercased semantic tokens of an api name or label: SF suffixes
    stripped, camelCase and non-alphanumerics split. Deterministic."""
    if not name:
        return ()
    s = name
    for suf in _SF_SUFFIXES:
        # strip anywhere-terminal suffixes on dotted parts ("Obj__c.Field__c")
        s = s.replace(suf + ".", ".")
        if s.endswith(suf):
            s = s[: -len(suf)]
    s = _CAMEL_RE.sub(" ", s)
    return tuple(t.lower() for t in _SPLIT_RE.split(s) if t)


def _trigrams(tokens: tuple[str, ...]) -> frozenset:
    """Character trigrams of the space-joined token string (typo signal)."""
    joined = " ".join(tokens)
    if len(joined) < 3:
        return frozenset({joined} if joined else ())
    return frozenset(joined[i:i + 3] for i in range(len(joined) - 2))


def _dice(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return 2.0 * len(a & b) / (len(a) + len(b))


def similarity(proposed: str, candidate_api: str,
               candidate_label: Optional[str] = None) -> float:
    """Deterministic similarity in [0, 1]: equal-weight blend of token-set
    Dice (word overlap) and character-trigram Dice (typo tolerance), taken as
    the max over the candidate's api name and display label."""
    p_tok = _tokenize(proposed)
    p_tri = _trigrams(p_tok)
    best = 0.0
    for comparand in (candidate_api, candidate_label):
        if not comparand:
            continue
        c_tok = _tokenize(comparand)
        score = 0.5 * _dice(frozenset(p_tok), frozenset(c_tok)) \
            + 0.5 * _dice(p_tri, _trigrams(c_tok))
        if score > best:
            best = score
    return best


def _context_matches(api: str, label: Optional[str],
                     ctx_tokens: frozenset) -> int:
    """How many of the candidate's own tokens (api or label, whichever matches
    more) appear in the requirement text — the grounded relatedness signal.
    'PLS FB Order' scores 3 against a requirement that speaks of PLS FB Order
    records; the standard 'Order' object scores 1; 'WorkOrder' matches only
    incidental words. Deterministic; 0 when no context supplied."""
    if not ctx_tokens:
        return 0
    return max(len(frozenset(_tokenize(api)) & ctx_tokens),
               len(frozenset(_tokenize(label)) & ctx_tokens) if label else 0)


def rank_candidates(proposed: str,
                    pool: Iterable[tuple[str, Optional[str]]],
                    *, limit: int = DEFAULT_LIMIT,
                    min_score: float = DEFAULT_MIN_SCORE,
                    context_text: Optional[str] = None,
                    ) -> tuple[RecoveryCandidate, ...]:
    """Rank a grounded pool against the failed reference. ``pool`` yields
    ``(sf_api_name, display_name)`` drawn from the pinned snapshot. Returns at
    most ``limit`` candidates whose guess-similarity clears ``min_score``,
    ordered deterministically.

    ``context_text`` (the run's own requirement text — grounded input, no LLM)
    adds a relatedness term: candidates whose own name tokens appear in the
    requirement outrank same-similarity strangers. Live-observed need: for the
    generic guess 'Order__c' pure similarity ranks the standard 'Order' and
    'WorkOrder' objects above the requirement's actual 'PLS FB Order', and the
    model then anchors on a plausible-wrong object. The blend (equal weight,
    matched-token count normalized within the pool) is still fully
    deterministic: score desc, then api-name asc; without context the ranking
    is pure similarity, unchanged."""
    if not proposed:
        return ()
    ctx_tokens = frozenset(_tokenize(context_text)) if context_text else frozenset()
    seen: set[str] = set()
    prelim: list[tuple[str, Optional[str], float, int]] = []
    for api, label in pool:
        if not api or api in seen:
            continue
        seen.add(api)
        s = similarity(proposed, api, label)
        if s >= min_score:
            prelim.append((api, label, s, _context_matches(api, label, ctx_tokens)))
    if not prelim:
        return ()
    max_ctx = max(p[3] for p in prelim)
    scored = [
        RecoveryCandidate(api, label, round(
            0.5 * s + 0.5 * (ctx / max_ctx) if max_ctx else s, 4))
        for api, label, s, ctx in prelim]
    scored.sort(key=lambda c: (-c.score, c.sf_api_name))
    return tuple(scored[:limit])


def format_candidates(candidates: tuple[RecoveryCandidate, ...]) -> str:
    """The model-facing feedback tail for a candidate set. Alternatives, never
    a conclusion: the model must re-propose its own choice (which re-enters
    normal resolution) or refuse honestly. Empty string when no candidates."""
    if not candidates:
        return ""
    listed = ", ".join(
        f"{c.sf_api_name} ({c.display_name!r})" if c.display_name
        else c.sf_api_name
        for c in candidates)
    return (" Closest grounded entities at this version: " + listed +
            ". If one of these is what you meant, re-propose using it; "
            "if none is, refuse honestly rather than invent a name.")


def offer_payload(entity_type: Optional[str], proposed: Optional[str],
                  candidates: tuple[RecoveryCandidate, ...]) -> dict:
    """Structured telemetry record of one candidate offer (what the substrate
    disclosed, for the outcome's audit trail — provenance: substrate)."""
    return {
        "entity_type": entity_type,
        "proposed": proposed,
        "candidates": [
            {"sf_api_name": c.sf_api_name, "display_name": c.display_name,
             "score": c.score} for c in candidates],
        "source": "substrate",
    }
