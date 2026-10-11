"""Manufacturer plugins named by a product: detection and the download refusal."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

from editor_gui.device_plugins import DevicePlugins, device_plugins
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.connection.service import ConnectionService


def _device(extension: object | None, *, required: bool = False) -> Any:
    program = SimpleNamespace(static=SimpleNamespace(extension=extension))
    return SimpleNamespace(
        app=SimpleNamespace(program=program),
        download_plugin_required=required,
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
