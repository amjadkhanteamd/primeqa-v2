"""MetadataAccessor — the flagged v1 metadata read-switch (D-158, cutover Step 3).

The single switch point routing v1's metadata reads to either the S1 semantic
org model (when the per-tenant ``cutover_read_s1`` flag is on **and** an S1 reader
is available) or the ``meta_*`` ``MetadataRepository`` (the flag-off fallback).
A transparent proxy over the repo's read-interface: the consumer call sites
(``TestCaseGenerator``, ``TestCaseValidator``, and via them the
``GenerationLinter``) inject the accessor in place of the raw repo and are
duck-typed — they accept it unchanged.

The flag is read **once** at construction (tolerant try/except → False, the
``_domain_packs_enabled`` pattern — a transient DB blip can't break generation).
With ``s1_reader=None`` (Slice 3.1) the accessor is **pure passthrough** —
identical v1 behavior regardless of the flag. The S1 reader lands in 3.2
(generation) / 3.4 (validator).

The db session for the flag read is sourced from the wrapped repo
(``metadata_repo.db``) — the ``MetadataRepository`` always carries it, and the v1
``TestManagementService`` holds no ``db`` of its own.
"""
from __future__ import annotations

import logging

from sqlalchemy import text

log = logging.getLogger(__name__)


def cutover_read_s1_enabled(db, tenant_id) -> bool:
    """Tolerant read of the per-tenant ``cutover_read_s1`` flag (→ False on a
    missing row / column / any DB error). Shared by the accessor (to gate usage)
    and the service (to gate the — potentially expensive — S1 reader build, so a
    flag-off tenant never hydrates S1)."""
    try:
        row = db.execute(text(
            "SELECT cutover_read_s1 FROM tenant_agent_settings "
            "WHERE tenant_id = :tid"), {"tid": tenant_id}).first()
        return bool(row and row[0])
    except Exception as exc:                     # missing row/column / DB blip
        log.warning("cutover_read_s1 read failed for tenant %s: %s "
                    "(defaulting to meta_*)", tenant_id, exc)
        return False


class MetadataAccessor:
    """Flag-gated facade over the ``meta_*`` read-interface. Per read:
    ``cutover_read_s1 AND s1_reader ? S1 : meta_*``. Everything not switched
    (writes, version/freshness, any other repo method) delegates to the repo."""

    def __init__(self, tenant_id, metadata_repo, s1_reader=None):
        self._tenant_id = tenant_id
        self._repo = metadata_repo
        self._s1 = s1_reader
        # Resolve the route once: S1 only when a reader is present AND the flag
        # is on. Default (no reader, or flag off / unreadable) → meta_*.
        self._use_s1 = bool(s1_reader) and self._flag_on()

    def _flag_on(self) -> bool:
        return cutover_read_s1_enabled(self._repo.db, self._tenant_id)

    # -- the switched reads (S1 when routed, else meta_*) --------------------
    def get_objects(self, meta_version_id):
        return (self._s1.get_objects(meta_version_id) if self._use_s1
                else self._repo.get_objects(meta_version_id))

    def get_fields(self, meta_version_id, object_id=None):
        return (self._s1.get_fields(meta_version_id, object_id) if self._use_s1
                else self._repo.get_fields(meta_version_id, object_id))

    def get_validation_rules(self, meta_version_id, object_id=None):
        return (self._s1.get_validation_rules(meta_version_id, object_id)
                if self._use_s1
                else self._repo.get_validation_rules(meta_version_id, object_id))

    def get_object_by_api_name(self, meta_version_id, api_name):
        return (self._s1.get_object_by_api_name(meta_version_id, api_name)
                if self._use_s1
                else self._repo.get_object_by_api_name(meta_version_id, api_name))

    def get_version(self, version_id):
        # The accessor's content reads are meta_version_id-keyed; this version row
        # stays on meta_* (the S1 "version" is an env-keyed logical version_seq, a
        # different space). Preflight's env-keyed freshness/health — the GAP-2 of
        # D-158 — is now routed to S1 separately via
        # s1_sync_console.read_s1_freshness behind the same cutover_read_s1 flag
        # (D-183); MetadataService.check_drift's "drift since" anchor is routed the
        # same way (D-184). Both consume the env-scoped read_s1_freshness, not this
        # version row.
        return self._repo.get_version(version_id)

    # -- everything else: transparent delegation to the repo ----------------
    def __getattr__(self, name):
        # __getattr__ fires only for attributes not found normally (i.e. not the
        # switched reads above, not the _-prefixed instance attrs). Guard
        # private/dunder names to avoid recursion before _repo is set.
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._repo, name)
