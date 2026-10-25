"""The drop-downs of the details: the Bus Connection lists the stored connections, a separator and
the interfaces found; the Current Line offers none and the lines; a value a list lacks is shown
as it is and is not chosen again."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.project.ui import status_field
from editor_gui.plugins.project.ui.status_field import (
    bus_connection_combo,
    choice_combo,
    line_combo,
)

from ._clicks import Clicks


@pytest.fixture
def clicks(monkeypatch: pytest.MonkeyPatch) -> Iterator[Clicks]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield Clicks(monkeypatch, status_field)
    imgui.destroy_context(ctx)


def _frame(render: Callable[[], Any]) -> Any:
    imgui.new_frame()
    imgui.begin("Details")
    result = render()
    imgui.end()
    imgui.end_frame()
    return result


_STORED = [("", "Automatic"), ("Type=IpTunneling;HostAddress=10.0.0.9", "Stored")]
_FOUND = [("Type=IpTunneling;HostAddress=10.0.0.5", "IP-Interface (10.0.0.5:3671 Udp)")]


def _bus(current: str) -> str | None:
    return bus_connection_combo("##bus", current, "Automatic", _STORED, _FOUND)


def test_the_bus_connection_offers_stored_and_found_ones(clicks: Clicks) -> None:
    assert _frame(lambda: _bus("")) is None
    clicks.combos.add("##bus")
    clicks.items.add("IP-Interface (10.0.0.5:3671 Udp)##2")
    assert _frame(lambda: _bus("")) == _FOUND[0][0]
    # Choosing the one chosen changes nothing.
    clicks.combos.add("##bus")
    clicks.items.add("Automatic##0")
    assert _frame(lambda: _bus("")) is None
    clicks.combos.add("##bus")
    clicks.items.add("Automatic##0")
    assert _frame(lambda: _bus(_STORED[1][0])) == ""


def test_the_current_line_offers_none_and_the_lines(clicks: Clicks) -> None:
    lines = [(3, "1.1 Office"), (4, "1.2 Garden")]

    def render(own: int | None) -> tuple[bool, int | None]:
        return line_combo("##line", own, "-", lines, "-")

    assert _frame(lambda: render(3)) == (False, 3)
    clicks.combos.add("##line")
    clicks.items.add("1.2 Garden##4")
    assert _frame(lambda: render(3)) == (True, 4)
    clicks.combos.add("##line")
    clicks.items.add("-##None")
    assert _frame(lambda: render(3)) == (True, None)
    clicks.combos.add("##line")
    clicks.items.add("1.1 Office##3")
    assert _frame(lambda: render(3)) == (False, 3)


def test_a_value_the_list_lacks(
    clicks: Clicks, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = [("a", "First"), ("b", "Second")]
    shown: list[list[str]] = []
    pick: list[int] = []

    def combo(
        _label: str, index: int, texts: list[str], _rows: int = -1
    ) -> tuple[bool, int]:
        shown.append(list(texts))
        return (True, pick.pop()) if pick else (False, index)

    monkeypatch.setattr(status_field, "combo", combo)
    assert _frame(lambda: choice_combo("##c", options, "z")) is None
    assert shown[-1] == ["First", "Second", "z"]
    pick.append(2)
    assert _frame(lambda: choice_combo("##c", options, "z")) is None
    pick.append(1)
    assert _frame(lambda: choice_combo("##c", options, "z")) == "b"
