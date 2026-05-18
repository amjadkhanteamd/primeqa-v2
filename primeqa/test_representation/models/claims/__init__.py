"""Substrate-2 claim body models, grouped by archetype.

Per SPEC §3: claim-kinds are partitioned across five archetypes
(data-behavior, configuration, permission, ui, integration). Each
archetype has its own subpackage:

  - :mod:`primeqa.test_representation.models.claims.data_behavior`
    — B-β (this track): value, state-transition, automation-effect,
    prohibition.
  - Future: ``configuration`` / ``permission`` / ``ui`` /
    ``integration`` arrive in subsequent tracks.

This package itself is intentionally empty. Importing it does NOT
import its subpackages — bodies register at the import time of their
own module, per the contract in
:mod:`primeqa.test_representation.models.registry`. Callers that
need data-behavior claim bodies import the
``data_behavior`` subpackage explicitly.
"""
