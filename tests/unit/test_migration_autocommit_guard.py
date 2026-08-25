"""FIX-1 guard: autocommit_block is prohibited in alembic migrations.

Under the current env.py transaction model, env.py owns the migration
transaction (SQLAlchemy ``connection.begin()`` at alembic/env.py:113);
alembic's own ``MigrationContext._transaction`` is never set. A migration
calling ``op.get_context().autocommit_block()`` therefore hits
``assert self._transaction is not None`` (alembic runtime/migration.py) and
CRASHES every fresh-chain run — which rolls the whole chain back atomically
and blocks new-tenant provisioning. FIX-1 (docs/reviews/PLIMSOL_FIX_PLAN.md,
resolved 2026-08-24, D-459) removed the one such call; this test prevents a
new one from landing.

The scan matches the CALL syntax ``autocommit_block(`` — not the bare word —
so a docstring that mentions the identifier historically (as the repaired
revision now does) is not a false positive.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_VERSIONS_DIR = Path(__file__).resolve().parents[2] / "alembic" / "versions"


def test_no_migration_calls_autocommit_block():
    assert _VERSIONS_DIR.is_dir(), f"missing {_VERSIONS_DIR}"
    offenders = sorted(
        str(f.relative_to(_VERSIONS_DIR.parent.parent))
        for f in _VERSIONS_DIR.rglob("*.py")
        if "autocommit_block(" in f.read_text(encoding="utf-8")
    )
    assert offenders == [], (
        "autocommit_block() is prohibited in migrations under the current "
        "env.py transaction model (env.py owns the transaction via "
        "connection.begin(); alembic never does — the call crashes every "
        f"fresh chain). Offending files: {offenders}. See FIX-1 in "
        "docs/reviews/PLIMSOL_FIX_PLAN.md and D-459."
    )
