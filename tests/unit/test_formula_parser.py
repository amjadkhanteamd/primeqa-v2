"""Unit tests for the Salesforce validation-rule formula parser (D-107, slice 1).

Pure, no I/O. Three concerns: grammar coverage (each recognized construct ->
correct node), the fail-loud boundary (genuinely unrecognized -> NotParsed, no
guessing), and the both-consumer shape — slice 2 must extract
(field, reference_type) pairs incl. priorvalue/ischanged/isnew + dotted; slice 3
must walk the predicate structure.
"""
from __future__ import annotations

from primeqa.semantic.formula import (
    And, Comparison, FieldRef, FunctionCall, Literal, Not, NotParsed, Or,
    is_parsed, parse, walk,
)


# ---------------------------------------------------------------------------
# Grammar coverage
# ---------------------------------------------------------------------------

def test_comparison_field_vs_literal():
    n = parse("Amount < 0")
    assert isinstance(n, Comparison) and n.op == "<"
    assert n.left == FieldRef(("Amount",)) and n.right == Literal(0, "number")


def test_comparison_operator_normalization():
    assert parse("X == 1").op == "="          # == -> =
    assert parse("X != 1").op == "<>"          # != -> <>
    assert parse("X <> 1").op == "<>"
    for op in ("<=", ">=", ">", "<", "="):
        assert parse(f"X {op} 1").op == op


def test_string_and_boolean_literals():
    assert parse('Name = "Acme"').right == Literal("Acme", "string")
    assert parse("Flag__c = TRUE").right == Literal(True, "boolean")
    assert parse("Flag__c = FALSE()").right == Literal(False, "boolean")


def test_isblank_isnull_ispickval():
    assert parse("ISBLANK(Reason__c)") == FunctionCall("ISBLANK", (FieldRef(("Reason__c",)),))
    assert parse("ISNULL(Reason__c)") == FunctionCall("ISNULL", (FieldRef(("Reason__c",)),))
    n = parse('ISPICKVAL(StageName, "Closed Won")')
    assert n == FunctionCall("ISPICKVAL", (FieldRef(("StageName",)), Literal("Closed Won", "string")))


def test_boolean_operator_and_function_forms_normalize():
    op_form = parse("ISBLANK(A__c) && ISBLANK(B__c)")
    fn_form = parse("AND(ISBLANK(A__c), ISBLANK(B__c))")
    assert isinstance(op_form, And) and isinstance(fn_form, And)
    assert op_form == fn_form                  # both normalize to the same node
    assert isinstance(parse("A__c || B__c"), Or)
    assert isinstance(parse("OR(A__c, B__c)"), Or)
    assert isinstance(parse("!ISBLANK(A__c)"), Not)
    assert isinstance(parse("NOT(ISBLANK(A__c))"), Not)


def test_precedence_and_binds_tighter_than_or():
    n = parse("a || b && c")
    assert isinstance(n, Or) and len(n.operands) == 2
    assert n.operands[0] == FieldRef(("a",)) and isinstance(n.operands[1], And)


def test_change_context_functions_parse():
    assert parse("PRIORVALUE(Amount)") == FunctionCall("PRIORVALUE", (FieldRef(("Amount",)),))
    assert parse("ISCHANGED(OwnerId)") == FunctionCall("ISCHANGED", (FieldRef(("OwnerId",)),))
    assert parse("ISNEW()") == FunctionCall("ISNEW", ())
    # used in a comparison — change-fn on one side
    n = parse("Amount <> PRIORVALUE(Amount)")
    assert isinstance(n, Comparison) and n.op == "<>"
    assert isinstance(n.right, FunctionCall) and n.right.name == "PRIORVALUE"


def test_dotted_field_refs_parse():
    n = parse('Account.Industry = "Tech"')
    assert n.left == FieldRef(("Account", "Industry"))
    assert n.left.is_dotted and n.left.name == "Account.Industry"


# ---------------------------------------------------------------------------
# Fail-loud boundary — unrecognized -> NotParsed, never a guess
# ---------------------------------------------------------------------------

def test_unknown_functions_not_parsed():
    for f in ("VLOOKUP($X.Y, A, B)", "CONTAINS(Name, \"x\")",
              'CASE(StageName, "A", 1, 0) = 1'):
        r = parse(f)
        assert isinstance(r, NotParsed), f"{f!r} should be NotParsed, got {r!r}"
        assert not is_parsed(r) and r.reason


def test_today_and_regex_now_parse():
    # D-344: TODAY()/REGEX() are recognized so their FIELD refs flow to selection
    # and R1 padding; they stay structurally inert to the evaluators (the
    # evaluators return NonEvaluable/None/NotDerivable — verified elsewhere).
    from primeqa.semantic.formula import FunctionCall, FieldRef, Comparison
    t = parse("TODAY()")
    assert isinstance(t, FunctionCall) and t.name == "TODAY" and t.args == ()
    r = parse('REGEX(Name, "[0-9]+")')
    assert isinstance(r, FunctionCall) and r.name == "REGEX"
    assert isinstance(r.args[0], FieldRef) and r.args[0].path == ("Name",)
    c = parse("Close_Date__c < TODAY()")     # a comparison against TODAY() parses
    assert is_parsed(c) and isinstance(c, Comparison)


