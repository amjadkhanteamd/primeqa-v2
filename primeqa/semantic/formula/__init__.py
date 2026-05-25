"""Salesforce validation-rule formula parser (D-107, slice 1).

Pure, dependency-free recursive-descent parser → typed AST. The AST is the
shared input for two consumers (Fork 1A): S1 REFERENCES-edge extraction
(slice 2, closes corrections-log §17) and S3 verified-negative derivation
(slice 3). Both re-parse ``formula_text`` at use; nothing is persisted.

    from primeqa.semantic.formula import parse, is_parsed, NotParsed
    result = parse(formula_text)
    if is_parsed(result):
        ...  # walk the AST
    else:
        ...  # result.reason — fall back (caveated negative, D-101)
"""
from __future__ import annotations

from .nodes import (
    And,
    Comparison,
    FieldRef,
    FunctionCall,
    Literal,
    Node,
    Not,
    NotParsed,
    Or,
    walk,
)
from .parser import is_parsed, parse

__all__ = [
    "parse",
    "is_parsed",
    "walk",
    "NotParsed",
    "Node",
    "Literal",
    "FieldRef",
    "FunctionCall",
    "Comparison",
    "And",
    "Or",
    "Not",
]
