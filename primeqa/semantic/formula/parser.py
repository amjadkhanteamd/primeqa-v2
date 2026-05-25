"""Recursive-descent parser for Salesforce validation-rule formulas
(D-107, slice 1). Pure, dependency-free.

Broad recognition (Fork X): the covered boolean/comparison grammar plus the
change-context functions (PRIORVALUE / ISCHANGED / ISNEW) and dotted field refs.
Fail-loud: any genuinely unrecognized construct — unknown function, REGEX,
VLOOKUP, date math, CASE, malformed syntax — returns :class:`NotParsed` rather
than a partial or guessed AST.

Operator precedence (lowest -> highest): ``||`` < ``&&`` < unary ``!`` <
comparison < primary. AND/OR/NOT also have function forms, normalized to the
same :class:`And` / :class:`Or` / :class:`Not` nodes.
"""
from __future__ import annotations

from typing import Union

from .nodes import (
    And, Comparison, FieldRef, FunctionCall, Literal, Node, Not, NotParsed, Or,
)

# Recognized non-structural functions (AND/OR/NOT/TRUE/FALSE are structural,
# handled directly). Anything outside this set -> NotParsed.
_RECOGNIZED_FUNCS = frozenset(
    {"ISBLANK", "ISNULL", "ISPICKVAL", "PRIORVALUE", "ISCHANGED", "ISNEW"}
)
_COMPARE_OPS = frozenset({"=", "==", "<>", "!=", "<", ">", "<=", ">="})
_COMPARE_NORMALIZE = {"==": "=", "!=": "<>"}


class _ParseError(Exception):
    """Internal — caught at the public boundary and turned into NotParsed."""


# --------------------------------------------------------------------------
# Tokenizer
# --------------------------------------------------------------------------

def _tokenize(s: str) -> list[tuple[str, object]]:
    """-> list of (kind, value); kinds: STRING, NUMBER, IDENT, OP, EOF."""
    toks: list[tuple[str, object]] = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c.isspace():
            i += 1
            continue
        if c == '"':
            j, buf = i + 1, []
            while j < n and s[j] != '"':
                if s[j] == "\\" and j + 1 < n:        # \" and \\ escapes
                    buf.append(s[j + 1])
                    j += 2
                    continue
                buf.append(s[j])
                j += 1
            if j >= n:
                raise _ParseError("unterminated string literal")
            toks.append(("STRING", "".join(buf)))
            i = j + 1
            continue
        if c.isdigit() or (c == "." and i + 1 < n and s[i + 1].isdigit()):
            j, dot = i, False
            while j < n and (s[j].isdigit() or (s[j] == "." and not dot)):
                dot = dot or s[j] == "."
                j += 1
            num = s[i:j]
            toks.append(("NUMBER", float(num) if "." in num else int(num)))
            i = j
            continue
        if c.isalpha() or c == "_":
            j = i
            while j < n and (s[j].isalnum() or s[j] == "_"):
                j += 1
            toks.append(("IDENT", s[i:j]))
            i = j
            continue
        two = s[i:i + 2]
        if two in ("&&", "||", "<>", "<=", ">=", "!=", "=="):
            toks.append(("OP", two))
            i += 2
            continue
        if c in "=<>!(),.":
            toks.append(("OP", c))
            i += 1
            continue
        raise _ParseError(f"unexpected character {c!r}")
    toks.append(("EOF", None))
    return toks


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------

