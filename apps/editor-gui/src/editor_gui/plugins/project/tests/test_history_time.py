"""The History panel shows when each step was made, in local time in the regional formats."""

from __future__ import annotations

import datetime

import pytest

from editor_gui import local_time
from editor_gui.plugins.project.ui.history import history_time_text
from editor_gui.regional import RegionalFormat


@pytest.fixture(autouse=True)
def _german(monkeypatch: pytest.MonkeyPatch) -> None:
    regional = RegionalFormat(short_date="dd.MM.yyyy", long_time="HH:mm:ss")
    monkeypatch.setattr(local_time, "regional_format", lambda: regional)


def test_a_step_of_today_shows_its_time_of_day() -> None:
    moment = datetime.datetime(2026, 10, 27, 8, 12, 3, tzinfo=datetime.UTC)
    local = moment.astimezone()
    assert history_time_text(moment, now=local) == local.strftime("%H:%M:%S")


def test_an_older_step_shows_its_date_too() -> None:
    moment = datetime.datetime(2026, 10, 20, 8, 12, 3, tzinfo=datetime.UTC)
    later = (moment + datetime.timedelta(days=7)).astimezone()
    assert history_time_text(moment, now=later) == moment.astimezone().strftime(
        "%d.%m.%Y %H:%M:%S"
    )


def test_a_step_without_a_time_shows_none() -> None:
    assert history_time_text(None) == ""
