"""Manufacturer plugins a product's data asks for, which XKNX Editor cannot run."""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from editor_gui.device import Device


# An application program id without its hash: manufacturer, number and version.
_APPLICATION = re.compile(r"(M-[0-9A-F]{4}_A-[0-9A-F]{4}-[0-9A-F]{2})-[0-9A-F]{4}")


@dataclass(frozen=True, slots=True)
class ConfigurationApp:
    """A device configuration app, from the manifest of an ``.etsapp`` package."""

    app_id: str
    name: str
    # Applications it configures: manufacturer, number and version; the manifest leaves out the hash.
    applications: tuple[str, ...]

    def applies_to(self, application_id: str) -> bool:
        return any(application_id.startswith(f"{a}-") for a in self.applications)


def configuration_apps(files: Mapping[str, bytes]) -> list[ConfigurationApp]:
    """The device configuration apps among a project's files (``*.etsapp`` packages)."""
    apps: list[ConfigurationApp] = []
    for name, data in files.items():
        if not name.lower().endswith(".etsapp"):
            continue
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as package:
                root = ET.fromstring(package.read("AddInManifest.xml"))
        except (zipfile.BadZipFile, KeyError, ET.ParseError):
            continue
        applications = tuple(
            match.group(1)
            for element in root.iter("ApplicationProgram")
            if (match := _APPLICATION.fullmatch(element.get("Id", "")))
        )
        if not applications:
            continue
        display = root.find("DisplayNames/DisplayName")
        app_id = root.get("AppId", "")
        apps.append(
            ConfigurationApp(
                app_id=app_id,
                name=display.get("Name", app_id) if display is not None else app_id,
                applications=applications,
            )
        )
    return apps


@dataclass(frozen=True, slots=True)
class DevicePlugins:
    """The plugins of a device: from its application's static ``Extension`` and its hardware."""

    user_interface: bool = False
    download: bool = False
    data_handler: bool = False
    external_software: bool = False
    # The hardware's NoDownloadWithoutPlugin: the device must not be programmed without the plugin.
    download_required: bool = False
    # Name of a device configuration app the project carries for the device's application.
    configuration_app: str | None = None

    @property
    def any(self) -> bool:
        return (
            self.user_interface
            or self.download
            or self.data_handler
            or self.external_software
            or self.download_required
            or self.configuration_app is not None
        )


def device_plugins(device: Device) -> DevicePlugins:
    """The manufacturer plugins `device`'s product data names."""
    program = device.app.program if device.app is not None else None
    static = getattr(program, "static", None)
    extension = getattr(static, "extension", None)
    return DevicePlugins(
        user_interface=bool(getattr(extension, "ets_ui_plugin", None)),
        download=bool(getattr(extension, "ets_download_plugin", None)),
        data_handler=bool(getattr(extension, "ets_data_handler", None)),
        external_software=bool(getattr(extension, "requires_external_software", False)),
        download_required=device.download_plugin_required,
        configuration_app=device.configuration_app,
    )
