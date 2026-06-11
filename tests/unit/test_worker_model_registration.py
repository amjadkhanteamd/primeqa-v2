"""D-181 regression: the worker process must register the FULL SQLAlchemy
model set at import time.

The worker (`python -m primeqa.worker`) is a separate process that never
imports app.py. Before D-181 it loaded models only lazily/partially, so a
flush of a model carrying a cross-module string ForeignKey — e.g.
``LLMUsageLog.requirement_id -> requirements`` (exercised by S1 summary
enrichment's usage.record() once D-179 made the worker call the LLM gateway)
— raised ``NoReferencedTableError`` because the FK target table wasn't yet in
``Base.metadata``. The fix mirrors app.py's registration block at worker module
scope.
"""
from __future__ import annotations

import pathlib

import pytest

pytestmark = pytest.mark.unit

_WORKER_SRC = pathlib.Path(__file__).resolve().parents[2] / "primeqa" / "worker.py"


def test_worker_registers_full_model_set_at_module_scope() -> None:
    """Pollution-proof guard: assert the import lines exist in worker.py source
    (runtime metadata can be polluted by other tests importing the models)."""
    src = _WORKER_SRC.read_text()
    for mod in (
        "primeqa.core.models",
        "primeqa.test_management.models",
        "primeqa.execution.models",
        "primeqa.intelligence.models",
        "primeqa.release.models",
        "primeqa.metadata.models",
        "primeqa.vector.models",
    ):
        assert f"import {mod}" in src, \
            f"worker.py must import {mod} at module scope (D-181)"


def test_llm_usage_log_foreign_keys_resolve_after_worker_import() -> None:
    """The actual failure mode reproduced: once the worker is imported,
    resolving LLMUsageLog's ForeignKeys must not raise NoReferencedTableError.
    Accessing ``fk.column`` forces resolution against Base.metadata."""
    import primeqa.worker  # noqa: F401  - import side-effect registers the models
    from primeqa.intelligence.models import LLMUsageLog

    # .column.table.name forces FK resolution; raises if a target is unregistered.
    targets = {fk.column.table.name
               for fk in LLMUsageLog.__table__.foreign_keys}
    # 'requirements' is the table that was missing pre-D-181.
    assert "requirements" in targets


def test_worker_import_populates_fk_target_tables() -> None:
    """After importing the worker, the FK-target tables LLMUsageLog points at
    are registered in Base.metadata (meaningful in isolation)."""
    import primeqa.worker  # noqa: F401
    from primeqa.db import Base

    tables = set(Base.metadata.tables)
    # D-221 R4: the v1 product tables left the ORM; the surviving FK
    # targets are the shared pivot + auth tables.
    for t in ("llm_usage_log", "requirements", "users", "tenants"):
        assert t in tables, f"{t} not registered in Base.metadata after worker import"
