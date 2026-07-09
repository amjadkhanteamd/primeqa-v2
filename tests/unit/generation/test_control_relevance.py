"""Control-relevance nomination — Amendment B (AK 2026-07-09).

Verifies the net-new nomination operation against ALL TEN real req-315 VRs: it
must uniquely nominate VR08 for the RecordType=Enterprise context hypothesis with
subject=Discount / role=cap, WITHOUT threshold entailment — the architectural
separation of finding the relevant control from deriving tests from it.
"""
from primeqa.generation import control_relevance as cr

# The ten real req-315 validation-rule error-condition formulas
# (sandbox_fixtures/pls_benchmark_v1/.../validationRules/*).
VR01 = "NOT(ISBLANK(PLS_BM_Deal_Value__c)) && PLS_BM_Deal_Value__c <= 0"
VR02 = "PLS_BM_Discount__c > 0.20 && ISBLANK(PLS_BM_Approval_Reason__c)"
VR03 = ("PLS_BM_Deal_Value__c > 1000000 && (PLS_BM_Discount__c > 0.15 || "
        "ISPICKVAL(PLS_BM_Risk_Level__c, \"High\") || "
        "ISPICKVAL(PLS_BM_Risk_Level__c, \"Critical\")) && "
        "NOT(PLS_BM_Compliance_Approved__c)")
VR04 = ("(ISPICKVAL(PLS_BM_Stage__c, \"Contract Review\") || "
        "ISPICKVAL(PLS_BM_Stage__c, \"Approved\")) && ISBLANK(PLS_BM_Contract_Number__c)")
VR05 = "ISPICKVAL(PRIORVALUE(PLS_BM_Stage__c), \"Approved\") && ISCHANGED(PLS_BM_Deal_Value__c)"
VR06 = ("ISPICKVAL(PLS_BM_Stage__c, \"Approved\") && "
        "(ISBLANK(PLS_BM_Contract_Start_Date__c) || PLS_BM_Contract_Start_Date__c < TODAY())")
VR07 = ("ISPICKVAL(PLS_BM_Risk_Level__c, \"Critical\") && "
        "(NOT(PLS_BM_Compliance_Approved__c) || ISBLANK(PLS_BM_Override_Reason__c))")
VR08 = "RecordType.DeveloperName = \"PLS_BM_Enterprise\" && PLS_BM_Discount__c > 0.25"
VR09 = ("NOT(ISBLANK(PLS_BM_External_Reference__c)) && "
        "NOT(REGEX(PLS_BM_External_Reference__c, \"^EXT-[0-9]{8}$\"))")
VR10 = ("ISPICKVAL(PLS_BM_Deal_Type__c, \"Enterprise\") && "
        "ISPICKVAL(PLS_BM_Stage__c, \"Approved\") && ISCHANGED(PLS_BM_Stage__c) && "
        "PLS_BM_Deal_Value__c > 2000000 && (PLS_BM_Discount__c > 0.20 || "
        "ISPICKVAL(PLS_BM_Risk_Level__c, \"High\") || ISPICKVAL(PLS_BM_Risk_Level__c, \"Critical\") || "
        "NOT(PLS_BM_Compliance_Approved__c) || ISBLANK(PLS_BM_Contract_Number__c) || "
        "ISBLANK(PLS_BM_Contract_Start_Date__c) || PLS_BM_Contract_Start_Date__c < TODAY())")

ALL_VRS = [
    ("VR01", VR01), ("VR02", VR02), ("VR03", VR03), ("VR04", VR04), ("VR05", VR05),
    ("VR06", VR06), ("VR07", VR07), ("VR08", VR08), ("VR09", VR09), ("VR10", VR10),
]

ENTERPRISE = "PLS_BM_Enterprise"
DISCOUNT = "PLS_BM_Discount__c"


# -- the headline: unique nomination of VR08 without entailment ---------------

def test_nominate_vr08_unique_for_enterprise_discount_cap():
    got = cr.nominate(ALL_VRS, ENTERPRISE, DISCOUNT, cr.CAP)
    assert got == VR08


