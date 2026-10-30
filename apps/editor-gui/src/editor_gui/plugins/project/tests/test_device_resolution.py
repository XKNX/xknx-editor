"""A device loads its application through its hardware program, and a device whose application can
not be loaded stays visible as an unloaded device."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from editor_gui.device import Device, UnloadedDevice
from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import DeviceProduct, ProjectService
from editor_gui.plugins.project.ui.topology import _address_order

_APP_ID = "M-0008_A-7072-21-5CC3-O000A"


def _fixture() -> Path:
    for parent in Path(__file__).resolve().parents:
        cand = (
            parent / "packages/prod/tests/fixtures/gira_2gang_button_interface.knxprod"
        )
        if cand.exists():
            return cand
    raise FileNotFoundError("gira_2gang_button_interface.knxprod not found")


def _open(cat: CatalogService, path: Path) -> ProjectService:
    proj = ProjectService(cat)
    proj.set_logger(Logger(LogService(), "project"))
    proj.open(path)
    return proj


def _project_with_device(tmp_path: Path) -> tuple[Path, int, str]:
    """A saved project with one device of the fixture product; returns its path, id and program."""
    cat = CatalogService(tmp_path / "c.xknxcatalog")
    cat.import_knxprod(_fixture())
    product = next(p for p in cat.get_products() if p.application_id == _APP_ID)
    app = cat.get_application(_APP_ID)
    assert app is not None and product.hardware2program_ref_id is not None
    proj = ProjectService(cat)
    proj.set_logger(Logger(LogService(), "project"))
    path = tmp_path / "p.xknx"
    proj.new(path)
    device_id = proj.add_device(
        product.product_ref_id, product.hardware2program_ref_id, "Button", app
    )
    assert device_id is not None
    proj.close()
    return path, device_id, product.hardware2program_ref_id


def test_program_without_catalog_item_loads(tmp_path: Path) -> None:
    path, device_id, program = _project_with_device(tmp_path)
    # Product data whose Catalog.xml has no item for the program still ingests the program.
    with sqlite3.connect(tmp_path / "c.xknxcatalog") as db:
        db.execute(
            "DELETE FROM catalog_section_products WHERE hardware_program_id = ?",
            (program,),
        )
    cat = CatalogService(tmp_path / "c.xknxcatalog")
    assert all(p.hardware2program_ref_id != program for p in cat.get_products())

    proj = _open(cat, path)
    device = proj.find_device_by_node_id(device_id)
    assert device is not None
    assert device.app is not None
    assert device.app.id == _APP_ID
    assert proj.unloaded_devices == []
    proj.close()


def test_device_without_application_stays_listed(tmp_path: Path) -> None:
    path, device_id, program = _project_with_device(tmp_path)

    proj = _open(CatalogService(tmp_path / "empty.xknxcatalog"), path)
    assert proj.devices == []
    (unloaded,) = proj.unloaded_devices
    assert unloaded == UnloadedDevice(
        node_id=device_id,
        name="Button",
        product_name="",
        individual_address=unloaded.individual_address,
        program_ref=program,
    )
    assert unloaded.individual_address
    assert unloaded.display_name == "Button"

    proj.remove_device(device_id)
    assert proj.unloaded_devices == []
    proj.close()


def test_product_without_application_is_a_device(tmp_path: Path) -> None:
    path, device_id, program = _project_with_device(tmp_path)
    # A product whose Hardware2Program loads no application, as a power supply's.
    with sqlite3.connect(tmp_path / "c.xknxcatalog") as db:
        db.execute(
            "UPDATE hardware_programs SET application_id = NULL WHERE id = ?",
            (program,),
        )

    proj = _open(CatalogService(tmp_path / "c.xknxcatalog"), path)
    (device,) = proj.devices
    assert (device.node_id, device.name, device.app) == (device_id, "Button", None)
    assert not device.has_application
    assert device.get_ui() == []
    assert device.get_visible_com_objects() == []
    assert proj.unloaded_devices == []
    assert proj.missing_program_refs() == []
    proj.close()


def test_a_product_without_application_is_added(tmp_path: Path) -> None:
    cat = CatalogService(tmp_path / "c.xknxcatalog")
    cat.import_knxprod(_fixture())
    product = next(p for p in cat.get_products() if p.application_id == _APP_ID)
    with sqlite3.connect(tmp_path / "c.xknxcatalog") as db:
        db.execute(
            "UPDATE hardware_programs SET application_id = NULL WHERE id = ?",
            (product.hardware2program_ref_id,),
        )
    proj = ProjectService(cat)
    proj.set_logger(Logger(LogService(), "project"))
    proj.new(tmp_path / "p.xknx")
    device_id = proj.add_device(
        product.product_ref_id,
        product.hardware2program_ref_id,
        "",
        None,
        product=DeviceProduct(product_name="Power Supply"),
    )
    assert device_id is not None
    device = proj.find_device_by_node_id(device_id)
    assert device is not None
    assert (device.app, device.display_name) == (None, "Power Supply")
    assert device.individual_address
    assert device.get_visible_com_objects() == []
    assert proj.secure_commissioning_of(device_id) is False
    proj.undo()
    assert proj.find_device_by_node_id(device_id) is None
    proj.close()


def test_display_name_prefers_product_name(tmp_path: Path) -> None:
    cat = CatalogService(tmp_path / "c.xknxcatalog")
    cat.import_knxprod(_fixture())
    app = cat.get_application(_APP_ID)
    assert app is not None

    named = Device(
        node_id=1, name="Hall", product_name="Product", app=app, individual_address=""
    )
    unnamed = Device(
        node_id=2, name="", product_name="Product", app=app, individual_address=""
    )
    bare = Device(node_id=3, name="", app=app, individual_address="")
    assert named.display_name == "Hall"
    assert unnamed.display_name == "Product"
    assert bare.display_name == app.name
    assert UnloadedDevice(4, "", "Product", "1.1.4", None).display_name == "Product"


def test_inserted_device_shows_its_product(tmp_path: Path) -> None:
    cat = CatalogService(tmp_path / "c.xknxcatalog")
    cat.import_knxprod(_fixture())
    product = next(p for p in cat.get_products() if p.application_id == _APP_ID)
    app = cat.get_application(_APP_ID)
    assert app is not None and product.name
    proj = ProjectService(cat)
    proj.set_logger(Logger(LogService(), "project"))
    proj.new(tmp_path / "p.xknx")
    device_id = proj.add_device(
        product.product_ref_id,
        product.hardware2program_ref_id,
        "",
        app,
        product=DeviceProduct.of(product),
    )
    assert device_id is not None
    device = proj.find_device_by_node_id(device_id)
    assert device is not None
    assert (device.name, device.display_name) == ("", product.name)
    info = proj.get_device_info(device_id)
    assert info is not None
    assert info.hardware_name == product.hardware_name
    assert info.order_number == product.order_number

    (copy_id,) = proj.clone_device(device_id)
    copy = proj.find_device_by_node_id(copy_id)
    assert copy is not None
    assert (copy.name, copy.display_name) == ("", product.name)
    proj.close()


def test_tree_orders_devices_by_address() -> None:
    def unloaded(node_id: int, address: str) -> UnloadedDevice:
        return UnloadedDevice(node_id, "", "", address, None)

    devices = [
        unloaded(1, "1.0.49"),
        unloaded(2, "1.0.5"),
        unloaded(3, ""),
        unloaded(4, "1.1.1"),
        unloaded(5, "1.0.4"),
    ]
    ordered = sorted(devices, key=_address_order)
    assert [d.node_id for d in ordered] == [5, 2, 1, 4, 3]


def test_project_devices_ordered_by_address(tmp_path: Path) -> None:
    cat = CatalogService(tmp_path / "c.xknxcatalog")
    cat.import_knxprod(_fixture())
    product = next(p for p in cat.get_products() if p.application_id == _APP_ID)
    app = cat.get_application(_APP_ID)
    assert app is not None and product.hardware2program_ref_id is not None
    proj = ProjectService(cat)
    proj.set_logger(Logger(LogService(), "project"))
    proj.new(tmp_path / "p.xknx")
    ids = [
        proj.add_device(
            product.product_ref_id, product.hardware2program_ref_id, name, app
        )
        for name in ("A", "B", "C")
    ]
    for node_id, number in zip(ids, (49, 5, 10), strict=True):
        assert node_id is not None
        device = proj.find_device_by_node_id(node_id)
        assert device is not None
        line = device.individual_address.rsplit(".", 1)[0]
        assert proj.set_device_individual_address(
            node_id, device.individual_address, f"{line}.{number}"
        )
    assert [d.name for d in proj.devices] == ["B", "C", "A"]
    proj.close()
