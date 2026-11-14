"""Generic product, panel and Powerline configuration paths."""

import pytest

from editor_gui.plugin_providers.powertool import PROVIDER
from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.plugin_export import binary_data_for_export
from editor_gui.plugins.project.service import ProjectService
from packages.vendor.tests.powertool_fixtures import open_archive
from xknxeditor.prod import parse_application_xml, parse_hardware_xml
from xknxeditor.vendor.powertool.family import FamilyEvaluation
from xknxeditor.vendor.powertool.native import load

from .test_powertool import assert_current_blob, current
from .test_powertool_family import family_project


def generic_project(tmp_path, monkeypatch, powerline=False, service=None):
    with open_archive("all-profile-products.zip") as archive:
        if powerline:
            prefix = "host/M-0007/"
            ids = [
                next(
                    n
                    for n in archive.namelist()
                    if n.startswith(prefix + "M-0007_A-6128-" + v)
                )
                for v in ("90-", "A0-")
            ]
        else:
            prefix = "smarttouch/M-0007/"
            hw = parse_hardware_xml(archive.read(prefix + "Hardware.xml"))
            program = next(
                p for p in hw.programs.values() if len(p.application_ref_ids) == 2
            )
            ids = [prefix + i + ".xml" for i in program.application_ref_ids]
        apps = [parse_application_xml(archive.read(i), "M-0007")[0] for i in ids]
    project = service or ProjectService(
        CatalogService(tmp_path / "catalog.xknxcatalog")
    )
    project.set_logger(Logger(LogService(), "project"))
    project.new(tmp_path / "project.xknx")
    monkeypatch.setattr(
        project, "_second_application_source", lambda _: lambda: apps[1]
    )
    monkeypatch.setattr(project, "_resolve_app", lambda *a: apps[0])
    monkeypatch.setattr(project, "_application_source", lambda *a: None)
    node = project.add_device("P-1", "HP-1", "Panel", apps[0])
    assert node is not None
    return project, node


def test_smarttouch_edit_rebuild_undo_and_reopen(tmp_path, monkeypatch):
    project, node = generic_project(tmp_path, monkeypatch)
    project.setup_plugin(current(project, node))
    device = current(project, node)
    assert device.native_error is None
    config = load(project.export_plugin_config(device))
    state = FamilyEvaluation(config)
    control = next(
        c
        for c in state.channels
        if "App_Switch" in c.spec["applications"] and state.channel_exists(c)
    )
    reference = "powertool:family:" + control.path
    before = project.export_plugin_config(device)
    cursor = project.cursor
    project.edit_plugin_parameter(
        device, reference, str(control.spec["applications"].index("App_Switch"))
    )
    assert project.cursor == cursor + 1
    device = current(project, node)
    assert device.native_error is None
    assert PROVIDER.capabilities(device).preflight
    prepared = project.prepared_download(device)
    assert prepared and prepared.programming_allowed
    after = project.export_plugin_config(device)
    project.undo()
    assert project.export_plugin_config(current(project, node)) == before
    project.redo()
    assert project.export_plugin_config(current(project, node)) == after
    project.close()
    project.open(tmp_path / "project.xknx")
    device = current(project, node)
    assert device.native_error is None
    assert project.export_plugin_config(device) == after
    assert project.prepared_download(device).image == prepared.image
    project.close()


@pytest.mark.parametrize("powerline", [False, True])
def test_generic_export_import_keeps_configuration_and_links(
    tmp_path, monkeypatch, powerline
):
    from xknxeditor.proj import export_knxproj, import_knxproj
    from xknxeditor.proj.models import GroupAddress
    from xknxeditor.vendor.powertool import BLOB_NAME

    project, node = generic_project(tmp_path, monkeypatch, powerline=powerline)
    project.setup_plugin(current(project, node))
    device = current(project, node)
    active = assert_current_blob(project, node)
    co = device.get_visible_com_objects()[0]
    ga_id = project.create_group_address("1/2/3", "Linked")
    project.link_com_object_to_ga(co.db_id, ga_id)
    row = next(r for r in project._svc.devices(project._pid) if r.id == node)
    if powerline:
        row.segment.medium_type = "MT-1"
        row.segment.domain_address = 0x12
        ga = project._svc.row(project._pid, GroupAddress, ga_id)
        ga.source_id = "P-1-0_GA-17"
        next(c for c in row.com_objects if c.id == co.db_id).acknowledges = "GA-17"
        project._svc._state(project._pid).session.commit()
        monkeypatch.setattr(
            project._catalog, "hardware_is_powerline_repeater", lambda _: False
        )
    before = project.export_plugin_config(current(project, node))
    image = project.prepared_download(current(project, node)).image
    project.close()
    export_knxproj(
        tmp_path / "project.xknx",
        tmp_path / "export.knxproj",
        schema="23",
        binary_data_transform=binary_data_for_export,
    )
    import_knxproj(tmp_path / "export.knxproj", tmp_path / "import.xknx")
    project.open(tmp_path / "import.xknx")
    device = project.devices[0]
    assert device.native_error is None
    assert project.device_binary_data(device, BLOB_NAME) == before
    assert project.device_binary_data(device, "PT2EtsPlugin") == active
    communication = project.group_communication_for(device)
    assert len(communication.links) == 1
    assert communication.links[0].group_speaker == powerline
    assert project.prepared_download(device).image == image
    project.close()


