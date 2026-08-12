"""The S4 materialisation boundary for temporal values (VR06 arc).

ONE owner for the run's temporal reference and ONE explicit boundary where
symbolic values become transport values: the client wrapper materialises
``field_values`` / ``field_changes`` immediately before Salesforce, so every
payload path (positive chain, negative setup, entry updates, the mutation,
world-provisioned parent creates) crosses the same boundary by construction.
An unrecognized symbolic value raises BEFORE the org is touched — unresolved
values never cross the execution boundary.

``RUN_DATE`` (:func:`capture_temporal_reference`): the current date in the
org-default timezone (``Organization.TimeZoneSidKey`` — the closest cheap
proxy for the date semantics a validation rule's ``TODAY()`` evaluates under;
an integration user normally inherits it). Any capture failure falls back to
UTC, with ``source`` recording which path was taken — the reference is always
explicit, never ambient.

Known bounded residual (documented, accepted): a run that straddles the org's
midnight between a payload's materialisation and the org's save re-evaluates
``TODAY()`` one day later — a sub-minute exposure window per run.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from primeqa.test_representation.temporal import (
    TemporalReference, is_symbolic, materialise,
)


def capture_temporal_reference(client) -> TemporalReference:
    """Capture the run's single temporal reference (once, at run entry)."""
    captured = datetime.now(timezone.utc)
    try:
        rows = client.query("SELECT TimeZoneSidKey FROM Organization")
        tz_key = rows[0]["TimeZoneSidKey"]
        ref_date = datetime.now(ZoneInfo(tz_key)).date()
        return TemporalReference(
            reference_date=ref_date, reference_timezone=tz_key,
            captured_at=captured.isoformat(), source="organization_timezone")
    except Exception:
        return TemporalReference(
            reference_date=captured.date(), reference_timezone="UTC",
            captured_at=captured.isoformat(), source="utc_fallback")


class TemporalBoundaryClient:
    """The materialisation boundary: wraps the run's Salesforce client and
    resolves symbolic temporal values in create/update payloads immediately
    before transport. Everything else delegates untouched. Raises
    ``ValueError`` on an unrecognized symbolic value (refuse-before-Salesforce).

    The reference is captured LAZILY, on the first payload that actually
    carries a symbolic value — a run with none never pays the org query and is
    byte-identical to the unwrapped client (also keeps stub/test clients with
    scripted query responses undisturbed). Once captured, ONE reference serves
    the whole run (all payloads anchor to the same date)."""

    def __init__(self, inner, reference: Optional[TemporalReference] = None):
        self._inner = inner
        self._reference = reference
        # D-449: the transport payload of the MOST RECENT create/update,
        # recorded ONLY when materialisation actually changed it (a symbolic
        # value was present); None otherwise. Read immediately after each
        # call by the evidence assembly (`_realized`) — calls are strictly
        # sequential per run, so most-recent is unambiguous.
        self.last_realized: Optional[dict] = None
        # D-449: True once ANY symbolic value was materialised this run —
        # the envelope-stamp signal (a pre-supplied reference alone must not
        # imply temporal activity).
        self.materialised_any: bool = False

    @property
    def reference(self) -> Optional[TemporalReference]:
        return self._reference

    def materialise_value(self, value):
        """Materialise ONE symbolic value (C4: the assertion side — a claim
        whose EXPECTED value is a RelativeDate needs the same run-anchored
        date the payload boundary uses). Captures the run reference lazily,
        exactly like the payload path; non-symbolic values pass through
        untouched."""
        if not is_symbolic(value):
            return value
        if self._reference is None:
            self._reference = capture_temporal_reference(self._inner)
        self.materialised_any = True
        return materialise(value, self._reference.reference_date)

    def _mat(self, payload):
        if not payload or not any(is_symbolic(v) for v in payload.values()):
            return payload
        if self._reference is None:
            self._reference = capture_temporal_reference(self._inner)
        self.materialised_any = True
        ref = self._reference.reference_date
        return {k: materialise(v, ref) for k, v in payload.items()}

    def create(self, sobject, field_values):
        mat = self._mat(field_values)
        # D-449: `_mat` returns the payload IDENTITY-unchanged when nothing
        # was symbolic — the recording is None exactly then.
        self.last_realized = dict(mat) if mat is not field_values else None
        return self._inner.create(sobject, mat)

    def update(self, sobject, record_id, field_changes):
        mat = self._mat(field_changes)
        self.last_realized = dict(mat) if mat is not field_changes else None
        return self._inner.update(sobject, record_id, mat)

    def __getattr__(self, name):
        return getattr(self._inner, name)
