"""While a device's application loads in the background, the programming row does not load it on
the UI thread to learn what its plug-in allows: all scopes are offered and programming waits."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast

import pytest

from editor_gui.plugins.project.ui import configure
from editor_gui.plugins.project.ui.configure import ConfigurePanel
from xknxeditor.download.scope import DownloadScope


class _Plugin:
    """A plug-in that allows only full downloads, asked for its capabilities."""

    def __init__(self) -> None:
        self.asked = 0

    def __call__(self, _device: Any) -> Any:
        self.asked += 1
        return SimpleNamespace(
            download_scopes=(DownloadScope.FULL,), programming_error=None
        )


def _panel(ready: list[bool], plugin: _Plugin, programmed: list[Any]) -> ConfigurePanel:
    def unused(*_args: Any) -> None:
        raise AssertionError("not expected")

    return ConfigurePanel(
        get_devices=lambda: [],
        get_selected_device=lambda: None,
        set_selected_device=unused,
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
        application_ready=lambda _device: ready[0],
        plugin_capabilities=plugin,
        on_program_device=lambda device, scope: programmed.append((device, scope)),
    )


@pytest.fixture
def row(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, list[Any]]]:
    """The scope choices and the buttons (label, disabled) the programming row draws."""
    imgui = configure.imgui
    ctx = imgui.create_context()
    imgui.get_io().display_size = imgui.ImVec2(1200, 600)
    imgui.get_io().backend_flags |= imgui.BackendFlags_.renderer_has_textures
    imgui.new_frame()
    imgui.begin("Configure")
    drawn: dict[str, list[Any]] = {"scopes": [], "buttons": []}
    disabled = [False]
    monkeypatch.setattr(
        imgui, "begin_disabled", lambda value=True: disabled.__setitem__(0, value)
    )
    monkeypatch.setattr(imgui, "end_disabled", lambda: None)
    monkeypatch.setattr(
        imgui,
        "button",
        lambda label, *_a: drawn["buttons"].append((label, disabled[0])) or False,
    )
    monkeypatch.setattr(
        configure,
        "combo",
        lambda _id, i, labels: (drawn["scopes"].append(list(labels)) or False, i),
    )
    yield drawn
    monkeypatch.undo()
    imgui.end()
    imgui.end_frame()
    imgui.destroy_context(ctx)


def _device() -> Any:
    return SimpleNamespace(
        node_id=1, name="", individual_address="1.1.1", has_application=True
    )


def test_the_plugin_is_asked_only_once_the_application_is_loaded(
    row: dict[str, list[Any]],
) -> None:
    ready = [False]
    plugin = _Plugin()
    panel = _panel(ready, plugin, [])
    device = _device()

    panel._render_programming(device)  # pyright: ignore[reportPrivateUsage]
    assert plugin.asked == 0
    # Every scope offered, programming not yet.
    assert len(row["scopes"][-1]) == len(configure._SCOPE_ORDER)  # pyright: ignore[reportPrivateUsage]
    assert (configure.S.BTN_PROGRAM_DEVICE, True) in row["buttons"]

    ready[0] = True
    row["buttons"].clear()
    panel._render_programming(device)  # pyright: ignore[reportPrivateUsage]
    assert plugin.asked > 0
    assert row["scopes"][-1] == [configure.S.SCOPE_FULL]
    assert (configure.S.BTN_PROGRAM_DEVICE, False) in row["buttons"]
