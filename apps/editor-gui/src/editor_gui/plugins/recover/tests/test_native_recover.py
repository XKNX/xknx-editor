"""Recovering a device configured through a manufacturer's plug-in (PowerTool): the device's
configuration is read off it with the device, decoded and given to the recovered device, and
an association names the slot of the device's object table."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from editor_gui.plugin_providers import device_reader
from editor_gui.plugins.project.tests.test_powertool import current, native_project
from editor_gui.plugins.recover.service import RecoverService
from xknxeditor.vendor.powertool.native import compile_native, for_product, load
from xknxeditor.vendor.powertool.recover import DeviceImage, image_of


def _held(proj: Any, node_id: int) -> DeviceImage:
    """What the device holds after a download of its configuration."""
    device = current(proj, node_id)
    config = load(proj.export_plugin_config(device))
    assert device.app is not None
    secondary = device.second_application() if device.second_application else None
    return image_of(compile_native(for_product(config, device.app, secondary)))


@pytest.mark.parametrize("variant", ["01", "02"])
def test_the_configuration_read_off_a_device_is_the_recovered_devices(
    tmp_path, monkeypatch, variant
):
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
    configured, node_id = native_project(tmp_path / "a", monkeypatch, variant=variant)
    configured.setup_plugin(current(configured, node_id))
    configured.edit_plugin_parameter(
        current(configured, node_id), "powertool:0:function", "dim_rocker"
    )
    image = _held(configured, node_id)

    project, recovered_id = native_project(tmp_path / "b", monkeypatch, variant=variant)
    device = current(project, recovered_id)
    reader = device_reader(device.app)
    assert reader is not None
    service = cast(Any, SimpleNamespace(_api=SimpleNamespace(project=project)))
    result = RecoverService._apply_native(
        service, device, cast(Any, SimpleNamespace(native_image=image)), device.app
    )
    assert result is not None
    device, slots = result
    assert _held(project, recovered_id) == image
    # Each slot of the object table names a group object of the recovered device.
    assert slots
    assert all(device.find_com_object(ref) is not None for ref in slots.values())


def test_a_device_without_a_plug_in_reads_nothing_of_its_own(tmp_path, monkeypatch):
    project, node_id = native_project(tmp_path, monkeypatch)
    device = current(project, node_id)
    service = cast(Any, SimpleNamespace(_api=SimpleNamespace(project=project)))
    assert (
        RecoverService._apply_native(
            service, device, cast(Any, SimpleNamespace(native_image=None)), device.app
        )
        is None
    )
    assert device_reader(SimpleNamespace(id="M-00FA_A-0001-01-0000")) is None
