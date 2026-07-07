"""The D-107 parser's VALUE dialect (req-302 robustness R3 — the D-304
deferred piece): arithmetic, IF, NULL parse under ``dialect="value"`` ONLY;
the default vr dialect stays byte-identical (a value construct still yields
NotParsed there)."""
from __future__ import annotations

from primeqa.semantic.formula import (
    Arithmetic,
    Comparison,
    FieldRef,
    If,
    Literal,
    NotParsed,
    is_parsed,
    parse,
    walk,
)

LTV = "IF(Property_Value__c > 0, Loan_Amount__c / Property_Value__c, null)"


def test_vr_dialect_still_rejects_value_constructs():
    assert isinstance(parse(LTV), NotParsed)
    assert isinstance(parse("Subtotal__c * 1.1"), NotParsed)
    assert isinstance(parse("A + B"), NotParsed)
    # NULL is a plain FieldRef in vr dialect (no new literal there)
    ast = parse("ISBLANK(null_field__c)")
    assert is_parsed(ast)


def test_value_dialect_parses_the_live_ltv_formula():
    ast = parse(LTV, dialect="value")
    assert isinstance(ast, If)
    assert isinstance(ast.cond, Comparison) and ast.cond.op == ">"
    assert isinstance(ast.then, Arithmetic) and ast.then.op == "/"
    assert ast.els == Literal(None, "null")


def test_value_dialect_arithmetic_and_precedence():
    ast = parse("A + B * C", dialect="value")
    assert isinstance(ast, Arithmetic) and ast.op == "+"
    assert isinstance(ast.right, Arithmetic) and ast.right.op == "*"
    tax = parse("Subtotal__c * 1.1", dialect="value")
    assert tax == Arithmetic("*", FieldRef(("Subtotal__c",)),
                             Literal(1.1, "number"))


def test_value_dialect_unary_minus():
    assert parse("-5", dialect="value") == Literal(-5, "number")
    neg = parse("-Amount__c", dialect="value")
    assert isinstance(neg, Arithmetic) and neg.op == "-" \
        and neg.left == Literal(0, "number")


def test_value_dialect_if_arity_is_strict():
    assert isinstance(parse("IF(A > 1, 2)", dialect="value"), NotParsed)
    assert isinstance(parse("IF(A > 1, 2, 3, 4)", dialect="value"), NotParsed)


def test_value_dialect_still_fails_loud_on_unknown():
    assert isinstance(parse("VLOOKUP(A, B, C)", dialect="value"), NotParsed)
    assert isinstance(parse("CASE(X, 1, 2, 3)", dialect="value"), NotParsed)


def test_walk_covers_the_new_nodes():
    ast = parse(LTV, dialect="value")
    names = {n.name for n in walk(ast) if isinstance(n, FieldRef)}
    assert names == {"Property_Value__c", "Loan_Amount__c"}
