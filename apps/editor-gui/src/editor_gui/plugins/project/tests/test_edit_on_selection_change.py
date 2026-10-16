"""An edit whose field loses the focus by selecting another device is stored for the device it
was made in: that device stays shown until the field has reported it."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.project.ui.configure import ConfigurePanel


def _device(node_id: int) -> Any:
    return cast(Any, SimpleNamespace(node_id=node_id, app=None))


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    yield
    imgui.destroy_context(ctx)


def _panel(devices: list[Any], selected: list[Any]) -> ConfigurePanel:
    def unused(*_args: Any) -> None:
        raise AssertionError("not expected")

    return ConfigurePanel(
        get_devices=lambda: devices,
        get_selected_device=lambda: selected[0],
        set_selected_device=lambda d: selected.__setitem__(0, d),
        on_param_change=unused,
        on_individual_address_change=unused,
        on_name_change=unused,
        on_description_change=unused,
        set_flag=unused,
        get_links_for_com_object=cast(Any, unused),
        get_all_group_addresses=cast(Any, unused),
        on_link_com_object=unused,
        on_unlink_com_object=unused,
        on_auto_create_gas=unused,
        get_device_info=lambda _node_id: None,
        get_selected_node_ids=lambda: (
            {d.node_id for d in devices if d is not selected[0]} | {selected[0].node_id}
        ),
        on_param_change_selected=unused,
    )


def _edit_lost_focus(*, edited: bool, frames_ago: int = 0) -> None:
    data = imgui.get_current_context().deactivated_item_data
    data.id_ = 42
    data.has_been_edited_before = edited
    data.elapse_frame = imgui.get_frame_count() + 1 - frames_ago


def test_the_edited_device_stays_shown_until_its_edit_is_stored(context: None) -> None:
    first, second, third = _device(1), _device(2), _device(3)
    selected = [first]
    panel = _panel([first, second, third], selected)
    assert panel._current_device() is first  # pyright: ignore[reportPrivateUsage]

    selected[0] = second
    _edit_lost_focus(edited=True)
    assert panel._current_device() is first  # pyright: ignore[reportPrivateUsage]
    # A joint edit of all selected devices does not take the edit either.
    assert panel._joint_devices(first) == [first]  # pyright: ignore[reportPrivateUsage]

    _edit_lost_focus(edited=True, frames_ago=2)
    assert panel._current_device() is second  # pyright: ignore[reportPrivateUsage]


def test_without_an_edit_the_selection_is_shown_at_once(context: None) -> None:
    first, second = _device(1), _device(2)
    selected = [first]
    panel = _panel([first, second], selected)
    panel._current_device()  # pyright: ignore[reportPrivateUsage]
    selected[0] = second
    _edit_lost_focus(edited=False)
    assert panel._current_device() is second  # pyright: ignore[reportPrivateUsage]


def test_a_removed_device_is_not_held(context: None) -> None:
    first, second = _device(1), _device(2)
    selected = [first]
    devices = [first, second]
    panel = _panel(devices, selected)
    panel._current_device()  # pyright: ignore[reportPrivateUsage]
    devices.remove(first)
    selected[0] = second
    _edit_lost_focus(edited=True)
    assert panel._current_device() is second  # pyright: ignore[reportPrivateUsage]
