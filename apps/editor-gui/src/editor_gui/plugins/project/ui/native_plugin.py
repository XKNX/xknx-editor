"""Native configuration setup and file actions."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from imgui_bundle import imgui

from editor_gui import file_dialogs
from editor_gui.plugin_providers import provider_named
from editor_gui.plugins.project.strings import S
from editor_gui.theme import status_color

if TYPE_CHECKING:
    from editor_gui.device import Device
    from editor_gui.plugins.project.service import ProjectService


class NativePluginPanel:
    def __init__(self) -> None:
        self._open: file_dialogs.OpenFileDialog | None = None
        self._save: file_dialogs.SaveFileDialog | None = None
        self._target: tuple[int, int] | None = None
        self._data: bytes = b""
        self._errors: dict[tuple[int, int], str] = {}
        self._generation: int | None = None
        self._save_target: tuple[int, int] | None = None

    def render(self, device: Device, project: ProjectService) -> None:
        if self._generation != project.generation:
            self._errors.clear()
            self._generation = project.generation
        self._poll(project)
        key = (project.generation, device.node_id)
        try:
            capabilities = project.plugin_capabilities(device)
            mapping = project.plugin_mapping(device)
            if mapping is None or not mapping.provider:
                return
            provider = provider_named(mapping.provider)
        except ValueError as exc:
            imgui.text_colored(status_color("error"), str(exc))
            return
        if capabilities is None:
            return
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
                self._errors.pop(key, None)
            except ValueError as exc:
                self._errors[key] = str(exc)
        if device.last_lost_links:
            imgui.text_wrapped(S.PLUGIN_NATIVE_LOST_LINKS)
            for link in device.last_lost_links:
                imgui.text_unformatted(str(link["address"]))
        if error := self._errors.get(key):
            imgui.text_colored(status_color("error"), error)
        if imgui.button(S.PLUGIN_NATIVE_IMPORT):
            self._target = (project.generation, device.node_id)
            self._open = file_dialogs.open_file(
                title=S.PLUGIN_NATIVE_IMPORT, filters=["JSON", "*.json"]
            )
        if capabilities.configured and imgui.button(S.PLUGIN_NATIVE_EXPORT):
            try:
                self._data = project.export_plugin_config(device)
                self._save_target = key
                self._save = file_dialogs.save_file(
                    title=S.PLUGIN_NATIVE_EXPORT,
                    default_path=f"{provider.id}-config.json",
                    filters=["JSON", "*.json"],
                )
            except ValueError as exc:
                self._errors[key] = str(exc)

    def _poll(self, project: ProjectService) -> None:
        if self._open is not None and self._open.ready():
            result = self._open.result()
            self._open = None
            if (
                result
                and self._target is not None
                and self._target[0] == project.generation
            ):
                key = self._target
                device = project.find_device_by_node_id(key[1])
                if device is not None:
                    try:
                        project.import_plugin_config(
                            device, Path(result[0]).read_bytes()
                        )
                        self._errors.pop(key, None)
                    except (ValueError, OSError) as exc:
                        self._errors[key] = str(exc)
        if self._save is not None and self._save.ready():
            result = self._save.result()
            self._save = None
            if result and self._save_target is not None:
                key = self._save_target
                try:
                    Path(result).write_bytes(self._data)
                    self._errors.pop(key, None)
                except OSError as exc:
                    self._errors[key] = str(exc)
