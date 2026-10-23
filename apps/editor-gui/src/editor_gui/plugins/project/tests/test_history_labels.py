"""Every project event has a translated label in the History panel."""

import pytest

from editor_gui.plugins.project.service import _history_label
from editor_gui.strings import get_locale, set_locale
from xknxeditor.proj.core.events import EVENT_TYPES


@pytest.mark.parametrize("event_type", sorted(EVENT_TYPES))
def test_every_event_has_a_label(event_type: str) -> None:
    assert _history_label(event_type, {}) != event_type


def test_labels_follow_the_language() -> None:
    previous = get_locale()
    try:
        set_locale("de")
        assert _history_label("SetDeviceName", {"name": "Licht"}) == (
            "Gerät in 'Licht' umbenennen"
        )
    finally:
        set_locale(previous)
