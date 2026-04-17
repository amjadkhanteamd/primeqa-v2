"""Per-category metadata sync engine.

Ordered DAG: objects \u2192 {fields, record_types} \u2192 {validation_rules, flows, triggers}

Each category is an independently-committed job with its own row in
meta_sync_status. If a parent fails, dependents mark themselves
'skipped_parent_failed' (Q11). SSE events are emitted to
`primeqa.runs.streams.BUS` keyed by the meta_version_id (we reuse the same
in-process bus \u2014 subscribers use a dedicated "sync:<id>" key via helper
functions here).

Usage:
    engine = SyncEngine(db, metadata_repo, sf_client)
    engine.run(meta_version_id=42, categories={'objects', 'fields'})
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable, Dict, Iterable, List, Optional, Set

from primeqa.metadata.models import MetaSyncStatus
from primeqa.runs.streams import BUS

log = logging.getLogger(__name__)


# DAG ordering (Q11). Keys = category, values = set of direct parents.
DEPENDS_ON: Dict[str, Set[str]] = {
    "objects":          set(),
    "fields":           {"objects"},
    "record_types":     {"objects"},
    "validation_rules": {"objects", "fields"},
    "flows":            {"objects", "fields"},
    "triggers":         {"objects", "fields"},
}

ALL_CATEGORIES: List[str] = [
    "objects", "fields", "record_types", "validation_rules", "flows", "triggers",
]


def sync_bus_key(meta_version_id: int) -> int:
    """Reuse the EventBus; sync channels are at `-meta_version_id`."""
    # Negatives don't collide with run_ids (always positive).
    return -meta_version_id


def emit_sync_event(meta_version_id: int, event_type: str, **data):
    BUS.publish(sync_bus_key(meta_version_id),
                {"type": event_type, "data": data})


class SyncEngine:
    """Runs selected categories in topological order with independent commits."""

    def __init__(self, db, metadata_repo, sf_fetchers: Dict[str, Callable]):
        """
        `sf_fetchers` is a dict category -> callable(meta_version_id, metadata_repo)
        that performs the SF API fetch + DB write for that category and returns
        an int `items_count`. Kept as a strategy so tests can inject mocks.
        """
        self.db = db
        self.metadata_repo = metadata_repo
        self.sf_fetchers = sf_fetchers

    # ---- Public API -----------------------------------------------------

    def run(self, meta_version_id: int, categories: Iterable[str]) -> Dict[str, str]:
        requested: Set[str] = set(categories) & set(ALL_CATEGORIES)
        if not requested:
            return {}

        # Topologically expand requested set so we don't ask to sync 'fields'
        # without at least checking that 'objects' is complete in this run
        # (a prior run's healthy objects is fine, tracked below).
        order = self._topo_order(requested)

        # Ensure a status row exists for each in-scope category
        for cat in order:
            self._upsert_status(meta_version_id, cat, "pending")
        emit_sync_event(meta_version_id, "sync_started", categories=order)

        outcomes: Dict[str, str] = {}

        for cat in order:
            # Skip if dependents' parents failed within this run OR prior runs
            # left a parent in an unhealthy state.
            if not self._parents_healthy(meta_version_id, cat, outcomes):
                self._mark(meta_version_id, cat, "skipped_parent_failed",
                           error_message=f"Parent category failed; retry parent first.")
                outcomes[cat] = "skipped_parent_failed"
                emit_sync_event(meta_version_id, "category_finished",
                                category=cat, status="skipped_parent_failed")
                continue

            fetcher = self.sf_fetchers.get(cat)
            if not fetcher:
                self._mark(meta_version_id, cat, "skipped",
                           error_message=f"No fetcher registered for '{cat}'")
                outcomes[cat] = "skipped"
                emit_sync_event(meta_version_id, "category_finished",
                                category=cat, status="skipped")
                continue

            self._mark(meta_version_id, cat, "running", started_at=datetime.now(timezone.utc))
            emit_sync_event(meta_version_id, "category_started", category=cat)

            try:
                items_count = int(fetcher(meta_version_id, self.metadata_repo) or 0)
                self._mark(meta_version_id, cat, "complete",
                           items_count=items_count,
                           completed_at=datetime.now(timezone.utc))
                outcomes[cat] = "complete"
                emit_sync_event(meta_version_id, "category_finished",
                                category=cat, status="complete", items_count=items_count)
            except Exception as e:
                log.exception("sync %s failed", cat)
                self._mark(meta_version_id, cat, "failed",
                           error_message=str(e)[:500],
                           completed_at=datetime.now(timezone.utc),
                           retry_increment=True)
                outcomes[cat] = "failed"
                emit_sync_event(meta_version_id, "category_finished",
                                category=cat, status="failed",
                                error_message=str(e)[:200])

        # Compute overall meta_version.status
        mv_status = self._overall_status(meta_version_id)
        try:
            mv = self.metadata_repo.get_version(meta_version_id)
            if mv:
                mv.status = mv_status
                if mv_status in ("complete", "partial", "failed") and not mv.completed_at:
                    mv.completed_at = datetime.now(timezone.utc)
                self.db.commit()
        except Exception:
            self.db.rollback()

        emit_sync_event(meta_version_id, "sync_finished",
                        status=mv_status, outcomes=outcomes)
        return outcomes

    def get_status(self, meta_version_id: int) -> List[Dict[str, object]]:
        rows = self.db.query(MetaSyncStatus).filter(
            MetaSyncStatus.meta_version_id == meta_version_id,
        ).all()
        return [self._row_dict(r) for r in rows]

    # ---- Helpers --------------------------------------------------------

    def _topo_order(self, requested: Set[str]) -> List[str]:
        order = []
        for cat in ALL_CATEGORIES:
            if cat in requested or self._has_requested_descendant(cat, requested):
                order.append(cat)
        return order

    def _has_requested_descendant(self, cat, requested):
        # Walk down: if any category in requested depends (transitively) on cat,
        # include cat so we can verify parent health.
        for r in requested:
            if cat in self._transitive_parents(r):
                return True
        return False

    def _transitive_parents(self, cat):
        seen = set()
        stack = list(DEPENDS_ON.get(cat, set()))
        while stack:
            p = stack.pop()
            if p in seen:
                continue
            seen.add(p)
            stack.extend(DEPENDS_ON.get(p, set()))
        return seen

    def _parents_healthy(self, mvid: int, cat: str, outcomes: Dict[str, str]) -> bool:
        for p in DEPENDS_ON.get(cat, set()):
            # Within this run: check outcomes; across runs: check prior state
            out = outcomes.get(p)
            if out == "complete":
                continue
            if out in ("failed", "skipped", "skipped_parent_failed"):
                return False
            # Parent wasn't in this run \u2014 fall back to DB check
            row = self.db.query(MetaSyncStatus).filter(
                MetaSyncStatus.meta_version_id == mvid,
                MetaSyncStatus.category == p,
            ).first()
            if not row or row.status != "complete":
                return False
        return True

    def _upsert_status(self, mvid: int, cat: str, status: str):
        row = self.db.query(MetaSyncStatus).filter(
            MetaSyncStatus.meta_version_id == mvid,
            MetaSyncStatus.category == cat,
        ).first()
        if row:
            row.status = status
            row.updated_at = datetime.now(timezone.utc)
        else:
            row = MetaSyncStatus(meta_version_id=mvid, category=cat, status=status)
            self.db.add(row)
        self.db.commit()
        return row

    def _mark(self, mvid: int, cat: str, status: str, *,
              error_message: Optional[str] = None,
              items_count: Optional[int] = None,
              started_at=None, completed_at=None,
              retry_increment: bool = False):
        row = self.db.query(MetaSyncStatus).filter(
            MetaSyncStatus.meta_version_id == mvid,
            MetaSyncStatus.category == cat,
        ).first()
        if not row:
            row = MetaSyncStatus(meta_version_id=mvid, category=cat)
            self.db.add(row)
        row.status = status
        if error_message is not None:
            row.error_message = error_message
        if items_count is not None:
            row.items_count = items_count
        if started_at is not None:
            row.started_at = started_at
        if completed_at is not None:
            row.completed_at = completed_at
        if retry_increment:
            row.retry_count = (row.retry_count or 0) + 1
        row.updated_at = datetime.now(timezone.utc)
        self.db.commit()

    def _overall_status(self, mvid: int) -> str:
        rows = self.db.query(MetaSyncStatus).filter(
            MetaSyncStatus.meta_version_id == mvid,
        ).all()
        if not rows:
            return "in_progress"
        statuses = {r.status for r in rows}
        if statuses == {"complete"}:
            return "complete"
        if "running" in statuses or "pending" in statuses:
            return "in_progress"
        if "failed" in statuses and "complete" not in statuses:
            return "failed"
        # Mixed success/skip = partial
        return "partial"

    @staticmethod
    def _row_dict(r: MetaSyncStatus) -> Dict[str, object]:
        return {
            "id": r.id, "meta_version_id": r.meta_version_id,
            "category": r.category, "status": r.status,
            "items_count": r.items_count, "retry_count": r.retry_count,
            "error_message": r.error_message,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
