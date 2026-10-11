"""Manufacturer plugins named by a product: detection and the download refusal."""

from __future__ import annotations

import io
import threading
import zipfile
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

from editor_gui.device_plugins import DevicePlugins, configuration_apps, device_plugins
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.connection.service import ConnectionService


def _device(extension: object | None, *, required: bool = False) -> Any:
    program = SimpleNamespace(static=SimpleNamespace(extension=extension))
    return SimpleNamespace(
        app=SimpleNamespace(program=program),
        download_plugin_required=required,
        configuration_app=None,
        individual_address="1.1.1",
        name="Dev",
    )


def test_no_extension_means_no_plugins() -> None:
    assert device_plugins(_device(None)) == DevicePlugins()
    assert not device_plugins(_device(None)).any


def test_plugins_from_the_extension_and_hardware() -> None:
    extension = SimpleNamespace(
        ets_ui_plugin="{A}",
        ets_download_plugin="{B}",
        ets_data_handler=None,
        requires_external_software=True,
    )
    plugins = device_plugins(_device(extension, required=True))
    assert plugins == DevicePlugins(
        user_interface=True,
        download=True,
        data_handler=False,
        external_software=True,
        download_required=True,
    )
    assert plugins.any


def test_program_device_refuses_a_device_that_needs_its_plugin() -> None:
    service = ConnectionService()
    service._xknx = MagicMock()  # pyright: ignore[reportPrivateUsage]
    service.set_logger(MagicMock())
    device = _device(None, required=True)
    assert service.program_device(device) is None
    assert service.busy_operation is None


def test_catalog_reads_the_hardware_flag() -> None:
    catalog = cast(Any, object.__new__(CatalogService))
    catalog._io_lock = threading.RLock()
    catalog._service = MagicMock()
    catalog._service.get_hardware.return_value = SimpleNamespace(
        no_download_without_plugin=True
    )
    assert catalog.requires_download_plugin("M-0083_H-89-1_HP-0057-10-DCE9")
    catalog._service.get_hardware.assert_called_once_with("M-0083_H-89-1")
    catalog._service.get_hardware.return_value = None
    assert not catalog.requires_download_plugin("M-0083_H-89-1_HP-0057-10-DCE9")


_MANIFEST = """<?xml version="1.0" encoding="utf-8" ?>
<Knxapp AppId="M0083-A0158" AddInAssembly="Mdt-DaliDCAPlus.dll">
  <DisplayNames><DisplayName LanguageId="en-US" Name="SCN-DA641(2).04" /></DisplayNames>
  <DeviceConfiguration>
    <ApplicationProgram Id="M-0083_A-0154-40-0000" />
    <ApplicationProgram Id="M-0083_A-0155-40-0000" />
  </DeviceConfiguration>
</Knxapp>
"""


def _etsapp(manifest: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        package.writestr("AddInManifest.xml", manifest)
        package.writestr("Mdt-DaliDCAPlus.dll", b"MZ")
    return buffer.getvalue()


def test_configuration_apps_from_embedded_packages() -> None:
    files = {
        "UserFiles/M0083-A0158.etsapp": _etsapp(_MANIFEST),
        "UserFiles/plan.pdf": b"%PDF",
        "UserFiles/broken.etsapp": b"not a zip",
    }
    (app,) = configuration_apps(files)
    assert (app.app_id, app.name) == ("M0083-A0158", "SCN-DA641(2).04")
    assert app.applies_to("M-0083_A-0154-40-0F69-O00EF")
    assert app.applies_to("M-0083_A-0155-40-1234")
    assert not app.applies_to("M-0083_A-0154-41-0F69")
    assert not app.applies_to("M-0083_A-01540-40-0F69")
