"""Unit tests for the pure REFERENCES extraction (D-107, slice 2).

`extract_field_refs(formula_text) -> (parsed, refs, has_cross_object)`:
read/priorvalue/ischanged reference_types, multi-reftype per field, ISNEW
contributes no field (1a), cross-object dotted is skipped + flagged (2a),
dedup, and the NotParsed/unparsed boundary. Pure — no DB.
"""
from __future__ import annotations

from primeqa.sync.validation_rule_refs import extract_field_refs


def test_bare_read():
    parsed, refs, xobj = extract_field_refs("ISBLANK(Reason__c)")
    assert parsed and not xobj
    assert refs == [(("Reason__c",), "read")]


def test_priorvalue_and_read_multi_reftype():
    parsed, refs, xobj = extract_field_refs("Amount <> PRIORVALUE(Amount)")
    assert parsed and not xobj
    # same field, two reference_types -> two rows
    assert (("Amount",), "read") in refs
    assert (("Amount",), "priorvalue") in refs
    assert len(refs) == 2


def test_ischanged():
    parsed, refs, xobj = extract_field_refs("ISCHANGED(OwnerId)")
    assert parsed and refs == [(("OwnerId",), "ischanged")]


def test_ispickval_arg_is_read():
    parsed, refs, _ = extract_field_refs('ISPICKVAL(StageName, "Closed Won")')
    assert parsed and refs == [(("StageName",), "read")]


def test_isnew_contributes_no_field():
    parsed, refs, xobj = extract_field_refs("ISNEW()")
    assert parsed and refs == [] and not xobj           # 1a: record-level, no field


def test_cross_object_dotted_skipped_and_flagged():
    parsed, refs, xobj = extract_field_refs('Account.Industry = "Tech"')
    assert parsed and refs == [] and xobj is True       # 2a: deferred


def test_mixed_same_and_cross_object():
    parsed, refs, xobj = extract_field_refs('AND(ISBLANK(Reason__c), Account.Type = "Key")')
    assert parsed and xobj is True
    assert refs == [(("Reason__c",), "read")]           # same-object kept, cross-object skipped


def test_dedup_same_field_same_reftype():
    parsed, refs, _ = extract_field_refs("ISBLANK(A__c) && ISBLANK(A__c)")
    assert parsed and refs == [(("A__c",), "read")]     # de-duplicated


def test_unparsed_boundary():
    for f in ("CASE(StageName, \"A\", 1, 0) = 1", "", "Amount <"):
        parsed, refs, xobj = extract_field_refs(f)
        assert parsed is False and refs == [] and xobj is False, f


def test_regex_now_extracts_field_ref():
    # D-344: REGEX() is recognized, so a VR09-shaped format rule now yields its
    # field ref (references_status flips unparsed -> complete/partial) instead of
    # being invisible. The field is a same-object read.
    parsed, refs, xobj = extract_field_refs('NOT(REGEX(Ext_Ref__c, "^E[0-9]+$"))')
    assert parsed is True and refs == [(("Ext_Ref__c",), "read")] and xobj is False
