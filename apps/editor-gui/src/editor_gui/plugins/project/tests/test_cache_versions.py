"""Regression test: the lazy view caches (devices / topology / group addresses) must invalidate
independently.

They used to share a single ``_cache_version``. After an edit, whichever cache was read first
rebuilt and stamped the shared version, so the *other* caches then saw ``version ==`` and returned
stale data until the next edit. Reading ``devices`` right after adding a group address, for example,
hid the new GA from ``group_addresses``.
"""

from __future__ import annotations

from pathlib import Path

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import DeviceProduct, ProjectService


def _project(tmp_path: Path) -> ProjectService:
    proj = ProjectService(CatalogService(tmp_path / "c.xknxcatalog"))
    proj.set_logger(Logger(LogService(), "project"))
    proj.new(tmp_path / "p.xknx")
    return proj


def test_group_addresses_not_stale_after_reading_devices(tmp_path: Path) -> None:
    proj = _project(tmp_path)
    proj.create_group_address(address="1/1/1", name="first")

    # Read a *different* cache first — this used to stamp the shared version and hide later edits.
    _ = proj.devices
    proj.create_group_address(address="31/7/201", name="second")
    _ = proj.devices  # poison: rebuilds devices, would stamp the shared version

    addresses = {g.address for g in proj.group_addresses}
    assert "31/7/201" in addresses
    assert "1/1/1" in addresses


def test_topology_not_stale_after_reading_group_addresses(tmp_path: Path) -> None:
    proj = _project(tmp_path)
    _ = proj.group_addresses
    area_id = proj.create_area(7, "Area 7")
    assert area_id is not None
    _ = proj.group_addresses  # poison

    assert any(a.area_number == 7 for a in proj.get_areas())


def test_create_group_address_value_is_idempotent_and_visible(tmp_path: Path) -> None:
    """``create_group_address_value`` (the recover apply path) must create the GA and
    return the same id on a second call for the same raw value.

    Regression: the idempotency read passed an extra installation argument the core
    ``group_addresses(project_id)`` does not accept, so every call raised ``TypeError``.
    In recover-apply that exception was swallowed per device, leaving the device added
    but no group addresses created.
    """
    proj = _project(tmp_path)
    raw = (1 << 11) | (1 << 8) | 5  # 1/1/5 in the three-level style

    ga_id = proj.create_group_address_value(raw, "recovered")
    assert ga_id is not None
    assert raw in {g.raw for g in proj.group_addresses}

    # Second call for the same value must not duplicate the row.
    assert proj.create_group_address_value(raw) == ga_id
    assert sum(1 for g in proj.group_addresses if g.raw == raw) == 1


def _fixture() -> Path:
    for parent in Path(__file__).resolve().parents:
        cand = (
            parent / "packages/prod/tests/fixtures/gira_2gang_button_interface.knxprod"
        )
        if cand.exists():
            return cand
    raise FileNotFoundError("gira_2gang_button_interface.knxprod not found")


def test_device_added_into_a_space_is_listed(tmp_path: Path) -> None:
    """The catalog's Add with a room picked adds the device and then places it in the room. Placing
    it (a non-structural change) used to mark the device list from before the add as current, so
    the new device was missing from the Topology tree until the project was opened again."""
    cat = CatalogService(tmp_path / "c.xknxcatalog")
    cat.import_knxprod(_fixture())
    product = cat.get_products()[0]
    assert product.application_id is not None
    app = cat.get_application(product.application_id)
    assert app is not None
    proj = ProjectService(cat)
    proj.set_logger(Logger(LogService(), "project"))
    proj.new(tmp_path / "p.xknx")
    room = proj.create_space(None, "Building", "House")
    assert room is not None
    _ = proj.devices  # the Topology tree reads them every frame

    device_id = proj.add_device(
        product.product_ref_id,
        product.hardware2program_ref_id,
        "",
        app,
        product=DeviceProduct.of(product),
    )
    assert device_id is not None
    proj.set_device_space(device_id, room)

    assert device_id in [d.node_id for d in proj.devices]
    proj.close()
