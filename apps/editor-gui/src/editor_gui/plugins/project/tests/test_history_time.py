"""The History panel shows when each step was made, in local time."""

from __future__ import annotations

import datetime

from editor_gui.plugins.project.ui.history import history_time_text


def test_a_step_of_today_shows_its_time_of_day() -> None:
    moment = datetime.datetime(2026, 10, 27, 8, 12, 3, tzinfo=datetime.UTC)
    local = moment.astimezone()
    assert history_time_text(moment, now=local) == local.strftime("%H:%M:%S")


def test_an_older_step_shows_its_date_too() -> None:
    moment = datetime.datetime(2026, 10, 20, 8, 12, 3, tzinfo=datetime.UTC)
    later = (moment + datetime.timedelta(days=7)).astimezone()
    assert history_time_text(moment, now=later) == moment.astimezone().strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def test_a_step_without_a_time_shows_none() -> None:
    assert history_time_text(None) == ""
