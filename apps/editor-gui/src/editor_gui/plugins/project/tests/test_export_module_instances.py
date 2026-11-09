"""The ``.knxproj`` export of a device whose module instances are not stored (a project saved
before they were stored): the project gives the export the instances of the evaluated
application, so the module's group objects are written with their links and instance."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

import pytest

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService
from editor_gui.plugins.project.tests.test_module_instance_objects import (
    _XML,
    APP,
    INSTANCE,
    SWITCH,
)
from xknxeditor.prod import Application
from xknxeditor.prod.application import parse_application_xml
from xknxeditor.proj.core.knxproj_export import export_knxproj


@pytest.fixture
def app() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


@pytest.fixture
def proj(tmp_path: Path) -> ProjectService:
    service = ProjectService(CatalogService(tmp_path / "c.xknxcatalog"))
    service.set_logger(Logger(LogService(), "project"))
    service.new(tmp_path / "p.xknx")
    return service


def _without_stored_instances(proj: ProjectService, node_id: int) -> None:
    """Make the device look like one of a project saved before module instances were stored."""
    assert proj._pid is not None  # pyright: ignore[reportPrivateUsage]
    core = proj._svc  # pyright: ignore[reportPrivateUsage]
    row = next(r for r in core.devices(proj._pid) if r.id == node_id)  # pyright: ignore[reportPrivateUsage]
    assert [mi.instance_id for mi in row.module_instances] == [INSTANCE]
    session = core._state(proj._pid).session  # pyright: ignore[reportPrivateUsage]
    for mi in list(row.module_instances):
        session.delete(mi)
    session.commit()
    proj._refresh_device(node_id)  # pyright: ignore[reportPrivateUsage]


def test_a_device_without_stored_instances_gives_the_export_its_evaluated_ones(
    proj: ProjectService, app: Application, tmp_path: Path
) -> None:
    node_id = proj.add_device("P-1", "HP-1", "Channel", app)
    assert node_id is not None
    _without_stored_instances(proj, node_id)
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    assert device.module_instances == []
    device.release_dynamic_ui()
    assert set(proj.module_arguments()[node_id]) == {INSTANCE}
    # The evaluation does not keep the evaluator of a device not in use.
    assert not device.evaluated

    # Its module group object is linked; the export writes it with its instance.
    assert proj._pid is not None  # pyright: ignore[reportPrivateUsage]
    core = proj._svc  # pyright: ignore[reportPrivateUsage]
    row = next(r for r in core.devices(proj._pid) if r.id == node_id)  # pyright: ignore[reportPrivateUsage]
    (co,) = row.com_objects
    ga = core.create_group_address(proj._pid, 0, 0x0801, "Light")  # pyright: ignore[reportPrivateUsage]
    core.link_com_object(proj._pid, co.id, ga, sending=True)  # pyright: ignore[reportPrivateUsage]
    out = tmp_path / "out.knxproj"
    export_knxproj(tmp_path / "p.xknx", out, module_arguments=proj.module_arguments())
    with zipfile.ZipFile(out) as zf:
        zero = next(n for n in zf.namelist() if n.endswith("/0.xml"))
        xml = zf.read(zero).decode("utf-8")
    relative = SWITCH.removeprefix(f"{APP}_")
    assert re.search(rf'<ComObjectInstanceRef RefId="{relative}"[^>]*Links="', xml)
    assert re.search(
        rf'<ModuleInstance Id="{INSTANCE.removeprefix(f"{APP}_")}" RefId="MD-1_M-1"',
        xml,
    )


def test_a_device_without_module_values_gets_no_evaluation(
    proj: ProjectService, app: Application
) -> None:
    node_id = proj.add_device("P-1", "HP-1", "Channel", app)
    assert node_id is not None
    # The instance ends: nothing of a module instance is left on the device.
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    proj.set_param(device, f"{APP}_P-1_R-1", "0")
    assert proj.module_arguments() == {}
