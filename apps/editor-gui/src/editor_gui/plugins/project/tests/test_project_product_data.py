"""A project carries the product data of its devices: a device added from the catalog brings its
product data into the project file, and a project opened where the catalog lacks a product imports
it from there, so its devices are editable on any computer."""

from __future__ import annotations

import io
import sqlite3
import zipfile
from pathlib import Path

import pytest

import editor_gui.master_data
from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService

_APP_ID = "M-0008_A-7072-21-5CC3-O000A"


def _fixture() -> Path:
    for parent in Path(__file__).resolve().parents:
        cand = (
            parent / "packages/prod/tests/fixtures/gira_2gang_button_interface.knxprod"
        )
        if cand.exists():
            return cand
    raise FileNotFoundError("gira_2gang_button_interface.knxprod not found")


@pytest.fixture(autouse=True)
def _master_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """The app's master data without a download: the fixture product's own."""
    with zipfile.ZipFile(_fixture()) as zf:
        master = zf.read("knx_master.xml")
    monkeypatch.setattr(editor_gui.master_data, "master_xml_bytes", lambda: master)


def _service(catalog: CatalogService) -> ProjectService:
    proj = ProjectService(catalog)
    proj.set_logger(Logger(LogService(), "project"))
    return proj


def _members(path: Path) -> dict[str, bytes]:
    with sqlite3.connect(path) as db:
        (blob,) = db.execute("SELECT product_members FROM projects").fetchone()
    if blob is None:
        return {}
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def _project_with_device(tmp_path: Path) -> tuple[Path, int, str]:
    """A project with one device of the fixture product, added from a catalog that has it."""
    catalog = CatalogService(tmp_path / "c.xknxcatalog")
    catalog.import_knxprod(_fixture())
    product = next(p for p in catalog.get_products() if p.application_id == _APP_ID)
    app = catalog.get_application(_APP_ID)
    assert app is not None and product.hardware2program_ref_id is not None
    proj = _service(catalog)
    path = tmp_path / "p.xknx"
    proj.new(path)
    device_id = proj.add_device(
        product.product_ref_id, product.hardware2program_ref_id, "Button", app
    )
    assert device_id is not None
    proj.close()
    return path, device_id, product.hardware2program_ref_id


def test_added_device_brings_its_product_data(tmp_path: Path) -> None:
    path, _device_id, program = _project_with_device(tmp_path)
    members = _members(path)
    # The device's hardware, product and program, its application program and its catalog entry.
    assert f"M-0008/{_APP_ID}.xml" in members
    assert program.encode() in members["M-0008/Hardware.xml"]
    assert "M-0008/Catalog.xml" in members
    # Only the manufacturer's data, no master data (that is the app's).
    assert all(name.startswith("M-0008") for name in members)


def test_project_opens_with_its_product_data_on_another_computer(
    tmp_path: Path,
) -> None:
    path, device_id, program = _project_with_device(tmp_path)

    # A computer whose catalog has never seen the product.
    other = CatalogService(tmp_path / "other.xknxcatalog")
    assert other.get_program_source(program) is None
    proj = _service(other)
    proj.open(path)
    (device,) = proj.devices
    assert device.node_id == device_id
    assert device.app is not None and device.app.id == _APP_ID
    assert proj.unloaded_devices == []
    # The catalog has the product now, for further devices of it too.
    assert other.get_program_source(program) is not None
    proj.close()


def test_without_master_data_the_devices_stay_unloaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, device_id, _program = _project_with_device(tmp_path)
    monkeypatch.setattr(editor_gui.master_data, "master_xml_bytes", lambda: None)

    proj = _service(CatalogService(tmp_path / "other.xknxcatalog"))
    proj.open(path)
    assert proj.devices == []
    assert [d.node_id for d in proj.unloaded_devices] == [device_id]
    proj.close()
    # The project keeps its product data for the next open.
    assert f"M-0008/{_APP_ID}.xml" in _members(path)


def test_older_project_gets_the_product_data_when_opened(tmp_path: Path) -> None:
    """A project saved before it carried product data gets it from the catalog it is opened with."""
    path, _device_id, _program = _project_with_device(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE projects SET product_members = NULL")

    proj = _service(CatalogService(tmp_path / "c.xknxcatalog"))
    proj.open(path)
    proj.close()
    assert f"M-0008/{_APP_ID}.xml" in _members(path)


def test_device_without_product_file_leaves_the_project_as_it_is(
    tmp_path: Path,
) -> None:
    path, _device_id, _program = _project_with_device(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE projects SET product_members = NULL")

    # Neither the catalog nor the project has the product: nothing to add, nothing fails.
    proj = _service(CatalogService(tmp_path / "other.xknxcatalog"))
    proj.open(path)
    proj.set_device_name(proj.unloaded_devices[0].node_id, "Button", "Renamed")
    proj.close()
    assert _members(path) == {}


def test_compact_removes_the_product_data_of_removed_devices(tmp_path: Path) -> None:
    path, device_id, _program = _project_with_device(tmp_path)
    proj = _service(CatalogService(tmp_path / "c.xknxcatalog"))
    proj.open(path)
    # A removed device keeps its product data (undo brings the device back) ...
    proj.remove_device(device_id)
    assert _members(path)
    # ... until the project is compacted.
    proj.compact()
    proj.close()
    assert _members(path) == {}
