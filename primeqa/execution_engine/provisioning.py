"""S4 created-record tracking + reverse-order cleanup (F6.1, D-196).

The cleanup spine for the provisioning vertical. As the executor creates records
during a positive run — the recipe's target, and (from F6.2) the required
master-detail/lookup **parents** it constructs — it records each on a
:class:`CreatedRecordTracker`. At teardown the tracker deletes them
**reverse-order** (children before parents) so a multi-record run leaves the org
as found. This generalises ``data_executor._run_positive``'s prior inline single
delete to N records.

Decoupled from ``data_executor`` by design: ``teardown`` takes the delete
function (``delete_fn(client, sobject, record_id) -> CleanupRecord``) by
injection, so this module imports nothing from the executor (no import cycle) and
is trivially unit-testable with a stub delete.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CreatedRecord:
    """One record S4 created in a run — the unit of the cleanup audit."""
    sobject: str
    record_id: str
    created_seq: int        # in-run creation order; teardown walks this reversed


class CreatedRecordTracker:
    """Accumulates the records S4 creates during one run, in create order, and
    tears them down reverse-order. Best-effort — the injected ``delete_fn`` is
    expected to record (not raise) a failed delete, so the run outcome is never
    changed by cleanup (the N-5 discipline)."""

    def __init__(self):
        self._records: list[CreatedRecord] = []

    def record(self, sobject: str, record_id: str) -> CreatedRecord:
        """Track one created record (assigns the next in-run sequence)."""
        rec = CreatedRecord(sobject, record_id, len(self._records))
        self._records.append(rec)
        return rec

    @property
    def records(self) -> tuple:
        """The tracked records, in creation order."""
        return tuple(self._records)

    def teardown(self, client, delete_fn) -> tuple:
        """Delete every tracked record **reverse-order** (children before
        parents), via ``delete_fn(client, sobject, record_id) -> CleanupRecord``.
        Returns the per-record cleanup results in teardown order. Never raises
        (delegated to ``delete_fn``'s best-effort contract)."""
        return tuple(
            delete_fn(client, rec.sobject, rec.record_id)
            for rec in reversed(self._records)
        )
