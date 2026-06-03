"""S8 claim-grounding leg (D-139) — does a claim's subject still resolve in the
current org?

The second leg of the S8 grounding-validity predicate (SPEC §2), the
claim-grounding axis. It asks the question *prior* to recipe-grounding: before
"does the payload still violate?", does the thing the claim is *about* still
exist? It re-asks generation's own resolution step — ``resolve_subject`` →
``SemanticOrgModel.get_entities(type, at_seq, filters={"sf_api_name":
external_id})`` — against today's S1 active set. A subject that no longer
resolves (a renamed/deleted Field or Object) means the test can no longer
address what it claims: broken grounding, independent of any validation rule.

**By external_id, not entity_id (D-139).** Claim subjects are pinned
``IdentityBearingRef``s carrying both ``entity_id`` and ``external_id``. A rename
supersedes the S1 entity (the ``entity_id`` persists, the ``external_id``
changes), and execution addresses the org by api-name (``external_id``) — so the
execution-faithful "still resolves" is *by external_id*: it catches the
rename-break that an ``entity_id`` check would mask.

**Parallel-siblings (D-112).** S8 reads S1's resolution through its OWN port
(:class:`SubjectResolver`), duck-typed; the production adapter over
``SemanticOrgModel`` lands with the composition (slice 3). S8 imports no S6 type.

Faculty-first / produce-only: a pure verdict, nothing wired, no persistence.
Claim-grounding axis ONLY, over ``asserted_truth`` (the subject — what the claim
is about); ``semantic_conditions`` scoping refs are a later extension.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Protocol

from pydantic import BaseModel

from primeqa.test_representation.models.common import BodyBase
from primeqa.test_representation.models.references import (
    IdentityBearingRef,
    LogicalRef,
    PinnedRef,
)

# Two-valued: resolution is binary (a subject resolves or it doesn't). The
# value/formula *drift* axes belong to the recipe-grounding + field-value legs.
ClaimGroundingVerdict = Literal["intact", "broken"]


@dataclass(frozen=True)
class ClaimGroundingResult:
    """The claim-grounding verdict. ``reason`` (``"subject_not_resolved"``) and
    ``unresolved`` are set only on ``broken`` and are **load-bearing** —
    ``unresolved`` carries the ``(entity_type, external_id)`` pairs that no
    longer resolve, so a consumer can name exactly which subject(s) broke."""

    verdict: ClaimGroundingVerdict
    reason: Optional[str] = None
    unresolved: tuple[tuple[str, str], ...] = ()


class SubjectResolver(Protocol):
    """Read-port for "does this subject still resolve in the current org?".
    Production delegates to S1's query interface (``get_entities`` filtered by
    ``sf_api_name``); tests inject a stub. S8's OWN port — S6's reader satisfies
    the shape, but S8 does not import it (parallel-siblings, D-112)."""

    def resolves(self, entity_type: str, external_id: str) -> bool:
        ...


def claim_grounding_validity(
    entity_type: str, external_id: str, *, s1: SubjectResolver,
) -> ClaimGroundingResult:
    """Does the subject ``(entity_type, external_id)`` still resolve? Resolves →
    ``intact``; not → ``broken`` / ``subject_not_resolved``."""
    if s1.resolves(entity_type, external_id):
        return ClaimGroundingResult("intact")
    return ClaimGroundingResult(
        "broken", reason="subject_not_resolved",
        unresolved=((entity_type, external_id),))


def claim_grounding_validity_for_claim(
    asserted_truth: BodyBase, *, s1: SubjectResolver,
) -> ClaimGroundingResult:
    """Adapter: walk a claim's ``asserted_truth`` for **every**
    identity-bearing subject and resolve each against the current org.

    Claim-kind-agnostic — every body kind names its subject(s) differently
    (``subject`` / ``target`` / ``source`` / ``granting_subject`` / ``automation``
    / ``layout`` / ``field``), so the walk finds them generically (mirrors the
    D-058 §5.4 coverage walk, projected to ``external_id``). ``broken`` if **any**
    subject no longer resolves — a claim grounded on a deleted Field *or* Object
    is broken. A degenerate body with no identity-bearing ref is vacuously
    ``intact`` (nothing to be ungrounded)."""
    pairs = _identity_bearing_subjects(asserted_truth)
    unresolved = tuple(
        (etype, ext) for (etype, ext) in pairs if not s1.resolves(etype, ext))
    if unresolved:
        return ClaimGroundingResult(
            "broken", reason="subject_not_resolved", unresolved=unresolved)
    return ClaimGroundingResult("intact")


def _identity_bearing_subjects(body: BodyBase) -> tuple[tuple[str, str], ...]:
    """Recursive descent collecting the ``(entity_type, external_id)`` of every
    :class:`IdentityBearingRef` in ``body``, deduplicated, in first-seen order
    (deterministic). Mirrors ``coverage._walk`` but projects to ``external_id``
    (the resolution key here) rather than ``entity_id`` — so ``coverage`` is not
    reused directly (shared-walker extraction is tracked adjacent work). S8 is a
    read-only judge, so it is **lenient**: operational refs
    (``PinnedRef``/``LogicalRef``) in identity slots are skipped, not raised on
    (unlike the write-time coverage walk)."""
    acc: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    _walk(body, acc, seen)
    return tuple(acc)


def _walk(value, acc: list, seen: set) -> None:
    if value is None:
        return
    # IdentityBearingRef BEFORE PinnedRef — it is a subclass.
    if isinstance(value, IdentityBearingRef):
        key = (value.entity_type, value.external_id)
        if key not in seen:
            seen.add(key)
            acc.append(key)
        return
    if isinstance(value, (PinnedRef, LogicalRef)):
        return  # operational ref, not an identity-bearing subject — skip
    if isinstance(value, BaseModel):
        for field_name in type(value).model_fields:
            if field_name == "body_schema_version":
                continue
            _walk(getattr(value, field_name), acc, seen)
        return
    if isinstance(value, list):
        for item in value:
            _walk(item, acc, seen)
        return
    if isinstance(value, dict):
        for sub_value in value.values():
            _walk(sub_value, acc, seen)
        return
    # scalar — carries no ref; skip.
