"""A page that comes up selects its context help, as a click on it does: the page a device opens on,
a device switch and another page; a help selected later on the same page is kept."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from editor_gui.device import Device
from editor_gui.widgets import parameter_widgets
from editor_gui.widgets.parameter_widgets import ParameterPage, take_selected_help
from xknxeditor.prod.parser_v2.ui import UiParameterBlock


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    parameter_widgets._shown_page.clear()  # pyright: ignore[reportPrivateUsage]
    take_selected_help()
    yield
    parameter_widgets._shown_page.clear()  # pyright: ignore[reportPrivateUsage]
    take_selected_help()


def _page(key: str, help_context: str | None) -> ParameterPage:
    block = UiParameterBlock(id=key, text=key, children=(), help_context=help_context)
    return ParameterPage(key=key, label=key, icon=None, content=(), block=block)


def _show(device: Device, page: ParameterPage) -> None:
    parameter_widgets._show_page_help(device, page)  # pyright: ignore[reportPrivateUsage]


def test_the_page_a_device_opens_on_shows_its_help() -> None:
    _show(Device(1, "A", None, "1.1.1"), _page("general", "Help-General"))
    assert take_selected_help() == "Help-General"


def test_a_page_shown_again_keeps_the_help_selected_on_it() -> None:
    device = Device(1, "A", None, "1.1.1")
    page = _page("general", "Help-General")
    _show(device, page)
    take_selected_help()
    _show(device, page)
    assert take_selected_help() is None


def test_another_page_or_device_shows_its_help() -> None:
    first, second = Device(1, "A", None, "1.1.1"), Device(2, "B", None, "1.1.2")
    _show(first, _page("general", "Help-General"))
    _show(first, _page("time", "Help-Time"))
    assert take_selected_help() == "Help-Time"
    _show(second, _page("time", "Help-Time-2"))
    assert take_selected_help() == "Help-Time-2"


def test_a_page_without_help_selects_none() -> None:
    _show(Device(1, "A", None, "1.1.1"), _page("general", None))
    assert take_selected_help() is None
