"""Settings > General > Memory Use: the devices kept evaluated are the ones used last, also those
evaluated outside the device selection (an MCP tool, a check, an export), and never the selected
ones (#359)."""

from __future__ import annotations

from pathlib import Path

import pytest

from editor_gui.device import Device
from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService
from editor_gui.plugins.project.tests.test_module_instance_objects import _XML
from xknxeditor.prod import Application
from xknxeditor.prod.application import parse_application_xml


@pytest.fixture
def app() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


@pytest.fixture
def proj(tmp_path: Path) -> ProjectService:
    service = ProjectService(CatalogService(tmp_path / "c.xknxcatalog"))
    service.set_logger(Logger(LogService(), "project"))
    service.new(tmp_path / "p.xknx")
    return service


def _devices(proj: ProjectService, app: Application, count: int) -> list[Device]:
    ids = [proj.add_device("P-1", "HP-1", f"Device {n}", app) for n in range(count)]
    devices = [proj.find_device_by_node_id(i) for i in ids if i is not None]
    assert len(devices) == count
    # As after opening the project: nothing evaluated.
    proj.keep_resident_devices()
    for device in devices:
        assert device is not None
        device.release_dynamic_ui()
    return [d for d in devices if d is not None]


def _evaluated(devices: list[Device]) -> list[bool]:
    return [d.evaluated for d in devices]


def test_devices_evaluated_outside_the_selection_are_released(
    proj: ProjectService, app: Application
) -> None:
    proj.set_resident_devices(1)
    shown, first, second = _devices(proj, app, 3)
    proj.selected_device = shown
    shown.get_ui()
    # Evaluated without being selected, as MCP project_list_parameters does.
    first.get_ui()
    second.get_ui()
    assert _evaluated([shown, first, second]) == [True, True, True]
    proj.keep_resident_devices()
    assert _evaluated([shown, first, second]) == [True, False, False]


def test_the_devices_used_last_are_kept(proj: ProjectService, app: Application) -> None:
    proj.set_resident_devices(2)
    shown, first, second = _devices(proj, app, 3)
    proj.selected_device = shown
    shown.get_ui()
    first.get_ui()
    second.get_ui()
    proj.keep_resident_devices()
    # The selected device and the one evaluated last.
    assert _evaluated([shown, first, second]) == [True, False, True]
    # Evaluated again: it is the one used last now.
    first.get_ui()
    proj.keep_resident_devices()
    assert _evaluated([shown, first, second]) == [True, True, False]


def test_selected_devices_are_never_released(
    proj: ProjectService, app: Application
) -> None:
    proj.set_resident_devices(1)
    one, two, other = _devices(proj, app, 3)
    proj.set_multi_selection(one.node_id, [one.node_id, two.node_id])
    one.get_ui()
    two.get_ui()
    other.get_ui()
    proj.keep_resident_devices()
    assert _evaluated([one, two, other]) == [True, True, False]


def test_a_device_released_by_its_user_takes_no_place(
    proj: ProjectService, app: Application
) -> None:
    proj.set_resident_devices(2)
    shown, kept, passing = _devices(proj, app, 3)
    proj.selected_device = shown
    shown.get_ui()
    kept.get_ui()
    proj.keep_resident_devices()
    # Evaluated and released again by the code that needed it (as the exports do).
    passing.get_ui()
    passing.release_dynamic_ui()
    proj.keep_resident_devices()
    assert _evaluated([shown, kept, passing]) == [True, True, False]
