"""Substrate-2 Pydantic models.

Per SPEC §4.7. Three submodules in B-α:
  - :mod:`primeqa.test_representation.models.common` — abstract
    bases, sentinel markers, type aliases shared across bodies.
  - :mod:`primeqa.test_representation.models.references` — reference
    type hierarchy per D-058 §5 and D-060 §4.7.6.
  - :mod:`primeqa.test_representation.models.registry` — body-schema
    registry per SPEC §4.4 + §4.7.1.

Body models per kind (value-claim, state-transition-claim, etc.)
are added in sub-tracks B-β through B-ε.
"""
