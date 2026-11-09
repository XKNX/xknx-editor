"""Sender findings follow effective project flags and module activity."""

import sqlite3
from pathlib import Path

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.health.service import Finding, HealthService
from editor_gui.plugins.health.strings import S
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService
from editor_gui.plugins.project.tests.test_application_change import _setup
from editor_gui.plugins.project.tests.test_module_instance_objects import _XML, ON
from xknxeditor.prod.application import parse_application_xml


def _group_findings(health: HealthService) -> list[Finding]:
    return [f for f in health.findings() if f.category == "group-address"]


def test_inherited_flags_overrides_and_undo_redo(tmp_path: Path) -> None:
    _, project, _ = _setup(tmp_path)
    try:
        device = project.devices[0]
        transmitter = next(
            co for co in device.get_visible_com_objects() if co.flags.transmit
        )
        receiver = next(
            co for co in device.get_visible_com_objects() if not co.flags.transmit
        )
        ga_id = project.create_group_address("1/1/1", "Light", "1.001")
        assert ga_id is not None
        for co in (transmitter, receiver):
            assert co.db_id is not None
            assert (
                project.link_com_object_to_ga(co.db_id, ga_id, is_sending=True)
                is not None
            )
        project.prepare_views()
        health = HealthService(project)
        assert _group_findings(health) == []

        project.set_flag(device, receiver.id, "transmit", True)
        assert receiver.flags.write and receiver.flags.update
        project.prepare_views()
        (finding,) = _group_findings(health)
        assert finding.ga_id == ga_id
        assert finding.message == S.HEALTH_SENDERS(2).format(
            address="1/1/1", name="Light", count=2
        )
        assert project.undo()
        assert _group_findings(health) == []
        assert project.redo()
        assert _group_findings(health) == [finding]

        device = project.devices[0]
        project.set_flag(device, receiver.id, "communication", False)
        assert _group_findings(health) == []
        project.set_flag(device, transmitter.id, "transmit", False)
        assert _group_findings(health) == []
        project.set_flag(device, transmitter.id, "read", False)
        (missing,) = _group_findings(health)
        assert missing.message == S.HEALTH_NO_SENDER.format(
            address="1/1/1", name="Light"
        )
    finally:
        project.close()


def test_missing_product_data_does_not_prove_a_missing_source(tmp_path: Path) -> None:
    _, project, _ = _setup(tmp_path)
    device = project.devices[0]
    co = next(co for co in device.get_visible_com_objects() if not co.flags.transmit)
    ga_id = project.create_group_address("1/1/1", "Light", "1.001")
    assert ga_id is not None and co.db_id is not None
    assert project.link_com_object_to_ga(co.db_id, ga_id, is_sending=True) is not None
    assert len(_group_findings(HealthService(project))) == 1
    project.close()
    path = tmp_path / "p.xknx"
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM product_data")
    reopened = ProjectService(CatalogService(tmp_path / "empty.xknxcatalog"))
    reopened.set_logger(Logger(LogService(), "project"))
    reopened.open(path)
    try:
        assert not reopened.devices and reopened.unloaded_devices
        assert reopened.device_links()
        assert _group_findings(HealthService(reopened)) == []
    finally:
        reopened.close()


def test_module_parameters_and_undo_update_sender_findings(tmp_path: Path) -> None:
    app = parse_application_xml(_XML.encode(), "M-00FA")[0]
    project = ProjectService(CatalogService(tmp_path / "c.xknxcatalog"))
    project.set_logger(Logger(LogService(), "project"))
    project.new(tmp_path / "p.xknx")
    try:
        ga_id = project.create_group_address("1/1/1", "Light", "1.001")
        assert ga_id is not None
        node_ids: list[int] = []
        for name in ("A", "B"):
            node_id = project.add_device("P-1", "HP-1", name, app)
            assert node_id is not None
            node_ids.append(node_id)
            device = project.find_device_by_node_id(node_id)
            assert device is not None
            (co,) = device.get_visible_com_objects()
            assert co.db_id is not None
            project.set_flag(device, co.id, "transmit", True)
            assert (
                project.link_com_object_to_ga(co.db_id, ga_id, is_sending=True)
                is not None
            )
        health = HealthService(project)
        (finding,) = _group_findings(health)
        assert finding.message == S.HEALTH_SENDERS(2).format(
            address="1/1/1", name="Light", count=2
        )
        device = project.find_device_by_node_id(node_ids[-1])
        assert device is not None
        project.set_param(device, ON, "0")
        project.prepare_views()
        assert _group_findings(health) == []
        assert project.undo()
        assert _group_findings(health) == [finding]
    finally:
        project.close()
