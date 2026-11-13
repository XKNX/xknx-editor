"""Native configuration file actions and the complete offline plan."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from imgui_bundle import imgui

from editor_gui import file_dialogs
from editor_gui.plugin_providers import provider_for
from editor_gui.plugins.project.strings import S
from editor_gui.theme import status_color

if TYPE_CHECKING:
    from editor_gui.device import Device
    from editor_gui.plugins.project.service import ProjectService
    from xknxeditor.prod import MasterData


class NativePluginPanel:
    def __init__(self) -> None:
        self._open: file_dialogs.OpenFileDialog | None = None
        self._save: file_dialogs.SaveFileDialog | None = None
        self._target: tuple[int, int] | None = None
        self._data: bytes = b""
        self._error: str = ""
        self._plan: str = ""
        self._plan_key: tuple[int, int, int] | None = None

    def render(
        self, device: Device, project: ProjectService, master: MasterData | None
    ) -> None:
        self._poll(project)
        try:
            provider = provider_for(device)
        except ValueError as exc:
            imgui.text_colored(status_color("error"), str(exc))
            return
        if provider is None:
            return
        capabilities = provider.capabilities(device)
        if not capabilities.supported:
            imgui.text_wrapped(capabilities.reason)
            return
        if device.native_error:
            imgui.text_wrapped(device.native_error)
        elif capabilities.reason:
            imgui.text_wrapped(capabilities.reason)
        if not capabilities.configured and imgui.button(S.PLUGIN_NATIVE_SETUP):
            try:
                project.setup_plugin(device)
                self._error = ""
            except ValueError as exc:
                self._error = str(exc)
        if device.last_lost_links:
            imgui.text_wrapped(S.PLUGIN_NATIVE_LOST_LINKS)
            for link in device.last_lost_links:
                imgui.text_unformatted(str(link["address"]))
        if self._error:
            imgui.text_colored(status_color("error"), self._error)
        if imgui.button(S.PLUGIN_NATIVE_IMPORT):
            self._target = (project.generation, device.node_id)
            self._open = file_dialogs.open_file(
                title=S.PLUGIN_NATIVE_IMPORT, filters=["JSON", "*.json"]
            )
        if capabilities.preflight:
            if imgui.button(S.PLUGIN_NATIVE_EXPORT):
                try:
                    self._data = project.export_plugin_config(device)
                    self._save = file_dialogs.save_file(
                        title=S.PLUGIN_NATIVE_EXPORT,
                        default_path=f"{provider.id}-config.json",
                        filters=["JSON", "*.json"],
                    )
                except ValueError as exc:
                    self._error = str(exc)
            if imgui.collapsing_header(S.PLUGIN_NATIVE_PLAN):
                key = (project.generation, project.revision, device.node_id)
                if key != self._plan_key:
                    try:
                        self._plan = json.dumps(
                            project.preview_plugin(device, master), indent=2
                        )
                        self._plan_key = key
                        self._error = ""
                    except (ValueError, RuntimeError) as exc:
                        self._error = str(exc)
                        self._plan = ""
                imgui.text_unformatted(self._plan)

    def _poll(self, project: ProjectService) -> None:
        if self._open is not None and self._open.ready():
            result = self._open.result()
            self._open = None
            if (
                result
                and self._target is not None
                and self._target[0] == project.generation
            ):
                device = project.find_device_by_node_id(self._target[1])
                if device is not None:
                    try:
                        project.import_plugin_config(
                            device, Path(result[0]).read_bytes()
                        )
                        self._error = ""
                    except (ValueError, OSError) as exc:
                        self._error = str(exc)
        if self._save is not None and self._save.ready():
            result = self._save.result()
            self._save = None
            if result:
                try:
                    Path(result).write_bytes(self._data)
                    self._error = ""
                except OSError as exc:
                    self._error = str(exc)
