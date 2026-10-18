"""Additional individual addresses in the GUI service: assigned with a new device from its
application, edited with history labels, skipped by the next free address and handed to the
IP download."""

from __future__ import annotations

from pathlib import Path

import pytest

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import (
    ProjectService,
    _history_label,
    additional_address_error_text,
)
from editor_gui.plugins.project.strings import S
from xknxeditor.prod import Application
from xknxeditor.prod.application import parse_application_xml
from xknxeditor.proj import AdditionalAddressError


def _fixture() -> Path:
    for parent in Path(__file__).resolve().parents:
        cand = parent / "packages/prod/tests/fixtures/calculations_application.xml"
        if cand.exists():
            return cand
    raise FileNotFoundError("calculations_application.xml not found")


@pytest.fixture
def app() -> Application:
    application = parse_application_xml(_fixture().read_bytes(), "M-00FA")[0]
    application.program.additional_addresses_count = 8
    return application


@pytest.fixture
def proj(tmp_path: Path) -> ProjectService:
    service = ProjectService(CatalogService(tmp_path / "c.xknxcatalog"))
    service.set_logger(Logger(LogService(), "project"))
    service.new(tmp_path / "p.xknx")
    return service


def _add(proj: ProjectService, app: Application, name: str = "Interface") -> int:
    node_id = proj.add_device("P-1", "HP-1", name, app)
    assert node_id is not None
    return node_id


def _line(proj: ProjectService, node_id: int) -> str:
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    return device.individual_address.rsplit(".", 1)[0]


def test_add_device_assigns_the_application_addresses(
    proj: ProjectService, app: Application
) -> None:
    node_id = _add(proj, app)
    line = _line(proj, node_id)
    assert [e.address for e in proj.additional_addresses(node_id)] == [
        f"{line}.{n}" for n in range(255, 247, -1)
    ]
    second = _add(proj, app, "Second")
    assert proj.additional_addresses(second)[0].address == f"{line}.247"
    # Undo removes the second device together with its addresses.
    proj.undo()
    assert proj.find_device_by_node_id(second) is None
    assert proj.get_device_info(second) is None


def test_device_added_without_addresses_when_the_line_is_full(
    proj: ProjectService, app: Application
) -> None:
    app.program.additional_addresses_count = 255
    node_id = _add(proj, app)
    assert proj.additional_addresses(node_id) == []


def test_set_address_name_and_description_with_history(
    proj: ProjectService, app: Application
) -> None:
    node_id = _add(proj, app)
    line = _line(proj, node_id)
    proj.set_additional_address(node_id, 1, "200")
    assert proj.history()[0].display_text == (
        f"Changed additional address of device 'Interface' from '{line}.255' to "
        f"'{line}.200'."
    )
    proj.set_additional_address_name(node_id, 1, "Visualisation")
    assert proj.history()[0].display_text == S.HISTORY_ADDITIONAL_ADDRESS_NAMES
    proj.set_additional_address_description(node_id, 1, "Tablet")
    assert proj.history()[0].display_text == (S.HISTORY_ADDITIONAL_ADDRESS_DESCRIPTIONS)
    entry = proj.additional_addresses(node_id)[0]
    assert (entry.address, entry.name, entry.description) == (
        f"{line}.200",
        "Visualisation",
        "Tablet",
    )
    with pytest.raises(AdditionalAddressError) as error:
        proj.set_additional_address(node_id, 2, f"{line}.200")
    assert additional_address_error_text(error.value) == (
        S.ADDITIONAL_ADDRESS_IN_USE.format(address=f"{line}.200")
    )
    proj.undo()
    proj.undo()
    proj.undo()
    assert proj.additional_addresses(node_id)[0].address == f"{line}.255"
    proj.redo()
    assert proj.additional_addresses(node_id)[0].address == f"{line}.200"


def test_assign_missing_addresses(proj: ProjectService, app: Application) -> None:
    node_id = _add(proj, app)
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    proj.set_additional_address(node_id, 3, None)
    assert proj.additional_addresses(node_id)[2].address is None
    proj.assign_additional_addresses(device)
    assert proj.history()[0].display_text == S.HISTORY_ASSIGN_ADDITIONAL_ADDRESSES
    assert all(e.address for e in proj.additional_addresses(node_id))
    with pytest.raises(AdditionalAddressError) as error:
        proj.assign_additional_addresses(device, 255)
    assert additional_address_error_text(error.value) == (
        S.ADDITIONAL_ADDRESSES_NOT_ENOUGH
    )


def test_next_free_device_number_skips_additional_addresses(
    proj: ProjectService, app: Application
) -> None:
    node_id = _add(proj, app)
    area, line = (int(part) for part in _line(proj, node_id).split("."))
    proj.set_additional_address(node_id, 1, "2")
    proj.set_additional_address(node_id, 2, "3")
    assert proj.next_free_device_number(area, line) == 4
    assert proj.next_free_device_number(area, 99) is None


def test_ip_download_gets_the_addresses(
    proj: ProjectService, app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    node_id = _add(proj, app)

    def ip_enabled(_ref: str) -> bool:
        return True

    monkeypatch.setattr(proj._catalog, "hardware_is_ip_enabled", ip_enabled)
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    config = proj.ip_download_config(device)
    assert config is not None
    assert tuple(config.additional_addresses) == tuple(range(255, 247, -1))


def test_history_label_without_a_label() -> None:
    assert (
        _history_label("SetDeviceAdditionalAddresses", {"device_id": 1, "entries": []})
        == S.HISTORY_ADDITIONAL_ADDRESSES
    )
