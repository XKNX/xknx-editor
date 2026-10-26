"""The IP settings of an IP device in the GUI service: only for IP devices, changed with their
history texts, rejected addresses, and the routing multicast address on an IP medium."""

from __future__ import annotations

from pathlib import Path

import pytest

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService
from editor_gui.plugins.project.strings import S
from xknxeditor.prod import Application
from xknxeditor.prod.application import parse_application_xml


def _fixture() -> Path:
    for parent in Path(__file__).resolve().parents:
        cand = parent / "packages/prod/tests/fixtures/calculations_application.xml"
        if cand.exists():
            return cand
    raise FileNotFoundError("calculations_application.xml not found")


@pytest.fixture
def app() -> Application:
    return parse_application_xml(_fixture().read_bytes(), "M-00FA")[0]


@pytest.fixture
def proj(tmp_path: Path) -> ProjectService:
    service = ProjectService(CatalogService(tmp_path / "c.xknxcatalog"))
    service.set_logger(Logger(LogService(), "project"))
    service.new(tmp_path / "p.xknx")
    return service


def _ip_device(
    proj: ProjectService,
    app: Application,
    monkeypatch: pytest.MonkeyPatch,
    ip_config: str = "Tool",
) -> int:
    monkeypatch.setattr(proj._catalog, "hardware_is_ip_enabled", lambda _ref: True)
    monkeypatch.setattr(proj._catalog, "ip_config", lambda _ref: ip_config)
    node_id = proj.add_device("P-1", "HP-1", "Router", app)
    assert node_id is not None
    return node_id


def test_only_ip_devices_have_ip_settings(
    proj: ProjectService, app: Application
) -> None:
    node_id = proj.add_device("P-1", "HP-1", "Dimmer", app)
    assert node_id is not None
    assert proj.ip_settings(node_id) is None
    entries = len(proj.history())
    assert proj.set_ip_settings(node_id, assign="Fixed") is None
    assert len(proj.history()) == entries


def test_changes_with_their_history_texts(
    proj: ProjectService, app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    node_id = _ip_device(proj, app, monkeypatch)
    settings = proj.ip_settings(node_id)
    assert settings is not None
    assert (settings.tool_managed, settings.assign, settings.ip_address) == (
        True,
        "Auto",
        None,
    )
    assert settings.multicast_address is None
    assert proj.set_ip_settings(node_id, assign="Fixed") is None
    # The newest entry comes first.
    assert proj.history()[0].display_text == S.HISTORY_IP_CONFIG_CHANGED.format(
        "Router"
    )
    assert proj.set_ip_settings(node_id, ip_address="10.1") is None
    assert proj.history()[0].display_text == S.HISTORY_IP_ADDRESS_CHANGED.format(
        "Router", "10.0.0.1"
    )
    settings = proj.ip_settings(node_id)
    assert settings is not None
    assert (settings.assign, settings.ip_address) == ("Fixed", "10.0.0.1")


def test_an_unreadable_address_is_rejected(
    proj: ProjectService, app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    node_id = _ip_device(proj, app, monkeypatch, "Custom")
    entries = len(proj.history())
    assert proj.set_ip_settings(node_id, ip_address="300.1.1.1") == (
        S.CONFIGURE_IP_INVALID
    )
    assert len(proj.history()) == entries
    settings = proj.ip_settings(node_id)
    assert settings is not None
    assert settings.tool_managed is False


def test_the_multicast_address_on_an_ip_medium_or_a_coupler(
    proj: ProjectService, app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    node_id = _ip_device(proj, app, monkeypatch)
    monkeypatch.setattr(proj, "segment_medium", lambda _segment: ("MT-5", True))
    proj._bump(structural=False)
    settings = proj.ip_settings(node_id)
    assert settings is not None
    assert settings.multicast_address == "224.0.23.12"
    monkeypatch.setattr(proj, "segment_medium", lambda _segment: ("MT-0", True))
    app.program.mask_version = "MV-091A"
    proj._bump(structural=False)
    settings = proj.ip_settings(node_id)
    assert settings is not None
    assert settings.multicast_address == "224.0.23.12"


def test_a_download_finds_an_automatic_address_once(
    proj: ProjectService, app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    node_id = _ip_device(proj, app, monkeypatch)
    # Without a serial number there is nothing to search for.
    assert proj.serial_to_find_ip(node_id) is None
    serial = bytes.fromhex("00fa12345678")
    proj.record_download(node_id, "full", serial)
    assert proj.serial_to_find_ip(node_id) == serial
    proj.record_found_ip(node_id, "192.168.16.103")
    settings = proj.ip_settings(node_id)
    assert settings is not None
    assert (settings.assign, settings.ip_address) == ("Auto", "192.168.16.103")
    # Kept with the download's record, not as an edit.
    assert proj.history()[0].display_text == S.HISTORY_EV_SET_COMMISSIONING
    # A known address is kept.
    assert proj.serial_to_find_ip(node_id) is None
    proj.record_found_ip(node_id, "192.168.16.104")
    settings = proj.ip_settings(node_id)
    assert settings is not None
    assert settings.ip_address == "192.168.16.103"


def test_a_static_address_is_not_searched_for(
    proj: ProjectService, app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    node_id = _ip_device(proj, app, monkeypatch)
    proj.record_download(node_id, "full", bytes.fromhex("00fa12345678"))
    proj.set_ip_settings(node_id, assign="Fixed")
    assert proj.serial_to_find_ip(node_id) is None
    proj.record_found_ip(node_id, "192.168.16.103")
    settings = proj.ip_settings(node_id)
    assert settings is not None
    assert settings.ip_address is None
