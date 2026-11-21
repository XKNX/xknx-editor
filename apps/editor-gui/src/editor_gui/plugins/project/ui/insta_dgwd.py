"""Offline DALI parameter exchange through desktop and browser file dialogs."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from imgui_bundle import imgui

from editor_gui import file_dialogs
from editor_gui.plugins.project.strings import S
from editor_gui.widgets.flow import button_width, same_line_if_fits

if TYPE_CHECKING:
    from editor_gui.device import Device
    from editor_gui.plugins.project.service import ProjectService


@dataclass
class Exchange:
    open_dialog: file_dialogs.OpenFileDialog | None = None
    save_dialog: file_dialogs.SaveFileDialog | None = None
    expected: tuple[int, int, str, str, str] | None = None
    export_data: bytes = b""
    message: str = ""

    def render(self, device: Device, project: ProjectService) -> None:
        target = (
            project.generation,
            device.node_id,
            device.app.id if device.app else "",
            device.individual_address,
        )
        if self.open_dialog is not None and self.open_dialog.ready():
            paths = self.open_dialog.result()
            self.open_dialog = None
            if paths:
                try:
                    if self.expected is None or self.expected[:4] != target:
                        raise ValueError(S.E64_CONFIGURATION_CHANGED)
                    project.import_insta_dgwd(
                        device, self.expected[4], Path(paths[0]).read_bytes()
                    )
                    self.message = S.E64_IMPORTED
                except (ValueError, OSError) as exc:
                    self.message = str(exc)
        if self.save_dialog is not None and self.save_dialog.ready():
            path = self.save_dialog.result()
            self.save_dialog = None
            if path:
                try:
                    Path(path).write_bytes(self.export_data)
                    self.message = S.E64_EXPORTED
                except OSError as exc:
                    self.message = str(exc)
            self.export_data = b""
        imgui.push_id("dgwd")
        try:
            imgui.begin_disabled(
                self.open_dialog is not None or self.save_dialog is not None
            )
            if imgui.button(S.E64_IMPORT_XML):
                try:
                    data = project.export_insta_dgwd(device)
                    self.expected = (*target, hashlib.sha256(data).hexdigest())
                    self.open_dialog = file_dialogs.open_file(
                        S.E64_IMPORT_XML, filters=["DALI (.dgwd)", "*.dgwd"]
                    )
                except (ValueError, OSError) as exc:
                    self.message = str(exc)
            same_line_if_fits(button_width(S.E64_EXPORT_XML))
            if imgui.button(S.E64_EXPORT_XML):
                try:
                    self.export_data = project.export_insta_dgwd(device)
                    self.save_dialog = file_dialogs.save_file(
                        S.E64_EXPORT_XML,
                        default_path=f"dali-{device.individual_address or device.node_id}.dgwd",
                        filters=["DALI (.dgwd)", "*.dgwd"],
                    )
                except (ValueError, OSError) as exc:
                    self.message = str(exc)
            imgui.end_disabled()
            if self.message:
                imgui.text_wrapped(self.message)
        finally:
            imgui.pop_id()
