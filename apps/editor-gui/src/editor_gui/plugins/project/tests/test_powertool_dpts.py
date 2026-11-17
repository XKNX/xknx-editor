"""Stored configurations enrich object types without changing the project."""

import pytest

from editor_gui.plugin_providers.powertool import PROVIDER
from xknxeditor.vendor.powertool import BLOB_NAME, ChannelConfig, PowerToolConfig
from xknxeditor.vendor.powertool.project_writer import write_project_blob

from .test_powertool import current, native_project


def stored_project(
    tmp_path, monkeypatch, function="dim_rocker", values=None, service=None
):
    project, nid = native_project(tmp_path, monkeypatch, service, variant="02")
    config = PowerToolConfig(
        "02",
        (ChannelConfig(function, values or {}),) + (ChannelConfig("inactive"),) * 3,
    )
    original = write_project_blob(None, config)
    project.persist_device_blob(current(project, nid), "PT2EtsPlugin", original)
    state = PROVIDER.read(current(project, nid))
    project._svc.set_parameters(project._pid, nid, list(state.parameters.items()))
    project._svc.sync_device_com_objects(
        project._pid, nid, [(o.reference, None) for o in state.objects]
    )
    for row in project._svc.devices(project._pid)[0].com_objects:
        ga = project.create_group_address(f"1/0/{row.id}", "Linked object")
        project._svc.link_com_object(project._pid, row.id, ga, sending=True)
    project._refresh_device(nid)
    project._bump(structural=False)
    return project, nid, original


@pytest.mark.parametrize(
    ("function", "values", "expected"),
    [
        ("switch_rocker", {}, ["1.001"]),
        ("dim_rocker", {}, ["1.001", "3.007"]),
        ("blind_rocker", {}, ["1.008", "1.007"]),
        ("blind_rocker", {"object_type": 1}, ["5.001", "5.001"]),
        ("scene_button", {}, ["18.001"]),
    ],
)
def test_stored_roles_supply_dpts_without_mutation(
    tmp_path, monkeypatch, function, values, expected
):
    project, nid, original = stored_project(tmp_path, monkeypatch, function, values)
    device = current(project, nid)
    cursor = project.cursor
    links = project.group_communication_for(device).links
    image = project.prepared_download(device).image
    objects = device.get_visible_com_objects()
    identities = [(co.id, co.db_id, co.flags) for co in objects]
    assert [co.dpt.code for co in objects] == expected
    assert all(co.dpt in co.supported_dpts for co in objects)
    assert all(co.datapoint_type_override is None for co in objects)
    assert device.native_objects is None
    device.release_dynamic_ui()
    assert [co.dpt.code for co in device.get_visible_com_objects()] == expected
    assert [(co.id, co.db_id, co.flags) for co in device.com_objects] == identities
    assert project.prepared_download(device).image == image
    assert project.group_communication_for(device).links == links
    assert project.cursor == cursor
    assert project.device_binary_data(device, BLOB_NAME) is None
    assert project.device_binary_data(device, "PT2EtsPlugin") == original
    project.close()
    project.open(tmp_path / "project.xknx")
    device = current(project, nid)
    assert [co.dpt.code for co in device.get_visible_com_objects()] == expected
    assert project.group_communication_for(device).links == links
    assert project.device_binary_data(device, "PT2EtsPlugin") == original
    project.close()


@pytest.mark.parametrize("explicit", ["DPT-1", "DPST-1-2"])
def test_stored_dpt_never_replaces_an_explicit_type(tmp_path, monkeypatch, explicit):
    project, nid, _ = stored_project(tmp_path, monkeypatch, "switch_rocker")
    row = project._svc.devices(project._pid)[0].com_objects[0]
    row.datapoint_type_override = explicit
    project._refresh_device(nid)
    device = current(project, nid)
    assert device.get_visible_com_objects()[0].dpt.token == explicit
    assert "1.001" not in [d.code for d in device.com_objects[0].supported_dpts]
    assert row.datapoint_type_override == explicit
    project.close()


def test_replacing_only_stored_blob_invalidates_the_object_view(tmp_path, monkeypatch):
    from dataclasses import replace

    from editor_gui.plugin_providers.powertool import configuration

    project, nid, original = stored_project(tmp_path, monkeypatch, "switch_rocker")
    device = current(project, nid)
    config = replace(
        configuration(device),
        channels=(ChannelConfig("blind_rocker"),) + (ChannelConfig("inactive"),) * 3,
    )
    project.persist_device_blob(
        device, "PT2EtsPlugin", write_project_blob(None, config)
    )
    device = current(project, nid)
    assert [co.dpt.code for co in device.get_visible_com_objects()] == ["1.008"]
    project.undo()
    device = current(project, nid)
    assert project.device_binary_data(device, "PT2EtsPlugin") == original
    assert [co.dpt.code for co in device.get_visible_com_objects()] == ["1.001"]
    project.redo()
    assert [co.dpt.code for co in current(project, nid).get_visible_com_objects()] == [
        "1.008"
    ]
    project.close()


def test_ambiguous_stored_role_does_not_supply_a_type(tmp_path, monkeypatch):
    from editor_gui.dpt import lookup_or_make_dpt
    from editor_gui.plugin_providers.powertool import apply_stored_object_dpts
    from xknxeditor.namespaces.intermediate import ComObjectInstanceRef

    project, nid, _ = stored_project(tmp_path, monkeypatch, "blind_rocker")
    device = current(project, nid)
    first = device.get_visible_com_objects()[0]
    first.dpt = lookup_or_make_dpt("1")
    device.com_object_instance_refs.append(ComObjectInstanceRef(ref_id=first.id))
    apply_stored_object_dpts(device)
    assert first.dpt.token == "DPT-1"
    project.close()


def test_stored_configuration_is_evaluated_before_snapshot_lookup(
    tmp_path, monkeypatch
):
    from unittest.mock import Mock

    project, nid, _ = stored_project(tmp_path, monkeypatch)
    lookup = Mock(side_effect=AssertionError("stale catalog snapshot"))
    monkeypatch.setattr(project, "_stored_snapshot", lookup)
    project._refresh_device(nid)
    assert [co.dpt.code for co in current(project, nid).get_visible_com_objects()] == [
        "1.001",
        "3.007",
    ]
    lookup.assert_not_called()
    project.close()


@pytest.mark.parametrize("corrupt", ["truncated", "unknown_database", "bad_header"])
def test_unreadable_stored_config_keeps_catalog_objects(tmp_path, monkeypatch, corrupt):
    project, nid, original = stored_project(tmp_path, monkeypatch, "blind_rocker")
    identity = [o.id for o in current(project, nid).com_objects]
    data = {
        "truncated": original[:100],
        "unknown_database": original.replace(
            b"ToolBObjektTasterDatabase", b"ToolBUnknownDummyDatabase"
        ),
        "bad_header": b"invalid",
    }[corrupt]
    project.persist_device_blob(current(project, nid), "PT2EtsPlugin", data)
    device = current(project, nid)
    assert [o.id for o in device.com_objects] == identity
    assert [o.dpt.code for o in device.get_visible_com_objects()] == ["1.000", "0.000"]
    assert device.native_objects is None
    assert project.device_binary_data(device, "PT2EtsPlugin") == data
    project.close()
