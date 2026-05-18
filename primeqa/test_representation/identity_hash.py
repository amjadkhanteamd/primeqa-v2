"""identity_hash — semantic equivalence fingerprint.

Per SPEC §6.3 + D-059 (canonicalization + governance) + D-057
(versioning). The ``identity_hash`` is the **semantic equivalence
fingerprint** of a claim: two claims with the same hash mean the
same thing; two claims with different hashes mean (possibly)
different things. It is NOT a unique identifier — multiple rows
may share a hash — and comparison is only valid **within the
same** :data:`IDENTITY_HASH_VERSION` per §6.3.7.

This module defines:

  - :data:`IDENTITY_HASH_VERSION` — the canonicalization policy
    version. v1 is the policy defined by SPEC §6.3.2–§6.3.5 and
    implemented in :mod:`primeqa.test_representation.canonicalization`.
    Future v2 will bump this constant when policy rules change in
    ways that alter canonical output for any existing body class.

  - :func:`compute_identity_hash` — the hash function. Composes
    archetype + claim_kind + canonical(asserted_truth) +
    canonical(semantic_conditions) into a SHA-256 hex digest per
    §6.3.6.

Hash input scope (§6.3.1): identity is determined by the
identity-bearing layers ONLY — asserted truth and semantic
conditions. Operational layers (causal initiation / observation
realization / execution environment / provenance) are EXCLUDED.
Changing a trigger's mechanism doesn't change the test's meaning;
changing the claim's subject does. The hash reflects that
distinction.

``identity_hash_version`` is itself EXCLUDED from the hash input
per §6.3.5: storing the version alongside the hash lets readers
detect cross-version comparisons (which are invalid) without
making the hash circular under version bumps. Forward-compat:
when v2 lands, both the hash AND a separate ``identity_hash_version``
column on the persisted row identify which policy produced the
hash; comparing across versions surfaces as a known-incompatible
condition, not as a silent identity collision.
"""
from __future__ import annotations

import hashlib

from primeqa.test_representation.canonicalization import (
    canonical_serialize,
    canonicalize,
)
from primeqa.test_representation.models.common import BodyBase, IdentityHash


IDENTITY_HASH_VERSION: int = 1
"""The current canonicalization policy version per SPEC §6.3.7.

Hashes are only directly comparable across rows that share this
version. The substrate stores ``identity_hash_version`` alongside
``identity_hash`` on every persisted row so cross-version
comparisons surface explicitly rather than silently producing a
false-positive identity collision.

**Changes to this constant are governance-critical.** A v1 → v2
bump means: every hash computed under v1 is no longer comparable
to hashes computed under v2; the canonicalization policy has
materially changed. Bumping requires:
  1. An explicit migration documenting which canonicalization
     rule changed and why.
  2. A re-hashing pass over the substrate's persisted bodies (or
     a window during which both versions are accepted, per
     §6.3.7).
  3. Updates to the canonicalizer registry's
     ``identity_hash_version`` key so v2 canonicalizers don't
     collide with v1 entries.
See SPEC §6.3.9 for the six governance rules.
"""


def compute_identity_hash(
    archetype: str,
    claim_kind: str,
    asserted_truth: BodyBase,
    semantic_conditions: BodyBase,
) -> IdentityHash:
    """Compute the SHA-256 hex digest identifying the claim's
    semantic content per SPEC §6.3.6.

    Hash input dict (canonical-serialized, UTF-8 encoded,
    SHA-256, hex)::

        {
          "archetype": archetype,
          "claim_kind": claim_kind,
          "asserted_truth": canonicalize(asserted_truth),
          "semantic_conditions": canonicalize(semantic_conditions),
        }

    Per SPEC §6.3.5, ``identity_hash_version`` is **NOT** included
    in the input — it's stored alongside the hash on the
    persisted row, not folded into it.

    Args:
      archetype: e.g., ``"data_behavior"`` per D-052.
      claim_kind: e.g., ``"value-claim"`` per D-053.
      asserted_truth: the claim body (must be an identity-bearing
        layer body — typically a claim body for the matching
        ``claim_kind``).
      semantic_conditions: the conditions body (typically an
        instance of :class:`SemanticConditionsBody`).

    Returns:
      A 64-character lowercase hex string (the SHA-256 digest).
      The return type alias :data:`IdentityHash` resolves to
      ``str`` at v1; v2 may wrap in an envelope class without
      breaking this signature (consumers should accept the alias,
      not the concrete ``str`` type).
    """
    hash_input = {
        "archetype": archetype,
        "claim_kind": claim_kind,
        "asserted_truth": canonicalize(asserted_truth),
        "semantic_conditions": canonicalize(semantic_conditions),
    }
    canonical_form = canonical_serialize(hash_input)
    digest = hashlib.sha256(canonical_form.encode("utf-8")).hexdigest()
    return digest