def test_powerline_project_host_data_and_missing_domain(tmp_path, monkeypatch):
    project, node = generic_project(tmp_path, monkeypatch, powerline=True)
    monkeypatch.setattr(
        project._catalog, "hardware_is_powerline_repeater", lambda _: False
    )
    project.setup_plugin(current(project, node))
    device = current(project, node)
    assert device.native_error is None
    with pytest.raises(ValueError, match="domain address"):
        project.prepared_download(device)
    row = next(r for r in project._svc.devices(project._pid) if r.id == node)
    row.segment.medium_type = "MT-1"
    row.segment.domain_address = 0x1234
    co = device.get_visible_com_objects()[0]
    ga_id = project.create_group_address("1/2/3", "Acknowledged")
    project.link_com_object_to_ga(co.db_id, ga_id)
    from xknxeditor.proj.models import GroupAddress

    ga = project._svc.row(project._pid, GroupAddress, ga_id)
    ga.source_id = "P-1-0_GA-17"
    stored_co = next(c for c in row.com_objects if c.id == co.db_id)
    stored_co.acknowledges = "GA-17"
    communication = project.group_communication_for(current(project, node))
    assert communication.links[0].group_speaker
    prepared = project.prepared_download(device)
    assert prepared.domain_address == 0x1234
    props = {(p.object_index, p.property_id): p.data for p in prepared.image.properties}
    assert props[0, 73] == b"\xff"
    monkeypatch.setattr(
        project._catalog, "hardware_is_powerline_repeater", lambda _: True
    )
    assert (
        next(
            p.data
            for p in project.prepared_download(device).image.properties
            if p.property_id == 73
        )
        == b"\xef"
    )
    project.close()


def test_general_channel_shift_keeps_links_atomically(tmp_path, monkeypatch):
    project, node = family_project(tmp_path, monkeypatch, "DI-5", "PraesenzKomfort")
    project.setup_plugin(current(project, node))
    config = load(project.export_plugin_config(current(project, node)))
    state = FamilyEvaluation(config)
    parent = next(d for d in state.devices if d.path in state.count_limits)
    first = parent.channels[0]
    option = next(
        i
        for i, k in enumerate(first.spec["applications"])
        if state.schema["nodes"][k]["objects"]
    )
    project.edit_plugin_parameter(
        current(project, node), "powertool:family:" + first.path, str(option)
    )
    state = FamilyEvaluation(load(project.export_plugin_config(current(project, node))))
    second = state.nodes[parent.path].channels[1]
    project.edit_plugin_parameter(
        current(project, node), "powertool:family:" + second.path, str(option)
    )
    device = current(project, node)
    config = load(project.export_plugin_config(device))
    role = next(k for k in config.bindings if k.startswith(second.path + "/"))
    co = device.find_com_object(config.bindings[role])
    ga = project.create_group_address("1/2/3", "Survivor")
    assert co and ga is not None
    project.link_com_object_to_ga(co.db_id, ga)
    before = project.export_plugin_config(current(project, node))
    before_blob = assert_current_blob(project, node)
    cursor = project.cursor
    project.edit_plugin_parameter(
        current(project, node), "powertool:family:" + first.path, "0"
    )
    assert project.cursor == cursor + 1
    device = current(project, node)
    config = load(project.export_plugin_config(device))
    shifted_role = first.path + role[len(second.path) :]
    shifted = device.find_com_object(config.bindings[shifted_role])
    assert shifted.db_id == co.db_id
    assert project.get_links_for_com_object(shifted.db_id)[0].group_address_id == ga
    shifted_blob = assert_current_blob(project, node)
    project.undo()
    assert project.export_plugin_config(current(project, node)) == before
    assert assert_current_blob(project, node) == before_blob
    project.redo()
    assert current(project, node).native_error is None
    assert assert_current_blob(project, node) == shifted_blob
    project.close()
    project.open(tmp_path / "project.xknx")
    project.set_flag(current(project, node), shifted.id, "read_on_init", True)
    assert_current_blob(project, node)
    assert project.get_links_for_com_object(co.db_id)[0].group_address_id == ga
    project.undo()
    assert assert_current_blob(project, node) == shifted_blob
    project.close()


