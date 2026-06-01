"""Cross-step reference resolution for data recipes (D-115 side B).

The read-resolution convention. A positive data recipe's ``ReadStep`` reads back
the record the ``CreateStep`` just made — but the create's record Id is only
known *at run time*. Side A authors the dependency as a textual reference in the
SOQL::

    SELECT Status__c FROM Account WHERE Id = '$create-record.id'

``resolve_step_refs`` is where side B *defines* that reference: after the create
succeeds the executor records ``state["create-record"] = {"id": "<sf-id>"}``, and
this function substitutes every ``$<step_id>.<attr>`` token in the SOQL with the
literal value from ``state`` before the query is issued. The substitution is
**literal-for-token** — the authored surrounding quotes (``'$create-record.id'``)
are preserved, so a resolved Id lands as ``'006...'`` — and **fail-loud**: an
unresolved reference raises :class:`StepRefResolutionError` (a recipe defect,
never a silently ungrounded read).

Substrate-owned. v1's ``primeqa/execution/executor.py:_resolve_soql_refs`` is the
conceptual precedent (the same ``$var`` idea), but this is a fresh, narrow
implementation — v1 is **not** imported.
"""
from __future__ import annotations

import re
from typing import Any

from primeqa.execution_engine.errors import StepRefResolutionError

# ``$<step_id>.<attr>`` — step_ids may contain hyphens ("create-record"); attrs
# are simple identifiers ("id"). The token does not include surrounding quotes,
# so any quoting the recipe author placed around it is preserved verbatim.
_REF = re.compile(r"\$([\w-]+)\.(\w+)")


def resolve_step_refs(soql: str, state: dict[str, dict[str, Any]]) -> str:
    """Substitute every ``$<step_id>.<attr>`` in ``soql`` with ``state[step_id]
    [attr]`` (stringified), preserving surrounding text. Raises
    :class:`StepRefResolutionError` on the first reference that does not resolve.

    ``state`` maps a prior step_id to its captured outputs, e.g.
    ``{"create-record": {"id": "006..."}}``.
    """

    def _sub(m: re.Match) -> str:
        step_id, attr = m.group(1), m.group(2)
        bag = state.get(step_id)
        if not isinstance(bag, dict) or attr not in bag or bag[attr] is None:
            available = (
                f"step {step_id!r} has {sorted(bag)}" if isinstance(bag, dict)
                else f"no step {step_id!r} in state (have {sorted(state)})"
            )
            raise StepRefResolutionError(
                f"unresolved reference ${step_id}.{attr} in SOQL: {available}")
        return str(bag[attr])

    return _REF.sub(_sub, soql)
