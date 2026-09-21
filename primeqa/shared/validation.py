"""Boundary validation for create/update services (AUD-011, triage round 2,
batch 2) — the ``create_section`` shape (audit fix C-3, 2026-04-19), lifted.

The class the audit named: "the database is the only validator". A
20,000-character name, a requirement_id of "x", a target_date of
"not-a-date", a NUL byte, an integer where text goes — each an unhandled
DataError from Postgres and the themed 500 page, across seven create/update
routes (and, the boundary gate found, eight more of the same class).

THE RULE: a create/update service checks every incoming value against the
COLUMN it lands in BEFORE any row is built. A string is a string, trimmed,
bounded by the column's declared length, free of NUL; an integer is an
integer in the column's range; a boolean is a boolean; a date is an ISO
date; JSON is a dict or a list. The refusal is a ``ValueError`` naming the
field — the exception every create/update route already maps to 400
VALIDATION_ERROR — so no route changes shape and nothing oversize or
mistyped reaches the database.

``columns(model, values)`` reads the bounds from the SQLAlchemy model itself
(``String(n)`` → n characters, ``Integer`` → a 32-bit integer, ``Boolean``,
``Date``, ``DateTime``, ``Text``, ``JSON``, ``Float``), so a column added
tomorrow is bounded the day it appears: there is no second table of bounds
to drift from the schema. Keys that are not columns pass through untouched
(the repositories ignore them); a non-nullable text column that arrives
empty is "required".
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable, Mapping, Optional

from sqlalchemy import (BigInteger, Boolean, Date, DateTime, Float, Integer, JSON, Numeric, SmallInteger, String,
                        Text)

INT32 = (-2 ** 31, 2 ** 31 - 1)
INT64 = (-2 ** 63, 2 ** 63 - 1)
TRUE = {"true", "1", "yes", "on", "t", "y"}
FALSE = {"false", "0", "no", "off", "f", "n"}
# columns a caller never sets through a body
NEVER_FROM_BODY = ("id", "tenant_id", "created_by", "created_at", "updated_at", "deleted_at", "deleted_by", "version")


def _empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def text_value(value: Any, field: str, *, max_chars: Optional[int] = None, required: bool = False) -> Optional[str]:
    """A trimmed string bounded by ``max_chars`` (the column's length), NUL-free;
    ``None`` for an empty optional; ``ValueError`` naming the field otherwise."""
    if _empty(value):
        if required:
            raise ValueError(f"{field} is required")
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    value = value.strip()
    if "\x00" in value:
        raise ValueError(f"{field} must not contain NUL characters")
    if max_chars is not None and len(value) > max_chars:
        raise ValueError(f"{field} too long (max {max_chars} characters)")
    return value


def int_value(value: Any, field: str, *, required: bool = False, positive: bool = False,
              bounds: tuple = INT32) -> Optional[int]:
    """An integer (an int, or a string of digits from a form) inside ``bounds``;
    ``positive`` demands > 0 (an id). Booleans and floats are not integers."""
    if _empty(value):
        if required:
            raise ValueError(f"{field} is required")
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    if isinstance(value, int):
        n = value
    elif isinstance(value, str) and value.strip().lstrip("-").isdigit():
        n = int(value.strip())
    else:
        raise ValueError(f"{field} must be an integer")
    if n < bounds[0] or n > bounds[1] or (positive and n <= 0):
        raise ValueError(f"{field} must be a positive integer" if positive else f"{field} is out of range")
    return n


def bool_value(value: Any, field: str, *, required: bool = False) -> Optional[bool]:
    if _empty(value):
        if required:
            raise ValueError(f"{field} is required")
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in TRUE | FALSE:
        return value.strip().lower() in TRUE
    raise ValueError(f"{field} must be true or false")


def date_value(value: Any, field: str, *, required: bool = False) -> Optional[date]:
    if _empty(value):
        if required:
            raise ValueError(f"{field} is required")
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            pass
    raise ValueError(f"{field} must be a date (YYYY-MM-DD)")


def datetime_value(value: Any, field: str, *, required: bool = False) -> Optional[datetime]:
    if _empty(value):
        if required:
            raise ValueError(f"{field} is required")
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            pass
    raise ValueError(f"{field} must be a date-time (ISO 8601)")


def float_value(value: Any, field: str, *, required: bool = False) -> Optional[float]:
    if _empty(value):
        if required:
            raise ValueError(f"{field} is required")
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a number")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            pass
    raise ValueError(f"{field} must be a number")


def json_value(value: Any, field: str, *, required: bool = False) -> Any:
    if value is None or value == "":
        if required:
            raise ValueError(f"{field} is required")
        return None
    if isinstance(value, (dict, list)):
        return value
    raise ValueError(f"{field} must be an object or a list")


def columns(model, values: Mapping[str, Any], *, required: Iterable[str] = (),
            skip: Iterable[str] = NEVER_FROM_BODY) -> dict:
    """Every key of ``values`` that names a column of ``model`` is checked
    against that column's type and length; the cleaned copy is returned.
    ``required`` names fields that must be present and non-empty; a
    non-nullable text column that arrives empty is refused as required too.
    Keys that are not columns pass through untouched."""
    if not isinstance(values, Mapping):
        raise ValueError("the body must be an object")
    cols = {c.name: c for c in model.__table__.columns}
    out = {}
    for field in required:
        if _empty(values.get(field)):
            raise ValueError(f"{field} is required")
    for field, value in values.items():
        col = cols.get(field)
        if col is None or field in skip:
            out[field] = value
            continue
        t = col.type
        must = (field in required) or (not col.nullable and isinstance(t, (String, Text)))
        if isinstance(t, (String, Text)):
            out[field] = text_value(value, field, max_chars=getattr(t, "length", None), required=must)
        elif isinstance(t, Boolean):
            out[field] = bool_value(value, field, required=field in required)
        elif isinstance(t, (SmallInteger, Integer, BigInteger)):
            bounds = INT64 if isinstance(t, BigInteger) else INT32
            out[field] = int_value(value, field, required=field in required, positive=field.endswith("_id"), bounds=bounds)
        elif isinstance(t, DateTime):
            out[field] = datetime_value(value, field, required=field in required)
        elif isinstance(t, Date):
            out[field] = date_value(value, field, required=field in required)
        elif isinstance(t, (Float, Numeric)):
            out[field] = float_value(value, field, required=field in required)
        elif isinstance(t, JSON):
            out[field] = json_value(value, field, required=field in required)
        else:
            out[field] = value
    return out
