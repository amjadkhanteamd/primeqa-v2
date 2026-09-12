"""One tenant session per request, for READ paths (close 2).

A page render used to open a tenant connection per console: the release
decision tab opened **eleven** for one GET, and the same claims were read three
times over because each console assembled them from scratch. This module gives
a view one session it can hand to every console through the ``session=`` seams
they already carry.

Two deliberate limits:

* **Reads only.** ``get_tenant_connection`` commits on clean exit, so sharing
  one connection across a request that also WRITES would batch those writes
  into a single transaction and change when they land. Write paths keep opening
  their own connection, exactly as before. This helper is for GET renders.
* **Explicit, not ambient.** The view opens the scope and passes the session
  down. Nothing reaches for a global; a console called without a session
  behaves exactly as it always did.

The memo is the query saving: within one scope, a read that is deterministic
for its inputs is performed once. It is keyed by the session, so it cannot
outlive the transaction it was read in.
"""
from __future__ import annotations

import logging
from contextlib import ExitStack, contextmanager

log = logging.getLogger(__name__)

_MEMO = "_plimsol_read_memo"


@contextmanager
def read_scope(tenant_id: int):
    """Yield ONE tenant session for a read-only render, or ``None`` when the
    substrate cannot be reached — callers then fall back to their own
    best-effort behaviour, which is what they did before this existed."""
    from sqlalchemy.orm import Session

    from primeqa.semantic.connection import get_tenant_connection
    stack = ExitStack()
    try:
        conn = stack.enter_context(get_tenant_connection(tenant_id))
        session = Session(bind=conn)
        setattr(session, _MEMO, {})
        stack.callback(session.close)
    except Exception as exc:  # noqa: BLE001 — a render must not die on the scope
        stack.close()
        log.warning("read scope unavailable for tenant %s: %s", tenant_id, exc)
        yield None
        return
    # Only the ACQUISITION is best-effort. An exception raised by the render
    # itself belongs to the render: it closes the scope and propagates, exactly
    # as the per-console connections did before this existed.
    try:
        yield session
    except BaseException as exc:
        if not stack.__exit__(type(exc), exc, exc.__traceback__):
            raise
    else:
        stack.close()


def in_scope(session) -> bool:
    """True when ``session`` is a read scope opened by :func:`read_scope`, so a
    console may take the consolidated path. A console handed a plain session —
    every existing caller — answers False and behaves exactly as before."""
    return isinstance(getattr(session, _MEMO, None), dict)


def conn_of(session):
    """The raw Connection behind a scope, or ``None``. Several consoles read
    with ``text()`` on a Connection rather than through a Session; this lets
    them join the render's session without changing how they query."""
    return None if session is None else session.connection()


def memo(session, key, produce):
    """Run ``produce()`` once per (session, key). Outside a scope — or on a
    session that carries no memo — it simply calls through, so behaviour is
    identical either way."""
    if session is None:
        return produce()
    cache = getattr(session, _MEMO, None)
    # A real dict, not merely an attribute: a test double answers every getattr,
    # and a memo that believed one would hand back the double instead of a read.
    if not isinstance(cache, dict):
        return produce()
    if key not in cache:
        cache[key] = produce()
    return cache[key]
