"""A device's "Last Modified" and "Last Downloaded" times as the details show them."""

from __future__ import annotations

import datetime

from editor_gui.plugins.project.ui.configure import timestamp_text


def test_a_stored_time_is_shown_in_local_time_to_the_minute() -> None:
    stored = "2026-01-24T11:38:26.8111136Z"
    local = (
        datetime.datetime(2026, 1, 24, 11, 38, tzinfo=datetime.UTC)
        .astimezone()
        .strftime("%Y-%m-%d %H:%M")
    )
    assert timestamp_text(stored) == local
    assert timestamp_text("2026-01-24T11:38:26Z") == local
    assert timestamp_text("2026-01-24T11:38:26+00:00") == local


def test_no_time_shows_a_dash() -> None:
    assert timestamp_text(None) == "-"
    assert timestamp_text("") == "-"
    assert timestamp_text("yesterday") == "-"