def test_malformed_not_parsed():
    for f in ("Amount <", "ISBLANK(", 'Name = "unterminated', "Amount = 0 extra",
              "&& Amount", "Account. = 1"):
        assert isinstance(parse(f), NotParsed), f"{f!r} should be NotParsed"


def test_empty_not_parsed():
    assert isinstance(parse(""), NotParsed)
    assert isinstance(parse("   "), NotParsed)
    assert isinstance(parse(None), NotParsed)  # type: ignore[arg-type]


def test_arity_guards():
    assert isinstance(parse("AND(ISBLANK(A__c))"), NotParsed)   # AND needs >= 2
    assert isinstance(parse("NOT(A__c, B__c)"), NotParsed)      # NOT needs exactly 1


# ---------------------------------------------------------------------------
# Both-consumer shape
# ---------------------------------------------------------------------------

def _ref_types(node, ctx="read"):
    """Slice-2 extraction shape: (field_name, reference_type) pairs.

    `read` for a direct field ref; `priorvalue` / `ischanged` for a ref inside
    that change-fn; `isnew` (no field) for ISNEW()."""
    out = []
    if isinstance(node, FieldRef):
        out.append((node.name, ctx))
    elif isinstance(node, FunctionCall):
        if node.name == "ISNEW":
            out.append((None, "isnew"))
        sub = {"PRIORVALUE": "priorvalue", "ISCHANGED": "ischanged"}.get(node.name, "read")
        for a in node.args:
            out += _ref_types(a, sub)
    elif isinstance(node, Comparison):
        out += _ref_types(node.left, ctx) + _ref_types(node.right, ctx)
    elif isinstance(node, (And, Or)):
        for o in node.operands:
            out += _ref_types(o, ctx)
    elif isinstance(node, Not):
        out += _ref_types(node.operand, ctx)
    return out


def test_reference_type_extraction_for_slice2():
    # read + priorvalue + ischanged + isnew + dotted, all extractable.
    n = parse('AND(ISCHANGED(OwnerId), Amount <> PRIORVALUE(Amount), '
              'ISPICKVAL(Account.Type, "Key"), ISNEW())')
    refs = dict((f, t) for f, t in _ref_types(n) if f is not None)
    assert refs["OwnerId"] == "ischanged"
    assert refs["Amount"] == "priorvalue"      # the PRIORVALUE-wrapped one wins
    assert refs["Account.Type"] == "read"      # dotted, read
    assert ("Amount", "read") in _ref_types(n)  # the bare Amount on the LHS is read
    assert (None, "isnew") in _ref_types(n)


def test_predicate_walkable_for_slice3():
    n = parse('ISPICKVAL(StageName, "Closed Won") && ISBLANK(CloseReason__c)')
    nodes = list(walk(n))
    assert isinstance(nodes[0], And)
    assert any(isinstance(x, FunctionCall) and x.name == "ISPICKVAL" for x in nodes)
    assert any(isinstance(x, FieldRef) and x.name == "CloseReason__c" for x in nodes)
    assert any(isinstance(x, Literal) and x.value == "Closed Won" for x in nodes)


# ---------------------------------------------------------------------------
# Representative real validation-rule formulas
# ---------------------------------------------------------------------------

def test_representative_formulas_structure():
    # "require a reason when closing as lost"
    n = parse('AND(ISPICKVAL(StageName, "Closed Lost"), ISBLANK(Loss_Reason__c))')
    assert isinstance(n, And) and len(n.operands) == 2
    assert all(isinstance(o, FunctionCall) for o in n.operands)

    # "amount must be positive"
    assert parse("Amount <= 0") == Comparison("<=", FieldRef(("Amount",)), Literal(0, "number"))

    # "owner can't change after close" (change-context + nested boolean)
    n = parse("ISCHANGED(OwnerId) && ISPICKVAL(StageName, \"Closed Won\")")
    assert isinstance(n, And)
    assert n.operands[0] == FunctionCall("ISCHANGED", (FieldRef(("OwnerId",)),))


# ---------------------------------------------------------------------------
# Single-quoted string literals (D-209) — the admin-written quote style
# ---------------------------------------------------------------------------

def test_single_quoted_literal_parses_identically():
    # byte-identical AST to the double-quoted twin (claim identity unaffected)
    assert parse("ISPICKVAL(StageName, 'Closed Won')") == \
        parse('ISPICKVAL(StageName, "Closed Won")')


def test_single_quoted_comparison():
    assert parse("Status = 'Active'") == \
        Comparison("=", FieldRef(("Status",)), Literal("Active", "string"))


def test_real_org_formula_now_parses():
    # the live env-59 rule that was NotParsed pre-D-209
    n = parse("AND(ISPICKVAL(StageName, 'Closed Won'), Amount <= 0)")
    assert isinstance(n, And) and len(n.operands) == 2


def test_quote_styles_do_not_cross_terminate():
    # a double quote inside a single-quoted string is content, and vice versa
    assert parse("Name = 'a\"b'") == \
        Comparison("=", FieldRef(("Name",)), Literal('a"b', "string"))
    assert parse('Name = "a\'b"') == \
        Comparison("=", FieldRef(("Name",)), Literal("a'b", "string"))


def test_unterminated_single_quote_not_parsed():
    from primeqa.semantic.formula import NotParsed
    assert isinstance(parse("Status = 'Active"), NotParsed)
