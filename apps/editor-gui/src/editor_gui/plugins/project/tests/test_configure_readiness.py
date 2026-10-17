"""The device settings read what comes from the application only once it is loaded: selecting a
device whose application is still unloaded never parses it on the render thread."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from editor_gui.plugins.project.ui.configure import ConfigurePanel


def _panel(device: Any, ready: list[bool], asked: list[Any]) -> ConfigurePanel:
    def unused(*_args: Any) -> None:
        raise AssertionError("not expected")

    def ip_secure(d: Any) -> None:
        asked.append(d)

    return ConfigurePanel(
        get_devices=lambda: [device],
        get_selected_device=lambda: device,
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
        get_ip_secure=cast(Any, ip_secure),
        application_ready=lambda _d: ready[0],
    )


@pytest.mark.parametrize("loaded", [False, True])
def test_ip_secure_settings_wait_for_the_application(
    monkeypatch: pytest.MonkeyPatch, loaded: bool
) -> None:
    device = cast(Any, SimpleNamespace(node_id=1, app=None, name="", description=""))
    device.individual_address = "1.1.1"
    asked: list[Any] = []
    panel = _panel(device, [loaded], asked)
    # Everything else of the section draws widgets; only the readiness gate is under test.
    for name in (
        "_render_header_fields",
        "_render_plugin_notices",
        "_render_device_actions",
        "_render_application_program",
        "_render_manual_button",
        "_render_ip_config",
    ):
        monkeypatch.setattr(panel, name, lambda *_args: None)
    monkeypatch.setattr(
        "editor_gui.plugins.project.ui.configure.imgui.spacing", lambda: None
    )
    monkeypatch.setattr(panel, "_project_serial", lambda _d: None)
    panel._render_device_section()  # pyright: ignore[reportPrivateUsage]
    assert asked == ([device] if loaded else [])
