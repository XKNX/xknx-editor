"""The device's Name field: entering and leaving it unchanged keeps the name, a custom one as well
as none (the default name filled in on entering is not stored as a custom name)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any, cast

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.project.ui.configure import ConfigurePanel


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    io.config_flags |= imgui.ConfigFlags_.nav_enable_keyboard
    yield
    imgui.destroy_context(ctx)


def _panel(renamed: list[str]) -> ConfigurePanel:
    def unused(*_args: Any) -> None:
        raise AssertionError("not expected")

    return ConfigurePanel(
        get_devices=list,
        get_selected_device=lambda: None,
        set_selected_device=unused,
        on_param_change=unused,
        on_individual_address_change=unused,
        on_name_change=lambda _device, name: renamed.append(name),
        on_description_change=unused,
        set_flag=unused,
        get_links_for_com_object=cast(Any, unused),
        get_all_group_addresses=cast(Any, unused),
        on_link_com_object=unused,
        on_unlink_com_object=unused,
        on_auto_create_gas=unused,
        get_device_info=lambda _node_id: None,
    )


def _device(name: str) -> Any:
    device = SimpleNamespace(
        node_id=1, name=name, product_name="Switch Actuator", app=None
    )
    device.display_name = name or device.product_name
    return device


def _frames(panel: ConfigurePanel, device: Any) -> Callable[[], None]:
    def frame() -> None:
        imgui.new_frame()
        imgui.set_next_window_pos(imgui.ImVec2(0, 0))
        imgui.set_next_window_size(imgui.ImVec2(400, 300))
        imgui.begin("Device")
        panel._name_input(device)  # pyright: ignore[reportPrivateUsage]
        imgui.button("Other")
        imgui.end()
        imgui.end_frame()
        imgui.render()

    return frame


def _enter_and_leave(frame: Callable[[], None], keys: str = "") -> None:
    """Clicks into the field, types ``keys`` at its end, and clicks below it."""
    io = imgui.get_io()
    frame()
    frame()
    for pos in ((50.0, 30.0), (50.0, 200.0)):
        io.add_mouse_pos_event(*pos)
        frame()
        io.add_mouse_button_event(0, True)
        frame()
        io.add_mouse_button_event(0, False)
        frame()
        if keys:
            io.add_key_event(imgui.Key.end, True)
            frame()
            io.add_key_event(imgui.Key.end, False)
            io.add_input_characters_utf8(keys)
            frame()
            keys = ""


@pytest.mark.usefixtures("context")
@pytest.mark.parametrize("name", ["Kitchen Light", ""])
def test_leaving_the_field_unchanged_keeps_the_name(name: str) -> None:
    renamed: list[str] = []
    device = _device(name)
    _enter_and_leave(_frames(_panel(renamed), device))
    assert renamed == []


@pytest.mark.usefixtures("context")
def test_typing_renames() -> None:
    renamed: list[str] = []
    device = _device("Kitchen")
    _enter_and_leave(_frames(_panel(renamed), device), " Light")
    assert renamed == ["Kitchen Light"]
