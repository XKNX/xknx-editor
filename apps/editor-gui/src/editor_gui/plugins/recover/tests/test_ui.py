"""The recovery results remain usable with desktop and narrow browser viewports."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.recover.matching import OnlineVersion
from editor_gui.plugins.recover.ui import RecoverPanel

from .test_matching import Catalog, identify, master_data, product


@pytest.mark.parametrize("width", [400, 1100])
@pytest.mark.parametrize("variants", [1, 2])
def test_results_render_candidates_without_changing_selection(
    width: int, variants: int
) -> None:
    context = imgui.create_context()
    try:
        io = imgui.get_io()
        io.display_size = imgui.ImVec2(width, 600)
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        io.set_ini_filename("")
        service, entry = identify(
            Catalog(*(product(str(i)) for i in range(variants))), master=master_data()
        )
        service._api.project = SimpleNamespace(is_open=False)
        service.fetch_error = "alternative catalog: offline"
        panel = RecoverPanel(service)
        for _ in range(3):
            imgui.new_frame()
            imgui.set_next_window_size(imgui.ImVec2(width, 550))
            imgui.begin("Recovery")
            panel._render_status()
            panel._render_results()
            imgui.end()
            imgui.end_frame()
        assert entry.selected == (variants == 1)
        assert len(entry.catalog_candidates) == variants
    finally:
        imgui.destroy_context(context)


@pytest.mark.parametrize("width", [320, 1100])
def test_other_versions_dialog_fits_and_keeps_device_deselected(width: int) -> None:
    context = imgui.create_context()
    try:
        io = imgui.get_io()
        io.display_size = imgui.ImVec2(width, 600)
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        io.set_ini_filename("")
        service, entry = identify(Catalog(), master=master_data())
        entry.online_versions = (
            OnlineVersion("v2", "Actuator", "SA/S", 0x20),
            OnlineVersion("v3", "Actuator", "SA/S", 0x30),
        )
        panel = RecoverPanel(service)
        panel._versions_address = entry.address
        panel._versions_requested = True
        for _ in range(4):
            imgui.new_frame()
            imgui.set_next_window_size(imgui.ImVec2(width, 550))
            imgui.begin("Recovery")
            panel._render_results()
            panel._render_other_versions()
            assert imgui.is_popup_open("###recover_other_versions")
            imgui.end()
            imgui.end_frame()
        assert not entry.selected and entry.recovered is None and not service.busy
    finally:
        imgui.destroy_context(context)
