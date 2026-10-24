"""The choices of an element's "Status" and of a room's "Use", with their texts in the language of
the user interface."""

from __future__ import annotations

from editor_gui.plugins.project.space_usages import SPACE_USAGES
from editor_gui.plugins.project.strings import S
from editor_gui.strings import get_locale
from xknxeditor.proj.models import COMPLETION_STATUSES

# The text of "no use" in the drop-down.
NO_USAGE_TEXT = "-"


def status_label(status: str) -> str:
    """The text of a ``CompletionStatus`` value; an unknown value as it is."""
    labels = {
        "Undefined": S.STATUS_UNDEFINED,
        "Editing": S.STATUS_EDITING,
        "FinishedDesign": S.STATUS_FINISHED_DESIGN,
        "FinishedCommissioning": S.STATUS_FINISHED_COMMISSIONING,
        "Tested": S.STATUS_TESTED,
        "Accepted": S.STATUS_ACCEPTED,
        "Locked": S.STATUS_LOCKED,
    }
    return labels.get(status, status)


def status_options() -> list[tuple[str, str]]:
    """``(value, text)`` of every status, in the order of the drop-down."""
    return [(status, status_label(status)) for status in COMPLETION_STATUSES]


def usage_label(usage: str, fallback: str = "") -> str:
    """The text of a room use (a SpaceUsage id) in the language of the user interface;
    ``fallback`` (e.g. the text an imported project brought) for an id the master data lacks."""
    texts = SPACE_USAGES.get(usage)
    if texts is None:
        return fallback or usage
    return texts.get(get_locale(), texts["en"])


def usage_options() -> list[tuple[str, str]]:
    """``(usage id, text)`` of "no use" and every room use of the master data, sorted by text."""
    uses = sorted(
        ((usage, usage_label(usage)) for usage in SPACE_USAGES),
        key=lambda item: item[1].casefold(),
    )
    return [("", NO_USAGE_TEXT), *uses]
