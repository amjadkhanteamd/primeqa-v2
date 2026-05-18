"""ui-trigger body (operational layer, runtime plane).

Per SPEC §3 + D-055. A ui-trigger represents a user-driven UI
action — clicking a button, submitting a form, navigating between
pages, editing a field inline, invoking a list-view action.

Operational layer; refs use :data:`OperationalRef`. UI triggers
typically resolve their page-context references logically (by
name) because UI navigation patterns are stable across version
changes; pinning is opt-in for tests that need to assert
reproducibility against a specific version of a Lightning page.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict

from primeqa.test_representation.models.common import BodyBase
from primeqa.test_representation.models.references import OperationalRef
from primeqa.test_representation.models.registry import register_body


# ---------------------------------------------------------------------------
# PageContext
# ---------------------------------------------------------------------------

class PageContext(BaseModel):
    """The page / surface on which the UI action occurs.

    ``parent_record`` and ``parent_object`` are both optional and
    independent: a record-detail page typically has a
    parent_record reference (the specific record), a list-view
    typically has a parent_object reference (the list-view's
    object type), and pages like ``lightning_app`` may have
    neither. The substrate doesn't enforce a coupling between
    page_kind and which of the two is set — the Coordinator
    decides whether the combination is meaningful for the test.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    page_kind: Literal[
        "record_detail",
        "list_view",
        "edit_form",
        "lightning_app",
        "experience_cloud",
        "console",
    ]
    """The kind of page hosting the action."""

    parent_record: Optional[OperationalRef] = None
    """If the page targets a specific record, the record reference.
    Operational-layer ref."""

    parent_object: Optional[OperationalRef] = None
    """If the page targets an Object (e.g., a list-view of
    Opportunities), the Object reference. Operational-layer ref."""


# ---------------------------------------------------------------------------
# UITriggerBody
# ---------------------------------------------------------------------------

@register_body("ui-trigger", 1)
class UITriggerBody(BodyBase):
    """The ui-trigger body shape (v1).

    ``target_element`` is a free-form string at v1 — the
    Lightning DOM is too fluid for a structured selector model
    that survives schema versioning. Tests typically identify
    targets by stable labels ("Submit", "Save") or by
    well-known stable identifiers. Structured selector model
    deferred to a future schema version.
    """

    model_config = ConfigDict(extra="forbid", frozen=False)

    body_schema_version: Literal[1] = 1
    kind: Literal["ui-trigger"] = "ui-trigger"

    action_type: Literal[
        "button_click",
        "form_submit",
        "navigation",
        "inline_edit",
        "list_view_action",
        "related_list_action",
    ]
    """What kind of user action is performed."""

    page_context: PageContext
    """Where the action occurs. Required because UI actions
    without a page context can't be reproduced."""

    target_element: Optional[str] = None
    """Free-form identifier for the element being acted on
    (button label, field API name, action name). Optional
    because some actions — global navigation, e.g. — don't
    target a specific element."""

    input_values: Optional[dict[str, Any]] = None
    """For ``form_submit`` / ``inline_edit``: the values the
    user types in. Loosely typed at v1; the recipe + Coordinator
    handle SF-type validation."""
