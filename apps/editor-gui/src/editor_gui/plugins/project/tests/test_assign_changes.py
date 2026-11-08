"""The values Assigns write in an edit, and the defaults of the targets whose Assigns stop applying,
are stored with the edit as one undo step; an undo keeps the stored values as they are."""

from __future__ import annotations

from pathlib import Path

import pytest

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService
from xknxeditor.prod import Application
from xknxeditor.prod.application import parse_application_xml

APP = "M-00FA_A-0007-01-0000"
ENABLED = f"{APP}_P-1_R-1"
TYPE = f"{APP}_P-2_R-2"
MODE = f"{APP}_P-3_R-3"


def _fixture() -> Path:
    for parent in Path(__file__).resolve().parents:
        cand = (
            parent
            / "packages"
            / "prod"
            / "tests"
            / "fixtures"
            / "fan_coil_application.xml"
        )
        if cand.exists():
            return cand
    raise FileNotFoundError("fan_coil_application.xml not found")


@pytest.fixture
def app() -> Application:
    return parse_application_xml(_fixture().read_bytes(), "M-00FA")[0]


@pytest.fixture
def proj(tmp_path: Path) -> ProjectService:
    service = ProjectService(CatalogService(tmp_path / "c.xknxcatalog"))
    service.set_logger(Logger(LogService(), "project"))
    service.new(tmp_path / "p.xknx")
    return service


def _stored(proj: ProjectService, node_id: int) -> dict[str, str]:
    proj._refresh_device(node_id)
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    return {p.ref_id: p.value for p in device.parameter_instance_refs}


def _value(proj: ProjectService, node_id: int, ref_id: str) -> str | None:
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    device.get_ui()
    return device.get_param_value(ref_id)


def _objects(proj: ProjectService, node_id: int) -> list[str]:
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    return sorted(co.id.rsplit("_", 2)[-2] for co in device.com_objects)


def test_an_edit_stores_what_its_assigns_did(
    proj: ProjectService, app: Application
) -> None:
    node_id = proj.add_device("P-1", "HP-1", "Fan Coil", app)
    assert node_id is not None
    assert _value(proj, node_id, TYPE) == "0"
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    proj.set_param(device, ENABLED, "1")
    # Type's Assign stopped applying: its default; Type 1 assigns Mode 2.
    assert _stored(proj, node_id) == {ENABLED: "1", TYPE: "1", MODE: "2"}
    assert "O-3" in _objects(proj, node_id)

    assert proj.undo()
    assert _stored(proj, node_id) == {}
    assert _value(proj, node_id, TYPE) == "0"
    assert proj.redo()
    assert _stored(proj, node_id) == {ENABLED: "1", TYPE: "1", MODE: "2"}


def test_an_undo_keeps_the_stored_values(
    proj: ProjectService, app: Application
) -> None:
    node_id = proj.add_device("P-1", "HP-1", "Fan Coil", app)
    assert node_id is not None
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    proj.set_param(device, ENABLED, "1")
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    proj.set_param(device, TYPE, "2")
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    proj.set_param(device, ENABLED, "0")
    assert _stored(proj, node_id) == {ENABLED: "0", TYPE: "0", MODE: "1"}
    # Back to Type 2 with the channel on: no reset of Type, although its Assign stops applying.
    assert proj.undo()
    assert _stored(proj, node_id) == {ENABLED: "1", TYPE: "2", MODE: "2"}
    assert _value(proj, node_id, TYPE) == "2"
