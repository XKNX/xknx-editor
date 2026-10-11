"""Manufacturer plugins a product's data asks for, which XKNX Editor cannot run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from editor_gui.device import Device


@dataclass(frozen=True, slots=True)
class DevicePlugins:
    """The plugins of a device: from its application's static ``Extension`` and its hardware."""

    user_interface: bool = False
    download: bool = False
    data_handler: bool = False
    external_software: bool = False
    # The hardware's NoDownloadWithoutPlugin: the device must not be programmed without the plugin.
    download_required: bool = False

    @property
    def any(self) -> bool:
        return (
            self.user_interface
            or self.download
            or self.data_handler
            or self.external_software
            or self.download_required
        )


def device_plugins(device: Device) -> DevicePlugins:
    """The manufacturer plugins `device`'s product data names."""
    static = getattr(device.app.program, "static", None)
    extension = getattr(static, "extension", None)
    return DevicePlugins(
        user_interface=bool(getattr(extension, "ets_ui_plugin", None)),
        download=bool(getattr(extension, "ets_download_plugin", None)),
        data_handler=bool(getattr(extension, "ets_data_handler", None)),
        external_software=bool(getattr(extension, "requires_external_software", False)),
        download_required=device.download_plugin_required,
    )
