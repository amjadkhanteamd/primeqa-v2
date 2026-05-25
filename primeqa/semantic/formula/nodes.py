"""Typed AST for the Salesforce validation-rule formula parser (D-107, slice 1).

Frozen dataclasses — hashable, comparable, the shared shape consumed by S1
REFERENCES-edge extraction (slice 2) and S3 verified-negative derivation
(slice 3). Recognition is broad on purpose (D-107.1 / Fork X): the parser
recognizes valid VR formula structure, including the change-context functions
(PRIORVALUE / ISCHANGED / ISNEW) and dotted field refs — whether a parsed
formula yields a *verified* negative is the slice-3 derivation bar, not the
parser's concern.

:class:`NotParsed` is the fail-loud sentinel: a formula containing any construct
v1 does not recognize yields ``NotParsed(reason)``, never a partial / guessed AST.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator, Union


@dataclass(frozen=True)
class Literal:
    value: Any
    kind: str                       # "string" | "number" | "boolean"


@dataclass(frozen=True)
class FieldRef:
    """A field reference as path segments: ``("Amount",)`` bare,
    ``("Account", "Industry")`` dotted. Field-name / object disambiguation
    (§17) is the consumer's job; the parser records the path verbatim."""

    path: tuple[str, ...]

    @property
    def is_dotted(self) -> bool:
        return len(self.path) > 1

    @property
    def name(self) -> str:
        return ".".join(self.path)


@dataclass(frozen=True)
class FunctionCall:
    """A recognized function: ISBLANK / ISNULL / ISPICKVAL (boolean) or
    PRIORVALUE / ISCHANGED / ISNEW (change-context). ``name`` is upper-cased;
    only the recognized set reaches here — anything else is NotParsed."""

    name: str
    args: tuple["Node", ...]


@dataclass(frozen=True)
class Comparison:
    op: str                         # "=", "<>", "<", ">", "<=", ">="
    left: "Node"
    right: "Node"


@dataclass(frozen=True)
class And:
    operands: tuple["Node", ...]    # >= 2; `a && b` and `AND(a, b)` both land here


@dataclass(frozen=True)
class Or:
    operands: tuple["Node", ...]


@dataclass(frozen=True)
class Not:
    operand: "Node"


@dataclass(frozen=True)
class NotParsed:
    """Fail-loud sentinel — the formula contains a construct v1 does not fully
    recognize. Carries a human reason; never a partial AST."""

    reason: str


Node = Union[Literal, FieldRef, FunctionCall, Comparison, And, Or, Not]


def walk(node: Node) -> Iterator[Node]:
    """Yield ``node`` and all descendant expression nodes (pre-order). Generic
    traversal for both consumers — REFERENCES field extraction (slice 2) and
    predicate walking (slice 3)."""
    yield node
    if isinstance(node, (And, Or)):
        for op in node.operands:
            yield from walk(op)
    elif isinstance(node, Not):
        yield from walk(node.operand)
    elif isinstance(node, Comparison):
        yield from walk(node.left)
        yield from walk(node.right)
    elif isinstance(node, FunctionCall):
        for a in node.args:
            yield from walk(a)
    # Literal, FieldRef are leaves
