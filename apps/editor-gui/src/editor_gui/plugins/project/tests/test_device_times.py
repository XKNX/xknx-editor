"""A device's "Last Modified" and "Last Downloaded" times as the details show them."""

from __future__ import annotations

import datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from imgui_bundle import imgui

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


def test_the_device_header_shows_the_times_and_the_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from editor_gui.plugins.project.ui import configure

    ctx = imgui.create_context()
    imgui.get_io().display_size = imgui.ImVec2(800, 600)
    imgui.get_io().backend_flags |= imgui.BackendFlags_.renderer_has_textures
    try:
        shown: list[str] = []
        real_text = imgui.text_unformatted
        monkeypatch.setattr(
            imgui,
            "text_unformatted",
            lambda text, *a: (shown.append(text), real_text(text, *a)),
        )
        monkeypatch.setattr(
            configure, "status_combo", lambda _label, _status: "Accepted"
        )
        changes: list[tuple[int, str]] = []
        device = cast(
            Any,
            SimpleNamespace(
                node_id=3,
                app=None,
                name="Dimmer",
                display_name="Dimmer",
                product_name="Universal Dimmer",
                individual_address="1.1.5",
                description="",
            ),
        )

        def unused(*_args: Any) -> None:
            raise AssertionError("not expected")

        panel = configure.ConfigurePanel(
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
            get_device_info=lambda _node_id: cast(
                Any,
                SimpleNamespace(
                    last_modified="2026-01-24T11:38:26Z", last_download=None
                ),
            ),
            get_status=lambda _device: "Tested",
            on_status_change=lambda d, status: changes.append((d.node_id, status)),
        )
        imgui.new_frame()
        imgui.begin("Device")
        panel._render_header_fields(device)  # pyright: ignore[reportPrivateUsage]
        imgui.end()
        imgui.end_frame()
        assert timestamp_text("2026-01-24T11:38:26Z") in shown and "-" in shown
        assert changes == [(3, "Accepted")]
    finally:
        imgui.destroy_context(ctx)
