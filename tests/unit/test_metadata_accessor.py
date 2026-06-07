"""Unit tests for MetadataAccessor (D-158, cutover Step 3 / slice 3.1).

The flag-gated read-switch facade: routes `cutover_read_s1 AND s1_reader ? S1 :
meta_*`. Mocked repo + s1_reader (no DB). Slice 3.1 ships `s1_reader=None`
(passthrough); the routing-with-a-reader cases prove the gate ahead of 3.2.
"""
from __future__ import annotations

from primeqa.metadata_bridge.accessor import MetadataAccessor


class _Row:
    def __init__(self, val):
        self._val = val

    def first(self):
        return self._val


class _FakeDb:
    """Stand-in for ``metadata_repo.db``: the flag SELECT returns ``(flag,)`` or
    ``None``, or raises when ``raise_on_read``."""

    def __init__(self, flag, raise_on_read=False):
        self._flag = flag
        self._raise = raise_on_read

    def execute(self, sql, params=None):
        if self._raise:
            raise RuntimeError("db blip")
        return _Row(None if self._flag is None else (self._flag,))


class _FakeRepo:
    """meta_* repo stand-in; each read returns a ``('meta', ...)`` sentinel so the
    test can assert which source answered."""

    def __init__(self, *, flag, raise_on_read=False):
        self.db = _FakeDb(flag, raise_on_read)

    def get_objects(self, mv):
        return ("meta", "objects", mv)

    def get_fields(self, mv, object_id=None):
        return ("meta", "fields", mv, object_id)

    def get_validation_rules(self, mv, object_id=None):
        return ("meta", "vrs", mv, object_id)

    def get_object_by_api_name(self, mv, api):
        return ("meta", "oba", mv, api)

    def get_version(self, vid):
        return ("meta", "version", vid)

    def store_objects(self, *a):                 # a non-switched (write) method
        return ("meta", "store_objects", a)


class _FakeS1:
    def get_objects(self, mv):
        return ("s1", "objects", mv)

    def get_fields(self, mv, object_id=None):
        return ("s1", "fields", mv, object_id)

    def get_validation_rules(self, mv, object_id=None):
        return ("s1", "vrs", mv, object_id)

    def get_object_by_api_name(self, mv, api):
        return ("s1", "oba", mv, api)


# --- passthrough (s1_reader=None — the 3.1 production shape) -----------------

def test_passthrough_no_reader_flag_off():
    acc = MetadataAccessor(1, _FakeRepo(flag=False), s1_reader=None)
    assert acc.get_objects(7) == ("meta", "objects", 7)
    assert acc.get_fields(7, 3) == ("meta", "fields", 7, 3)
    assert acc.get_validation_rules(7) == ("meta", "vrs", 7, None)


def test_passthrough_no_reader_flag_on():
    # 3.1: s1_reader=None → meta_* even with the flag ON (no S1 reader exists yet).
    acc = MetadataAccessor(1, _FakeRepo(flag=True), s1_reader=None)
    assert acc.get_objects(7) == ("meta", "objects", 7)
    assert acc.get_fields(7) == ("meta", "fields", 7, None)


# --- routing (the gate, proven ahead of 3.2) --------------------------------

def test_routes_to_s1_when_reader_and_flag_on():
    acc = MetadataAccessor(1, _FakeRepo(flag=True), s1_reader=_FakeS1())
    assert acc.get_objects(7) == ("s1", "objects", 7)
    assert acc.get_fields(7, 3) == ("s1", "fields", 7, 3)
    assert acc.get_validation_rules(7) == ("s1", "vrs", 7, None)
    assert acc.get_object_by_api_name(7, "Account") == ("s1", "oba", 7, "Account")


def test_flag_off_wins_even_with_reader():
    acc = MetadataAccessor(1, _FakeRepo(flag=False), s1_reader=_FakeS1())
    assert acc.get_objects(7) == ("meta", "objects", 7)


def test_get_version_always_meta():
    # version/freshness stays on meta_* (GAP-2) even when routed to S1.
    acc = MetadataAccessor(1, _FakeRepo(flag=True), s1_reader=_FakeS1())
    assert acc.get_version(9) == ("meta", "version", 9)


# --- tolerance + delegation -------------------------------------------------

def test_flag_read_tolerant_on_db_error():
    acc = MetadataAccessor(1, _FakeRepo(flag=True, raise_on_read=True),
                           s1_reader=_FakeS1())
    assert acc.get_objects(7) == ("meta", "objects", 7)   # error → meta_*


def test_flag_read_tolerant_on_absent_row():
    acc = MetadataAccessor(1, _FakeRepo(flag=None), s1_reader=_FakeS1())
    assert acc.get_objects(7) == ("meta", "objects", 7)   # no row → meta_*


def test_getattr_delegates_non_switched_methods():
    acc = MetadataAccessor(1, _FakeRepo(flag=True), s1_reader=_FakeS1())
    assert acc.store_objects("x") == ("meta", "store_objects", ("x",))
