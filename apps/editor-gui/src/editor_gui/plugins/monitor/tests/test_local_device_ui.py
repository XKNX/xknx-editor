"""While the editor's connection reaches only a USB interface's own device (local device access),
the Group Monitor's and the Group Objects tab's Read and Write and the Replay are disabled, with
the reason as their tooltip."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from imgui_bundle import imgui
from xknx import XKNX

from editor_gui.plugins.connection.service import ConnectionService, LocalDevice
from editor_gui.plugins.monitor import table as table_module
from editor_gui.plugins.monitor.formatting import GroupAddressEntry, ProjectIndex
from editor_gui.plugins.monitor.service import MonitorKind, MonitorService
from editor_gui.plugins.monitor.strings import S
from editor_gui.plugins.monitor.ui import MonitorPanel

_REASON = "group telegrams can't be sent"


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(1000, 700)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def _panel(local: bool) -> tuple[MonitorPanel, MonitorService]:
    connection = ConnectionService()
    connection.set_logger(MagicMock())
    connection.set_connection(XKNX(), None)
    if local:
        connection.local_device = LocalDevice("1.0.2")
    service = MonitorService(connection)
    index = ProjectIndex(
        group_addresses={
            (1 << 11) | (2 << 8) | 3: GroupAddressEntry("1/2/3", "Light", "DPST-1-1")
        }
    )
    panel = MonitorPanel(service, connection, lambda: [], lambda: index)
    return panel, service


def _buttons(monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, bool], list[str]]:
    """Whether each button was drawn disabled, and the tooltips."""
    disabled: dict[str, bool] = {}
    tooltips: list[str] = []
    real_button, real_tooltip = imgui.button, imgui.set_item_tooltip

    def button(label: str, *args: Any) -> bool:
        disabled[label] = bool(
            imgui.get_current_context().current_item_flags & imgui.ItemFlags_.disabled
        )
        return bool(real_button(label, *args))

    def set_item_tooltip(text: str) -> None:
        tooltips.append(text)
        real_tooltip(text)

    monkeypatch.setattr(imgui, "button", button)
    monkeypatch.setattr(imgui, "set_item_tooltip", set_item_tooltip)
    return disabled, tooltips


def _frame(draw: Any) -> None:
    imgui.new_frame()
    imgui.begin("monitor")
    draw()
    imgui.end()
    imgui.end_frame()


@pytest.mark.parametrize("local", [True, False])
def test_the_group_monitor_command_bar(
    context: None, monkeypatch: pytest.MonkeyPatch, local: bool
) -> None:
    panel, service = _panel(local)
    disabled, tooltips = _buttons(monkeypatch)
    panel._cmd_address = "1/2/3"  # pyright: ignore[reportPrivateUsage]
    _frame(lambda: panel._render_group_command_bar(panel._get_index()))  # pyright: ignore[reportPrivateUsage]
    read = f"{S.MONITOR_READ}##mon_cmd_read"
    assert disabled[read] is local
    assert (service.local_device_reason() in tooltips) is local
    if local:
        assert _REASON in service.local_device_reason()


@pytest.mark.parametrize("local", [True, False])
def test_the_group_objects_command_bar(
    context: None, monkeypatch: pytest.MonkeyPatch, local: bool
) -> None:
    panel, service = _panel(local)
    disabled, tooltips = _buttons(monkeypatch)
    panel._selected = "1/2/3"  # pyright: ignore[reportPrivateUsage]
    _frame(panel._render_command_bar)  # pyright: ignore[reportPrivateUsage]
    assert disabled[S.MONITOR_READ] is local
    assert disabled[S.MONITOR_WRITE] is True or not local
    assert (service.local_device_reason() in tooltips) is local


def test_no_replay(context: None, monkeypatch: pytest.MonkeyPatch) -> None:
    panel, service = _panel(local=True)
    disabled, tooltips = _buttons(monkeypatch)
    view = panel._views[MonitorKind.GROUP]  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(view, "_channel", service.channels[MonitorKind.GROUP])
    service.channels[MonitorKind.GROUP].log.append(MagicMock())
    _frame(view._render_toolbar)  # pyright: ignore[reportPrivateUsage]
    replay = f"{table_module.ICON_REPLAY}##{S.TOOL_REPLAY}_{MonitorKind.GROUP.value}"
    assert disabled[replay]
    assert f"{S.TOOL_REPLAY}\n{service.local_device_reason()}" in tooltips
