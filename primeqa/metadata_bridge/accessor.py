"""MetadataAccessor — the S1-only v1 metadata read facade (D-195, cutover Step 5a.1).

Was the flag-gated read-*switch* (D-158): ``cutover_read_s1 AND s1_reader ? S1 :
meta_*``. Step 5a.1 retires the meta_* side — S1 is now the **sole** metadata
source, so the accessor is a thin, duck-typed facade over the injected
``s1_reader`` (an :class:`~primeqa.metadata_bridge.s1_reader.MetadataS1Reader`).
The consumer call sites (``TestCaseGenerator``, ``TestCaseValidator``, and via
them the ``GenerationLinter``) inject the accessor in place of the raw repo and
are unchanged — they call only ``get_objects`` / ``get_fields`` /
``get_validation_rules``.

No org model hydrated (``s1_reader=None``, e.g. a tenant with no S1 versions) →
the reads return empty (``[]`` / ``None``); there is **no** meta_* fallback. The
old ``_flag_on`` gate, the ``meta_*`` ``_repo`` path, ``get_version``, and the
``__getattr__`` delegation are all gone (the generator/validator never reached
them — verified before removal).

``cutover_read_s1_enabled`` (the per-tenant flag read) remains **only** for
``MetadataService.check_drift``'s S1-freshness anchor (D-184), which relocates +
goes S1-only when ``primeqa/metadata/`` is deleted in Step 5a.3; the flag column
is dropped then too.
"""
from __future__ import annotations

import logging

from sqlalchemy import text

log = logging.getLogger(__name__)


def cutover_read_s1_enabled(db, tenant_id) -> bool:
    """Tolerant read of the per-tenant ``cutover_read_s1`` flag (→ False on a
    missing row / column / any DB error).

    Step 5a.1 retired the accessor's use of this; its sole remaining caller is
    ``MetadataService.check_drift``'s S1-freshness anchor, pending that method's
    relocation + S1-only rewrite in Step 5a.3 (when the flag column is dropped)."""
    try:
        row = db.execute(text(
            "SELECT cutover_read_s1 FROM tenant_agent_settings "
            "WHERE tenant_id = :tid"), {"tid": tenant_id}).first()
        return bool(row and row[0])
    except Exception as exc:                     # missing row/column / DB blip
        log.warning("cutover_read_s1 read failed for tenant %s: %s "
                    "(defaulting to off)", tenant_id, exc)
        return False


class MetadataAccessor:
    """S1-only facade over the metadata read-interface. Every read returns the S1
    semantic org model via the injected ``s1_reader``; with no reader (no org
    model hydrated) the reads return empty — there is no meta_* fallback."""

    def __init__(self, tenant_id, s1_reader=None):
        self._tenant_id = tenant_id
        self._s1 = s1_reader

    def get_objects(self, meta_version_id=None):
        return self._s1.get_objects(meta_version_id) if self._s1 else []

    def get_fields(self, meta_version_id=None, object_id=None):
        return self._s1.get_fields(meta_version_id, object_id) if self._s1 else []

    def get_validation_rules(self, meta_version_id=None, object_id=None):
        return (self._s1.get_validation_rules(meta_version_id, object_id)
                if self._s1 else [])

    def get_object_by_api_name(self, meta_version_id, api_name):
        return (self._s1.get_object_by_api_name(meta_version_id, api_name)
                if self._s1 else None)