class _Parser:
    def __init__(self, toks: list[tuple[str, object]]):
        self.toks = toks
        self.i = 0

    def _peek(self) -> tuple[str, object]:
        return self.toks[self.i]

    def _next(self) -> tuple[str, object]:
        t = self.toks[self.i]
        self.i += 1
        return t

    def _at_op(self, *vals: str) -> bool:
        k, v = self._peek()
        return k == "OP" and v in vals

    def _expect_op(self, val: str) -> None:
        k, v = self._next()
        if not (k == "OP" and v == val):
            raise _ParseError(f"expected {val!r}, got {v!r}")

    def expect_eof(self) -> None:
        if self._peek()[0] != "EOF":
            raise _ParseError(f"trailing tokens at {self._peek()[1]!r}")

    # -- grammar (precedence climbing) --
    def parse_expression(self) -> Node:
        return self._parse_or()

    def _parse_or(self) -> Node:
        left = self._parse_and()
        if self._at_op("||"):
            ops = [left]
            while self._at_op("||"):
                self._next()
                ops.append(self._parse_and())
            return Or(tuple(ops))
        return left

    def _parse_and(self) -> Node:
        left = self._parse_not()
        if self._at_op("&&"):
            ops = [left]
            while self._at_op("&&"):
                self._next()
                ops.append(self._parse_not())
            return And(tuple(ops))
        return left

    def _parse_not(self) -> Node:
        if self._at_op("!"):
            self._next()
            return Not(self._parse_not())
        return self._parse_comparison()

    def _parse_comparison(self) -> Node:
        left = self._parse_primary()
        k, v = self._peek()
        if k == "OP" and v in _COMPARE_OPS:
            self._next()
            right = self._parse_primary()
            return Comparison(_COMPARE_NORMALIZE.get(v, v), left, right)
        return left

    def _parse_primary(self) -> Node:
        k, v = self._peek()
        if k == "OP" and v == "(":
            self._next()
            e = self.parse_expression()
            self._expect_op(")")
            return e
        if k == "STRING":
            self._next()
            return Literal(v, "string")
        if k == "NUMBER":
            self._next()
            return Literal(v, "number")
        if k == "IDENT":
            self._next()
            if self._at_op("("):
                return self._function(str(v))
            up = str(v).upper()
            if up in ("TRUE", "FALSE"):
                return Literal(up == "TRUE", "boolean")
            path = [str(v)]                              # field ref (bare/dotted)
            while self._at_op("."):
                self._next()
                k2, v2 = self._next()
                if k2 != "IDENT":
                    raise _ParseError("expected field name after '.'")
                path.append(str(v2))
            return FieldRef(tuple(path))
        raise _ParseError(f"unexpected token {v!r}")

    def _function(self, name: str) -> Node:
        self._expect_op("(")
        args: list[Node] = []
        if not self._at_op(")"):
            args.append(self.parse_expression())
            while self._at_op(","):
                self._next()
                args.append(self.parse_expression())
        self._expect_op(")")
        up = name.upper()
        if up == "AND":
            if len(args) < 2:
                raise _ParseError("AND() requires >= 2 arguments")
            return And(tuple(args))
        if up == "OR":
            if len(args) < 2:
                raise _ParseError("OR() requires >= 2 arguments")
            return Or(tuple(args))
        if up == "NOT":
            if len(args) != 1:
                raise _ParseError("NOT() requires exactly 1 argument")
            return Not(args[0])
        if up in ("TRUE", "FALSE"):
            if args:
                raise _ParseError(f"{up}() takes no arguments")
            return Literal(up == "TRUE", "boolean")
        if up in _RECOGNIZED_FUNCS:
            return FunctionCall(up, tuple(args))
        raise _ParseError(f"unknown function {name!r}")


def parse(formula: str) -> Union[Node, NotParsed]:
    """Parse a validation-rule formula into a typed AST, or return
    :class:`NotParsed` (fail-loud) for anything v1 does not fully recognize.
    Never returns a partial AST."""
    if not formula or not formula.strip():
        return NotParsed("empty formula")
    try:
        p = _Parser(_tokenize(formula))
        node = p.parse_expression()
        p.expect_eof()
        return node
    except _ParseError as e:
        return NotParsed(str(e))


def is_parsed(result: Union[Node, NotParsed]) -> bool:
    """True iff ``result`` is a parsed AST (not the NotParsed sentinel)."""
    return not isinstance(result, NotParsed)
