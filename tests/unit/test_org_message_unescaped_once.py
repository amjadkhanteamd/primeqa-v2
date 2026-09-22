"""Round 4 (AUD-042): the org's own words are unescaped ONCE, at the read, so
the run page shows the character the org wrote and never the entity's source
(production showed 'Loans over &amp;#8377;50,00,000' where the org wrote
'&#8377;'). Also: a message with no entity is untouched, and the fallback
rejection body gets the same treatment."""
from __future__ import annotations

import pytest

from primeqa.intelligence.claim_presentation import org_rejection_message

pytestmark = pytest.mark.unit


def test_an_entity_in_the_orgs_message_becomes_its_character():
    steps = [{"kind": "create", "success": False,
              "message": "Loans over &#8377;50,00,000 require Manager Approval"}]
    assert org_rejection_message(steps) == "Loans over ₹50,00,000 require Manager Approval"


def test_the_fallback_rejection_body_is_unescaped_too_and_plain_text_is_untouched():
    steps = [{"kind": "update", "success": False, "message": "",
              "rejection_body": [{"message": "Tom &amp; Jerry &lt; 3"}]}]
    assert org_rejection_message(steps) == "Tom & Jerry < 3"
    plain = [{"kind": "create", "success": False, "message": "Plain words, no entity"}]
    assert org_rejection_message(plain) == "Plain words, no entity"
    assert org_rejection_message([{"kind": "read", "success": True}]) is None


def test_it_is_unescaped_once_not_twice():
    """'&amp;#8377;' is the SOURCE of an entity, not the rupee: one unescape
    gives '&#8377;' and stops — the org wrote an ampersand entity, and that is
    what the page must show."""
    steps = [{"kind": "create", "success": False, "message": "shows &amp;#8377; literally"}]
    assert org_rejection_message(steps) == "shows &#8377; literally"