def test_only_vr08_context_gate_matches_enterprise():
    matched = [name for name, text in ALL_VRS
               if cr.context_gate_match(text, ENTERPRISE)]
    assert matched == ["VR08"]


def test_vr10_is_deal_type_gated_not_record_type():
    # VR10 mentions Enterprise, but via the Deal_Type FIELD (ISPICKVAL) — NOT a
    # RecordType context gate. It must never context-match the RecordType hypothesis.
    assert cr.context_gate_match(VR10, ENTERPRISE) is False
    assert cr.context_gate_match(VR10, "PLS_BM_Standard") is False


# -- behavioural-role grammar -------------------------------------------------

def test_vr08_role_on_discount_is_cap():
    assert cr.vr_role_on_field(VR08, DISCOUNT) == cr.CAP


def test_vr10_role_on_discount_is_transition_not_cap():
    # VR10 carries ISCHANGED → a transition gate; its Discount is an incidental
    # OR-branch, never a direct cap.
    assert cr.vr_role_on_field(VR10, DISCOUNT) == cr.TRANSITION


def test_vr03_discount_is_compound_not_cap():
    # VR03's Discount lives inside a nested OR (compound eligibility gate), so even
    # though it is > a literal it is NOT a top-level cap.
    assert cr.vr_role_on_field(VR03, DISCOUNT) == cr.COMPOUND


def test_vr01_role_on_deal_value_is_floor():
    assert cr.vr_role_on_field(VR01, "PLS_BM_Deal_Value__c") == cr.FLOOR


def test_vr04_role_on_contract_number_is_requiredness():
    assert cr.vr_role_on_field(VR04, "PLS_BM_Contract_Number__c") == cr.REQUIREDNESS


def test_role_absent_field_is_unknown():
    assert cr.vr_role_on_field(VR08, "PLS_BM_Contract_Number__c") == cr.UNKNOWN


# -- refuse-on-non-unique / guards --------------------------------------------

def test_nominate_refuses_when_devname_has_no_gated_vr():
    # No VR gates on the Standard record type → zero qualifiers → refuse.
    assert cr.nominate(ALL_VRS, "PLS_BM_Standard", DISCOUNT, cr.CAP) is None


def test_nominate_refuses_wrong_subject_field():
    # VR08 governs Discount, not Contract_Number → zero qualifiers → refuse.
    assert cr.nominate(ALL_VRS, ENTERPRISE, "PLS_BM_Contract_Number__c", cr.CAP) is None


def test_nominate_refuses_role_mismatch():
    # Right context + subject, but asking for a FLOOR when VR08 is a CAP → refuse.
    assert cr.nominate(ALL_VRS, ENTERPRISE, DISCOUNT, cr.FLOOR) is None


def test_nominate_refuses_unknown_requirement_role():
    assert cr.nominate(ALL_VRS, ENTERPRISE, DISCOUNT, cr.UNKNOWN) is None


def test_nominate_refuses_two_gated_caps():
    # If a SECOND VR were RecordType=Enterprise-gated with a Discount cap, the two
    # qualify equally → refuse-on-non-unique (never pick by order).
    vr_x = "RecordType.DeveloperName = \"PLS_BM_Enterprise\" && PLS_BM_Discount__c > 0.30"
    items = ALL_VRS + [("VRX", vr_x)]
    assert cr.nominate(items, ENTERPRISE, DISCOUNT, cr.CAP) is None


# -- requirement role from the proposed condition predicate -------------------

def test_role_from_predicate():
    assert cr.role_from_condition_predicate("exceeds") == cr.CAP
    assert cr.role_from_condition_predicate(">") == cr.CAP
    assert cr.role_from_condition_predicate("<=") == cr.FLOOR
    assert cr.role_from_condition_predicate("is_null") == cr.REQUIREDNESS
    assert cr.role_from_condition_predicate("equals") == cr.UNKNOWN


def test_unparseable_formula_is_inert():
    assert cr.context_gate_match("this is not a formula (((", ENTERPRISE) is False
    assert cr.vr_role_on_field("!!broken", DISCOUNT) == cr.UNKNOWN
    assert cr.governs_field("!!broken", DISCOUNT) is False