def test_powerline_acknowledgement_is_atomic_and_survives_export(tmp_path, monkeypatch):
    from xknxeditor.proj import export_knxproj, import_knxproj

    project, node = generic_project(tmp_path, monkeypatch, powerline=True)
    project.setup_plugin(current(project, node))
    device = current(project, node)
    row = next(r for r in project._svc.devices(project._pid) if r.id == node)
    project._svc.set_fields(
        project._pid,
        [
            [
                "segment",
                row.segment.id,
                {"medium_type": "MT-1", "domain_address": 0x12},
            ],
        ],
    )
    first, second = device.get_visible_com_objects()[:2]
    ga = project.create_group_address("1/2/3", "Acknowledged")
    for co in (first, second):
        project.link_com_object_to_ga(co.db_id, ga)
    project._svc.set_fields(
        project._pid, [["device", node, {"communication_part_loaded": True}]]
    )
    cursor = project.cursor
    project.set_com_object_acknowledgement(first.db_id, ga, True)
    assert project.cursor == cursor + 1
    assert project.com_object_acknowledges(first.db_id, ga)
    assert not row.communication_part_loaded
    project.undo()
    assert not project.com_object_acknowledges(first.db_id, ga)
    assert row.communication_part_loaded
    project.redo()
    project.set_com_object_acknowledgement(second.db_id, ga, True)
    assert not project.com_object_acknowledges(first.db_id, ga)
    assert project.com_object_acknowledges(second.db_id, ga)
    project.undo()
    assert project.com_object_acknowledges(first.db_id, ga)
    project.close()
    export_knxproj(tmp_path / "project.xknx", tmp_path / "export.knxproj", schema="23")
    import_knxproj(tmp_path / "export.knxproj", tmp_path / "import.xknx")
    project.open(tmp_path / "import.xknx")
    device = project.devices[0]
    co = device.find_com_object(first.id)
    linked = project.get_links_for_com_object(co.db_id)[0]
    assert project.com_object_acknowledges(co.db_id, linked.group_address_id)
    project.set_group_address_central(linked.group_address_id, True)
    with pytest.raises(ValueError, match="Central"):
        other = device.find_com_object(second.id)
        project.set_com_object_acknowledgement(
            other.db_id, linked.group_address_id, True
        )
    project.close()


def test_group_address_menu_changes_acknowledgement(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from imgui_bundle import imgui

    from editor_gui.plugins.project.plugin import ProjectPlugin

    project, node = generic_project(tmp_path, monkeypatch, powerline=True)
    project.setup_plugin(current(project, node))
    co = current(project, node).get_visible_com_objects()[0]
    row = next(r for r in project._svc.devices(project._pid) if r.id == node)
    project._svc.set_fields(
        project._pid,
        [["segment", row.segment.id, {"medium_type": "MT-1", "domain_address": 0x12}]],
    )
    ga = project.create_group_address("1/2/3", "Menu")
    project.link_com_object_to_ga(co.db_id, ga)
    items = []
    monkeypatch.setattr(imgui, "menu_item", lambda *a: items.append(a) or (True, True))
    stub = SimpleNamespace(
        _api=SimpleNamespace(project=project),
        _object_group_value=SimpleNamespace(render_menu_items=lambda *a: None),
    )
    cursor = project.cursor
    ProjectPlugin._group_object_ga_menu(stub, ga, co)
    assert items[0][2:] == (False, True)
    assert project.cursor == cursor + 1
    assert project.com_object_acknowledges(co.db_id, ga)
    project.close()
